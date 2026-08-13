"""Serving an archived copy when the portal will not.

The archive answers a question the portal cannot: *what did this file say?*
A resource that reads today may not read tomorrow — the 2026-08-08 census of
the whole catalog found 15 links already dead and 99 institutions whose sites
had grown rules refusing programmatic access.
"""

from __future__ import annotations

import json

import pytest

from datosgobdo_mcp import analytics, archive


@pytest.fixture
def archived(tmp_path, monkeypatch, sample_csv_bytes):
    """A one-resource archive on disk, shaped like `mirror/manifest.json`."""
    import duckdb

    root = tmp_path / "archive"
    (root / "parquet").mkdir(parents=True)
    src = tmp_path / "n.csv"
    src.write_bytes(sample_csv_bytes)
    parquet = root / "parquet" / "r1.parquet"
    duckdb.connect().execute(
        f"COPY (SELECT * FROM read_csv_auto('{src}')) TO '{parquet}' (FORMAT PARQUET)"
    )
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "datosgobdo-mirror/1",
                "resources": {
                    "https://example.test/caido.csv": {
                        "parquet": "parquet/r1.parquet",
                        "sha256": "abc123",
                        "fetched_at": "Tue, 07 Jul 2026 15:01:24 GMT",
                        "licence": "Open Data Commons Open Database License (ODbL)",
                        "parser_build": "d800daf0",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(archive.ENV_DIR, str(root))
    return root


async def test_the_archive_is_off_unless_asked_for(httpx_mock, tmp_cache_dir, monkeypatch):
    """Silence is the wrong default. An operator has to opt in."""
    monkeypatch.delenv(archive.ENV_DIR, raising=False)
    url = "https://example.test/caido.csv"
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "e"})
    httpx_mock.add_response(url=url, method="GET", status_code=503)
    out = await analytics.get_resource_schema(url, "csv")
    assert "error" in out


async def test_a_dead_portal_falls_back_to_the_archived_copy(httpx_mock, tmp_cache_dir, archived):
    url = "https://example.test/caido.csv"
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "e"})
    httpx_mock.add_response(url=url, method="GET", status_code=503)
    out = await analytics.get_resource_schema(url, "csv")
    assert "error" not in out, out
    assert out["row_count"] == 7


async def test_an_archived_answer_says_so(httpx_mock, tmp_cache_dir, archived):
    """Non-negotiable. A tool that answers with yesterday's copy as though it
    were today's has stopped being an audit tool."""
    url = "https://example.test/caido.csv"
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "e"})
    httpx_mock.add_response(url=url, method="GET", status_code=503)
    out = await analytics.summarize_resource(url, "csv")
    prov = out["cache"]["provenance"]
    assert out["cache"]["cache"] == "archive"
    assert prov["source"] == "archive"
    assert prov["original_url"] == url
    assert prov["sha256"] == "abc123"
    assert prov["fetched_at"] == "Tue, 07 Jul 2026 15:01:24 GMT"
    assert prov["licence"].startswith("Open Data Commons")
    assert "503" in prov["reason"] or "Download" in prov["reason"]
    assert "not from the portal" in prov["note"]


async def test_the_portal_still_wins_when_it_answers(httpx_mock, tmp_cache_dir, archived):
    """The archive is a fallback, not a cache. Fresh data is the point."""
    url = "https://example.test/caido.csv"
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "e"})
    httpx_mock.add_response(url=url, method="GET", content=b"a;b\n1;2\n")
    out = await analytics.get_resource_schema(url, "csv")
    assert out["row_count"] == 1
    assert out["cache"]["cache"] != "archive"


async def test_a_url_the_archive_never_held_still_fails(httpx_mock, tmp_cache_dir, archived):
    """An archive only holds what could be downloaded.

    It therefore does not contain the resources a portal refuses — the natural
    assumption, and a false one.
    """
    url = "https://example.test/nunca-descargado.csv"
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "e"})
    httpx_mock.add_response(url=url, method="GET", status_code=403)
    out = await analytics.get_resource_schema(url, "csv")
    assert "error" in out


def test_a_manifest_promising_a_missing_file_is_ignored(tmp_path, monkeypatch):
    """Worse than an absent manifest is one that lies about what it has."""
    root = tmp_path / "a"
    root.mkdir()
    (root / "manifest.json").write_text(
        json.dumps({"resources": {"https://x.test/y.csv": {"parquet": "parquet/no.parquet"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(archive.ENV_DIR, str(root))
    assert archive.lookup("https://x.test/y.csv") is None


def test_a_misconfigured_archive_dir_warns_instead_of_going_quiet(tmp_path, monkeypatch, caplog):
    """The module promises "opt-in, never silent". That has to hold for the
    misconfigured case, not only the working one: a relative value resolves
    against a working directory the client never defined (`/` on macOS), so the
    fallback silently stayed off while the operator believed it was armed.
    """
    archive._warned_dirs.clear()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(archive.ENV_DIR, "mi-archivo")
    with caplog.at_level("WARNING", logger="datosgobdo_mcp.archive"):
        assert archive.archive_dir() is None
    assert "mi-archivo" in caplog.text
    assert "relative path" in caplog.text
    assert "stays off" in caplog.text


def test_the_misconfiguration_warning_is_not_repeated(tmp_path, monkeypatch, caplog):
    """archive_dir() runs on every resource fetch — one warning per bad value."""
    archive._warned_dirs.clear()
    monkeypatch.setenv(archive.ENV_DIR, str(tmp_path / "no-existe"))
    with caplog.at_level("WARNING", logger="datosgobdo_mcp.archive"):
        for _ in range(5):
            assert archive.archive_dir() is None
    assert caplog.text.count("Archive fallback stays off") == 1
    # An absolute path is wrong for a different reason, so it gets no relative hint.
    assert "relative path" not in caplog.text

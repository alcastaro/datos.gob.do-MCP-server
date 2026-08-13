"""Hosted-readiness tests: transport gating, DuckDB resource limits, query
timeout, cache lock/atomicity."""

from __future__ import annotations

import json

import pytest

from datosgobdo_mcp import analytics, server
from datosgobdo_mcp import cache as cache_mod

# ─── hosted tool gating ───────────────────────────────────────────────────────


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setenv("DATOSGOBDO_TRANSPORT", "streamable-http")


@pytest.fixture
def mock_csv_endpoint(httpx_mock, sample_csv_url, sample_csv_bytes):
    httpx_mock.add_response(
        url=sample_csv_url, method="HEAD", headers={"etag": "v1", "last-modified": "Mon"}
    )
    httpx_mock.add_response(url=sample_csv_url, method="GET", content=sample_csv_bytes)
    return sample_csv_url


async def test_save_query_to_csv_disabled_hosted(hosted):
    out = await server.save_query_to_csv(url="https://example.test/x.csv", format="csv")
    assert out.error == "This tool is disabled in hosted mode"
    assert "stdio" in out.hint


def test_clear_cache_disabled_hosted(hosted):
    out = server.clear_cache()
    assert out.error == "This tool is disabled in hosted mode"
    assert out.removed_entries is None


def test_cache_stats_redacts_path_hosted(hosted, monkeypatch):
    monkeypatch.setattr(
        server,
        "_get_cache_stats",
        lambda: {"cache_dir": "/srv/secret", "entries": 1, "total_bytes": 10, "max_bytes": 99},
    )
    out = server.get_cache_stats()
    assert out.cache_dir is None
    assert out.entries == 1


def test_tools_enabled_in_stdio_mode(monkeypatch, tmp_cache_dir):
    monkeypatch.delenv("DATOSGOBDO_TRANSPORT", raising=False)
    out = server.clear_cache()
    assert out.error is None
    assert isinstance(out.removed_entries, int)


# ─── DuckDB resource limits ───────────────────────────────────────────────────


def test_new_con_applies_default_limits():
    con = analytics._new_con()
    try:
        mem = con.execute("SELECT current_setting('memory_limit')").fetchone()[0]
        threads = con.execute("SELECT current_setting('threads')").fetchone()[0]
        assert mem.replace(" ", "").upper() in ("2.0GIB", "1.8GIB", "2GB")
        assert int(threads) == 4
    finally:
        con.close()


def test_new_con_env_overrides(monkeypatch):
    monkeypatch.setenv("DATOSGOBDO_DUCKDB_MEMORY", "512MB")
    monkeypatch.setenv("DATOSGOBDO_DUCKDB_THREADS", "2")
    con = analytics._new_con()
    try:
        threads = con.execute("SELECT current_setting('threads')").fetchone()[0]
        assert int(threads) == 2
    finally:
        con.close()


def test_new_con_rejects_invalid_memory_env(monkeypatch):
    """An env value that fails validation falls back to the default instead of
    reaching SQL (the env var is operator-controlled, but stay paranoid)."""
    monkeypatch.setenv("DATOSGOBDO_DUCKDB_MEMORY", "2GB'; ATTACH ':memory:'")
    con = analytics._new_con()  # must not raise
    con.close()


# ─── query timeout ────────────────────────────────────────────────────────────


async def test_query_timeout_interrupts_long_query(mock_csv_endpoint, tmp_cache_dir, monkeypatch):
    monkeypatch.setenv("DATOSGOBDO_QUERY_TIMEOUT", "0.05")
    out = await analytics.query_resource(
        mock_csv_endpoint,
        "csv",
        # Cartesian blowup — far longer than 50 ms.
        sql="SELECT count(*) FROM data a, data b, range(100000000) r",
    )
    assert "error" in out


async def test_query_runs_normally_with_timeout_set(mock_csv_endpoint, tmp_cache_dir, monkeypatch):
    monkeypatch.setenv("DATOSGOBDO_QUERY_TIMEOUT", "30")
    out = await analytics.query_resource(
        mock_csv_endpoint, "csv", sql="SELECT count(*) c FROM data"
    )
    assert "error" not in out
    assert out["rows"][0][0] == 7


async def test_query_invalid_timeout_env_ignored(mock_csv_endpoint, tmp_cache_dir, monkeypatch):
    monkeypatch.setenv("DATOSGOBDO_QUERY_TIMEOUT", "banana")
    out = await analytics.query_resource(mock_csv_endpoint, "csv", sql="SELECT 1 AS x FROM data")
    assert "error" not in out


# ─── cache: atomic index + deterministic eviction + lock ─────────────────────


def test_save_index_is_atomic(tmp_path):
    c = cache_mod.LocalDiskCache(cache_dir=tmp_path)
    c._index = {"k": {"accessed_at": 1.0, "bytes": 5}}
    c._save_index()
    assert json.loads(c.index_path.read_text())["k"]["bytes"] == 5
    assert not c.index_path.with_suffix(".json.tmp").exists()


def test_eviction_tie_break_is_deterministic(tmp_path):
    # Roomy while writing, so finalize does not evict as we go; the cap under
    # test is applied explicitly below.
    c = cache_mod.LocalDiskCache(cache_dir=tmp_path, max_bytes=10_000)
    for key in ("zz", "aa"):
        p = c.put_path(key)
        p.write_bytes(b"123456")  # 6 bytes each → 12 total > 10
        c.finalize(key, url=f"https://example.test/{key}.csv")
    # Identical timestamps, written to disk: eviction reloads the index inside
    # the lock, so state that exists only in memory is not state it can see.
    index = json.loads((tmp_path / cache_mod.INDEX_FILENAME).read_text())
    for key in ("zz", "aa"):
        index[key]["accessed_at"] = 100.0
        index[key]["bytes"] = 6
    (tmp_path / cache_mod.INDEX_FILENAME).write_text(json.dumps(index))
    c.evict_to_fit(10)
    # Same accessed_at → lexicographically smaller key evicted first.
    assert not (tmp_path / "aa.parquet").exists()
    assert (tmp_path / "zz.parquet").exists()


def test_lock_reentrant_flow_does_not_deadlock(tmp_path):
    """finalize() locks then calls the locked eviction helper — must not
    re-acquire (that would deadlock a same-process flow on some platforms)."""
    c = cache_mod.LocalDiskCache(cache_dir=tmp_path, max_bytes=4)
    p = c.put_path("k1")
    p.write_bytes(b"123456")
    c.finalize("k1", url="https://a/x.csv")  # triggers eviction inside the lock
    assert c.stats()["entries"] in (0, 1)  # completed without deadlock

"""Unit tests for preview.py."""

from __future__ import annotations

import json

from datosgobdo_mcp import analytics, preview

# ─── Sample selection ─────────────────────────────────────────────────────────


def test_select_rows_head():
    rows = [[i] for i in range(10)]
    out = preview._select_rows(rows, 3, "head")
    assert out == [[0], [1], [2]]


def test_select_rows_tail():
    rows = [[i] for i in range(10)]
    out = preview._select_rows(rows, 3, "tail")
    assert out == [[7], [8], [9]]


def test_select_rows_random_returns_subset():
    rows = [[i] for i in range(20)]
    out = preview._select_rows(rows, 5, "random")
    assert len(out) == 5
    flat = {r[0] for r in out}
    assert flat.issubset(set(range(20)))


def test_select_rows_random_n_gte_total_returns_all():
    rows = [[i] for i in range(3)]
    out = preview._select_rows(rows, 5, "random")
    assert sorted(out) == rows


def test_select_rows_empty_input():
    assert preview._select_rows([], 5, "head") == []


# ─── CSV preview ──────────────────────────────────────────────────────────────


def test_preview_csv_semicolon_delimiter(sample_csv_bytes):
    out = preview._preview_csv(sample_csv_bytes, rows=10, sample="head")
    assert out["format"] == "csv"
    assert out["delimiter"] == ";"
    assert out["columns"] == [
        "Nombre",
        "Departamento",
        "Estatus",
        "Sueldo",
        "Mes",
        "Año",
    ]
    assert out["total_rows_in_download"] == 7
    assert out["rows_returned"] == 7


def test_preview_csv_tail_mode(sample_csv_bytes):
    out = preview._preview_csv(sample_csv_bytes, rows=2, sample="tail")
    assert out["rows_returned"] == 2
    # Last two rows in the fixture.
    assert out["rows"][-1][0] == "ANA PEREZ"
    assert out["rows"][-1][4] == "Marzo"


def test_preview_csv_latin1_falls_back(sample_csv_latin1_bytes):
    out = preview._preview_csv(sample_csv_latin1_bytes, rows=5, sample="head")
    # Encoding should be detected as cp1252 / latin-1 family.
    assert out["encoding"] in ("cp1252", "iso-8859-1", "utf-8 (with replacements)")
    # Header should still parse — Año contains a non-ASCII char.
    assert any("Año" in c or "A\xf1o" in c for c in out["columns"])


def test_preview_csv_empty_returns_error():
    out = preview._preview_csv(b"", rows=5, sample="head")
    assert "error" in out


# ─── JSON preview ─────────────────────────────────────────────────────────────


def test_preview_json_array():
    payload = json.dumps([{"a": 1}, {"a": 2}, {"a": 3}]).encode("utf-8")
    out = preview._preview_json(payload, rows=2, sample="head")
    assert out["format"] == "json-array"
    assert out["total_items"] == 3
    assert out["rows_returned"] == 2


def test_preview_json_object_with_data_key():
    payload = json.dumps({"data": [{"x": 1}], "meta": {"page": 1}}).encode("utf-8")
    out = preview._preview_json(payload, rows=5, sample="head")
    assert out["format"] == "json-object"
    assert out["data_key"] == "data"
    assert out["total_items"] == 1
    assert "meta" in out["other_keys"]


def test_preview_json_invalid_returns_error():
    out = preview._preview_json(b"not json {", rows=5, sample="head")
    assert "error" in out


# ─── XLSX preview ─────────────────────────────────────────────────────────────


def test_preview_xlsx_extracts_header_and_rows(small_xlsx_bytes):
    out = preview._preview_xlsx(small_xlsx_bytes, rows=2, sample="head")
    assert out["format"] == "xlsx"
    assert out["columns"] == ["nombre", "estatus", "sueldo"]
    assert out["total_rows_in_download"] == 3
    assert out["rows_returned"] == 2
    assert out["rows"][0] == ["ANA", "FIJO", 25000]


# ─── preview_resource_data end-to-end (mocked HTTP) ───────────────────────────


async def test_preview_resource_data_unsupported_format():
    out = await preview.preview_resource_data("https://example.test/x.pdf", fmt="pdf", rows=5)
    assert "error" in out


async def test_preview_resource_data_csv_via_http_mock(httpx_mock, sample_csv_bytes):
    url = "https://example.test/n.csv"
    httpx_mock.add_response(url=url, content=sample_csv_bytes)
    out = await preview.preview_resource_data(url, fmt="csv", rows=3, sample="head")
    assert out["format"] == "csv"
    assert out["source_url"] == url
    assert out["bytes_downloaded"] == len(sample_csv_bytes)
    assert out["download_truncated"] is False
    assert out["rows_returned"] == 3


async def test_preview_rejects_html_error_page(httpx_mock):
    """Portals answer dead download links with a styled page and HTTP 200.
    Parsed as CSV that becomes fake data; it must be reported as an error."""
    url = "https://example.test/dead.csv"
    httpx_mock.add_response(
        url=url,
        method="GET",
        content=b"<!DOCTYPE html>\n<html><body>Archivo no disponible</body></html>",
    )
    out = await preview.preview_resource_data(url, "csv")
    assert "error" in out
    assert "HTML" in out["error"]


async def test_preview_returns_netguard_error_as_result(unresolvable_host):
    out = await preview.preview_resource_data(unresolvable_host, "csv")
    assert "error" in out
    assert "DNS resolution failed" in out["error"]


async def test_preview_supports_ods_via_cache(mock_ods_endpoint, tmp_cache_dir):
    """ODS is about a third of this catalog; refusing it made preview useless
    there while the analytics tools read the same files fine."""
    out = await preview.preview_resource_data(mock_ods_endpoint, "ods", rows=3)
    assert "error" not in out, out
    assert out["columns"]
    assert out["source"] == "parquet-cache"


async def test_preview_reuses_the_warm_cache_without_touching_the_network(
    sample_csv_url, sample_csv_bytes, tmp_cache_dir, httpx_mock
):
    """Once analytics has read a resource, preview must not download it again.

    Measured before this: preview was 20-25x slower than every other tool and
    put a fresh request on the portal each time an assistant glanced at a file
    it had already read.
    """
    httpx_mock.add_response(url=sample_csv_url, method="HEAD", headers={"etag": "w1"})
    httpx_mock.add_response(url=sample_csv_url, method="GET", content=sample_csv_bytes)
    await analytics.get_resource_schema(sample_csv_url, "csv")
    before = len(httpx_mock.get_requests())

    out = await preview.preview_resource_data(sample_csv_url, "csv", rows=3)
    assert "error" not in out, out
    assert out["source"] == "parquet-cache"
    assert len(httpx_mock.get_requests()) == before, "preview hit the network again"

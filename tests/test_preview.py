"""Unit tests for preview.py."""

from __future__ import annotations

import json

import pytest

from datosgobdo_mcp import preview


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
    assert out["columns"] == ["Nombre", "Departamento", "Estatus", "Sueldo", "Mes", "Año"]
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
    out = await preview.preview_resource_data(
        "https://example.test/x.pdf", fmt="pdf", rows=5
    )
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

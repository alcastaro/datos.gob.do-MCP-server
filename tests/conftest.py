"""Shared pytest fixtures.

Most analytics tests use a tiny in-memory CSV / XLSX served via pytest-httpx
mocks so they're hermetic and fast. A small handful of integration tests can
opt into hitting the real API by setting RUN_LIVE_TESTS=1; they're skipped by
default.
"""

from __future__ import annotations

import io
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from datosgobdo_mcp import cache as cache_mod

SAMPLE_NOMINA_CSV = (
    "Nombre;Departamento;Estatus;Sueldo;Mes;Año\n"
    "ANA PEREZ;RRHH;FIJO;25000;Abril;2026\n"
    "BENITO LOPEZ;TI;EMPLEADOS TEMPORALES;30000;Abril;2026\n"
    "CARLA RUIZ;RRHH;FIJO;28000;Abril;2026\n"
    "DIEGO SANTOS;TI;FIJO;45000;Abril;2026\n"
    "EVA MORALES;RRHH;TRAMITE DE PENSION;15000;Abril;2026\n"
    "FELIPE TORRES;TI;EMPLEADOS TEMPORALES;32000;Marzo;2026\n"
    "ANA PEREZ;RRHH;FIJO;25000;Marzo;2026\n"
)


@pytest.fixture(autouse=True)
def _netguard_trusts_test_host(monkeypatch):
    """Keep the SSRF guard ACTIVE in tests, but trust the mock host. Tests that
    exercise the guard itself override these env vars locally."""
    monkeypatch.setenv("DATOSGOBDO_ALLOW_HOSTS", "example.test")


@pytest.fixture
def sample_csv_bytes() -> bytes:
    """Tiny semicolon-delimited CSV that mirrors the Agricultura nómina shape."""
    return SAMPLE_NOMINA_CSV.encode("utf-8")


@pytest.fixture
def sample_csv_latin1_bytes() -> bytes:
    """Same CSV but encoded in Latin-1 to exercise the encoding fallback."""
    return SAMPLE_NOMINA_CSV.encode("latin-1")


@pytest.fixture
def sample_csv_url() -> str:
    return "https://example.test/nomina.csv"


@pytest.fixture
def tmp_cache_dir(tmp_path, monkeypatch) -> Iterator[Path]:
    """Redirect the cache singleton to a per-test temp dir."""
    cache_dir = tmp_path / "cache"
    monkeypatch.setenv("DATOSGOBDO_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("DATOSGOBDO_CACHE_MAX_BYTES", str(50 * 1024 * 1024))
    # Force re-init of the module-level singleton.
    cache_mod._singleton = None
    yield cache_dir
    cache_mod._singleton = None


@pytest.fixture
def small_xlsx_bytes() -> bytes:
    """In-memory XLSX with one sheet, three columns."""
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "datos"
    ws.append(["nombre", "estatus", "sueldo"])
    ws.append(["ANA", "FIJO", 25000])
    ws.append(["BENITO", "TEMPORAL", 30000])
    ws.append(["CARLA", "FIJO", 28000])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


SAMPLE_JSON_ARRAY = '[{"nombre":"ANA","sueldo":25000},{"nombre":"BENITO","sueldo":30000},{"nombre":"CARLA","sueldo":28000}]'


@pytest.fixture
def sample_json_bytes() -> bytes:
    return SAMPLE_JSON_ARRAY.encode("utf-8")


@pytest.fixture
def json_url() -> str:
    return "https://example.test/data.json"


@pytest.fixture
def mock_json_endpoint(httpx_mock, json_url, sample_json_bytes):
    httpx_mock.add_response(url=json_url, method="HEAD", headers={"etag": "j1"})
    httpx_mock.add_response(url=json_url, method="GET", content=sample_json_bytes)
    return json_url


@pytest.fixture
def xlsx_url() -> str:
    return "https://example.test/data.xlsx"


@pytest.fixture
def mock_xlsx_endpoint(httpx_mock, xlsx_url, small_xlsx_bytes):
    httpx_mock.add_response(url=xlsx_url, method="HEAD", headers={"etag": "x1"})
    httpx_mock.add_response(url=xlsx_url, method="GET", content=small_xlsx_bytes)
    return xlsx_url


@pytest.fixture
def sample_ods_bytes() -> bytes:
    """Tiny ODS spreadsheet built with odfpy."""
    import io

    from odf.opendocument import OpenDocumentSpreadsheet
    from odf.table import Table, TableCell, TableRow
    from odf.text import P

    doc = OpenDocumentSpreadsheet()
    table = Table(name="Sheet1")
    for row_vals in [
        ["nombre", "valor"],
        ["ANA", "100"],
        ["BENITO", "200"],
        ["CARLA", "300"],
    ]:
        row = TableRow()
        for v in row_vals:
            cell = TableCell()
            cell.addElement(P(text=v))
            row.addElement(cell)
        table.addElement(row)
    doc.spreadsheet.addElement(table)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


@pytest.fixture
def ods_url() -> str:
    return "https://example.test/data.ods"


@pytest.fixture
def mock_ods_endpoint(httpx_mock, ods_url, sample_ods_bytes):
    httpx_mock.add_response(url=ods_url, method="HEAD", headers={"etag": "ods1"})
    httpx_mock.add_response(url=ods_url, method="GET", content=sample_ods_bytes)
    return ods_url


@pytest.fixture
def mock_latin1_endpoint(httpx_mock, sample_csv_url, sample_csv_latin1_bytes):
    httpx_mock.add_response(url=sample_csv_url, method="HEAD", headers={"etag": "lat1"})
    httpx_mock.add_response(url=sample_csv_url, method="GET", content=sample_csv_latin1_bytes)
    return sample_csv_url


def pytest_collection_modifyitems(config, items):
    """Auto-skip live network tests unless RUN_LIVE_TESTS=1."""
    if os.environ.get("RUN_LIVE_TESTS") == "1":
        return
    skipper = pytest.mark.skip(reason="set RUN_LIVE_TESTS=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skipper)


def pytest_configure(config):
    config.addinivalue_line("markers", "live: hits real datos.gob.do API (skipped by default)")


SAMPLE_CSV_WITH_DUPES = (
    "Nombre;Cedula;Sueldo\n"
    "ANA PEREZ;001-0000001-1;25000\n"
    "BENITO LOPEZ;001-0000002-2;30000\n"
    "ANA PEREZ;001-0000001-1;25000\n"
    "CARLA RUIZ;001-0000003-3;28000\n"
    "BENITO LOPEZ;001-0000002-2;99000\n"
)


@pytest.fixture
def sample_dupes_csv_bytes() -> bytes:
    return SAMPLE_CSV_WITH_DUPES.encode("utf-8")


@pytest.fixture
def dupes_csv_url() -> str:
    return "https://example.test/dupes.csv"


@pytest.fixture
def mock_dupes_endpoint(httpx_mock, dupes_csv_url, sample_dupes_csv_bytes):
    httpx_mock.add_response(url=dupes_csv_url, method="HEAD", headers={"etag": "d1"})
    httpx_mock.add_response(url=dupes_csv_url, method="GET", content=sample_dupes_csv_bytes)
    return dupes_csv_url


SAMPLE_CSV_WITH_OUTLIERS = (
    "Nombre;Sueldo\n"
    "ANA;25000\n"
    "BENITO;28000\n"
    "CARLA;30000\n"
    "DIEGO;27000\n"
    "EVA;29000\n"
    "FRANK;26000\n"
    "GINA;31000\n"
    "HUGO;999999\n"
    "IVAN;100\n"
)


@pytest.fixture
def sample_outliers_csv_bytes() -> bytes:
    return SAMPLE_CSV_WITH_OUTLIERS.encode("utf-8")


@pytest.fixture
def outliers_csv_url() -> str:
    return "https://example.test/outliers.csv"


@pytest.fixture
def mock_outliers_endpoint(httpx_mock, outliers_csv_url, sample_outliers_csv_bytes):
    httpx_mock.add_response(url=outliers_csv_url, method="HEAD", headers={"etag": "o1"})
    httpx_mock.add_response(url=outliers_csv_url, method="GET", content=sample_outliers_csv_bytes)
    return outliers_csv_url

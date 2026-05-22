"""Shared pytest fixtures.

Most analytics tests use a tiny in-memory CSV / XLSX served via pytest-httpx
mocks so they're hermetic and fast. A small handful of integration tests can
opt into hitting the real API by setting RUN_LIVE_TESTS=1; they're skipped by
default.
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Iterator

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


def pytest_collection_modifyitems(config, items):
    """Auto-skip live network tests unless RUN_LIVE_TESTS=1."""
    if os.environ.get("RUN_LIVE_TESTS") == "1":
        return
    skipper = pytest.mark.skip(reason="set RUN_LIVE_TESTS=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skipper)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "live: hits real datos.gob.do API (skipped by default)"
    )

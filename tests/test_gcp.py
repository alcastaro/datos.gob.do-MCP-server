"""GCP pipeline tests — hermetic. google-cloud libs are NOT installed; fake
modules are injected into sys.modules and ensure_cached is stubbed."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

from datosgobdo_mcp import gcp

# ─── helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_google(monkeypatch):
    """Inject fake google.cloud.{storage,bigquery} modules; return their mocks."""
    storage_mod = types.ModuleType("google.cloud.storage")
    bigquery_mod = types.ModuleType("google.cloud.bigquery")

    storage_client = MagicMock(name="storage.Client()")
    storage_mod.Client = MagicMock(return_value=storage_client)

    bq_client = MagicMock(name="bigquery.Client()")
    bigquery_mod.Client = MagicMock(return_value=bq_client)
    bigquery_mod.ExternalConfig = MagicMock()
    bigquery_mod.Table = MagicMock()
    bigquery_mod.LoadJobConfig = MagicMock()

    cloud_mod = types.ModuleType("google.cloud")
    cloud_mod.storage = storage_mod
    cloud_mod.bigquery = bigquery_mod
    google_mod = types.ModuleType("google")
    google_mod.cloud = cloud_mod

    monkeypatch.setitem(sys.modules, "google", google_mod)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud_mod)
    monkeypatch.setitem(sys.modules, "google.cloud.storage", storage_mod)
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", bigquery_mod)
    monkeypatch.setattr(gcp, "gcp_available", lambda: True)

    return types.SimpleNamespace(
        storage=storage_mod,
        bigquery=bigquery_mod,
        storage_client=storage_client,
        bq_client=bq_client,
    )


@pytest.fixture
def stub_cache(monkeypatch, tmp_path):
    """ensure_cached → (tmp parquet path, meta) without network/DuckDB."""
    parquet = tmp_path / "cached.parquet"
    parquet.write_bytes(b"PAR1fake")

    async def fake_ensure_cached(url, kind, cache=None, force_refresh=False):
        return parquet, {"cache_key": "k1", "cache_hit": True}

    monkeypatch.setattr(gcp, "ensure_cached", fake_ensure_cached)
    return parquet


# ─── availability / registration ──────────────────────────────────────────────


def test_gcp_not_available_in_this_environment():
    assert gcp.gcp_available() is False


def test_register_returns_false_without_libs():
    from mcp.server.fastmcp import FastMCP

    fresh = FastMCP("test-no-gcp")
    assert gcp.register_gcp_tools(fresh) is False


async def test_register_adds_three_tools_with_fake_libs(fake_google):
    from mcp.server.fastmcp import FastMCP

    fresh = FastMCP("test-gcp")
    assert gcp.register_gcp_tools(fresh) is True
    tools = await fresh.list_tools()
    names = {t.name for t in tools}
    assert names == {
        "load_resource_to_bigquery_tool",
        "list_bigquery_exports_tool",
        "get_bigquery_table_info_tool",
    }
    by_name = {t.name: t for t in tools}
    assert by_name["load_resource_to_bigquery_tool"].annotations.readOnlyHint is False
    assert by_name["list_bigquery_exports_tool"].annotations.readOnlyHint is True


async def test_server_tool_count_stays_24_without_gcp():
    from datosgobdo_mcp import server

    tools = await server.mcp.list_tools()
    assert len(tools) == 24


# ─── load_resource_to_bigquery ────────────────────────────────────────────────


async def test_load_not_installed_error():
    out = await gcp.load_resource_to_bigquery("https://x/y.csv", "csv", project="p", dataset="d")
    assert out["error"] == "GCP support not installed"
    assert "[gcp]" in out["hint"]


async def test_load_unsupported_format(fake_google):
    out = await gcp.load_resource_to_bigquery(
        "https://x/y.pdf", "pdf", project="p", dataset="d", gcs_bucket="b"
    )
    assert "not supported" in out["error"]


async def test_load_missing_bucket(fake_google, stub_cache, monkeypatch):
    monkeypatch.delenv("DATOSGOBDO_GCS_BUCKET", raising=False)
    out = await gcp.load_resource_to_bigquery("https://x/y.csv", "csv", project="p", dataset="d")
    assert out["error"] == "No GCS bucket specified"
    assert "DATOSGOBDO_GCS_BUCKET" in out["hint"]


async def test_load_bucket_from_env(fake_google, stub_cache, monkeypatch):
    monkeypatch.setenv("DATOSGOBDO_GCS_BUCKET", "env-bucket")
    out = await gcp.load_resource_to_bigquery(
        "https://x/nomina.csv", "csv", project="p", dataset="d"
    )
    assert "error" not in out
    assert out["gcs_uri"] == "gs://env-bucket/datosgobdo/nomina.parquet"


async def test_load_external_mode_creates_external_table(fake_google, stub_cache):
    out = await gcp.load_resource_to_bigquery(
        "https://x/nomina.csv", "csv", project="proj", dataset="ds", gcs_bucket="bkt"
    )
    assert out["table"] == "proj.ds.nomina"
    assert out["mode"] == "external"
    assert out["gcs_uri"] == "gs://bkt/datosgobdo/nomina.parquet"
    assert "SELECT * FROM `proj.ds.nomina`" in out["hint"]
    assert out["cache"]["cache_key"] == "k1"
    # External path: create_table used, load job NOT used.
    assert fake_google.bq_client.create_table.called
    assert not fake_google.bq_client.load_table_from_uri.called
    # Upload went through the storage client with the parquet file.
    blob = fake_google.storage_client.bucket.return_value.blob
    blob.assert_called_once_with("datosgobdo/nomina.parquet")
    blob.return_value.upload_from_filename.assert_called_once_with(str(stub_cache))


async def test_load_load_mode_runs_load_job(fake_google, stub_cache):
    fake_google.bq_client.get_table.return_value.num_rows = 777
    out = await gcp.load_resource_to_bigquery(
        "https://x/nomina.csv", "csv", project="proj", dataset="ds", gcs_bucket="bkt", mode="load"
    )
    assert out["mode"] == "load"
    assert out["rows"] == 777
    assert fake_google.bq_client.load_table_from_uri.called
    fake_google.bq_client.load_table_from_uri.return_value.result.assert_called_once()


async def test_load_wraps_gcp_exception(fake_google, stub_cache):
    fake_google.storage_client.bucket.side_effect = RuntimeError("403 forbidden")
    out = await gcp.load_resource_to_bigquery(
        "https://x/y.csv", "csv", project="p", dataset="d", gcs_bucket="b"
    )
    assert out["error"].startswith("GCP: ")
    assert "403" in out["error"]


async def test_load_cache_failure_wrapped(fake_google, monkeypatch):
    async def boom(url, kind, cache=None, force_refresh=False):
        raise RuntimeError("download died")

    monkeypatch.setattr(gcp, "ensure_cached", boom)
    out = await gcp.load_resource_to_bigquery(
        "https://x/y.csv", "csv", project="p", dataset="d", gcs_bucket="b"
    )
    assert out["error"].startswith("Could not load resource")


# ─── table-name sanitization ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://x/Nómina Abril 2026.csv", "n_mina_abril_2026"),
        ("https://x/2026-presupuesto.csv", "t_2026_presupuesto"),
        ("https://x/data.csv?token=abc", "data"),
        ("https://x/---.csv", "resource"),
        ("https://x/" + "a" * 100 + ".csv", "a" * 60),
    ],
)
def test_auto_table_name(url, expected):
    assert gcp._auto_table_name(url) == expected


# ─── listers ──────────────────────────────────────────────────────────────────


async def test_list_exports_happy(fake_google):
    t = MagicMock(table_id="nomina", table_type="EXTERNAL", created=None)
    fake_google.bq_client.list_tables.return_value = [t]
    out = await gcp.list_bigquery_exports(project="p", dataset="d")
    assert out["count"] == 1
    assert out["tables"][0]["table"] == "p.d.nomina"
    assert out["tables"][0]["type"] == "EXTERNAL"


async def test_list_exports_not_installed():
    out = await gcp.list_bigquery_exports(project="p", dataset="d")
    assert out["error"] == "GCP support not installed"


async def test_list_exports_wraps_exception(fake_google):
    fake_google.bq_client.list_tables.side_effect = RuntimeError("404 dataset")
    out = await gcp.list_bigquery_exports(project="p", dataset="d")
    assert out["error"].startswith("GCP: ")


async def test_table_info_happy_external(fake_google):
    field = MagicMock(field_type="STRING")
    field.name = "Nombre"
    t = MagicMock(num_rows=10, schema=[field])
    t.external_data_configuration.source_uris = ["gs://b/x.parquet"]
    fake_google.bq_client.get_table.return_value = t
    out = await gcp.get_bigquery_table_info(project="p", dataset="d", table="t")
    assert out["num_rows"] == 10
    assert out["schema"] == [{"name": "Nombre", "type": "STRING"}]
    assert out["external_source_uris"] == ["gs://b/x.parquet"]


async def test_table_info_not_installed():
    out = await gcp.get_bigquery_table_info(project="p", dataset="d", table="t")
    assert out["error"] == "GCP support not installed"

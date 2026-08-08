"""Hermetic tests for the @mcp.tool wrapper functions in server.py.

FastMCP's @mcp.tool() registers the function and returns the original, so the
wrappers are directly awaitable. Each test monkeypatches the underlying
implementation (ckan.* / analytics underscore aliases / preview) with a
recording stub, then asserts:

  a) the wrapper passes its arguments through to the impl unchanged,
  b) the return value is wrapped in the right Pydantic model (or passed
     through untouched for the plain dict/list CKAN tools),
  c) for a couple of data tools, that an {"error": ...} impl return still
     constructs the model (error envelope flows through).

No network, no disk, no DuckDB.
"""

from __future__ import annotations

from typing import Any

from datosgobdo_mcp import server
from datosgobdo_mcp.models import (
    AggregateResult,
    CacheStatsResult,
    ClearCacheResult,
    DuplicatesResult,
    FilterResult,
    OutliersResult,
    PreviewResult,
    QuantilesResult,
    QueryResult,
    SaveCsvResult,
    SchemaResult,
    SummaryResult,
)

URL = "https://example.test/data.csv"


def _async_stub(return_value: Any) -> tuple[Any, dict]:
    """Async stub that records its call and returns a canned value."""
    calls: dict = {}

    async def stub(*args: Any, **kwargs: Any) -> Any:
        calls["args"] = args
        calls["kwargs"] = kwargs
        return return_value

    return stub, calls


def _sync_stub(return_value: Any) -> tuple[Any, dict]:
    """Sync stub that records its call and returns a canned value."""
    calls: dict = {}

    def stub(*args: Any, **kwargs: Any) -> Any:
        calls["args"] = args
        calls["kwargs"] = kwargs
        return return_value

    return stub, calls


# ─── Discovery (plain dict pass-through) ──────────────────────────────────────


async def test_search_datasets_passes_args_and_returns_dict(monkeypatch):
    payload = {"count": 1, "results": [{"name": "nomina-general"}]}
    stub, calls = _async_stub(payload)
    monkeypatch.setattr(server.ckan, "search_datasets", stub)

    result = await server.search_datasets(
        query="presupuesto",
        organization="digepres",
        tag="finanzas",
        group="economia",
        limit=25,
        offset=5,
    )

    assert result is payload
    assert calls["kwargs"] == {
        "query": "presupuesto",
        "organization": "digepres",
        "tag": "finanzas",
        "group": "economia",
        "limit": 25,
        "offset": 5,
    }


async def test_search_datasets_defaults(monkeypatch):
    stub, calls = _async_stub({"count": 0, "results": []})
    monkeypatch.setattr(server.ckan, "search_datasets", stub)

    result = await server.search_datasets()

    assert result == {"count": 0, "results": []}
    assert calls["kwargs"] == {
        "query": None,
        "organization": None,
        "tag": None,
        "group": None,
        "limit": 10,
        "offset": 0,
    }


async def test_get_dataset_passes_id(monkeypatch):
    payload = {"name": "nomina-general", "resources": []}
    stub, calls = _async_stub(payload)
    monkeypatch.setattr(server.ckan, "get_dataset", stub)

    result = await server.get_dataset(id="nomina-general")

    assert result is payload
    assert calls["args"] == ("nomina-general",)


async def test_list_recent_datasets_passes_limit(monkeypatch):
    payload = {"datasets": []}
    stub, calls = _async_stub(payload)
    monkeypatch.setattr(server.ckan, "list_recent_datasets", stub)

    result = await server.list_recent_datasets(limit=7)

    assert result is payload
    assert calls["kwargs"] == {"limit": 7}


async def test_get_site_stats_passes_through(monkeypatch):
    payload = {"datasets": 1054, "organizations": 266}
    stub, calls = _async_stub(payload)
    monkeypatch.setattr(server.ckan, "get_site_stats", stub)

    result = await server.get_site_stats()

    assert result is payload
    assert calls["args"] == ()
    assert calls["kwargs"] == {}


# ─── Resources ────────────────────────────────────────────────────────────────


async def test_get_resource_passes_id(monkeypatch):
    payload = {"id": "abc-123", "url": URL}
    stub, calls = _async_stub(payload)
    monkeypatch.setattr(server.ckan, "get_resource", stub)

    result = await server.get_resource(id="abc-123")

    assert result is payload
    assert calls["args"] == ("abc-123",)


async def test_search_resources_passes_args(monkeypatch):
    payload = {"count": 2, "results": []}
    stub, calls = _async_stub(payload)
    monkeypatch.setattr(server.ckan, "search_resources", stub)

    result = await server.search_resources(query="nomina", limit=15)

    assert result is payload
    assert calls["kwargs"] == {"query": "nomina", "limit": 15}


async def test_download_resource_preview_wraps_model(monkeypatch):
    payload = {
        "format": "csv",
        "source_url": URL,
        "columns": ["a", "b"],
        "rows_returned": 2,
        "rows": [[1, 2], [3, 4]],
    }
    stub, calls = _async_stub(payload)
    monkeypatch.setattr(server, "preview_resource_data", stub)

    result = await server.download_resource_preview(url=URL, format="csv", rows=5, sample="tail")

    assert isinstance(result, PreviewResult)
    assert result.columns == ["a", "b"]
    assert result.rows == [[1, 2], [3, 4]]
    assert result.error is None
    # Note arg renames: format → fmt.
    assert calls["kwargs"] == {"url": URL, "fmt": "csv", "rows": 5, "sample": "tail"}


# ─── Analytics (Pydantic-wrapped) ─────────────────────────────────────────────


async def test_get_resource_schema_wraps_model(monkeypatch):
    payload = {
        "source_url": URL,
        "format": "csv",
        "row_count": 7,
        "column_count": 1,
        "columns": [{"name": "Sueldo", "type": "BIGINT", "sample_values": [25000]}],
    }
    stub, calls = _async_stub(payload)
    monkeypatch.setattr(server, "_get_resource_schema", stub)

    result = await server.get_resource_schema(url=URL, format="csv", sample_rows=50)

    assert isinstance(result, SchemaResult)
    assert result.row_count == 7
    assert result.columns[0].name == "Sueldo"
    assert calls["kwargs"] == {"url": URL, "fmt": "csv", "sample_rows": 50}


async def test_summarize_resource_wraps_model(monkeypatch):
    payload = {
        "row_count": 7,
        "column_count": 1,
        "columns": [{"name": "Estatus", "type": "VARCHAR", "distinct_count": 3}],
    }
    stub, calls = _async_stub(payload)
    monkeypatch.setattr(server, "_summarize_resource", stub)

    result = await server.summarize_resource(url=URL, format="csv", max_categorical_top_n=5)

    assert isinstance(result, SummaryResult)
    assert result.columns[0].distinct_count == 3
    assert calls["kwargs"] == {"url": URL, "fmt": "csv", "max_categorical_top_n": 5}


async def test_filter_resource_wraps_model(monkeypatch):
    payload = {
        "matching_rows_total": 3,
        "rows_returned": 2,
        "columns": ["Nombre", "Sueldo"],
        "rows": [["ANA", 25000], ["CARLA", 28000]],
    }
    stub, calls = _async_stub(payload)
    monkeypatch.setattr(server, "_filter_resource", stub)

    filters = [{"col": "Mes", "op": "=", "val": "Abril"}]
    order_by = [{"col": "Sueldo", "dir": "desc"}]
    result = await server.filter_resource(
        url=URL,
        format="csv",
        filters=filters,
        columns=["Nombre", "Sueldo"],
        order_by=order_by,
        limit=2,
        offset=1,
    )

    assert isinstance(result, FilterResult)
    assert result.matching_rows_total == 3
    assert result.rows == [["ANA", 25000], ["CARLA", 28000]]
    assert calls["kwargs"] == {
        "url": URL,
        "fmt": "csv",
        "filters": filters,
        "columns": ["Nombre", "Sueldo"],
        "order_by": order_by,
        "limit": 2,
        "offset": 1,
    }


async def test_aggregate_resource_wraps_model(monkeypatch):
    payload = {
        "groups_returned": 1,
        "columns": ["Estatus", "empleados"],
        "rows": [["FIJO", 4]],
    }
    stub, calls = _async_stub(payload)
    monkeypatch.setattr(server, "_aggregate_resource", stub)

    aggregations = [{"col": None, "fn": "count", "alias": "empleados"}]
    having = [{"col": "empleados", "op": ">", "val": 1}]
    result = await server.aggregate_resource(
        url=URL,
        format="csv",
        aggregations=aggregations,
        group_by=["Estatus"],
        filters=None,
        having=having,
        order_by=None,
        limit=10,
    )

    assert isinstance(result, AggregateResult)
    assert result.rows == [["FIJO", 4]]
    assert calls["kwargs"] == {
        "url": URL,
        "fmt": "csv",
        "aggregations": aggregations,
        "group_by": ["Estatus"],
        "filters": None,
        "having": having,
        "order_by": None,
        "limit": 10,
    }


async def test_quantiles_resource_wraps_model(monkeypatch):
    payload = {
        "row_count": 7,
        "percentiles": [0.5],
        "columns": [{"name": "Sueldo", "type": "BIGINT", "p50": 28000}],
    }
    stub, calls = _async_stub(payload)
    monkeypatch.setattr(server, "_quantiles_resource", stub)

    result = await server.quantiles_resource(
        url=URL,
        format="csv",
        columns=["Sueldo"],
        percentiles=[0.5],
        filters=None,
    )

    assert isinstance(result, QuantilesResult)
    assert result.percentiles == [0.5]
    # Dynamic percentile key preserved via extra="allow".
    assert result.columns[0].p50 == 28000
    assert calls["kwargs"] == {
        "url": URL,
        "fmt": "csv",
        "columns": ["Sueldo"],
        "percentiles": [0.5],
        "filters": None,
    }


async def test_find_duplicates_resource_wraps_model(monkeypatch):
    payload = {
        "columns_checked": ["Nombre"],
        "duplicate_groups_found": 1,
        "groups_returned": 1,
        "total_duplicate_rows": 2,
        "columns": ["Nombre", "dup_count"],
        "rows": [["ANA PEREZ", 2]],
    }
    stub, calls = _async_stub(payload)
    monkeypatch.setattr(server, "_find_duplicates_resource", stub)

    result = await server.find_duplicates_resource(
        url=URL, format="csv", columns=["Nombre"], filters=None, limit=25
    )

    assert isinstance(result, DuplicatesResult)
    assert result.duplicate_groups_found == 1
    assert calls["kwargs"] == {
        "url": URL,
        "fmt": "csv",
        "columns": ["Nombre"],
        "filters": None,
        "limit": 25,
    }


async def test_detect_outliers_resource_wraps_model(monkeypatch):
    payload = {
        "column": "Sueldo",
        "method": "iqr",
        "q1": 26000.0,
        "q3": 30000.0,
        "iqr": 4000.0,
        "outlier_count_estimate": 2,
        "rows_returned": 2,
        "columns": ["Nombre", "Sueldo"],
        "rows": [["HUGO", 999999], ["IVAN", 100]],
    }
    stub, calls = _async_stub(payload)
    monkeypatch.setattr(server, "_detect_outliers_resource", stub)

    result = await server.detect_outliers_resource(
        url=URL, format="csv", column="Sueldo", filters=None, limit=50
    )

    assert isinstance(result, OutliersResult)
    assert result.column == "Sueldo"
    assert result.outlier_count_estimate == 2
    assert calls["kwargs"] == {
        "url": URL,
        "fmt": "csv",
        "column": "Sueldo",
        "filters": None,
        "limit": 50,
    }


async def test_save_query_to_csv_wraps_model(monkeypatch):
    payload = {
        "path": "/tmp/out.csv",
        "rows_written": 3,
        "columns": ["Nombre"],
        "bytes_written": 42,
    }
    stub, calls = _async_stub(payload)
    monkeypatch.setattr(server, "_save_query_to_csv", stub)

    result = await server.save_query_to_csv(
        url=URL,
        format="csv",
        dest="/tmp/out.csv",
        sql="SELECT Nombre FROM data",
        filters=None,
        columns=None,
        limit=500,
        overwrite=True,
    )

    assert isinstance(result, SaveCsvResult)
    assert result.path == "/tmp/out.csv"
    assert result.rows_written == 3
    assert calls["kwargs"] == {
        "url": URL,
        "fmt": "csv",
        "dest": "/tmp/out.csv",
        "sql": "SELECT Nombre FROM data",
        "filters": None,
        "columns": None,
        "limit": 500,
        "overwrite": True,
    }


async def test_query_resource_wraps_model(monkeypatch):
    payload = {
        "sql_executed": "SELECT * FROM (SELECT 1 AS x) LIMIT 5",
        "rows_returned": 1,
        "columns": ["x"],
        "rows": [[1]],
    }
    stub, calls = _async_stub(payload)
    monkeypatch.setattr(server, "_query_resource", stub)

    result = await server.query_resource(url=URL, format="csv", sql="SELECT 1 AS x", limit=5)

    assert isinstance(result, QueryResult)
    assert result.rows == [[1]]
    assert calls["kwargs"] == {"url": URL, "fmt": "csv", "sql": "SELECT 1 AS x", "limit": 5}


# ─── Error envelope flows through model construction ──────────────────────────


async def test_query_resource_error_envelope(monkeypatch):
    payload = {"error": "SQL rejected: forbidden keyword DROP", "hint": "Use SELECT only."}
    stub, _ = _async_stub(payload)
    monkeypatch.setattr(server, "_query_resource", stub)

    result = await server.query_resource(url=URL, format="csv", sql="DROP TABLE data", limit=5)

    assert isinstance(result, QueryResult)
    assert result.error == "SQL rejected: forbidden keyword DROP"
    assert result.hint == "Use SELECT only."
    assert result.rows == []


async def test_filter_resource_error_envelope(monkeypatch):
    payload = {"error": "download failed: 404", "hint": "Check the resource URL."}
    stub, _ = _async_stub(payload)
    monkeypatch.setattr(server, "_filter_resource", stub)

    result = await server.filter_resource(url=URL, format="csv")

    assert isinstance(result, FilterResult)
    assert result.error == "download failed: 404"
    assert result.rows == []


async def test_download_resource_preview_error_envelope(monkeypatch):
    payload = {"error": "Unsupported format: pdf", "hint": "Use csv, tsv, xlsx, or json."}
    stub, _ = _async_stub(payload)
    monkeypatch.setattr(server, "preview_resource_data", stub)

    result = await server.download_resource_preview(url=URL, format="pdf")

    assert isinstance(result, PreviewResult)
    assert result.error == "Unsupported format: pdf"
    assert result.rows is None


# ─── Cache management (sync tools) ────────────────────────────────────────────


def test_get_cache_stats_wraps_model(monkeypatch):
    payload = {
        "cache_dir": "/tmp/cache",
        "entries": 3,
        "total_bytes": 1024,
        "max_bytes": 52428800,
    }
    stub, calls = _sync_stub(payload)
    monkeypatch.setattr(server, "_get_cache_stats", stub)

    result = server.get_cache_stats()

    assert isinstance(result, CacheStatsResult)
    assert result.entries == 3
    assert result.total_bytes == 1024
    assert calls["args"] == ()
    assert calls["kwargs"] == {}


def test_clear_cache_wraps_model(monkeypatch):
    stub, calls = _sync_stub({"removed_entries": 4})
    monkeypatch.setattr(server, "_clear_cache", stub)

    result = server.clear_cache()

    assert isinstance(result, ClearCacheResult)
    assert result.removed_entries == 4
    assert calls["args"] == ()
    assert calls["kwargs"] == {}


# ─── Organizations / groups / tags / autocomplete (list pass-through) ─────────


async def test_list_organizations_passes_limit(monkeypatch):
    payload = [{"name": "bcrd", "package_count": 12}]
    stub, calls = _async_stub(payload)
    monkeypatch.setattr(server.ckan, "list_organizations", stub)

    result = await server.list_organizations(limit=100)

    assert result is payload
    assert calls["kwargs"] == {"limit": 100}


async def test_get_organization_passes_id(monkeypatch):
    payload = {"name": "ministerio-de-hacienda", "package_count": 30}
    stub, calls = _async_stub(payload)
    monkeypatch.setattr(server.ckan, "get_organization", stub)

    result = await server.get_organization(id="ministerio-de-hacienda")

    assert result is payload
    assert calls["args"] == ("ministerio-de-hacienda",)


async def test_list_groups_passes_through(monkeypatch):
    payload = [{"name": "economia"}, {"name": "salud"}]
    stub, calls = _async_stub(payload)
    monkeypatch.setattr(server.ckan, "list_groups", stub)

    result = await server.list_groups()

    assert result is payload
    assert calls["args"] == ()
    assert calls["kwargs"] == {}


async def test_list_tags_passes_args(monkeypatch):
    payload = ["finanzas", "finanzas-publicas"]
    stub, calls = _async_stub(payload)
    monkeypatch.setattr(server.ckan, "list_tags", stub)

    result = await server.list_tags(query="finan", limit=50)

    assert result is payload
    assert calls["kwargs"] == {"query": "finan", "limit": 50}


async def test_autocomplete_passes_args(monkeypatch):
    payload = [{"name": "ministerio-de-hacienda"}]
    stub, calls = _async_stub(payload)
    monkeypatch.setattr(server.ckan, "autocomplete", stub)

    result = await server.autocomplete(kind="organization", query="hacienda", limit=5)

    assert result is payload
    assert calls["kwargs"] == {"kind": "organization", "query": "hacienda", "limit": 5}


async def test_every_tool_declares_an_output_schema():
    """An unparameterised `-> dict` return annotation makes FastMCP skip the
    outputSchema, and the tool then answers with text and `structuredContent`
    null. Eleven tools — the whole discovery and catalog surface, which is the
    entry point of every conversation — were in that state, so a client on the
    structured path got nothing back from them."""
    from datosgobdo_mcp.server import mcp

    tools = await mcp.list_tools()
    missing = [t.name for t in tools if not t.outputSchema]
    assert not missing, f"tools without outputSchema: {missing}"

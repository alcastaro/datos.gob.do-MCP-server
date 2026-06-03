"""Unit tests for analytics.py.

Split into:
    - pure builders (_quote_ident, _quote_literal, _build_filter_clause, etc.)
    - SQL validator (security)
    - end-to-end: schema/summarize/filter/aggregate/query against a mock HTTP
      response, going through the real cache + DuckDB stack.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from datosgobdo_mcp import analytics

# ─── _quote_ident ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Nombre", '"Nombre"'),
        ("Sueldo Bruto", '"Sueldo Bruto"'),
        ("Año", '"Año"'),
        ("col_with_underscore", '"col_with_underscore"'),
    ],
)
def test_quote_ident_valid(name, expected):
    assert analytics._quote_ident(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        'has"quote',
        "has;semicolon",
        "has--comment",
        "has/*comment*/",
        "has\nnewline",
        "",
    ],
)
def test_quote_ident_rejects_invalid(name):
    with pytest.raises(analytics.AnalyticsError):
        analytics._quote_ident(name)


# ─── _quote_literal ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, "NULL"),
        (True, "TRUE"),
        (False, "FALSE"),
        (42, "42"),
        (3.14, "3.14"),
        ("hola", "'hola'"),
        ("with 'quote'", "'with ''quote'''"),  # SQL-escape single quote by doubling
    ],
)
def test_quote_literal(value, expected):
    assert analytics._quote_literal(value) == expected


# ─── Filter clause builder ────────────────────────────────────────────────────


def test_build_filter_eq():
    c = analytics._build_filter_clause({"col": "Mes", "op": "=", "val": "Abril"})
    assert c == "\"Mes\" = 'Abril'"


def test_build_filter_in_list():
    c = analytics._build_filter_clause({"col": "Estatus", "op": "in", "val": ["FIJO", "TEMPORAL"]})
    assert "IN ('FIJO', 'TEMPORAL')" in c
    assert c.startswith('"Estatus" IN')


def test_build_filter_in_requires_list():
    with pytest.raises(analytics.AnalyticsError):
        analytics._build_filter_clause({"col": "x", "op": "in", "val": "not a list"})


def test_build_filter_is_null_ignores_val():
    c = analytics._build_filter_clause({"col": "Sueldo", "op": "is_null"})
    assert c == '"Sueldo" IS NULL'


def test_build_filter_contains_uses_ilike():
    c = analytics._build_filter_clause({"col": "Nombre", "op": "contains", "val": "PEREZ"})
    assert "ILIKE" in c
    assert "PEREZ" in c


def test_build_filter_starts_with():
    c = analytics._build_filter_clause({"col": "Nombre", "op": "starts_with", "val": "ANA"})
    assert "ILIKE 'ANA%'" in c


def test_build_filter_rejects_unknown_op():
    with pytest.raises(analytics.AnalyticsError):
        analytics._build_filter_clause({"col": "x", "op": "DROP TABLE", "val": 1})


def test_build_filter_rejects_non_string_col():
    with pytest.raises(analytics.AnalyticsError):
        analytics._build_filter_clause({"col": None, "op": "=", "val": 1})


# ─── Aggregation expression builder ───────────────────────────────────────────


def test_build_agg_count_star():
    e = analytics._build_agg_expr({"col": None, "fn": "count", "alias": "total"})
    assert e == 'COUNT(*) AS "total"'


def test_build_agg_count_distinct():
    e = analytics._build_agg_expr({"col": "Nombre", "fn": "count_distinct", "alias": "empleados"})
    assert e == 'COUNT(DISTINCT "Nombre") AS "empleados"'


def test_build_agg_sum():
    e = analytics._build_agg_expr({"col": "Sueldo", "fn": "sum", "alias": "masa"})
    assert e == 'SUM("Sueldo") AS "masa"'


def test_build_agg_rejects_unknown_fn():
    with pytest.raises(analytics.AnalyticsError):
        analytics._build_agg_expr({"col": "x", "fn": "EXEC", "alias": "y"})


def test_build_agg_count_distinct_requires_col():
    with pytest.raises(analytics.AnalyticsError):
        analytics._build_agg_expr({"col": None, "fn": "count_distinct", "alias": "y"})


# ─── Order by ─────────────────────────────────────────────────────────────────


def test_build_order_by_multi():
    out = analytics._build_order_by(
        [{"col": "Estatus", "dir": "asc"}, {"col": "Mes", "dir": "desc"}]
    )
    assert out == 'ORDER BY "Estatus" ASC, "Mes" DESC'


def test_build_order_by_rejects_bad_dir():
    with pytest.raises(analytics.AnalyticsError):
        analytics._build_order_by([{"col": "x", "dir": "drop"}])


def test_build_order_by_none_returns_empty():
    assert analytics._build_order_by(None) == ""


def test_build_where_none_returns_empty():
    assert analytics._build_where(None) == ""


def test_build_where_multiple_anded():
    out = analytics._build_where(
        [{"col": "a", "op": "=", "val": 1}, {"col": "b", "op": "=", "val": 2}]
    )
    assert " AND " in out
    assert out.startswith("WHERE ")


# ─── SQL validator ────────────────────────────────────────────────────────────


def test_validate_sql_accepts_select():
    out = analytics._validate_sql("SELECT * FROM data")
    assert out == "SELECT * FROM data"


def test_validate_sql_accepts_with_cte():
    out = analytics._validate_sql("WITH t AS (SELECT 1) SELECT * FROM t")
    assert "WITH" in out


def test_validate_sql_strips_trailing_semicolon():
    out = analytics._validate_sql("SELECT 1;")
    assert out == "SELECT 1"


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE data",
        "DELETE FROM data",
        "INSERT INTO data VALUES (1)",
        "UPDATE data SET x = 1",
        "ALTER TABLE data ADD col INT",
        "ATTACH DATABASE 'evil.db'",
        "DETACH 'x'",
        "COPY data TO 'out.csv'",
        "CREATE TABLE t (a INT)",
        "PRAGMA foreign_keys = ON",
        "INSTALL extension",
        "LOAD extension",
        "SET memory_limit = '10MB'",
        "GRANT SELECT TO foo",
        "VACUUM",
    ],
)
def test_validate_sql_rejects_dangerous(sql):
    with pytest.raises(analytics.AnalyticsError):
        analytics._validate_sql(sql)


def test_validate_sql_rejects_multi_statement():
    with pytest.raises(analytics.AnalyticsError):
        analytics._validate_sql("SELECT 1; SELECT 2")


def test_validate_sql_rejects_keyword_in_middle():
    with pytest.raises(analytics.AnalyticsError):
        analytics._validate_sql("SELECT * FROM data UNION INSERT INTO foo VALUES(1)")


def test_validate_sql_rejects_empty():
    with pytest.raises(analytics.AnalyticsError):
        analytics._validate_sql("")
    with pytest.raises(analytics.AnalyticsError):
        analytics._validate_sql("   ")


def test_validate_sql_rejects_non_select_start():
    with pytest.raises(analytics.AnalyticsError):
        analytics._validate_sql("PRINT 'hi'")


# ─── End-to-end with HTTP mock ────────────────────────────────────────────────


@pytest.fixture
def mock_csv_endpoint(httpx_mock, sample_csv_url, sample_csv_bytes):
    """Mock both HEAD (cache key) and GET (download)."""
    httpx_mock.add_response(
        url=sample_csv_url,
        method="HEAD",
        headers={"etag": "v1", "last-modified": "Mon"},
    )
    httpx_mock.add_response(url=sample_csv_url, method="GET", content=sample_csv_bytes)
    return sample_csv_url


async def test_get_resource_schema_e2e(mock_csv_endpoint, tmp_cache_dir):
    out = await analytics.get_resource_schema(mock_csv_endpoint, "csv")
    assert "error" not in out, out
    assert out["row_count"] == 7
    names = {c["name"] for c in out["columns"]}
    assert "Nombre" in names
    assert "Estatus" in names
    assert "Año" in names


async def test_summarize_resource_e2e(mock_csv_endpoint, tmp_cache_dir):
    out = await analytics.summarize_resource(mock_csv_endpoint, "csv")
    assert "error" not in out, out
    by_name = {c["name"]: c for c in out["columns"]}
    # Estatus has 3 distinct: FIJO, TEMPORAL, TRAMITE DE PENSION
    assert by_name["Estatus"]["distinct_count"] == 3
    # top_values should be present for low-cardinality column.
    assert "top_values" in by_name["Estatus"]
    top = {tv["value"]: tv["count"] for tv in by_name["Estatus"]["top_values"]}
    assert top.get("FIJO", 0) >= 1


async def test_aggregate_resource_e2e(mock_csv_endpoint, tmp_cache_dir):
    out = await analytics.aggregate_resource(
        mock_csv_endpoint,
        "csv",
        aggregations=[
            {"col": None, "fn": "count", "alias": "registros"},
            {"col": "Nombre", "fn": "count_distinct", "alias": "empleados"},
        ],
        group_by=["Estatus"],
        filters=[
            {"col": "Año", "op": "=", "val": 2026},
            {"col": "Mes", "op": "=", "val": "Abril"},
        ],
        order_by=[{"col": "empleados", "dir": "desc"}],
    )
    assert "error" not in out, out
    rows = {r[0]: r for r in out["rows"]}
    # FIJO in April 2026: ANA, CARLA, DIEGO = 3 distinct names, 3 records.
    fijo = rows["FIJO"]
    assert fijo[1] == 3  # registros
    assert fijo[2] == 3  # empleados


async def test_filter_resource_e2e(mock_csv_endpoint, tmp_cache_dir):
    out = await analytics.filter_resource(
        mock_csv_endpoint,
        "csv",
        filters=[{"col": "Estatus", "op": "=", "val": "FIJO"}],
        columns=["Nombre", "Sueldo"],
        order_by=[{"col": "Sueldo", "dir": "desc"}],
        limit=10,
    )
    assert "error" not in out, out
    assert out["columns"] == ["Nombre", "Sueldo"]
    # 4 rows with Estatus=FIJO across both months.
    assert out["matching_rows_total"] == 4
    # Highest sueldo with FIJO is DIEGO (45000).
    assert out["rows"][0][0] == "DIEGO SANTOS"


async def test_query_resource_e2e(mock_csv_endpoint, tmp_cache_dir):
    out = await analytics.query_resource(
        mock_csv_endpoint,
        "csv",
        sql="SELECT Estatus, COUNT(*) AS n FROM data GROUP BY Estatus",
    )
    assert "error" not in out, out
    by = {r[0]: r[1] for r in out["rows"]}
    assert by["FIJO"] == 4
    assert by["EMPLEADOS TEMPORALES"] == 2


async def test_query_resource_blocks_injection(tmp_cache_dir):
    # Validation happens before any HTTP — no mocks needed.
    out = await analytics.query_resource(
        "https://example.test/never-called.csv", "csv", sql="DROP TABLE data"
    )
    assert "error" in out
    assert "SELECT" in out["error"] or "forbidden" in out["error"].lower()


# ─── Security: query_resource must not read local files / reach the network ────
# DuckDB exposes filesystem access via *table functions* (read_text, read_csv,
# glob, read_blob) that the keyword denylist does NOT cover. The sandbox
# (materialize + enable_external_access=false) must neutralize them so a
# model-supplied SELECT can never exfiltrate local files.


@pytest.mark.parametrize(
    "malicious_sql",
    [
        "SELECT * FROM read_text('/etc/passwd')",
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT size FROM glob('/*')",
        "SELECT * FROM read_blob('/etc/passwd')",
    ],
)
async def test_query_resource_blocks_file_access(mock_csv_endpoint, tmp_cache_dir, malicious_sql):
    out = await analytics.query_resource(mock_csv_endpoint, "csv", sql=malicious_sql)
    assert "error" in out, f"file access was NOT blocked: {out}"


async def test_query_resource_legit_query_still_works_after_sandbox(
    mock_csv_endpoint, tmp_cache_dir
):
    # Regression: the sandbox must not break normal queries against `data`.
    out = await analytics.query_resource(
        mock_csv_endpoint,
        "csv",
        sql="SELECT Estatus, COUNT(*) AS n FROM data GROUP BY Estatus",
    )
    assert "error" not in out, out
    by = {r[0]: r[1] for r in out["rows"]}
    assert by["FIJO"] == 4


# ─── #3: get_resource_schema.sample_rows must actually control the cap ─────────


async def test_get_resource_schema_sample_rows_limits(mock_csv_endpoint, tmp_cache_dir):
    out = await analytics.get_resource_schema(mock_csv_endpoint, "csv", sample_rows=2)
    by = {c["name"]: c for c in out["columns"]}
    # "Nombre" has 6 distinct values; sample_rows=2 must cap the sample at 2.
    assert len(by["Nombre"]["sample_values"]) <= 2


async def test_get_resource_schema_sample_rows_allows_more(mock_csv_endpoint, tmp_cache_dir):
    out = await analytics.get_resource_schema(mock_csv_endpoint, "csv", sample_rows=10)
    by = {c["name"]: c for c in out["columns"]}
    # All 6 distinct names should come back when the cap is high enough
    # (the old hardcoded LIMIT 5 would have truncated this to 5).
    assert len(by["Nombre"]["sample_values"]) == 6


async def test_cache_hit_on_second_call(mock_csv_endpoint, tmp_cache_dir):
    # First call: cold (HEAD + GET).
    out1 = await analytics.get_resource_schema(mock_csv_endpoint, "csv")
    assert out1["cache"]["cache"] == "miss"

    # Second call: with URL-lookup, skips HEAD and finds cache by URL.
    # No new mocks needed; it should hit the cache immediately.
    out2 = await analytics.get_resource_schema(mock_csv_endpoint, "csv")
    assert out2["cache"]["cache"] == "hit"


async def test_warm_cache_skips_head_request(mock_csv_endpoint, tmp_cache_dir, httpx_mock):
    """After a cold call, warm calls must not HEAD the server.

    The HEAD and GET mocks from mock_csv_endpoint are consumed on the cold call.
    If the warm call tries to HEAD, _head_metadata catches the ConnectError and
    returns (None, None) → a different cache key → MISS → GET attempt → error.
    With the URL-lookup fix, the warm call finds the key immediately and returns hit.
    """
    # Cold call consumes HEAD + GET mocks.
    out_cold = await analytics.get_resource_schema(mock_csv_endpoint, "csv")
    assert out_cold["cache"]["cache"] == "miss"

    # Warm call — no HEAD or GET mocks available.
    out_warm = await analytics.get_resource_schema(mock_csv_endpoint, "csv")
    assert "error" not in out_warm, f"warm call errored: {out_warm}"
    assert out_warm["cache"]["cache"] == "hit"


async def test_force_refresh_does_head(mock_csv_endpoint, tmp_cache_dir, httpx_mock):
    """force_refresh=True must re-HEAD and re-download even on warm cache."""
    # Cold call.
    await analytics.get_resource_schema(mock_csv_endpoint, "csv")

    # Re-prime HEAD + GET for the forced refresh.
    httpx_mock.add_response(
        url=mock_csv_endpoint,
        method="HEAD",
        headers={"etag": "v2", "last-modified": "Tue"},
    )
    httpx_mock.add_response(
        url=mock_csv_endpoint, method="GET", content=b"Nombre;Sueldo\nANA;25000\n"
    )
    out = await analytics.ensure_cached(mock_csv_endpoint, "csv", force_refresh=True)
    _, meta = out
    assert meta["cache"] == "miss"


# ─── quantiles_resource ───────────────────────────────────────────────────────


async def test_quantiles_resource_e2e(mock_csv_endpoint, tmp_cache_dir):
    out = await analytics.quantiles_resource(mock_csv_endpoint, "csv")
    assert "error" not in out, out
    by = {c["name"]: c for c in out["columns"]}
    assert "Sueldo" in by
    s = by["Sueldo"]
    assert "p25" in s and "p50" in s and "p75" in s and "p99" in s
    assert s["min"] <= s["p25"] <= s["p50"] <= s["p75"] <= s["max"]


async def test_quantiles_resource_specific_columns(mock_csv_endpoint, tmp_cache_dir):
    out = await analytics.quantiles_resource(mock_csv_endpoint, "csv", columns=["Sueldo"])
    assert "error" not in out
    assert len(out["columns"]) == 1
    assert out["columns"][0]["name"] == "Sueldo"


async def test_quantiles_resource_invalid_percentile(sample_csv_url, tmp_cache_dir):
    out = await analytics.quantiles_resource(sample_csv_url, "csv", percentiles=[0.5, 1.5])
    assert "error" in out


async def test_quantiles_resource_with_filter(mock_csv_endpoint, tmp_cache_dir):
    out = await analytics.quantiles_resource(
        mock_csv_endpoint,
        "csv",
        columns=["Sueldo"],
        filters=[{"col": "Mes", "op": "=", "val": "Abril"}],
    )
    assert "error" not in out
    assert out["columns"][0]["non_null_count"] == 5  # 5 rows in Abril


# ─── find_duplicates_resource ─────────────────────────────────────────────────


async def test_find_duplicates_all_columns(mock_dupes_endpoint, tmp_cache_dir):
    out = await analytics.find_duplicates_resource(mock_dupes_endpoint, "csv")
    assert "error" not in out, out
    # Only "ANA PEREZ;001-0000001-1;25000" is a full-row duplicate.
    assert out["duplicate_groups_found"] == 1
    assert out["total_duplicate_rows"] == 2


async def test_find_duplicates_specific_columns(mock_dupes_endpoint, tmp_cache_dir):
    out = await analytics.find_duplicates_resource(mock_dupes_endpoint, "csv", columns=["Nombre"])
    assert "error" not in out
    # ANA PEREZ appears 2 times, BENITO LOPEZ appears 2 times
    assert out["duplicate_groups_found"] == 2
    by_name = {r[0]: r[1] for r in out["rows"]}
    assert by_name["ANA PEREZ"] == 2
    assert by_name["BENITO LOPEZ"] == 2


async def test_find_duplicates_returns_empty_when_no_dupes(mock_csv_endpoint, tmp_cache_dir):
    out = await analytics.find_duplicates_resource(mock_csv_endpoint, "csv")
    assert "error" not in out
    assert out["duplicate_groups_found"] == 0
    assert out["rows"] == []


# ─── detect_outliers_resource ─────────────────────────────────────────────────


async def test_detect_outliers_finds_extreme_values(mock_outliers_endpoint, tmp_cache_dir):
    out = await analytics.detect_outliers_resource(mock_outliers_endpoint, "csv", column="Sueldo")
    assert "error" not in out, out
    assert out["method"] == "IQR"
    assert out["iqr"] > 0
    sueldo_values = [r[1] for r in out["rows"]]
    assert 999999 in sueldo_values
    assert 100 in sueldo_values


async def test_detect_outliers_no_outliers(mock_csv_endpoint, tmp_cache_dir):
    out = await analytics.detect_outliers_resource(mock_csv_endpoint, "csv", column="Sueldo")
    assert "error" not in out
    assert "q1" in out and "q3" in out and "iqr" in out
    assert "lower_fence" in out and "upper_fence" in out


async def test_detect_outliers_zero_iqr_returns_error(tmp_cache_dir, httpx_mock):
    uniform_csv = b"val\n5\n5\n5\n5\n5\n"
    url = "https://example.test/uniform.csv"
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "u1"})
    httpx_mock.add_response(url=url, method="GET", content=uniform_csv)
    out = await analytics.detect_outliers_resource(url, "csv", column="val")
    assert "error" in out
    assert "IQR" in out["error"] or "iqr" in out["error"].lower()


async def test_detect_outliers_nonexistent_column(mock_outliers_endpoint, tmp_cache_dir):
    out = await analytics.detect_outliers_resource(
        mock_outliers_endpoint, "csv", column="NonExistentCol"
    )
    assert "error" in out


# ─── save_query_to_csv ────────────────────────────────────────────────────────


async def test_save_query_to_csv_writes_file(mock_csv_endpoint, tmp_cache_dir, tmp_path):
    dest = str(tmp_path / "output.csv")
    out = await analytics.save_query_to_csv(
        mock_csv_endpoint,
        "csv",
        dest=dest,
        filters=[{"col": "Mes", "op": "=", "val": "Abril"}],
    )
    assert "error" not in out, out
    assert out["rows_written"] == 5
    assert out["path"] == dest
    assert out["bytes_written"] > 0
    import csv as _csv

    with open(dest, newline="", encoding="utf-8") as f:
        rows = list(_csv.reader(f))
    assert len(rows) == 6  # 1 header + 5 data rows


async def test_save_query_to_csv_with_sql(mock_csv_endpoint, tmp_cache_dir, tmp_path):
    dest = str(tmp_path / "sql_out.csv")
    out = await analytics.save_query_to_csv(
        mock_csv_endpoint,
        "csv",
        dest=dest,
        sql="SELECT Nombre, Sueldo FROM data WHERE Mes='Abril'",
    )
    assert "error" not in out
    assert out["rows_written"] == 5
    assert out["columns"] == ["Nombre", "Sueldo"]


async def test_save_query_to_csv_refuses_traversal(tmp_cache_dir):
    out = await analytics.save_query_to_csv(
        "https://example.test/any.csv", "csv", dest="../../../etc/passwd.csv"
    )
    assert "error" in out
    assert ".." in out["error"] or "path" in out["error"].lower()


async def test_save_query_to_csv_refuses_system_path(tmp_cache_dir):
    out = await analytics.save_query_to_csv(
        "https://example.test/any.csv", "csv", dest="/etc/evil.csv"
    )
    assert "error" in out


async def test_save_query_to_csv_refuses_non_csv_extension(tmp_cache_dir, tmp_path):
    out = await analytics.save_query_to_csv(
        "https://example.test/any.csv", "csv", dest=str(tmp_path / "output.xlsx")
    )
    assert "error" in out


async def test_save_query_to_csv_refuses_overwrite_by_default(
    sample_csv_url, tmp_cache_dir, tmp_path
):
    dest = str(tmp_path / "existing.csv")
    Path(dest).write_text("existing content")
    out = await analytics.save_query_to_csv(sample_csv_url, "csv", dest=dest)
    assert "error" in out
    assert "exists" in out["error"].lower() or "overwrite" in out["error"].lower()


async def test_save_query_to_csv_refuses_private_etc_on_macos(tmp_cache_dir):
    """macOS resolves /etc to /private/etc — both must be blocked."""
    out = await analytics.save_query_to_csv(
        "https://example.test/any.csv", "csv", dest="/private/etc/evil.csv"
    )
    assert "error" in out


async def test_quantiles_resource_duplicate_percentile_error(sample_csv_url, tmp_cache_dir):
    out = await analytics.quantiles_resource(sample_csv_url, "csv", percentiles=[0.5, 0.5])
    assert "error" in out


# ─── Format coverage: XLSX, JSON, ODS ────────────────────────────────────────


async def test_get_resource_schema_xlsx(mock_xlsx_endpoint, tmp_cache_dir):
    out = await analytics.get_resource_schema(mock_xlsx_endpoint, "xlsx")
    assert "error" not in out, out
    names = {c["name"] for c in out["columns"]}
    assert "nombre" in names
    assert out["row_count"] == 3


async def test_filter_resource_xlsx(mock_xlsx_endpoint, tmp_cache_dir):
    out = await analytics.filter_resource(
        mock_xlsx_endpoint, "xlsx",
        filters=[{"col": "estatus", "op": "=", "val": "FIJO"}],
    )
    assert "error" not in out, out
    assert out["rows_returned"] == 2  # ANA + CARLA are FIJO


async def test_aggregate_resource_xlsx(mock_xlsx_endpoint, tmp_cache_dir):
    out = await analytics.aggregate_resource(
        mock_xlsx_endpoint, "xlsx",
        aggregations=[{"col": None, "fn": "count", "alias": "n"}],
        group_by=["estatus"],
    )
    assert "error" not in out, out
    by = {r[0]: r[1] for r in out["rows"]}
    assert by.get("FIJO", 0) == 2
    assert by.get("TEMPORAL", 0) == 1


async def test_get_resource_schema_json(mock_json_endpoint, tmp_cache_dir):
    out = await analytics.get_resource_schema(mock_json_endpoint, "json")
    assert "error" not in out, out
    names = {c["name"] for c in out["columns"]}
    assert "nombre" in names and "sueldo" in names
    assert out["row_count"] == 3


async def test_filter_resource_json(mock_json_endpoint, tmp_cache_dir):
    out = await analytics.filter_resource(
        mock_json_endpoint, "json",
        filters=[{"col": "sueldo", "op": ">", "val": 25000}],
    )
    assert "error" not in out, out
    assert out["rows_returned"] == 2  # BENITO (30000) + CARLA (28000)


async def test_get_resource_schema_ods(mock_ods_endpoint, tmp_cache_dir):
    out = await analytics.get_resource_schema(mock_ods_endpoint, "ods")
    assert "error" not in out, out
    names = {c["name"] for c in out["columns"]}
    assert "nombre" in names
    assert out["row_count"] == 3


async def test_filter_resource_ods(mock_ods_endpoint, tmp_cache_dir):
    out = await analytics.filter_resource(mock_ods_endpoint, "ods", limit=10)
    assert "error" not in out, out
    assert out["rows_returned"] == 3


# ─── Encoding fallback: Latin-1 CSV ──────────────────────────────────────────


async def test_analytics_handles_latin1_encoding(mock_latin1_endpoint, tmp_cache_dir):
    """Non-UTF8 CSVs must be transcoded before DuckDB can read them."""
    out = await analytics.get_resource_schema(mock_latin1_endpoint, "csv")
    assert "error" not in out, out
    assert out["row_count"] == 7
    names = {c["name"] for c in out["columns"]}
    assert "Nombre" in names


async def test_filter_resource_latin1_encoding(mock_latin1_endpoint, tmp_cache_dir):
    out = await analytics.filter_resource(
        mock_latin1_endpoint, "csv",
        filters=[{"col": "Mes", "op": "=", "val": "Abril"}],
    )
    assert "error" not in out, out
    assert out["rows_returned"] == 5


# ─── ensure_cached error paths ────────────────────────────────────────────────


async def test_ensure_cached_zero_bytes_returns_error(tmp_cache_dir, httpx_mock):
    """A 0-byte download must propagate as an error, not silently succeed."""
    url = "https://example.test/empty.csv"
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "e1"})
    httpx_mock.add_response(url=url, method="GET", content=b"")
    out = await analytics.get_resource_schema(url, "csv")
    assert "error" in out


# ─── _quote_ident denylist branch ─────────────────────────────────────────────


def test_quote_ident_rejects_forbidden_substring_dash_dash():
    with pytest.raises(analytics.AnalyticsError):
        analytics._quote_ident("col--comment")


def test_quote_ident_rejects_forbidden_substring_block_comment():
    with pytest.raises(analytics.AnalyticsError):
        analytics._quote_ident("col/*bad")


def test_quote_ident_rejects_semicolon():
    with pytest.raises(analytics.AnalyticsError):
        analytics._quote_ident("col;drop")

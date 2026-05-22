"""Unit tests for analytics.py.

Split into:
    - pure builders (_quote_ident, _quote_literal, _build_filter_clause, etc.)
    - SQL validator (security)
    - end-to-end: schema/summarize/filter/aggregate/query against a mock HTTP
      response, going through the real cache + DuckDB stack.
"""

from __future__ import annotations

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
    c = analytics._build_filter_clause(
        {"col": "Estatus", "op": "in", "val": ["FIJO", "TEMPORAL"]}
    )
    assert "IN ('FIJO', 'TEMPORAL')" in c
    assert c.startswith('"Estatus" IN')


def test_build_filter_in_requires_list():
    with pytest.raises(analytics.AnalyticsError):
        analytics._build_filter_clause({"col": "x", "op": "in", "val": "not a list"})


def test_build_filter_is_null_ignores_val():
    c = analytics._build_filter_clause({"col": "Sueldo", "op": "is_null"})
    assert c == '"Sueldo" IS NULL'


def test_build_filter_contains_uses_ilike():
    c = analytics._build_filter_clause(
        {"col": "Nombre", "op": "contains", "val": "PEREZ"}
    )
    assert "ILIKE" in c
    assert "PEREZ" in c


def test_build_filter_starts_with():
    c = analytics._build_filter_clause(
        {"col": "Nombre", "op": "starts_with", "val": "ANA"}
    )
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
    e = analytics._build_agg_expr(
        {"col": "Nombre", "fn": "count_distinct", "alias": "empleados"}
    )
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
        sql='SELECT Estatus, COUNT(*) AS n FROM data GROUP BY Estatus',
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


async def test_cache_hit_on_second_call(mock_csv_endpoint, tmp_cache_dir, httpx_mock):
    # First call: cold (HEAD + GET).
    out1 = await analytics.get_resource_schema(mock_csv_endpoint, "csv")
    assert out1["cache"]["cache"] == "miss"

    # Re-prime HEAD for the second call. pytest-httpx consumes responses.
    httpx_mock.add_response(
        url=mock_csv_endpoint,
        method="HEAD",
        headers={"etag": "v1", "last-modified": "Mon"},
    )
    out2 = await analytics.get_resource_schema(mock_csv_endpoint, "csv")
    assert out2["cache"]["cache"] == "hit"

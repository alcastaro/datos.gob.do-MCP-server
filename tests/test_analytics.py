"""Unit tests for analytics.py.

Split into:
    - pure builders (_quote_ident, _quote_literal, _build_filter_clause, etc.)
    - SQL validator (security)
    - end-to-end: schema/summarize/filter/aggregate/query against a mock HTTP
      response, going through the real cache + DuckDB stack.
"""

from __future__ import annotations

import os
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
        "trailing_newline\n",  # Python $ matches before a trailing \n — must still reject
        "trailing_cr\r",
        "",
    ],
)
def test_quote_ident_rejects_invalid(name):
    with pytest.raises(analytics.AnalyticsError):
        analytics._quote_ident(name)


@pytest.mark.parametrize(
    "name",
    [
        # Verbatim column headers found in datos.gob.do files during the
        # 2026-08-07 catalog sweep. Every one of these was rejected by the
        # original allowlist, which made the whole file unusable.
        "Sueldo Bruto (RD$)",
        "% Abastecimiento de la Demanda",
        "RANGO DE EDAD 60 - 70",
        "FECHA DE REGISTRO / ADQUISICIÓN",
        "ALIMÉNTATE-COMER ES PRIMERO (PCP)",
        "Cantidad, total",
        "N° de expediente",
    ],
)
def test_quote_ident_accepts_real_government_headers(name):
    assert analytics._quote_ident(name) == f'"{name}"'


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Presupuesto \nAprobado", "Presupuesto Aprobado"),
        ("REGION\n", "REGION"),
        ("  spaced  out  ", "spaced out"),
        ("already clean", "already clean"),
    ],
)
def test_normalize_header_collapses_whitespace(raw, expected):
    assert analytics._normalize_header(raw) == expected


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


async def test_detect_outliers_zero_iqr_is_a_result_not_an_error(tmp_cache_dir, httpx_mock):
    """A flat column has no outliers — that is an answer, not a failure.

    Reporting it as an error made the tool look broken on 13 of 113 real
    columns in the catalog audit (years, constants, small repeated sets) and
    left the assistant with nothing to tell the user.
    """
    uniform_csv = b"val\n5\n5\n5\n5\n5\n"
    url = "https://example.test/uniform.csv"
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "u1"})
    httpx_mock.add_response(url=url, method="GET", content=uniform_csv)
    out = await analytics.detect_outliers_resource(url, "csv", column="val")
    assert "error" not in out, out
    assert out["outliers"] == []
    assert out["iqr"] == 0
    assert "no spread" in out["note"]


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


async def test_save_query_to_csv_opens_with_nofollow(
    mock_csv_endpoint, tmp_cache_dir, tmp_path, monkeypatch
):
    """The final write must use O_NOFOLLOW so a symlink swapped in after path
    validation (TOCTOU) raises instead of writing through the link."""
    if not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("platform without O_NOFOLLOW")
    seen = {}
    real_open = os.open

    def spy(path, flags, *args, **kwargs):
        seen["flags"] = flags
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(analytics.os, "open", spy)
    out = await analytics.save_query_to_csv(
        mock_csv_endpoint, "csv", dest=str(tmp_path / "out.csv")
    )
    assert "error" not in out
    assert seen["flags"] & os.O_NOFOLLOW


async def test_save_query_to_csv_refuses_system_var_path(tmp_cache_dir):
    """The OS-temp exception must not unblock the rest of /private/var (e.g. /private/var/db)."""
    out = await analytics.save_query_to_csv(
        "https://example.test/any.csv", "csv", dest="/private/var/db/evil.csv"
    )
    assert "error" in out
    assert "system path" in out["error"].lower()


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
        mock_xlsx_endpoint,
        "xlsx",
        filters=[{"col": "estatus", "op": "=", "val": "FIJO"}],
    )
    assert "error" not in out, out
    assert out["rows_returned"] == 2  # ANA + CARLA are FIJO


async def test_aggregate_resource_xlsx(mock_xlsx_endpoint, tmp_cache_dir):
    out = await analytics.aggregate_resource(
        mock_xlsx_endpoint,
        "xlsx",
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
        mock_json_endpoint,
        "json",
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
        mock_latin1_endpoint,
        "csv",
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


# ─── error envelope: handled failures must never escape as exceptions ─────────
#
# Found by the 2026-08-07 catalog sweep: a resource hosted on a domain whose DNS
# no longer resolves made netguard raise NetGuardError, which no tool caught —
# so the MCP client got a protocol-level traceback instead of a readable error.
# Same shape for a column name the identifier guard rejects: it was validated
# after the tool's only try block, so it escaped too.


async def test_netguard_error_is_returned_not_raised(tmp_cache_dir, monkeypatch):
    """A blocked/unresolvable host yields {"error": ...}, never an exception."""
    monkeypatch.delenv("DATOSGOBDO_ALLOW_HOSTS", raising=False)
    # .invalid is reserved by RFC 2606 and never resolves.
    out = await analytics.get_resource_schema("https://nonexistent.invalid/data.csv", "csv")
    assert "error" in out
    assert "invalid" in out["error"].lower()


@pytest.mark.parametrize(
    "tool,kwargs",
    [
        ("summarize_resource", {}),
        ("filter_resource", {}),
        ("query_resource", {"sql": "SELECT 1"}),
        ("aggregate_resource", {"aggregations": [{"col": None, "fn": "count"}]}),
    ],
)
async def test_every_tool_wraps_netguard_error(tool, kwargs, tmp_cache_dir, monkeypatch):
    monkeypatch.delenv("DATOSGOBDO_ALLOW_HOSTS", raising=False)
    out = await getattr(analytics, tool)("https://nonexistent.invalid/d.csv", "csv", **kwargs)
    assert "error" in out


async def test_html_error_page_is_rejected_not_parsed(tmp_cache_dir, httpx_mock):
    """A portal answering a dead link with an HTML page and HTTP 200 must fail
    loudly, not become a one-column table the assistant reports as data."""
    url = "https://example.test/gone.csv"
    page = b"<!DOCTYPE html>\n<html><head><title>404</title></head><body>No existe</body></html>"
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "e1"})
    httpx_mock.add_response(url=url, method="GET", content=page)
    out = await analytics.get_resource_schema(url, "csv")
    assert "error" in out
    # The wording moved from "HTML" to "web page" in 0.9.0; what the test is
    # actually about is that a page never becomes a one-column table.
    assert "page" in out["error"].lower()
    assert "columns" not in out


async def test_header_with_embedded_newline_is_usable(tmp_cache_dir, httpx_mock):
    """Headers that wrap across spreadsheet lines are normalized, so the file
    stays queryable instead of failing identifier validation."""
    url = "https://example.test/wrapped.csv"
    content = '"Presupuesto \nAprobado";Año\n1000;2026\n'.encode()
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "e1"})
    httpx_mock.add_response(url=url, method="GET", content=content)
    out = await analytics.get_resource_schema(url, "csv")
    assert "error" not in out, out
    assert "Presupuesto Aprobado" in [c["name"] for c in out["columns"]]


# ─── ODS streaming parser ─────────────────────────────────────────────────────
#
# The previous implementation loaded the whole document into an odfpy object
# tree. A sweep of the real catalog measured a 0.70 MB spreadsheet peaking at
# 0.41 GB of RSS and taking 8-12 s — roughly 580x the file size — with the
# 100 MB download cap implying tens of gigabytes worst case. It ran
# synchronously on the event loop, so nothing could interrupt it.


def _make_ods(rows: list[list[str]], extra_sheet: list[list[str]] | None = None) -> bytes:
    """Build a minimal but valid ODS in memory."""
    import io
    import zipfile
    from xml.sax.saxutils import escape

    def sheet(name: str, data: list[list[str]]) -> str:
        out = [f'<table:table table:name="{name}">']
        for r in data:
            out.append("<table:table-row>")
            for c in r:
                out.append(f"<table:table-cell><text:p>{escape(c)}</text:p></table:table-cell>")
            out.append("</table:table-row>")
        out.append("</table:table>")
        return "".join(out)

    body = sheet("Hoja1", rows) + (sheet("Hoja2", extra_sheet) if extra_sheet else "")
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<office:document-content "
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
        f"<office:body><office:spreadsheet>{body}</office:spreadsheet></office:body>"
        "</office:document-content>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
        z.writestr("content.xml", content)
    return buf.getvalue()


def test_ods_to_csv_reads_first_sheet_only(tmp_path):
    src = tmp_path / "x.ods"
    src.write_bytes(_make_ods([["a", "b"], ["1", "2"]], extra_sheet=[["ignored"]]))
    out = analytics._ods_to_csv(src).read_text(encoding="utf-8").splitlines()
    assert out == ["a,b", "1,2"]


def test_ods_to_csv_drops_trailing_padding_cells(tmp_path):
    """ODS pads rows to the grid width; those repeats must not become columns."""
    import zipfile

    src = tmp_path / "pad.ods"
    src.write_bytes(_make_ods([["a", "b"]]))
    with zipfile.ZipFile(src) as z:
        content = z.read("content.xml").decode()
    content = content.replace(
        "</table:table-row>",
        '<table:table-cell table:number-columns-repeated="16384"/></table:table-row>',
        1,
    )
    padded = tmp_path / "pad2.ods"
    with zipfile.ZipFile(padded, "w") as z:
        z.writestr("content.xml", content)
    assert analytics._ods_to_csv(padded).read_text(encoding="utf-8").splitlines() == ["a,b"]


def test_ods_to_csv_expands_repeated_value_cells(tmp_path):
    import zipfile

    src = tmp_path / "rep.ods"
    src.write_bytes(_make_ods([["x"]]))
    with zipfile.ZipFile(src) as z:
        content = z.read("content.xml").decode()
    content = content.replace(
        "<table:table-cell>", '<table:table-cell table:number-columns-repeated="3">', 1
    )
    rep = tmp_path / "rep2.ods"
    with zipfile.ZipFile(rep, "w") as z:
        z.writestr("content.xml", content)
    assert analytics._ods_to_csv(rep).read_text(encoding="utf-8").splitlines() == ["x,x,x"]


@pytest.mark.parametrize("raw,expected", [(None, 1), ("", 1), ("0", 1), ("7", 7), ("bad", 1)])
def test_ods_repeat_clamped(raw, expected):
    assert analytics._ods_repeat(raw) == expected


def test_ods_repeat_caps_absurd_padding():
    assert analytics._ods_repeat("1048576") == analytics._ODS_MAX_REPEAT


def test_ods_to_csv_rejects_non_zip(tmp_path):
    src = tmp_path / "bad.ods"
    src.write_bytes(b"this is not a zip archive")
    with pytest.raises(analytics.AnalyticsError):
        analytics._ods_to_csv(src)


def test_ods_to_csv_rejects_zip_without_content_xml(tmp_path):
    import zipfile

    src = tmp_path / "empty.ods"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
    with pytest.raises(analytics.AnalyticsError):
        analytics._ods_to_csv(src)


def test_ods_to_csv_rejects_document_with_no_table(tmp_path):
    import zipfile

    src = tmp_path / "notable.ods"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr(
            "content.xml",
            '<?xml version="1.0"?><office:document-content '
            'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"/>',
        )
    with pytest.raises(analytics.AnalyticsError):
        analytics._ods_to_csv(src)


async def test_cold_path_does_not_block_the_event_loop(mock_ods_endpoint, tmp_cache_dir):
    """Transcoding runs in a worker thread, so other coroutines keep running.

    Before this, a single large spreadsheet froze the whole server — including
    the timers meant to cut a runaway conversion short.
    """
    import asyncio

    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.001)
            ticks += 1

    beat = asyncio.create_task(heartbeat())
    try:
        out = await analytics.get_resource_schema(mock_ods_endpoint, "ods")
    finally:
        beat.cancel()
    assert "error" not in out, out
    assert ticks > 0, "event loop never got control during the cold path"


async def test_xlsx_falls_back_to_all_text_when_type_inference_fails(tmp_cache_dir, httpx_mock):
    """Workbooks put totals, footnotes and #REF! below a numeric column, after
    DuckDB has already inferred DOUBLE from the top of it. Losing the whole
    file over that is worse than reading every column as text.
    """
    import io

    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["codigo", "monto"])
    for i in range(1, 60):
        ws.append([f"c{i}", i * 100])
    ws.append(["TOTAL", "#REF!"])  # the cell that breaks inference
    buf = io.BytesIO()
    wb.save(buf)

    url = "https://example.test/mixed.xlsx"
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "x1"})
    httpx_mock.add_response(url=url, method="GET", content=buf.getvalue())

    out = await analytics.get_resource_schema(url, "xlsx")
    assert "error" not in out, out
    assert out["row_count"] == 60
    assert [c["name"] for c in out["columns"]] == ["codigo", "monto"]


async def test_error_message_is_never_empty(tmp_cache_dir, httpx_mock):
    """httpx.ConnectTimeout('') produced 'Could not load resource:' and nothing
    else — an error the assistant cannot relay and the user cannot act on."""
    import httpx

    url = "https://example.test/slow.csv"
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "s1"})
    httpx_mock.add_exception(httpx.ConnectTimeout(""), url=url, method="GET")
    out = await analytics.get_resource_schema(url, "csv")
    assert "error" in out
    assert out["error"].rstrip().endswith("ConnectTimeout")


# ─── audit fixes: context cost, invisible characters ──────────────────────────


async def test_schema_sample_default_is_small(mock_csv_endpoint, tmp_cache_dir):
    """The tool the server tells the model to call first must not be the most
    expensive one. Defaulting to the 1000-value ceiling produced a 352 KB reply
    against a real catalog file — roughly 88k tokens to learn column names.
    """
    assert analytics.SCHEMA_SAMPLE_DEFAULT <= 10
    out = await analytics.get_resource_schema(mock_csv_endpoint, "csv")
    for col in out["columns"]:
        assert len(col["sample_values"]) <= analytics.SCHEMA_SAMPLE_DEFAULT

    wide = await analytics.get_resource_schema(
        mock_csv_endpoint, "csv", sample_rows=analytics.SCHEMA_SAMPLE_ROWS
    )
    assert "error" not in wide  # the ceiling stays available on request


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Cod.Capí\xadtulo", "Cod.Capítulo"),  # soft hyphen, invisible
        ("A​B", "AB"),  # zero-width space
        ("‏RTL", "RTL"),  # bidi mark
        ("normal", "normal"),
    ],
)
def test_normalize_header_strips_invisible_characters(raw, expected):
    """A name rejected for a character nobody can see is impossible to act on."""
    assert analytics._normalize_header(raw) == expected


def test_quote_ident_accepts_name_after_invisible_chars_removed():
    cleaned = analytics._normalize_header("Cod.Capí\xadtulo")
    assert analytics._quote_ident(cleaned) == '"Cod.Capítulo"'


# ─── v0.7.4: shape tolerance, identifier resolution, CSV structure repair ────


def test_column_names_accepts_both_spellings():
    """Three of four list params on these tools take {"col": ...} objects and
    one takes bare strings; models generalise from the majority. In the
    directed battery that shape error alone accounted for 190 of 487 calls."""
    assert analytics._column_names(["Año", {"col": "Mes"}]) == ["Año", "Mes"]
    assert analytics._column_names(None) is None


def test_column_names_rejects_junk():
    with pytest.raises(analytics.AnalyticsError):
        analytics._column_names([{"nope": 1}])
    with pytest.raises(analytics.AnalyticsError):
        analytics._column_names([12])


async def test_aggregate_accepts_group_by_as_objects(mock_csv_endpoint, tmp_cache_dir):
    out = await analytics.aggregate_resource(
        mock_csv_endpoint,
        "csv",
        aggregations=[{"col": None, "fn": "count", "alias": "n"}],
        group_by=[{"col": "Estatus"}],
    )
    assert "error" not in out, out
    assert out["rows"]


def test_quote_ident_resolves_against_the_real_columns():
    """Membership in DuckDB's own column list is a stronger guarantee than a
    character allowlist, and it lets an odd header stay queryable."""
    assert analytics._quote_ident("A¤o", ["A¤o", "Mes"]) == '"A¤o"'
    # Case and whitespace differences are what a model actually produces.
    assert analytics._quote_ident("año", ["AÑO"]) == '"AÑO"'


def test_quote_ident_names_the_real_columns_when_it_cannot_resolve():
    with pytest.raises(analytics.AnalyticsError) as e:
        analytics._quote_ident("Sueldos", ["Sueldo", "Mes"])
    assert "Sueldo" in str(e.value)


def test_quote_ident_stays_strict_without_a_column_list():
    with pytest.raises(analytics.AnalyticsError):
        analytics._quote_ident("x; DROP TABLE y")


async def test_quantiles_accepts_percentile_zero_and_one(mock_csv_endpoint, tmp_cache_dir):
    """0 and 1 are the min and max, which DuckDB computes happily."""
    out = await analytics.quantiles_resource(
        mock_csv_endpoint, "csv", columns=["Sueldo"], percentiles=[0, 0.5, 1]
    )
    assert "error" not in out, out


def test_repair_csv_splits_a_semicolon_file_padded_with_empty_commas(tmp_path):
    """Excel exports `a;b;c,,,,,`. Commas are the most consistent separator, so
    the sniffer picks them and the whole record lands in column one."""
    f = tmp_path / "seguros.csv"
    f.write_text(
        "Seguro;Cantidad;Mes;Año,,,,,\n"
        "Aspectos Generales;1621;Septiembre;2018,,,,,\n"
        # A field containing the sniffed delimiter is exactly why it was wrong.
        "Vejez, Discapacidad (SVDS);209;Septiembre;2018,,,,\n",
        encoding="utf-8",
    )
    out = analytics._repair_csv_text(f)
    assert out != f
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "Seguro;Cantidad;Mes;Año"
    # The comma needs no quoting once ';' is the delimiter.
    assert lines[2] == "Vejez, Discapacidad (SVDS);209;Septiembre;2018"


def test_repair_csv_unwraps_a_doubly_quoted_file(tmp_path):
    """Every line quoted as one field: parsing it is correct, and yields a
    single column whose values are themselves CSV lines."""
    f = tmp_path / "isbn.csv"
    f.write_text(
        '"ISBN,""EDITOR"",""TITULO"""\n'
        '"978-9945,""LIBERTAD"",""LA FE"""\n'
        '"978-9946,""ESDRAC"",""OTRO"""\n',
        encoding="utf-8",
    )
    out = analytics._repair_csv_text(f)
    assert out != f
    assert out.read_text(encoding="utf-8").splitlines()[0] == "ISBN,EDITOR,TITULO"


def test_repair_csv_leaves_a_genuine_single_column_file_alone(tmp_path):
    f = tmp_path / "one.csv"
    f.write_text("Nombre\nANA\nBENITO\n", encoding="utf-8")
    assert analytics._repair_csv_text(f) == f


def test_repair_csv_leaves_a_normal_file_alone(tmp_path):
    f = tmp_path / "ok.csv"
    f.write_text("a;b;c\n1;2;3\n4;5;6\n", encoding="utf-8")
    assert analytics._repair_csv_text(f) == f


async def test_zip_formats_reject_a_web_page_before_duckdb_sees_it(httpx_mock, tmp_cache_dir):
    """Portals answer a gated download with a login page under the original
    filename and HTTP 200. DuckDB then says "Failed to open zip for reading",
    which reads like a bug in this server rather than a fact about the file."""
    url = "https://example.test/gated.xlsx"
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "z1"})
    httpx_mock.add_response(url=url, method="GET", content=b"<html><body>Ingrese</body></html>")
    out = await analytics.get_resource_schema(url, "xlsx")
    assert "error" in out
    assert "XLSX" in out["error"] or "page" in out["error"].lower()


def test_repair_leaves_a_multiline_quoted_header_alone(tmp_path):
    """A header wrapped across two lines inside a quoted field is legal CSV.
    The structure repair must not treat it as a collapsed table; recovering it
    is the strict_mode=false fallback's job, not this one's."""
    f = tmp_path / "precios.csv"
    f.write_text(
        'Orden;"Presentación y/o \nunidad de medida";07-Aug-23\n1;Libra;25\n2;Unidad;30\n',
        encoding="utf-8",
    )
    assert analytics._repair_csv_text(f) == f


async def test_sum_over_a_text_column_explains_itself(httpx_mock, tmp_cache_dir):
    """A spreadsheet that mixes "N/D" into a numeric column loads that column as
    text, and DuckDB then reports that sum(VARCHAR) does not exist — true and
    useless. The fix is a cast, and the caller cannot infer that from the text."""
    url = "https://example.test/mixto.csv"
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "m1"})
    httpx_mock.add_response(
        url=url,
        method="GET",
        content=b"Departamento;Monto\nSalud;1000\nEducacion;N/D\n",
    )
    out = await analytics.aggregate_resource(
        url,
        "csv",
        aggregations=[{"col": "Monto", "fn": "sum", "alias": "total"}],
        group_by=["Departamento"],
    )
    assert "error" in out, out
    assert "text" in out["error"].lower()
    assert "query_resource" in out["hint"]


def test_duckdb_error_passes_other_messages_through():
    import duckdb as _d

    out = analytics._duckdb_error(_d.Error("Binder Error: something else"))
    assert out["error"].startswith("DuckDB:")
    assert "hint" not in out


async def test_failed_cast_names_the_value_and_offers_try_cast(mock_csv_endpoint, tmp_cache_dir):
    """The largest error class in the directed battery: analysts write a plain
    CAST and the column carries thousands separators, non-breaking spaces or
    placeholders ("N/A", "-", "#REF!", "PROCESO CANCELADO")."""
    out = await analytics.query_resource(
        mock_csv_endpoint, "csv", sql='SELECT CAST("Estatus" AS DOUBLE) FROM data'
    )
    assert "error" in out, out
    assert "cast failed" in out["error"].lower()
    assert "TRY_CAST" in out["hint"]


async def test_unknown_column_in_sql_lists_the_real_ones(mock_csv_endpoint, tmp_cache_dir):
    out = await analytics.query_resource(mock_csv_endpoint, "csv", sql='SELECT "Sueldos" FROM data')
    assert "error" in out, out
    assert "Sueldo" in out["error"]


# ─── Numbers stored as text (V8.1) ────────────────────────────────────────────


async def test_sum_over_a_text_salary_column_now_answers(
    mock_dirty_numeric_endpoint, tmp_cache_dir
):
    """The largest failure class in this catalog: 202 of 284 directed errors.

    One `N/A` in a payroll makes the whole column VARCHAR, and every SUM over
    it failed with a cast error — for a column that is unambiguously a measure.
    """
    out = await analytics.aggregate_resource(
        mock_dirty_numeric_endpoint,
        "csv",
        aggregations=[{"col": "Sueldo Bruto (RD$)", "fn": "sum", "alias": "total"}],
        group_by=["Mes"],
    )
    assert "error" not in out, out
    totals = {r[0]: r[1] for r in out["rows"]}
    assert totals["Enero"] == pytest.approx(sum(40000 + i * 500 for i in range(20)))
    # RD$52,300.00 — the currency prefix is stripped, so Pedro is counted.
    febrero = sum(52000 + i * 250 + 0.5 for i in range(17)) + 52300.00
    assert totals["Febrero"] == pytest.approx(febrero)


async def test_the_answer_declares_what_it_threw_away(mock_dirty_numeric_endpoint, tmp_cache_dir):
    """Non-negotiable: this is an audit tool.

    Silently absorbing a publisher's defect would make the server the last
    place it is visible, and the caller would have no way to know the total it
    just received skipped three rows — one of which is a header the publisher
    left inside the data.
    """
    out = await analytics.aggregate_resource(
        mock_dirty_numeric_endpoint,
        "csv",
        aggregations=[{"col": "Sueldo Bruto (RD$)", "fn": "avg", "alias": "media"}],
    )
    report = out["numeric_coercion"][0]
    assert report["coerced"] is True
    assert report["column"] == "Sueldo Bruto (RD$)"
    assert report["values_used"] == 38
    assert report["values_excluded"] == 3
    excluded = {e["value"]: e["count"] for e in report["excluded_values"]}
    assert excluded["N/A"] == 1
    assert excluded["#REF!"] == 1
    assert excluded["Sueldo Bruto (RD$)"] == 1  # the repeated header row


async def test_counting_a_text_column_is_left_alone(mock_dirty_numeric_endpoint, tmp_cache_dir):
    """COUNT over text is a legitimate question about text.

    Coercing there would answer a different question — how many values happen
    to parse as numbers — while looking like it answered the one asked.
    """
    out = await analytics.aggregate_resource(
        mock_dirty_numeric_endpoint,
        "csv",
        aggregations=[{"col": "Empleado", "fn": "count", "alias": "n"}],
    )
    assert out["rows"][0][0] == 41
    assert "numeric_coercion" not in out


async def test_a_genuinely_textual_column_is_refused_not_coerced(
    mock_dirty_numeric_endpoint, tmp_cache_dir
):
    """Below the threshold the column stays text, and the reply says why.

    Coercing a column that is 30% numbers would answer a question about a
    measure using an arbitrary subset of rows — a number with no reason to
    doubt it, which is worse than a refusal.
    """
    out = await analytics.detect_outliers_resource(
        mock_dirty_numeric_endpoint, "csv", column="Empleado"
    )
    assert "error" in out
    report = out["numeric_coercion"][0]
    assert report["coerced"] is False
    assert report["values_numeric"] == 0


async def test_quantiles_reach_a_column_stored_as_text(mock_dirty_numeric_endpoint, tmp_cache_dir):
    """Asking for it by name is enough; the file's declared type is not the last word."""
    out = await analytics.quantiles_resource(
        mock_dirty_numeric_endpoint, "csv", columns=["Sueldo Bruto (RD$)"]
    )
    assert "error" not in out, out
    col = out["columns"][0]
    assert col["non_null_count"] == 38
    assert col["min"] == pytest.approx(40000.00)
    assert col["max"] == pytest.approx(56000.50)
    assert out["numeric_coercion"][0]["values_excluded"] == 3


async def test_outliers_work_on_a_text_measure(mock_dirty_numeric_endpoint, tmp_cache_dir):
    out = await analytics.detect_outliers_resource(
        mock_dirty_numeric_endpoint, "csv", column="Sueldo Bruto (RD$)"
    )
    assert "error" not in out, out
    assert out["q1"] is not None and out["q3"] is not None
    assert out["numeric_coercion"][0]["values_excluded"] == 3


def test_the_cleanup_never_removes_a_value_separator():
    """Measured and rejected: also stripping spaces.

    It rescued one more column across the whole mirror and would read the
    three codes `10 20 30` as the single number 102030. Removing a character a
    number cannot contain is safe; removing one that separates values is not.
    """
    assert " ', ''" not in analytics._NUMERIC_TEXT_CLEAN.replace("' '", "")
    assert "REPLACE({c}, ' ', '')" not in analytics._NUMERIC_TEXT_CLEAN


async def test_a_megabyte_that_parses_into_one_cell_is_flagged(httpx_mock, tmp_cache_dir):
    """The dangerous case is not the error, it is the success.

    A 12 MB JSON array that DuckDB folds into a single value comes back as
    "1 row, 1 column" with no error at all, and the assistant reports that one
    cell as the dataset. Measured over 1,926 readable resources in the catalog:
    12 do this, six of them JSON.
    """
    url = "https://example.test/grande.json"
    payload = b"[" + b",".join(b'{"a":%d}' % i for i in range(12000)) + b"]"
    assert len(payload) > 100_000
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "j1"})
    httpx_mock.add_response(url=url, method="GET", content=b'"' + payload + b'"')
    out = await analytics.get_resource_schema(url, "json")
    if "error" in out:
        return  # DuckDB refused it outright, which is also an acceptable answer
    aviso = out["cache"].get("parse_warning")
    assert aviso, "a file this size collapsing to one cell must not pass silently"
    assert "parse failure" in aviso


async def test_a_normal_file_carries_no_warning(mock_csv_endpoint, tmp_cache_dir):
    """The warning must stay rare, or it becomes noise nobody reads."""
    out = await analytics.get_resource_schema(mock_csv_endpoint, "csv")
    assert "parse_warning" not in out["cache"]

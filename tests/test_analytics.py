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


async def test_save_query_to_csv_refuses_a_relative_destination(tmp_cache_dir, monkeypatch):
    """A client-launched MCP server inherits an undefined working directory —
    `/` on macOS — so "export.csv" resolved to the filesystem root and the write
    failed with `[Errno 30] Read-only file system: '/export.csv'`, an error the
    caller cannot act on. On a writable root it would land where nobody looks.
    """
    monkeypatch.chdir("/")
    out = await analytics.save_query_to_csv(
        "https://example.test/any.csv", "csv", dest="export.csv"
    )
    assert "error" in out
    assert "absolute path" in out["error"]
    assert "~/Downloads/datosgobdo-exports/" in out["error"]


async def test_save_query_to_csv_expands_a_tilde_destination(
    mock_csv_endpoint, tmp_cache_dir, tmp_path, monkeypatch
):
    """`~/x.csv` is what a user types and is not absolute until expanded — it
    used to resolve to a literal `~` directory under the working directory.

    Both variables are set because `ntpath.expanduser` reads `USERPROFILE` and
    never consults `HOME`: patching only the POSIX one would expand to the real
    profile directory on Windows and write outside `tmp_path`.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    out = await analytics.save_query_to_csv(mock_csv_endpoint, "csv", dest="~/tilde.csv")
    assert "error" not in out, out
    assert out["path"] == str(tmp_path / "tilde.csv")
    assert (tmp_path / "tilde.csv").exists()


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


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="platform without O_NOFOLLOW")
async def test_save_query_to_csv_opens_with_nofollow(
    mock_csv_endpoint, tmp_cache_dir, tmp_path, monkeypatch
):
    """The final write must use O_NOFOLLOW so a symlink swapped in after path
    validation (TOCTOU) raises instead of writing through the link.

    The skip is a decorator, not a call in the body: `mock_csv_endpoint` registers
    two mocked responses before the body runs, and skipping from inside left them
    unrequested, which pytest-httpx fails at teardown. That turned a legitimate
    skip on Windows into `1 error` and cost the platform a green suite.
    """
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


async def test_save_query_to_csv_refuses_windows_system_paths(tmp_cache_dir):
    """The denylist protected /etc and /usr while C:\\Windows passed every
    check — the protection existed on the platforms the developers use and
    not on the platform most of this server's audience uses. The raw-string
    check runs before any filesystem call, so this is testable anywhere."""
    for dest in (
        "C:\\Windows\\Temp\\evil.csv",
        "c:/windows/system32/evil.csv",
        "C:/Program Files/evil.csv",
        "c:\\programdata\\evil.csv",
    ):
        out = await analytics.save_query_to_csv("https://example.test/any.csv", "csv", dest=dest)
        assert "error" in out, dest
        assert "system path" in out["error"].lower(), dest


def test_forbidden_dest_windows_folds_case_posix_does_not():
    """Windows paths compare case-insensitively because the filesystem does;
    POSIX prefixes stay exact — /Etc is legitimately a different directory."""
    assert analytics._is_forbidden_dest("C:\\WINDOWS\\evil.csv")
    assert analytics._is_forbidden_dest("/etc/evil.csv")
    assert not analytics._is_forbidden_dest("/Etc/evil.csv")
    assert not analytics._is_forbidden_dest("C:/Users/maria/Downloads/salida.csv")


@pytest.mark.parametrize(
    "dest",
    [
        # The extended-length prefix. Python writes through it and
        # Path.resolve() keeps it, so checking the raw and resolved spellings —
        # which is what catches /etc → /private/etc on macOS — did not help.
        "\\\\?\\C:\\Windows\\Temp\\evil.csv",
        "//?/C:/Windows/Temp/evil.csv",
        "\\\\.\\C:\\Windows\\evil.csv",
        # Administrative shares reach the same directory over UNC.
        "\\\\localhost\\C$\\Windows\\Temp\\evil.csv",
        "\\\\127.0.0.1\\ADMIN$\\Temp\\evil.csv",
        "\\\\?\\UNC\\localhost\\C$\\Windows\\evil.csv",
        # Any drive, not only C: the first list hard-coded the letter, so a
        # machine with Windows installed elsewhere had no protection at all.
        "D:\\Windows\\System32\\evil.csv",
        "e:/programdata/evil.csv",
    ],
)
def test_forbidden_dest_covers_the_spellings_windows_testing_found(dest):
    """Four ways past this guard, found on real Windows and not by reasoning
    about it. The check is pure string work, so it is testable anywhere."""
    assert analytics._is_forbidden_dest(dest), dest


@pytest.mark.parametrize(
    "dest",
    [
        "C:\\Users\\maria\\Downloads\\salida.csv",
        "D:\\Datos\\salida.csv",
        "/Users/maria/Downloads/salida.csv",
        # A Linux directory literally named /windows is not a system path: the
        # Windows list only applies behind a drive letter.
        "/windows/mi-carpeta/salida.csv",
    ],
)
def test_forbidden_dest_leaves_legitimate_destinations_alone(dest):
    assert not analytics._is_forbidden_dest(dest), dest


async def test_save_query_to_csv_refuses_a_unc_destination(tmp_cache_dir):
    """Refused wholesale — an admin share is not the only way to reach a system
    directory on another host, and a CSV a person will open does not need a remote
    destination. This assertion used to require the words "system path", which is
    what a Windows tester was shown for a perfectly ordinary company share; the
    refusal now names the policy it is actually applying.
    """
    out = await analytics.save_query_to_csv(
        "https://example.test/any.csv", "csv", dest="\\\\fileserver\\equipo\\salida.csv"
    )
    assert "error" in out
    assert "Network paths are not a supported destination" in out["error"]


def test_the_temp_exception_does_not_apply_to_a_windows_system_temp():
    """TEMP is C:\\Windows\\Temp for the SYSTEM account and some services. The
    scratch-space exception exists for macOS, whose per-user temp dir sits under
    /private/var/folders — it must not switch the denylist off on Windows."""
    assert analytics._forbidden_windows("C:\\Windows\\Temp")
    assert not analytics._forbidden_windows("C:\\Users\\maria\\AppData\\Local\\Temp")
    # macOS: covered by the POSIX list, and deliberately still allowed as scratch.
    assert not analytics._forbidden_windows("/private/var/folders/xy/T/")
    assert analytics._forbidden_posix("/private/var/folders/xy/T/")


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


# ─── error envelope: handled failures must never escape as exceptions ─────────
#
# Found by the 2026-08-07 catalog sweep: a resource hosted on a domain whose DNS
# no longer resolves made netguard raise NetGuardError, which no tool caught —
# so the MCP client got a protocol-level traceback instead of a readable error.
# Same shape for a column name the identifier guard rejects: it was validated
# after the tool's only try block, so it escaped too.


async def test_netguard_error_is_returned_not_raised(tmp_cache_dir, unresolvable_host):
    """A blocked/unresolvable host yields {"error": ...}, never an exception."""
    out = await analytics.get_resource_schema(unresolvable_host, "csv")
    assert "error" in out
    # Naming the failure, not merely reporting one: the old assertion matched
    # the substring "invalid" from the hostname itself, so it would have passed
    # on any error at all as long as the URL kept its name.
    assert "DNS resolution failed" in out["error"]


@pytest.mark.parametrize(
    "tool,kwargs",
    [
        ("summarize_resource", {}),
        ("filter_resource", {}),
        ("query_resource", {"sql": "SELECT 1"}),
        ("aggregate_resource", {"aggregations": [{"col": None, "fn": "count"}]}),
    ],
)
async def test_every_tool_wraps_netguard_error(tool, kwargs, tmp_cache_dir, unresolvable_host):
    out = await getattr(analytics, tool)(unresolvable_host, "csv", **kwargs)
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


@pytest.mark.parametrize(
    "call",
    [
        lambda url: analytics.get_resource_schema(url, "csv"),
        lambda url: analytics.summarize_resource(url, "csv"),
        lambda url: analytics.filter_resource(url, "csv", limit=5),
        lambda url: analytics.aggregate_resource(url, "csv", [{"col": None, "fn": "count"}]),
        lambda url: analytics.quantiles_resource(url, "csv"),
        lambda url: analytics.find_duplicates_resource(url, "csv"),
        lambda url: analytics.detect_outliers_resource(url, "csv", column="Sueldo"),
        lambda url: analytics.query_resource(url, "csv", sql="SELECT count(*) FROM data"),
    ],
    ids=[
        "schema",
        "summarize",
        "filter",
        "aggregate",
        "quantiles",
        "duplicates",
        "outliers",
        "query",
    ],
)
async def test_the_warm_path_does_not_block_the_event_loop(mock_csv_endpoint, tmp_cache_dir, call):
    """The cold path moved its conversion to a worker thread; the warm path,
    which serves every call but the first, still ran every DuckDB query on the
    event loop. Measured on a 40-column, 300k-row file: 160-220 ms per call with
    the loop frozen throughout — zero heartbeat ticks — which on a hosted
    instance is every other client waiting, and locally is the interrupt timer
    unable to fire.

    `sleep(0)` rather than a millisecond: the fixture is tiny and a query can
    finish before a timed sleep wakes, which would make a zero flaky. A tool that
    yields to a thread suspends at least once, so one tick is a hard guarantee;
    a tool that never yields produces exactly zero. That asymmetry is the test.
    """
    import asyncio

    await analytics.get_resource_schema(mock_csv_endpoint, "csv")  # warm the cache
    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:
            await asyncio.sleep(0)
            ticks += 1

    beat = asyncio.create_task(heartbeat())
    try:
        out = await call(mock_csv_endpoint)
    finally:
        beat.cancel()
    assert "error" not in out, out
    assert out["cache"]["cache"] == "hit", "must exercise the warm path"
    assert ticks > 0, "the event loop never got control during a warm call"


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


async def test_the_coercion_report_describes_the_filtered_rows(
    mock_dirty_numeric_endpoint, tmp_cache_dir
):
    """The `numeric_coercion` block is the audit claim, so it has to describe the
    rows the figure was computed over. It used to be measured over the whole file
    even when `filters` cut it down — 38 used and 3 excluded reported against a
    January total that only ever saw 20 values and skipped one."""
    out = await analytics.aggregate_resource(
        mock_dirty_numeric_endpoint,
        "csv",
        aggregations=[{"col": "Sueldo Bruto (RD$)", "fn": "sum", "alias": "total"}],
        filters=[{"col": "Mes", "op": "=", "val": "Enero"}],
    )
    assert "error" not in out, out
    report = out["numeric_coercion"][0]
    assert report["values_used"] == 20
    assert report["values_excluded"] == 1
    assert {e["value"] for e in report["excluded_values"]} == {"N/A"}


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
    # One object, one key, one scalar: DuckDB reads it as exactly one row and one
    # column, and succeeds. The earlier fixture wrapped a JSON array in quotes,
    # which DuckDB rejects as malformed — so the test took an early `return` on
    # the error and its assertions never ran. A test that cannot fail is not one.
    payload = b'{"data": "' + b"x" * 120_000 + b'"}'
    assert len(payload) > 100_000
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "j1"})
    httpx_mock.add_response(url=url, method="GET", content=payload)
    out = await analytics.get_resource_schema(url, "json")
    assert "error" not in out, out
    assert out["row_count"] == 1 and out["column_count"] == 1
    aviso = out["cache"].get("parse_warning")
    assert aviso, "a file this size collapsing to one cell must not pass silently"
    assert "parse failure" in aviso


async def test_a_normal_file_carries_no_warning(mock_csv_endpoint, tmp_cache_dir):
    """The warning must stay rare, or it becomes noise nobody reads."""
    out = await analytics.get_resource_schema(mock_csv_endpoint, "csv")
    assert "parse_warning" not in out["cache"]


async def test_a_wrong_key_names_itself_and_the_right_ones(mock_csv_endpoint, tmp_cache_dir):
    """`column` and `function` are the obvious names, and they are wrong.

    The old reply to that mistake was "Aggregation not allowed: " with nothing
    after the colon, because `fn` was missing rather than invalid. An error that
    names neither what arrived nor what was expected leaves the caller nowhere.
    """
    out = await analytics.aggregate_resource(
        mock_csv_endpoint, "csv", [{"column": "Sueldo Bruto", "function": "sum"}]
    )
    msg = out["error"]
    assert "column" in msg and "function" in msg, msg
    assert "col" in msg and "fn" in msg, msg


async def test_an_invalid_function_lists_the_valid_ones(mock_csv_endpoint, tmp_cache_dir):
    out = await analytics.aggregate_resource(
        mock_csv_endpoint, "csv", [{"col": "Sueldo Bruto", "fn": "promedio"}]
    )
    assert "promedio" in out["error"]
    assert "median" in out["error"] and "sum" in out["error"]


async def test_a_missing_function_does_not_produce_an_empty_message(
    mock_csv_endpoint, tmp_cache_dir
):
    out = await analytics.aggregate_resource(mock_csv_endpoint, "csv", [{"col": "Sueldo Bruto"}])
    assert not out["error"].rstrip().endswith(":"), out["error"]


async def test_a_cut_without_an_order_says_it_is_arbitrary(mock_csv_endpoint, tmp_cache_dir):
    """Ten groups with no order_by are ten arbitrary groups.

    The reply is shaped exactly like a top ten, and nothing in it says
    otherwise, so the caller quotes an arbitrary slice as the largest.
    """
    out = await analytics.aggregate_resource(
        mock_csv_endpoint,
        "csv",
        [{"col": None, "fn": "count", "alias": "n"}],
        group_by=["Nombre"],
        limit=2,
    )
    assert out["groups_returned"] == 2
    assert "arbitrary" in out["warning"]
    assert "order_by" in out["warning"]


async def test_no_warning_when_nothing_was_cut(mock_csv_endpoint, tmp_cache_dir):
    """A query that fit under the limit lost nothing.

    Warning on every unordered call trains the caller to ignore the field.
    """
    out = await analytics.aggregate_resource(
        mock_csv_endpoint,
        "csv",
        [{"col": None, "fn": "count", "alias": "n"}],
        group_by=["Estatus"],
        limit=500,
    )
    assert "warning" not in out


async def test_no_warning_when_the_caller_said_which_ones(mock_csv_endpoint, tmp_cache_dir):
    out = await analytics.aggregate_resource(
        mock_csv_endpoint,
        "csv",
        [{"col": None, "fn": "count", "alias": "n"}],
        group_by=["Nombre"],
        order_by=[{"col": "n", "dir": "desc"}],
        limit=2,
    )
    assert "warning" not in out


async def test_a_numeric_filter_reaches_a_column_stored_as_text(
    mock_dirty_numeric_endpoint, tmp_cache_dir
):
    """Aggregations already read text as numbers; filters did not.

    Comparing that same column against an integer raised a DuckDB binder error
    the caller could do nothing with, which is how an assistant ends up
    inventing a workaround.
    """
    out = await analytics.filter_resource(
        mock_dirty_numeric_endpoint,
        "csv",
        filters=[{"col": "Sueldo Bruto (RD$)", "op": ">", "val": 0}],
    )
    assert "error" not in out, out
    assert out["rows_returned"] > 0
    notes = [n for n in out.get("numeric_coercion", []) if n.get("where") == "filter"]
    assert notes and notes[0]["coerced"] is True


async def test_comparing_against_a_string_is_flagged_as_alphabetical(
    mock_dirty_numeric_endpoint, tmp_cache_dir
):
    """The obvious workaround succeeds and answers a different question.

    `"00" > "0"` is true as text and false as a number, so a comparison that
    looks numeric can be quietly wrong. The semantics are left alone — `=`
    against text codes is legitimate — and declared instead.
    """
    out = await analytics.filter_resource(
        mock_dirty_numeric_endpoint,
        "csv",
        filters=[{"col": "Sueldo Bruto (RD$)", "op": ">", "val": "0"}],
    )
    assert "error" not in out, out
    notes = [n for n in out.get("numeric_coercion", []) if n.get("where") == "filter"]
    assert notes and notes[0]["comparison"] == "lexicographic"


async def test_a_text_filter_on_a_text_column_is_left_alone(mock_csv_endpoint, tmp_cache_dir):
    """Equality against a name is not a measurement and must not become one."""
    out = await analytics.filter_resource(
        mock_csv_endpoint, "csv", filters=[{"col": "Estatus", "op": "=", "val": "Activo"}]
    )
    assert "error" not in out, out
    assert not [n for n in out.get("numeric_coercion", []) if n.get("where") == "filter"]


async def test_an_unknown_filter_key_names_itself(mock_csv_endpoint, tmp_cache_dir):
    out = await analytics.filter_resource(
        mock_csv_endpoint, "csv", filters=[{"column": "Estatus", "op": "=", "val": "Activo"}]
    )
    assert "column" in out["error"] and "col" in out["error"]


async def test_a_bad_operator_lists_the_good_ones(mock_csv_endpoint, tmp_cache_dir):
    out = await analytics.filter_resource(
        mock_csv_endpoint, "csv", filters=[{"col": "Estatus", "op": "like", "val": "A%"}]
    )
    assert "like" in out["error"]
    assert "contains" in out["error"] and "starts_with" in out["error"]


async def test_a_spreadsheet_registered_as_csv_is_read_anyway(
    httpx_mock, small_xlsx_bytes, tmp_cache_dir
):
    """The catalog says what someone typed; the bytes say what the file is.

    DuckDB used to read the ZIP header as a column name and answer
    `Parser Error: unterminated quoted identifier at or near ""PK`, which names
    nothing the caller can act on. One resource of the catalog is exactly this,
    and it holds 9,427 rows.
    """
    url = "https://example.test/mal-declarado.csv"
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "z1"})
    httpx_mock.add_response(url=url, method="GET", content=small_xlsx_bytes)
    out = await analytics.get_resource_schema(url, "csv")
    assert "error" not in out, out
    assert out["row_count"] > 0
    corrected = out["cache"]["format_corrected"]
    assert corrected["declared"] == "csv"
    assert corrected["actual"] == "xlsx"
    assert "publisher" in corrected["note"]


async def test_the_correction_survives_the_cache(httpx_mock, small_xlsx_bytes, tmp_cache_dir):
    """Same rule as every other piece of provenance: the warm path must repeat it."""
    url = "https://example.test/mal-declarado-2.csv"
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "z2"})
    httpx_mock.add_response(url=url, method="GET", content=small_xlsx_bytes)
    await analytics.get_resource_schema(url, "csv")
    second = await analytics.get_resource_schema(url, "csv")
    assert second["cache"]["cache"] == "hit"
    assert second["cache"]["format_corrected"]["actual"] == "xlsx"


async def test_a_real_csv_is_not_second_guessed(mock_csv_endpoint, tmp_cache_dir):
    out = await analytics.get_resource_schema(mock_csv_endpoint, "csv")
    assert "format_corrected" not in out["cache"]


# ─── Verification block (v0.12.0): source digest + computation ───────────────
# The motivating measurement: an assistant's own prose figures were exact
# while its retyped table drifted 300 million — a number that arrives in
# structuredContent was computed, a number the model retypes may not survive
# the trip. These fields make every reply checkable by a third party: same
# source digest + same SQL = same figure.


async def test_source_sha256_travels_cold_and_warm(
    httpx_mock, sample_csv_url, sample_csv_bytes, tmp_cache_dir
):
    import hashlib as _hl

    expected = _hl.sha256(sample_csv_bytes).hexdigest()
    httpx_mock.add_response(url=sample_csv_url, method="HEAD", headers={"etag": "v1"})
    httpx_mock.add_response(url=sample_csv_url, method="GET", content=sample_csv_bytes)

    cold = await analytics.get_resource_schema(sample_csv_url, "csv")
    assert cold["cache"]["source_sha256"] == expected

    # Warm path serves every call but the first; the digest must not vanish
    # with the download that produced it.
    warm = await analytics.get_resource_schema(sample_csv_url, "csv")
    assert warm["cache"]["cache"] == "hit"
    assert warm["cache"]["source_sha256"] == expected


async def test_truncated_download_carries_no_digest(
    httpx_mock, sample_csv_url, tmp_cache_dir, monkeypatch
):
    """A digest of partial bytes presented as the file's digest would be the
    exact false confidence the field exists to kill."""
    big = b"a;b;c\n" + b"1;2;3\n" * 5000
    httpx_mock.add_response(url=sample_csv_url, method="HEAD", headers={"etag": "t1"})
    httpx_mock.add_response(url=sample_csv_url, method="GET", content=big)
    monkeypatch.setattr(analytics, "ANALYTICS_MAX_BYTES", 100)
    out = await analytics.get_resource_schema(sample_csv_url, "csv")
    assert "source_sha256" not in out.get("cache", {})


async def test_aggregate_reports_computation(mock_csv_endpoint, tmp_cache_dir):
    out = await analytics.aggregate_resource(
        mock_csv_endpoint,
        "csv",
        aggregations=[{"col": "Sueldo", "fn": "sum"}],
    )
    comp = out["computation"]
    assert "FROM data" in comp["sql"]
    assert comp["rows_scanned"] > 0
    # The SQL must reference the data view, never a server-side path.
    assert "/" not in comp["sql"]


async def test_query_resource_reports_computation(mock_csv_endpoint, tmp_cache_dir):
    out = await analytics.query_resource(
        mock_csv_endpoint, "csv", sql="SELECT count(*) AS n FROM data"
    )
    comp = out["computation"]
    assert comp["rows_scanned"] > 0
    assert "FROM data" in comp["sql"]


# ─── CLI flags and the long-path hint ─────────────────────────────────────────


def test_a_long_path_failure_says_which_half_is_fixable(tmp_path):
    """Windows caps paths at 260 characters with long paths disabled, which is
    the default. `[WinError 206] The filename or extension is too long` is the
    whole message the OS gives, and the reply used to pass it through with
    `hint: null` — true, and useless to whoever picked the destination."""
    dest = tmp_path / ("x" * 200 + ".csv")
    err = OSError(36, "The filename or extension is too long")
    err.winerror = 206  # type: ignore[attr-defined]
    out = analytics._dest_open_error(err, dest)
    assert "too long" in out["error"]
    assert out["hint"] is not None
    assert str(len(str(dest))) in out["hint"]
    assert "LongPathsEnabled" in out["hint"]


def test_other_write_failures_do_not_get_a_windows_hint(tmp_path):
    out = analytics._dest_open_error(OSError(13, "Permission denied"), tmp_path / "x.csv")
    assert "Permission denied" in out["error"]
    assert "hint" not in out


# ─── JSON: the envelope, the object-size ceiling, and an honest truncation ─────


async def test_a_json_envelope_is_unnested_into_its_records(tmp_cache_dir, httpx_mock):
    """`{"data": [ … ]}` is one JSON value, so DuckDB reads one row with one LIST
    column and the call *succeeds* — a payroll reported as a single cell. Measured
    on MAP's national payroll: 1 row and a column called `data` becomes 69,097
    rows once unnested."""
    url = "https://example.test/envelope.json"
    payload = (
        b'{"data": ['
        b'{"Nombre": "AARON", "Sueldo_Bruto": 45000},'
        b'{"Nombre": "BENITA", "Sueldo_Bruto": 52000},'
        b'{"Nombre": "CARLOS", "Sueldo_Bruto": 38000}'
        b"]}"
    )
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "e1"})
    httpx_mock.add_response(url=url, method="GET", content=payload)

    out = await analytics.get_resource_schema(url, "json")

    assert "error" not in out, out
    assert out["row_count"] == 3, "the records, not the wrapper"
    assert {c["name"] for c in out["columns"]} == {"Nombre", "Sueldo_Bruto"}


async def test_two_top_level_keys_are_left_alone(tmp_cache_dir, httpx_mock):
    """Narrow on purpose. With more than one top-level key, deciding which one is
    "the data" is how a reader starts inventing datasets — so it does not."""
    url = "https://example.test/two-keys.json"
    payload = b'{"meta": {"version": 1}, "data": [{"a": 1}, {"a": 2}]}'
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "t1"})
    httpx_mock.add_response(url=url, method="GET", content=payload)

    out = await analytics.get_resource_schema(url, "json")

    assert "error" not in out, out
    assert {c["name"] for c in out["columns"]} == {"meta", "data"}


async def test_json_lines_under_a_json_name_still_reads(tmp_cache_dir, httpx_mock):
    url = "https://example.test/lines.json"
    payload = b'{"a": 1, "b": "x"}\n{"a": 2, "b": "y"}\n{"a": 3, "b": "z"}\n'
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "l1"})
    httpx_mock.add_response(url=url, method="GET", content=payload)

    out = await analytics.get_resource_schema(url, "json")

    assert "error" not in out, out
    assert out["row_count"] == 3


async def test_the_object_size_ceiling_is_the_download_cap(tmp_cache_dir, httpx_mock):
    """DuckDB's default refuses a single JSON value over 16 MB, which rejected
    seven catalog resources for a reason unrelated to the file. An object cannot
    be bigger than the download that carried it, so the download cap is the only
    ceiling worth having."""
    from datosgobdo_mcp.download import ANALYTICS_MAX_BYTES

    assert analytics.JSON_MAX_OBJECT_BYTES > ANALYTICS_MAX_BYTES
    assert analytics.JSON_MAX_OBJECT_BYTES > 16 * 1024 * 1024


async def test_a_file_we_cut_short_is_not_reported_as_malformed(
    tmp_cache_dir, httpx_mock, monkeypatch
):
    """ "Malformed JSON … unexpected end of data" is true and blames the wrong
    party. Five resources in this catalog are single JSON objects of ~115 MB
    against a 100 MB cap; a publisher told their file is malformed goes looking
    for a defect that is not there."""
    monkeypatch.setattr(analytics, "ANALYTICS_MAX_BYTES", 64)
    url = "https://example.test/huge.json"
    payload = b'{"data": [' + b'{"a": 1},' * 50 + b'{"a": 1}]}'
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "h1"})
    httpx_mock.add_response(url=url, method="GET", content=payload)

    out = await analytics.get_resource_schema(url, "json")

    assert "error" in out
    assert "cut short" in out["error"]
    assert "not malformed" in out["error"]


# ─── The catalog's format is a claim, not a fact ───────────────────────────────


async def test_an_ods_registered_as_csv_reads_as_an_ods(tmp_cache_dir, httpx_mock):
    """The Tribunal Constitucional publishes `mayo-2026.ods` and the catalog calls
    it CSV. The first version of the correction answered `PK` → XLSX, so it went to
    `read_xlsx` and came back as "No [Content_Types].xml found in xlsx file"."""
    url = "https://example.test/mayo-2026.ods"
    body = _make_ods([["Año", "Mes", "Cantidad"], ["2026", "mayo", "160"]])
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "o1"})
    httpx_mock.add_response(url=url, method="GET", content=body)

    out = await analytics.get_resource_schema(url, "csv")

    assert "error" not in out, out
    assert [c["name"] for c in out["columns"]] == ["Año", "Mes", "Cantidad"]
    corrected = out["cache"]["format_corrected"]
    assert (corrected["declared"], corrected["actual"]) == ("csv", "ods")


async def test_a_csv_registered_as_ods_reads_as_a_csv(tmp_cache_dir, httpx_mock):
    """The other direction, which was not handled at all: the check demanded that
    an ODS start with `PK` and refused everything else, so a readable CSV was
    reported as "not a valid ODS" — true about the declaration, useless about the
    file. Measured on DGP's passport series."""
    url = "https://example.test/pasaportes.ods"
    body = b"Provincia,Cantidad,Mes,Ano\r\nDISTRITO NACIONAL,12347,octubre,2017\r\n"
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "c1"})
    httpx_mock.add_response(url=url, method="GET", content=body)

    out = await analytics.get_resource_schema(url, "ods")

    assert "error" not in out, out
    assert [c["name"] for c in out["columns"]] == ["Provincia", "Cantidad", "Mes", "Ano"]
    assert out["cache"]["format_corrected"]["actual"] == "csv"


async def test_a_zipped_json_is_unpacked_and_the_digest_is_of_the_archive(
    tmp_cache_dir, httpx_mock
):
    """Unpacking happens after the digest on purpose: whoever re-downloads this URL
    gets the archive, so that is what the digest has to name."""
    import hashlib
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("listado.json", b'[{"a": 1}, {"a": 2}]')
    body = buf.getvalue()

    url = "https://example.test/listado.json"
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "z1"})
    httpx_mock.add_response(url=url, method="GET", content=body)

    out = await analytics.get_resource_schema(url, "json")

    assert "error" not in out, out
    assert out["row_count"] == 2
    assert out["cache"]["source_sha256"] == hashlib.sha256(body).hexdigest()
    assert "listado.json" in out["cache"]["format_corrected"]["detected_from"]


async def test_a_legacy_xls_is_refused_with_a_sentence_about_the_file(tmp_cache_dir, httpx_mock):
    url = "https://example.test/nomina.xls"
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "b1"})
    httpx_mock.add_response(
        url=url, method="GET", content=b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 128
    )

    out = await analytics.get_resource_schema(url, "xls")

    assert "error" in out
    assert "pre-2007" in out["error"]


async def test_one_ragged_line_does_not_collapse_a_csv_into_one_column(tmp_cache_dir, httpx_mock):
    """The most dangerous shape of all, because nothing fails. With `IGNORE_ERRORS`
    and no padding, a single row whose field count surprises the sniffer makes
    DuckDB fall back to *one* column named after the entire header — and it still
    returns every row, each as one string. Line 1,423 of DGP's passport series does
    exactly this."""
    rows = [b"Provincia,Cantidad,Mes,Ano"]
    rows += [b"DN,%d,octubre,2017" % n for n in range(40)]
    rows.append(b"SAMBIL,789,julio")  # one field short
    rows += [b"SANTIAGO,%d,julio,2018" % n for n in range(40)]
    body = b"\r\n".join(rows) + b"\r\n"

    url = "https://example.test/ragged.csv"
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "r1"})
    httpx_mock.add_response(url=url, method="GET", content=body)

    out = await analytics.get_resource_schema(url, "csv")

    assert "error" not in out, out
    assert [c["name"] for c in out["columns"]] == ["Provincia", "Cantidad", "Mes", "Ano"]
    assert out["row_count"] == 81


async def test_a_page_under_a_spreadsheet_name_is_refused_not_read_as_csv(
    tmp_cache_dir, httpx_mock
):
    """End to end for the same hazard: markup that slips past `looks_like_html`
    because it does not begin with a known marker must still be refused, not
    corrected into a one-column CSV."""
    url = "https://example.test/nomina.ods"
    body = (
        '<br /><b>Warning</b>: include failed<meta name="viewport" '
        'content="width=device-width, initial-scale=1.0"><title>Sesión expirada</title>'
    ).encode()
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "p1"})
    httpx_mock.add_response(url=url, method="GET", content=body)

    out = await analytics.get_resource_schema(url, "ods")

    assert "error" in out
    assert "not a valid ODS" in out["error"]


# ─── What the portal actually said, instead of a guess about it ───────────────


async def test_a_plain_text_error_body_is_quoted_not_guessed(tmp_cache_dir, httpx_mock):
    """Thirteen of the fifteen resources the census filed as "format not
    identifiable" are plain-text error messages of 23 to 36 bytes served with
    HTTP 200 — `Downloading failed`, `La url no existe`, `Categoria no
    encontrada`. The reply used to guess ("most likely served a web page"),
    which is wrong about those bodies and throws away the only evidence the
    caller could act on. The whole file fits in a sentence, so it is quoted."""
    url = "https://example.test/nomina-inexistente.xlsx"
    body = b"La url no existe"
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "t1"})
    httpx_mock.add_response(url=url, method="GET", content=body)

    out = await analytics.get_resource_schema(url, "xlsx")

    assert "error" in out
    assert '"La url no existe"' in out["error"]
    assert str(len(body)) in out["error"], "the size says it is a message, not a file"
    assert "most likely" not in out["error"], "it stopped guessing"


async def test_an_unreadable_body_still_gets_the_general_refusal(tmp_cache_dir, httpx_mock):
    """The quote replaces the guess only when there is something to quote. A
    truncated spreadsheet is also small, and echoing its binary header back at
    the caller would be noise dressed up as evidence."""
    url = "https://example.test/cortado.xlsx"
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "t2"})
    httpx_mock.add_response(url=url, method="GET", content=b"\x00\x01\x02\x81\x8d rubbish")

    out = await analytics.get_resource_schema(url, "xlsx")

    assert "error" in out
    assert "not a valid XLSX" in out["error"]


@pytest.mark.parametrize(
    "body,quoted",
    [
        (b"Categoria no encontrada", "Categoria no encontrada"),
        (b"  Downloading\n  failed\n", "Downloading failed"),  # collapsed to one line
        ("La categoría no existe".encode("cp1252"), "La categoría no existe"),
        (b"x" * 5000, None),  # a document, not a message
        (b"\x00\x01\x02", None),  # control characters: bytes, not text
        # Markup is small and printable and must still not be quoted: handing
        # a page fragment back as "the publisher's own words" is the hazard
        # looks_like_text_table refuses for the same reason. Caught by the
        # canary running the whole suite, not by the tests written for this.
        (b"<br /><b>Warning</b>: include failed", None),
        (b"Error <sin> datos", None),
        (b"", None),
        (b"   \n  ", None),  # whitespace only
    ],
)
def test_short_text_body_only_quotes_what_is_a_message(tmp_path, body, quoted):
    f = tmp_path / "cuerpo.bin"
    f.write_bytes(body)
    assert analytics._short_text_body(f) == quoted


async def test_a_network_destination_is_refused_as_a_network_path(tmp_cache_dir):
    """It used to be reported as a "system path", which a UNC share is not. The
    Windows tester's note was exact: someone who reads that goes looking for the
    problem where it is not, and the policy — this tool writes local files — never
    reaches them."""
    out = await analytics.save_query_to_csv(
        "https://example.test/any.csv", "csv", dest="\\\\servidor\\equipo\\salida.csv"
    )
    assert "error" in out
    assert "Network paths are not a supported destination" in out["error"]
    assert "system path" not in out["error"].lower()
    assert "copy it to the share afterwards" in out["hint"]


async def test_a_unc_path_into_a_system_directory_is_still_called_a_network_path(tmp_cache_dir):
    """An admin share is both. Network is the more useful half to report, because
    it is the one that tells the caller what to do instead."""
    out = await analytics.save_query_to_csv(
        "https://example.test/any.csv", "csv", dest="\\\\localhost\\C$\\Windows\\Temp\\evil.csv"
    )
    assert "error" in out
    assert "Network paths are not a supported destination" in out["error"]


# ─── A ZIP is not allowed to become arbitrarily large on disk ─────────────────


def test_a_zip_bomb_is_refused_and_leaves_nothing_behind(tmp_path):
    """The download cap bounds what arrives, not what it expands into.

    DEFLATE reaches roughly 1000:1, so an archive that passed every upstream
    check can still fill the disk — which on a hosted instance is shared with
    every other caller. The ceiling is counted while writing rather than read
    from `ZipInfo.file_size`, because that field is the archive author's own
    claim and a bomb declares whatever it likes.
    """
    import zipfile

    archive = tmp_path / "bomb.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("big.csv", b"0" * (analytics.ZIP_MAX_EXPANDED_BYTES + 1024))

    # The archive itself is small enough that no download cap would stop it.
    assert archive.stat().st_size < 5 * 1024 * 1024

    with pytest.raises(analytics.AnalyticsError, match="expands past"):
        analytics._extract_single_member(archive, "big.csv")

    assert not list(tmp_path.glob("*unpacked*")), "partial extraction left on disk"


def test_a_zip_within_the_ceiling_still_unpacks(tmp_path):
    """The guard must not break the three real resources that are a zipped file."""
    import zipfile

    archive = tmp_path / "ok.zip"
    payload = b"nombre;valor\nANA;1\nBENITO;2\n"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("data.csv", payload)

    out = analytics._extract_single_member(archive, "data.csv")
    try:
        assert out.read_bytes() == payload
        assert out.suffix == ".csv"
    finally:
        out.unlink(missing_ok=True)

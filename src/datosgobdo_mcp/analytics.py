"""Analytics tools backed by DuckDB with persistent Parquet cache.

v0.2 introduced get_resource_schema + summarize_resource using one-shot
in-memory DuckDB connections.

v0.3 adds:
    - Parquet on-disk cache keyed by URL + last_modified/ETag (cache.py).
    - aggregate_resource: typed GROUP BY / aggregation without SQL.
    - filter_resource: typed WHERE / SELECT / ORDER BY without SQL.
    - All analytics tools now go through ensure_cached() so repeated calls
      against the same resource skip re-downloading.

v0.4 will add raw query_resource + XLSX/ODS analytics.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Literal

import duckdb
import httpx

from . import USER_AGENT
from .cache import LocalDiskCache, build_cache_key, get_cache
from .download import (
    ANALYTICS_MAX_BYTES,
    classify_format,
    download_to_file,
)

logger = logging.getLogger(__name__)

SCHEMA_SAMPLE_ROWS = 1000
SUMMARIZE_MAX_TOP_N = 50
FILTER_MAX_LIMIT = 1000
AGGREGATE_MAX_LIMIT = 1000

# Identifier guard: only word chars + dot + space (for column names like
# "Sueldo Bruto" or "data.column"). We always pass identifiers through
# double-quote escaping anyway; this is the second line of defense.
# We explicitly forbid SQL-comment sequences and statement terminators.
# \A…\Z (not ^…$): in Python, $ also matches just before a trailing newline, so
# `^[...]+$` would accept an identifier like "col\n". \Z anchors the true end.
_IDENT_OK = re.compile(r"\A[\w .À-ſ]+\Z", re.UNICODE)
_IDENT_FORBIDDEN_SUBSTR = ("--", "/*", "*/", ";")

ALLOWED_AGG_FNS = {
    "count",
    "count_distinct",
    "sum",
    "avg",
    "mean",
    "median",
    "min",
    "max",
    "stddev",
    "variance",
}

ALLOWED_OPS = {
    "=",
    "!=",
    "<>",
    "<",
    "<=",
    ">",
    ">=",
    "in",
    "not_in",
    "contains",
    "starts_with",
    "ends_with",
    "is_null",
    "is_not_null",
}

# Raw SQL hatch: reject anything that isn't strictly a read-only SELECT/WITH.
# Multiple statements forbidden; DDL/DML forbidden.
_SQL_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|create|alter|attach|detach|copy|export|"
    r"import|truncate|grant|revoke|pragma|set|load|install|"
    r"vacuum|analyze)\b",
    re.IGNORECASE,
)
_SQL_ALLOWED_START = re.compile(r"^\s*(with|select)\b", re.IGNORECASE)
SQL_MAX_LIMIT = 1000

_FORBIDDEN_DEST_PREFIXES = (
    "/etc",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/boot",
    "/sys",
    "/proc",
    "/dev",
    "/root",
    # macOS canonical paths (symlinks resolve to /private/*)
    "/private/etc",
    "/private/var",
)


class AnalyticsError(RuntimeError):
    pass


def _quote_ident(name: str) -> str:
    """Quote a column identifier safely.

    Two layers of defence:
        1. Allowlist regex on chars (letters, digits, underscore, dot, space,
           Latin-1/extended accents).
        2. Denylist of forbidden substrings (--, /*, */, ;) so a name that
           somehow passes the regex still can't smuggle SQL syntax.

    Anything that fails either check raises AnalyticsError.
    """
    if not name or not _IDENT_OK.match(name):
        raise AnalyticsError(f"Invalid column identifier: {name!r}")
    for bad in _IDENT_FORBIDDEN_SUBSTR:
        if bad in name:
            raise AnalyticsError(f"Forbidden substring in identifier: {name!r}")
    return '"' + name.replace('"', '""') + '"'


def _quote_literal(value: Any) -> str:
    """Quote a value as a SQL literal. Caller picks the type via the operator."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    return "'" + s.replace("'", "''") + "'"


def _new_con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    for ext in ("httpfs", "excel"):
        try:
            con.execute(f"LOAD {ext}")
        except duckdb.Error:
            pass
    return con


def _normalize_csv_encoding(path: Path) -> Path:
    from .download import _detect_encoding

    with path.open("rb") as f:
        sample = f.read(200_000)
    enc = _detect_encoding(sample)
    if enc in ("utf-8", "utf-8-sig", "ascii"):
        return path
    utf8_path = path.with_suffix(path.suffix + ".utf8")
    with path.open("rb") as src, utf8_path.open("wb") as dst:
        decoder_buf = b""
        while True:
            chunk = src.read(1 << 20)
            if not chunk:
                break
            decoder_buf += chunk
            try:
                text = decoder_buf.decode(enc)
                decoder_buf = b""
            except UnicodeDecodeError as e:
                text = decoder_buf[: e.start].decode(enc, errors="replace")
                decoder_buf = decoder_buf[e.start :]
            dst.write(text.encode("utf-8"))
        if decoder_buf:
            dst.write(decoder_buf.decode(enc, errors="replace").encode("utf-8"))
    return utf8_path


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass
    sidecar = path.with_suffix(path.suffix + ".utf8")
    try:
        sidecar.unlink(missing_ok=True)
    except Exception:
        pass


# ─── Cache layer ──────────────────────────────────────────────────────────────


async def _head_metadata(url: str) -> tuple[str | None, str | None]:
    """Fetch ETag + Last-Modified via HEAD. Used as cache version tag."""
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=15.0,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            r = await client.head(url)
            return r.headers.get("etag"), r.headers.get("last-modified")
    except httpx.HTTPError:
        return None, None


def _ods_to_csv(src: Path) -> Path:
    """Convert ODS to CSV (first sheet only) using odfpy. Returns sibling .csv path.

    DuckDB has no native ODS reader as of 1.x. We extract once on cold-path
    download so Parquet conversion can proceed via the CSV pipeline.
    """
    try:
        from odf.opendocument import load
        from odf.table import Table, TableCell, TableRow
        from odf.text import P
    except ImportError as e:
        raise AnalyticsError(f"odfpy not installed: {e}") from e

    doc = load(str(src))
    tables = doc.spreadsheet.getElementsByType(Table)
    if not tables:
        raise AnalyticsError("ODS file has no tables")
    table = tables[0]
    csv_path = src.with_suffix(src.suffix + ".csv")
    import csv as _csv

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = _csv.writer(f)
        for row in table.getElementsByType(TableRow):
            cells = row.getElementsByType(TableCell)
            out_row: list[str] = []
            for cell in cells:
                # Handle repeated columns.
                repeated = int(cell.getAttribute("numbercolumnsrepeated") or 1)
                paragraphs = cell.getElementsByType(P)
                text = "".join(str(p) for p in paragraphs)
                out_row.extend([text] * repeated)
            # Trim trailing empty repeats that pad the row.
            while out_row and out_row[-1] == "":
                out_row.pop()
            writer.writerow(out_row)
    return csv_path


async def ensure_cached(
    url: str,
    fmt: str,
    cache: LocalDiskCache | None = None,
    force_refresh: bool = False,
) -> tuple[Path, dict[str, Any]]:
    """Make sure the resource is in cache as Parquet. Return (parquet_path, meta).

    Warm path (URL already cached, force_refresh=False): returns immediately without
    any network request. Cold path: HEAD → download → transcode → Parquet.
    """
    cache = cache or get_cache()

    # Warm path: URL→key reverse lookup skips HEAD entirely.
    if not force_refresh:
        url_hit = cache.get_by_url(url)
        if url_hit is not None:
            cached_path, key = url_hit
            logger.info("cache URL-HIT key=%s size=%d", key, cached_path.stat().st_size)
            cache.touch(key)
            return cached_path, {"cache": "hit", "key": key}

    # Cold path (or forced refresh): HEAD to compute version key.
    etag, last_mod = await _head_metadata(url)
    key = build_cache_key(url, etag=etag, last_modified=last_mod)
    cached = cache.get(key)
    if cached is not None:
        logger.info("cache HIT key=%s size=%d", key, cached.stat().st_size)
        return cached, {"cache": "hit", "key": key}

    logger.info("cache MISS key=%s — downloading %s", key, url)
    fd, tmp_path_str = tempfile.mkstemp(prefix="dgd-dl-", suffix="." + fmt)
    import os

    os.close(fd)
    raw = Path(tmp_path_str)
    raw_ods: Path | None = None  # declared here so finally block can always reference it
    try:
        bytes_written, truncated = await download_to_file(url, raw, max_bytes=ANALYTICS_MAX_BYTES)
        if bytes_written == 0:
            raise AnalyticsError("Downloaded zero bytes")

        effective_fmt = fmt
        if fmt == "ods":
            raw_ods = raw
            raw_csv = _ods_to_csv(raw)
            raw = raw_csv
            effective_fmt = "csv"

        usable = _normalize_csv_encoding(raw) if effective_fmt in ("csv", "tsv") else raw
        parquet_path = cache.put_path(key)

        con = _new_con()
        try:
            src = str(usable).replace("'", "''")
            dst = str(parquet_path).replace("'", "''")
            if effective_fmt in ("csv", "tsv"):
                con.execute(
                    f"COPY (SELECT * FROM read_csv_auto('{src}', "
                    f"SAMPLE_SIZE=-1, IGNORE_ERRORS=TRUE)) "
                    f"TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD)"
                )
            elif effective_fmt in ("xlsx", "xls", "xlsm"):
                con.execute(
                    f"COPY (SELECT * FROM read_xlsx('{src}')) "
                    f"TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD)"
                )
            elif effective_fmt == "json":
                con.execute(
                    f"COPY (SELECT * FROM read_json_auto('{src}')) "
                    f"TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD)"
                )
            else:
                raise AnalyticsError(f"Format '{fmt}' not supported")
        finally:
            con.close()

        cache.finalize(key, url=url)  # store URL for future warm-path lookups
        logger.info(
            "cache STORE key=%s parquet=%d source=%d",
            key,
            parquet_path.stat().st_size,
            bytes_written,
        )
        return parquet_path, {
            "cache": "miss",
            "key": key,
            "source_bytes": bytes_written,
            "source_truncated": truncated,
            "parquet_bytes": parquet_path.stat().st_size,
        }
    finally:
        _safe_unlink(raw)
        if raw_ods is not None:
            _safe_unlink(raw_ods)


def _open_view(con: duckdb.DuckDBPyConnection, parquet: Path) -> None:
    p = str(parquet).replace("'", "''")
    con.execute(f"CREATE OR REPLACE VIEW data AS SELECT * FROM read_parquet('{p}')")


def _open_sandboxed(con: duckdb.DuckDBPyConnection, parquet: Path) -> None:
    """Materialize the resource into an in-memory table, then revoke external access.

    Used by query_resource, whose SQL is model-supplied. Without this, a SELECT
    could call DuckDB table functions (read_text/read_csv/read_blob/glob) to read
    arbitrary local files or reach the network — the keyword denylist in
    _validate_sql does not cover those. We materialize FIRST (reading the local
    Parquet is itself "external access") and only then disable it, so the user
    query runs entirely against the in-memory `data` table.
    """
    p = str(parquet).replace("'", "''")
    con.execute(f"CREATE TABLE data AS SELECT * FROM read_parquet('{p}')")
    con.execute("SET enable_external_access=false")
    con.execute("SET lock_configuration=true")


# ─── Public analytics tools ───────────────────────────────────────────────────


async def get_resource_schema(
    url: str,
    fmt: str | None,
    sample_rows: int = SCHEMA_SAMPLE_ROWS,
) -> dict[str, Any]:
    kind = classify_format(fmt)
    if kind is None:
        return {"error": f"Format '{fmt}' not supported"}
    try:
        parquet, meta = await ensure_cached(url, kind)
    except (httpx.HTTPError, AnalyticsError, duckdb.Error) as e:
        return {"error": f"Could not load resource: {e}"}

    con = _new_con()
    try:
        _open_view(con, parquet)
        described = con.execute("DESCRIBE data").fetchall()
        columns_meta = [
            {"name": row[0], "type": row[1], "nullable": row[2] == "YES"} for row in described
        ]
        row_count = con.execute("SELECT COUNT(*) FROM data").fetchone()[0]  # type: ignore[index]

        n = min(max(int(sample_rows), 1), SCHEMA_SAMPLE_ROWS)
        for col in columns_meta:
            quoted = _quote_ident(col["name"])
            try:
                vals = con.execute(
                    f"SELECT DISTINCT {quoted} FROM data WHERE {quoted} IS NOT NULL LIMIT {n}"
                ).fetchall()
                col["sample_values"] = [v[0] for v in vals]
            except duckdb.Error:
                col["sample_values"] = []
    finally:
        con.close()

    return {
        "source_url": url,
        "format": kind,
        "cache": meta,
        "row_count": row_count,
        "column_count": len(columns_meta),
        "columns": columns_meta,
    }


def _column_stats(
    con: duckdb.DuckDBPyConnection,
    col_name: str,
    col_type: str,
    top_n: int,
) -> dict[str, Any]:
    quoted = _quote_ident(col_name)
    type_lower = col_type.lower()
    is_numeric = any(
        t in type_lower
        for t in (
            "int",
            "double",
            "float",
            "decimal",
            "numeric",
            "real",
            "hugeint",
            "bigint",
            "smallint",
        )
    )
    is_temporal = any(t in type_lower for t in ("date", "time", "timestamp"))

    base = con.execute(
        f"SELECT COUNT(*), COUNT({quoted}), COUNT(DISTINCT {quoted}) FROM data"
    ).fetchone()
    total, non_null, distinct = base  # type: ignore[misc]

    stats: dict[str, Any] = {
        "name": col_name,
        "type": col_type,
        "non_null_count": non_null,
        "null_count": total - non_null,
        "distinct_count": distinct,
    }

    if is_numeric:
        try:
            r = con.execute(
                f"SELECT MIN({quoted}), MAX({quoted}), AVG({quoted}), "
                f"MEDIAN({quoted}) FROM data WHERE {quoted} IS NOT NULL"
            ).fetchone()
            if r is not None:
                stats.update({"min": r[0], "max": r[1], "mean": r[2], "median": r[3]})
        except duckdb.Error:
            pass
    elif is_temporal:
        try:
            r = con.execute(
                f"SELECT MIN({quoted}), MAX({quoted}) FROM data WHERE {quoted} IS NOT NULL"
            ).fetchone()
            if r is not None:
                stats.update({"min": r[0], "max": r[1]})
        except duckdb.Error:
            pass

    if distinct <= max(top_n * 10, 100):
        try:
            rows = con.execute(
                f"SELECT {quoted}, COUNT(*) AS c FROM data "
                f"WHERE {quoted} IS NOT NULL "
                f"GROUP BY {quoted} ORDER BY c DESC LIMIT {top_n}"
            ).fetchall()
            stats["top_values"] = [{"value": r[0], "count": r[1]} for r in rows]
        except duckdb.Error:
            pass

    return stats


async def summarize_resource(
    url: str,
    fmt: str | None,
    max_categorical_top_n: int = 10,
) -> dict[str, Any]:
    kind = classify_format(fmt)
    if kind is None:
        return {"error": f"Format '{fmt}' not supported"}
    try:
        parquet, meta = await ensure_cached(url, kind)
    except (httpx.HTTPError, AnalyticsError, duckdb.Error) as e:
        return {"error": f"Could not load resource: {e}"}

    top_n = min(max(int(max_categorical_top_n), 1), SUMMARIZE_MAX_TOP_N)
    con = _new_con()
    try:
        _open_view(con, parquet)
        described = con.execute("DESCRIBE data").fetchall()
        columns_meta = [{"name": row[0], "type": row[1]} for row in described]
        row_count = con.execute("SELECT COUNT(*) FROM data").fetchone()[0]  # type: ignore[index]
        column_stats = [_column_stats(con, c["name"], c["type"], top_n) for c in columns_meta]
    finally:
        con.close()

    return {
        "source_url": url,
        "format": kind,
        "cache": meta,
        "row_count": row_count,
        "column_count": len(columns_meta),
        "columns": column_stats,
    }


# ─── Filter and aggregate ─────────────────────────────────────────────────────


Op = Literal[
    "=",
    "!=",
    "<>",
    "<",
    "<=",
    ">",
    ">=",
    "in",
    "not_in",
    "contains",
    "starts_with",
    "ends_with",
    "is_null",
    "is_not_null",
]


def _build_filter_clause(f: dict[str, Any]) -> str:
    col = f.get("col")
    op = f.get("op", "=")
    val = f.get("val")
    if not isinstance(col, str):
        raise AnalyticsError("filter.col must be a string")
    if op not in ALLOWED_OPS:
        raise AnalyticsError(f"Operator not allowed: {op}")
    q = _quote_ident(col)
    if op in ("is_null",):
        return f"{q} IS NULL"
    if op in ("is_not_null",):
        return f"{q} IS NOT NULL"
    if op == "in":
        if not isinstance(val, list) or not val:
            raise AnalyticsError("'in' requires non-empty list")
        joined = ", ".join(_quote_literal(v) for v in val)
        return f"{q} IN ({joined})"
    if op == "not_in":
        if not isinstance(val, list) or not val:
            raise AnalyticsError("'not_in' requires non-empty list")
        joined = ", ".join(_quote_literal(v) for v in val)
        return f"{q} NOT IN ({joined})"
    if op == "contains":
        if not isinstance(val, str):
            raise AnalyticsError("'contains' requires string val")
        esc = val.replace("'", "''").replace("%", r"\%").replace("_", r"\_")
        return f"{q} ILIKE '%' || '{esc}' || '%' ESCAPE '\\'"
    if op == "starts_with":
        if not isinstance(val, str):
            raise AnalyticsError("'starts_with' requires string val")
        esc = val.replace("'", "''").replace("%", r"\%").replace("_", r"\_")
        return f"{q} ILIKE '{esc}%' ESCAPE '\\'"
    if op == "ends_with":
        if not isinstance(val, str):
            raise AnalyticsError("'ends_with' requires string val")
        esc = val.replace("'", "''").replace("%", r"\%").replace("_", r"\_")
        return f"{q} ILIKE '%{esc}' ESCAPE '\\'"
    # Comparison ops.
    cmp_op = "<>" if op == "!=" else op
    return f"{q} {cmp_op} {_quote_literal(val)}"


def _build_where(filters: list[dict] | None) -> str:
    if not filters:
        return ""
    parts = [_build_filter_clause(f) for f in filters]
    return "WHERE " + " AND ".join(parts)


def _build_order_by(order_by: list[dict] | None) -> str:
    if not order_by:
        return ""
    parts = []
    for ob in order_by:
        col = ob.get("col")
        if not isinstance(col, str):
            raise AnalyticsError("order_by.col must be a string")
        direction = (ob.get("dir") or "asc").lower()
        if direction not in ("asc", "desc"):
            raise AnalyticsError(f"Invalid order direction: {direction}")
        parts.append(f"{_quote_ident(col)} {direction.upper()}")
    return "ORDER BY " + ", ".join(parts)


def _build_agg_expr(agg: dict) -> str:
    col = agg.get("col")
    fn = (agg.get("fn") or "").lower()
    alias = agg.get("alias") or f"{fn}_{col or 'all'}"
    if fn not in ALLOWED_AGG_FNS:
        raise AnalyticsError(f"Aggregation not allowed: {fn}")
    if fn == "count" and col in (None, "*"):
        expr = "COUNT(*)"
    elif fn == "count":
        if not isinstance(col, str):
            raise AnalyticsError("count requires col to be a string")
        expr = f"COUNT({_quote_ident(col)})"
    elif fn == "count_distinct":
        if not isinstance(col, str):
            raise AnalyticsError("count_distinct requires col")
        expr = f"COUNT(DISTINCT {_quote_ident(col)})"
    elif fn in ("avg", "mean"):
        if not isinstance(col, str):
            raise AnalyticsError(f"{fn} requires col")
        expr = f"AVG({_quote_ident(col)})"
    elif fn == "median":
        if not isinstance(col, str):
            raise AnalyticsError("median requires col")
        expr = f"MEDIAN({_quote_ident(col)})"
    elif fn in ("sum", "min", "max", "stddev", "variance"):
        if not isinstance(col, str):
            raise AnalyticsError(f"{fn} requires col")
        sql_fn = "STDDEV" if fn == "stddev" else ("VAR_SAMP" if fn == "variance" else fn.upper())
        expr = f"{sql_fn}({_quote_ident(col)})"
    else:
        raise AnalyticsError(f"Unhandled fn: {fn}")
    if not isinstance(alias, str):
        raise AnalyticsError("alias must be a string")
    return f"{expr} AS {_quote_ident(alias)}"


# ─── New analytics tools (v0.5) ───────────────────────────────────────────────

_NUMERIC_TYPE_FRAGMENTS = (
    "int",
    "double",
    "float",
    "decimal",
    "numeric",
    "real",
    "hugeint",
    "bigint",
    "smallint",
    "ubigint",
    "uinteger",
    "usmallint",
    "utinyint",
    "tinyint",
)


async def quantiles_resource(
    url: str,
    fmt: str | None,
    columns: list[str] | None = None,
    percentiles: list[float] | None = None,
    filters: list[dict] | None = None,
) -> dict[str, Any]:
    """Percentile distribution of numeric columns in a cached resource."""
    kind = classify_format(fmt)
    if kind is None:
        return {"error": f"Format '{fmt}' not supported"}

    if percentiles is None:
        percentiles = [0.25, 0.5, 0.75, 0.90, 0.95, 0.99]
    for p in percentiles:
        if not (0 < p < 1):
            return {"error": f"Percentile {p} must be in (0, 1) exclusive"}
    pctile_keys_check = [f"p{int(round(p * 100))}" for p in percentiles]
    if len(set(pctile_keys_check)) != len(pctile_keys_check):
        return {
            "error": "Duplicate percentile values after rounding (e.g., 0.904 and 0.905 both map to p90). Use distinct values."
        }

    try:
        parquet, meta = await ensure_cached(url, kind)
    except (httpx.HTTPError, AnalyticsError, duckdb.Error) as e:
        return {"error": f"Could not load resource: {e}"}

    con = _new_con()
    try:
        _open_view(con, parquet)
        described = con.execute("DESCRIBE data").fetchall()
        row_count = con.execute("SELECT COUNT(*) FROM data").fetchone()[0]  # type: ignore[index]

        all_numeric = [
            (row[0], row[1])
            for row in described
            if any(t in row[1].lower() for t in _NUMERIC_TYPE_FRAGMENTS)
        ]
        if columns is not None:
            all_names = {row[0] for row in described}
            for c in columns:
                if c not in all_names:
                    return {"error": f"Column '{c}' not found in resource"}
            selected = [(n, t) for n, t in all_numeric if n in set(columns)]
        else:
            selected = all_numeric

        if not selected:
            return {
                "error": "No numeric columns found (or none of the requested columns are numeric)"
            }

        try:
            where = _build_where(filters)
        except AnalyticsError as e:
            return {"error": str(e)}

        pctile_arr = "[" + ", ".join(repr(float(p)) for p in percentiles) + "]"
        pctile_keys = [f"p{int(round(p * 100))}" for p in percentiles]

        col_results = []
        for col_name, col_type in selected:
            quoted = _quote_ident(col_name)
            try:
                row = con.execute(
                    f"SELECT quantile_cont({quoted}, {pctile_arr}), "
                    f"min({quoted}), max({quoted}), avg({quoted}), "
                    f"count({quoted}), count(*) - count({quoted}) "
                    f"FROM data {where}"
                ).fetchone()
                if row is None:
                    continue
                q_arr, mn, mx, mean, non_null, null_ct = row
                result = {
                    "name": col_name,
                    "type": col_type,
                    "non_null_count": non_null,
                    "null_count": null_ct,
                    "min": mn,
                    "max": mx,
                    "mean": mean,
                }
                if q_arr is not None:
                    for key, val in zip(pctile_keys, q_arr):
                        result[key] = val
                col_results.append(result)
            except duckdb.Error as e:
                col_results.append({"name": col_name, "type": col_type, "error": str(e)})
    finally:
        con.close()

    return {
        "source_url": url,
        "format": kind,
        "cache": meta,
        "row_count": row_count,
        "percentiles": percentiles,
        "columns": col_results,
    }


async def find_duplicates_resource(
    url: str,
    fmt: str | None,
    columns: list[str] | None = None,
    filters: list[dict] | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Find rows duplicated on the specified columns (or all columns)."""
    kind = classify_format(fmt)
    if kind is None:
        return {"error": f"Format '{fmt}' not supported"}

    limit = min(max(int(limit), 1), 500)

    try:
        parquet, meta = await ensure_cached(url, kind)
    except (httpx.HTTPError, AnalyticsError, duckdb.Error) as e:
        return {"error": f"Could not load resource: {e}"}

    con = _new_con()
    try:
        _open_view(con, parquet)
        try:
            where = _build_where(filters)
        except AnalyticsError as e:
            return {"error": str(e)}

        if columns is None:
            described = con.execute("DESCRIBE data").fetchall()
            columns = [row[0] for row in described]

        try:
            group_cols = ", ".join(_quote_ident(c) for c in columns)
        except AnalyticsError as e:
            return {"error": str(e)}

        count_sql = (
            f"SELECT COUNT(*) AS grps, SUM(cnt) AS total_rows FROM ("
            f"SELECT COUNT(*) AS cnt FROM data {where} "
            f"GROUP BY {group_cols} HAVING COUNT(*) > 1) t"
        ).strip()
        try:
            count_row = con.execute(count_sql).fetchone()
        except duckdb.Error as e:
            return {"error": f"DuckDB: {e}"}

        duplicate_groups = count_row[0] if count_row else 0  # type: ignore[index]
        total_dup_rows = count_row[1] if count_row else 0  # type: ignore[index]

        main_sql = (
            f"SELECT {group_cols}, COUNT(*) AS duplicate_count "
            f"FROM data {where} "
            f"GROUP BY {group_cols} "
            f"HAVING COUNT(*) > 1 "
            f"ORDER BY duplicate_count DESC "
            f"LIMIT {limit}"
        ).strip()
        try:
            rs = con.execute(main_sql)
        except duckdb.Error as e:
            return {"error": f"DuckDB: {e}"}
        col_names = [d[0] for d in rs.description]
        rows = rs.fetchall()
    finally:
        con.close()

    return {
        "source_url": url,
        "format": kind,
        "cache": meta,
        "columns_checked": columns,
        "duplicate_groups_found": duplicate_groups,
        "groups_returned": len(rows),
        "total_duplicate_rows": total_dup_rows,
        "columns": col_names,
        "rows": [list(r) for r in rows],
    }


async def detect_outliers_resource(
    url: str,
    fmt: str | None,
    column: str,
    filters: list[dict] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Find rows where a numeric column falls outside the IQR fence (Q1-1.5*IQR, Q3+1.5*IQR)."""
    kind = classify_format(fmt)
    if kind is None:
        return {"error": f"Format '{fmt}' not supported"}

    limit = min(max(int(limit), 1), 500)

    try:
        _quote_ident(column)  # validate early
    except AnalyticsError as e:
        return {"error": str(e)}

    try:
        parquet, meta = await ensure_cached(url, kind)
    except (httpx.HTTPError, AnalyticsError, duckdb.Error) as e:
        return {"error": f"Could not load resource: {e}"}

    con = _new_con()
    try:
        _open_view(con, parquet)
        try:
            where = _build_where(filters)
        except AnalyticsError as e:
            return {"error": str(e)}

        quoted = _quote_ident(column)

        try:
            stats_row = con.execute(
                f"SELECT quantile_cont({quoted}, 0.25), quantile_cont({quoted}, 0.75) "
                f"FROM data {where}"
            ).fetchone()
        except duckdb.Error as e:
            return {"error": f"Could not compute IQR for column '{column}': {e}. Is it numeric?"}

        if stats_row is None or stats_row[0] is None or stats_row[1] is None:
            return {"error": f"Column '{column}' has no non-null values in the filtered data."}

        q1, q3 = stats_row[0], stats_row[1]
        iqr = q3 - q1
        if iqr == 0:
            return {
                "error": f"IQR is 0 for column '{column}' — all values are identical or the distribution has no spread. Outlier detection is undefined.",
                "q1": q1,
                "q3": q3,
                "iqr": 0,
            }

        lower_fence = q1 - 1.5 * iqr
        upper_fence = q3 + 1.5 * iqr

        outlier_where = (
            f"{where} AND ({quoted} < {lower_fence} OR {quoted} > {upper_fence})"
            if where
            else f"WHERE ({quoted} < {lower_fence} OR {quoted} > {upper_fence})"
        )
        try:
            count_row = con.execute(f"SELECT COUNT(*) FROM data {outlier_where}").fetchone()
            outlier_count = count_row[0] if count_row else 0  # type: ignore[index]
        except duckdb.Error:
            outlier_count = None

        try:
            rs = con.execute(
                f"SELECT *, {lower_fence} AS lower_fence, {upper_fence} AS upper_fence "
                f"FROM data {outlier_where} "
                f"ORDER BY ABS({quoted} - {(q1 + q3) / 2}) DESC "
                f"LIMIT {limit}"
            )
            col_names = [d[0] for d in rs.description]
            rows = rs.fetchall()
        except duckdb.Error as e:
            return {"error": f"DuckDB: {e}"}
    finally:
        con.close()

    return {
        "source_url": url,
        "format": kind,
        "cache": meta,
        "column": column,
        "method": "IQR",
        "q1": q1,
        "q3": q3,
        "iqr": iqr,
        "lower_fence": lower_fence,
        "upper_fence": upper_fence,
        "outlier_count_estimate": outlier_count,
        "rows_returned": len(rows),
        "columns": col_names,
        "rows": [list(r) for r in rows],
    }


async def save_query_to_csv(
    url: str,
    fmt: str | None,
    dest: str | None = None,
    sql: str | None = None,
    filters: list[dict] | None = None,
    columns: list[str] | None = None,
    limit: int = 10_000,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Run a filter or SQL query against a cached resource and write the result to CSV."""
    import csv as _csv
    import datetime
    import re

    kind = classify_format(fmt)
    if kind is None:
        return {"error": f"Format '{fmt}' not supported"}

    limit = min(max(int(limit), 1), 100_000)

    if dest is None:
        slug = re.sub(r"[^a-z0-9]", "-", Path(url).stem.lower())[:30]
        ts = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
        export_dir = Path.home() / "Downloads" / "datosgobdo-exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        dest_path = export_dir / f"{slug}-{ts}.csv"
    else:
        if ".." in Path(dest).parts:
            return {"error": "Destination path must not contain '..' components"}
        dest_path = Path(dest).resolve()
        if dest_path.suffix not in (".csv", ".tsv"):
            return {"error": "Destination must end in .csv or .tsv"}
        # The OS per-user temp dir is writable scratch space. On macOS it lives under
        # /private/var/folders/…, which would otherwise trip the /private/var denylist
        # entry below. Allow it explicitly before running the system-path check.
        tmp_root = Path(tempfile.gettempdir()).resolve()
        if tmp_root not in dest_path.parents:
            # Check both the raw path and the resolved path (macOS resolves /etc → /private/etc).
            for check_str in (dest, str(dest_path)):
                for prefix in _FORBIDDEN_DEST_PREFIXES:
                    if check_str.startswith(prefix):
                        return {"error": f"Cannot write to system path: {check_str}"}

    if dest_path.exists() and not overwrite:
        return {"error": f"File already exists: {dest_path}. Pass overwrite=True to replace."}

    try:
        parquet, meta = await ensure_cached(url, kind)
    except (httpx.HTTPError, AnalyticsError, duckdb.Error) as e:
        return {"error": f"Could not load resource: {e}"}

    con = _new_con()
    try:
        if sql is not None:
            try:
                cleaned = _validate_sql(sql)
            except AnalyticsError as e:
                return {"error": str(e)}
            _open_sandboxed(con, parquet)
            wrapped = f"SELECT * FROM ({cleaned}) AS _q LIMIT {limit}"
            try:
                rs = con.execute(wrapped)
            except duckdb.Error as e:
                return {"error": f"DuckDB: {e}"}
        else:
            _open_view(con, parquet)
            select_clause = "*"
            if columns:
                try:
                    select_clause = ", ".join(_quote_ident(c) for c in columns)
                except AnalyticsError as e:
                    return {"error": str(e)}
            try:
                where = _build_where(filters)
            except AnalyticsError as e:
                return {"error": str(e)}
            try:
                rs = con.execute(f"SELECT {select_clause} FROM data {where} LIMIT {limit}".strip())
            except duckdb.Error as e:
                return {"error": f"DuckDB: {e}"}

        col_names = [d[0] for d in rs.description]
        rows = rs.fetchall()
    finally:
        con.close()

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    # O_NOFOLLOW closes the TOCTOU window: a symlink swapped in between the
    # earlier path checks and this write would otherwise be followed when
    # overwrite=True. Raises ELOOP instead of writing through the link.
    open_flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(dest_path), open_flags, 0o644)
    except OSError as e:
        return {"error": f"Cannot open destination for writing: {e}"}
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
        writer = _csv.writer(f)
        writer.writerow(col_names)
        writer.writerows(rows)

    bytes_written = dest_path.stat().st_size
    return {
        "path": str(dest_path),
        "rows_written": len(rows),
        "columns": col_names,
        "bytes_written": bytes_written,
        "cache": meta,
    }


async def filter_resource(
    url: str,
    fmt: str | None,
    filters: list[dict] | None = None,
    columns: list[str] | None = None,
    order_by: list[dict] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Typed WHERE/SELECT/ORDER BY/LIMIT against a cached resource."""
    kind = classify_format(fmt)
    if kind is None:
        return {"error": f"Format '{fmt}' not supported"}
    try:
        parquet, meta = await ensure_cached(url, kind)
    except (httpx.HTTPError, AnalyticsError, duckdb.Error) as e:
        return {"error": f"Could not load resource: {e}"}

    limit = min(max(int(limit), 1), FILTER_MAX_LIMIT)
    offset = max(int(offset), 0)

    con = _new_con()
    try:
        _open_view(con, parquet)
        select_clause = "*"
        if columns:
            select_clause = ", ".join(_quote_ident(c) for c in columns)
        try:
            where = _build_where(filters)
            order = _build_order_by(order_by)
        except AnalyticsError as e:
            return {"error": str(e)}

        sql = (
            f"SELECT {select_clause} FROM data {where} {order} LIMIT {limit} OFFSET {offset}"
        ).strip()
        try:
            rs = con.execute(sql)
        except duckdb.Error as e:
            return {"error": f"DuckDB: {e}", "sql": sql}
        col_names = [d[0] for d in rs.description]
        rows = rs.fetchall()
        # Estimate total matching rows (separate count query).
        try:
            total = con.execute(f"SELECT COUNT(*) FROM data {where}".strip()).fetchone()[0]  # type: ignore[index]
        except duckdb.Error:
            total = None

    finally:
        con.close()

    return {
        "source_url": url,
        "format": kind,
        "cache": meta,
        "matching_rows_total": total,
        "rows_returned": len(rows),
        "columns": col_names,
        "limit": limit,
        "offset": offset,
        "rows": [list(r) for r in rows],
    }


async def aggregate_resource(
    url: str,
    fmt: str | None,
    aggregations: list[dict],
    group_by: list[str] | None = None,
    filters: list[dict] | None = None,
    having: list[dict] | None = None,
    order_by: list[dict] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Typed GROUP BY + aggregations + optional HAVING."""
    kind = classify_format(fmt)
    if kind is None:
        return {"error": f"Format '{fmt}' not supported"}
    if not aggregations:
        return {"error": "aggregations cannot be empty"}
    try:
        parquet, meta = await ensure_cached(url, kind)
    except (httpx.HTTPError, AnalyticsError, duckdb.Error) as e:
        return {"error": f"Could not load resource: {e}"}

    limit = min(max(int(limit), 1), AGGREGATE_MAX_LIMIT)

    con = _new_con()
    try:
        _open_view(con, parquet)
        try:
            agg_parts = [_build_agg_expr(a) for a in aggregations]
        except AnalyticsError as e:
            return {"error": str(e)}

        group_parts: list[str] = []
        if group_by:
            try:
                group_parts = [_quote_ident(c) for c in group_by]
            except AnalyticsError as e:
                return {"error": str(e)}

        select_clause = ", ".join([*group_parts, *agg_parts])
        try:
            where = _build_where(filters)
            order = _build_order_by(order_by)
        except AnalyticsError as e:
            return {"error": str(e)}
        group_clause = "GROUP BY " + ", ".join(group_parts) if group_parts else ""

        # HAVING uses the same filter syntax but column refs are agg aliases.
        having_clause = ""
        if having:
            try:
                # HAVING refers to aliases which are valid identifiers — same path.
                having_clause = "HAVING " + " AND ".join(_build_filter_clause(h) for h in having)
            except AnalyticsError as e:
                return {"error": str(e)}

        sql = (
            f"SELECT {select_clause} FROM data {where} {group_clause} "
            f"{having_clause} {order} LIMIT {limit}"
        ).strip()
        try:
            rs = con.execute(sql)
        except duckdb.Error as e:
            return {"error": f"DuckDB: {e}", "sql": sql}
        col_names = [d[0] for d in rs.description]
        rows = rs.fetchall()
    finally:
        con.close()

    return {
        "source_url": url,
        "format": kind,
        "cache": meta,
        "groups_returned": len(rows),
        "columns": col_names,
        "limit": limit,
        "rows": [list(r) for r in rows],
    }


# ─── Raw SQL escape hatch ─────────────────────────────────────────────────────


def _validate_sql(sql: str) -> str:
    """Reject anything that isn't a single read-only SELECT/WITH statement.

    DuckDB's parser would otherwise happily run DDL on the in-memory connection
    (the underlying file is read-only, but the in-memory view could be replaced
    or new tables created). We also strip semicolons to prevent multi-statement
    injection.
    """
    s = sql.strip().rstrip(";").strip()
    if not s:
        raise AnalyticsError("Empty SQL")
    if ";" in s:
        raise AnalyticsError("Multiple statements are not allowed; use a single SELECT")
    if not _SQL_ALLOWED_START.match(s):
        raise AnalyticsError("SQL must start with SELECT or WITH")
    if _SQL_FORBIDDEN.search(s):
        raise AnalyticsError("SQL contains a forbidden keyword (DDL/DML disallowed)")
    return s


async def query_resource(
    url: str,
    fmt: str | None,
    sql: str,
    limit: int = 200,
) -> dict[str, Any]:
    """Run an ad-hoc read-only SQL query against a cached resource.

    The cached resource is available as the table/view named `data`. Only
    SELECT/WITH statements are allowed; DDL, DML, COPY, PRAGMA, INSTALL, LOAD,
    ATTACH, etc. are blocked. The query is wrapped to enforce a hard row
    limit even if the user didn't include LIMIT.
    """
    kind = classify_format(fmt)
    if kind is None:
        return {"error": f"Format '{fmt}' not supported"}
    try:
        cleaned = _validate_sql(sql)
    except AnalyticsError as e:
        return {"error": str(e)}
    try:
        parquet, meta = await ensure_cached(url, kind)
    except (httpx.HTTPError, AnalyticsError, duckdb.Error) as e:
        return {"error": f"Could not load resource: {e}"}

    limit = min(max(int(limit), 1), SQL_MAX_LIMIT)
    wrapped = f"SELECT * FROM ({cleaned}) AS _user_q LIMIT {limit}"

    con = _new_con()
    try:
        _open_sandboxed(con, parquet)
        try:
            rs = con.execute(wrapped)
        except duckdb.Error as e:
            return {"error": f"DuckDB: {e}", "sql": wrapped}
        col_names = [d[0] for d in rs.description]
        rows = rs.fetchall()
    finally:
        con.close()

    return {
        "source_url": url,
        "format": kind,
        "cache": meta,
        "sql_executed": wrapped,
        "rows_returned": len(rows),
        "columns": col_names,
        "rows": [list(r) for r in rows],
    }


# ─── Cache management tool ────────────────────────────────────────────────────


def get_cache_stats() -> dict[str, Any]:
    return get_cache().stats()


def clear_cache() -> dict[str, Any]:
    removed = get_cache().clear()
    return {"removed_entries": removed}

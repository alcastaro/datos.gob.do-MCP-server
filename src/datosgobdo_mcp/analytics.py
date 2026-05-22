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

import asyncio
import logging
import re
import tempfile
from pathlib import Path
from typing import Any, Literal

import duckdb
import httpx

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
_IDENT_OK = re.compile(r'^[\w .À-ſ]+$', re.UNICODE)
_IDENT_FORBIDDEN_SUBSTR = ("--", "/*", "*/", ";")

ALLOWED_AGG_FNS = {
    "count", "count_distinct", "sum", "avg", "mean", "median",
    "min", "max", "stddev", "variance",
}

ALLOWED_OPS = {
    "=", "!=", "<>", "<", "<=", ">", ">=",
    "in", "not_in", "contains", "starts_with", "ends_with",
    "is_null", "is_not_null",
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
            con.execute(f"INSTALL {ext}; LOAD {ext};")
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
            headers={"User-Agent": "datosgobdo-mcp/0.3"},
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
        from odf.table import Table, TableRow, TableCell
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
) -> tuple[Path, dict[str, Any]]:
    """Make sure the resource is in cache as Parquet. Return (parquet_path, meta).

    Cold path: download → transcode → write Parquet via DuckDB.
    Warm path: just bump access time and return cached path.
    """
    cache = cache or get_cache()
    etag, last_mod = await _head_metadata(url)
    key = build_cache_key(url, etag=etag, last_modified=last_mod)
    cached = cache.get(key)
    if cached is not None:
        logger.info("cache HIT key=%s size=%d", key, cached.stat().st_size)
        return cached, {"cache": "hit", "key": key}

    logger.info("cache MISS key=%s — downloading %s", key, url)
    # Cold: download into a temp file, then convert to Parquet at cache path.
    fd, tmp_path = tempfile.mkstemp(prefix="dgd-dl-", suffix="." + fmt)
    import os

    os.close(fd)
    raw = Path(tmp_path)
    try:
        bytes_written, truncated = await download_to_file(
            url, raw, max_bytes=ANALYTICS_MAX_BYTES
        )
        if bytes_written == 0:
            raise AnalyticsError("Downloaded zero bytes")

        # ODS path: convert to CSV first, then run the CSV pipeline.
        effective_fmt = fmt
        if fmt == "ods":
            raw_csv = _ods_to_csv(raw)
            raw = raw_csv  # so cleanup gets both via sidecar suffix path
            effective_fmt = "csv"

        usable = (
            _normalize_csv_encoding(raw) if effective_fmt in ("csv", "tsv") else raw
        )
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

        cache.finalize(key)
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


def _open_view(con: duckdb.DuckDBPyConnection, parquet: Path) -> None:
    p = str(parquet).replace("'", "''")
    con.execute(f"CREATE OR REPLACE VIEW data AS SELECT * FROM read_parquet('{p}')")


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
            {"name": row[0], "type": row[1], "nullable": row[2] == "YES"}
            for row in described
        ]
        row_count = con.execute("SELECT COUNT(*) FROM data").fetchone()[0]

        n = min(int(sample_rows), 1000)
        for col in columns_meta:
            quoted = _quote_ident(col["name"])
            try:
                vals = con.execute(
                    f"SELECT DISTINCT {quoted} FROM data "
                    f"WHERE {quoted} IS NOT NULL LIMIT 5"
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
        for t in ("int", "double", "float", "decimal", "numeric", "real",
                  "hugeint", "bigint", "smallint")
    )
    is_temporal = any(t in type_lower for t in ("date", "time", "timestamp"))

    base = con.execute(
        f"SELECT COUNT(*), COUNT({quoted}), COUNT(DISTINCT {quoted}) FROM data"
    ).fetchone()
    total, non_null, distinct = base

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
            stats.update({"min": r[0], "max": r[1], "mean": r[2], "median": r[3]})
        except duckdb.Error:
            pass
    elif is_temporal:
        try:
            r = con.execute(
                f"SELECT MIN({quoted}), MAX({quoted}) FROM data "
                f"WHERE {quoted} IS NOT NULL"
            ).fetchone()
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
        row_count = con.execute("SELECT COUNT(*) FROM data").fetchone()[0]
        column_stats = [
            _column_stats(con, c["name"], c["type"], top_n) for c in columns_meta
        ]
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
    "=", "!=", "<>", "<", "<=", ">", ">=",
    "in", "not_in", "contains", "starts_with", "ends_with",
    "is_null", "is_not_null",
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
        expr = f"COUNT({_quote_ident(col)})"
    elif fn == "count_distinct":
        if col is None:
            raise AnalyticsError("count_distinct requires col")
        expr = f"COUNT(DISTINCT {_quote_ident(col)})"
    elif fn in ("avg", "mean"):
        expr = f"AVG({_quote_ident(col)})"
    elif fn == "median":
        expr = f"MEDIAN({_quote_ident(col)})"
    elif fn in ("sum", "min", "max", "stddev", "variance"):
        sql_fn = "STDDEV" if fn == "stddev" else ("VAR_SAMP" if fn == "variance" else fn.upper())
        expr = f"{sql_fn}({_quote_ident(col)})"
    else:
        raise AnalyticsError(f"Unhandled fn: {fn}")
    return f"{expr} AS {_quote_ident(alias)}"


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
            f"SELECT {select_clause} FROM data "
            f"{where} {order} LIMIT {limit} OFFSET {offset}"
        ).strip()
        try:
            rs = con.execute(sql)
        except duckdb.Error as e:
            return {"error": f"DuckDB: {e}", "sql": sql}
        col_names = [d[0] for d in rs.description]
        rows = rs.fetchall()
        # Estimate total matching rows (separate count query).
        try:
            total = con.execute(
                f"SELECT COUNT(*) FROM data {where}".strip()
            ).fetchone()[0]
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
                having_clause = "HAVING " + " AND ".join(
                    _build_filter_clause(h) for h in having
                )
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
        _open_view(con, parquet)
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

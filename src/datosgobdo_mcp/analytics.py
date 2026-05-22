"""Analytics tools backed by DuckDB.

v0.2 introduces:
    - get_resource_schema: column names + inferred types + sample values
    - summarize_resource: row count, null rates, distinct counts, top values

These tools fetch the file once into a temp path, register it with DuckDB,
and run aggregate queries server-side. No raw rows hit the LLM context.

v0.3 will wrap these with a persistent cache layer.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import duckdb
import httpx

from .download import (
    ANALYTICS_MAX_BYTES,
    classify_format,
    download_capped,
    download_to_file,
    normalize_format,
)

# How many rows to sample for type inference + previews inside profile.
SCHEMA_SAMPLE_ROWS = 1000
SUMMARIZE_MAX_TOP_N = 50

# DuckDB CSV read settings — let it auto-detect everything; fall back to
# explicit options on failure.
_CSV_AUTODETECT = "AUTO_DETECT=TRUE, IGNORE_ERRORS=TRUE"


class AnalyticsError(RuntimeError):
    pass


def _new_con() -> duckdb.DuckDBPyConnection:
    """Per-call in-memory DuckDB connection. v0.3 will swap for cached one."""
    con = duckdb.connect(":memory:")
    # Make sure httpfs and excel readers are available; loading is cheap.
    try:
        con.execute("INSTALL httpfs; LOAD httpfs;")
    except duckdb.Error:
        pass
    try:
        con.execute("INSTALL excel; LOAD excel;")
    except duckdb.Error:
        pass
    return con


def _normalize_csv_encoding(path: Path) -> Path:
    """If CSV is non-UTF-8, transcode it to a sibling UTF-8 file.

    DuckDB's read_csv assumes UTF-8. Files in CP1252/Latin-1 get their header
    row mangled, breaking column detection. We sniff the first chunk and, if
    needed, rewrite the file as UTF-8 next to the original.
    """
    from .download import _detect_encoding

    with path.open("rb") as f:
        sample = f.read(200_000)
    enc = _detect_encoding(sample)
    if enc in ("utf-8", "utf-8-sig", "ascii"):
        return path
    utf8_path = path.with_suffix(path.suffix + ".utf8")
    # Stream-transcode to avoid loading the whole file into memory.
    with path.open("rb") as src, utf8_path.open("wb") as dst:
        decoder_buf = b""
        while True:
            chunk = src.read(1 << 20)  # 1 MB
            if not chunk:
                break
            decoder_buf += chunk
            try:
                text = decoder_buf.decode(enc)
                decoder_buf = b""
            except UnicodeDecodeError as e:
                # Split at last valid boundary, keep tail for next loop.
                text = decoder_buf[: e.start].decode(enc, errors="replace")
                decoder_buf = decoder_buf[e.start :]
            dst.write(text.encode("utf-8"))
        if decoder_buf:
            dst.write(decoder_buf.decode(enc, errors="replace").encode("utf-8"))
    return utf8_path


def _table_from_file(
    con: duckdb.DuckDBPyConnection,
    file_path: Path,
    fmt: str,
) -> None:
    """Register file at file_path as DuckDB view named `data`."""
    if fmt in ("csv", "tsv"):
        # Pre-transcode non-UTF-8 files so DuckDB can detect headers properly.
        usable = _normalize_csv_encoding(file_path)
        p = str(usable).replace("'", "''")
        con.execute(
            f"CREATE OR REPLACE VIEW data AS "
            f"SELECT * FROM read_csv_auto('{p}', SAMPLE_SIZE=-1, IGNORE_ERRORS=TRUE)"
        )
    elif fmt in ("xlsx", "xls", "xlsm"):
        p = str(file_path).replace("'", "''")
        con.execute(f"CREATE OR REPLACE VIEW data AS SELECT * FROM read_xlsx('{p}')")
    elif fmt == "json":
        p = str(file_path).replace("'", "''")
        con.execute(
            f"CREATE OR REPLACE VIEW data AS SELECT * FROM read_json_auto('{p}')"
        )
    else:
        raise AnalyticsError(f"Format '{fmt}' not supported by analytics engine")


async def _fetch_to_temp(url: str, fmt: str) -> tuple[Path, int, bool]:
    """Download URL to a temp file. Returns (path, bytes, truncated)."""
    suffix = "." + fmt if fmt else ""
    fd, tmp_path = tempfile.mkstemp(prefix="dgd-", suffix=suffix)
    Path(tmp_path).touch()
    import os

    os.close(fd)
    path = Path(tmp_path)
    bytes_written, truncated = await download_to_file(
        url, path, max_bytes=ANALYTICS_MAX_BYTES
    )
    return path, bytes_written, truncated


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass
    # Also remove the transcoded sidecar if we created one.
    sidecar = path.with_suffix(path.suffix + ".utf8")
    try:
        sidecar.unlink(missing_ok=True)
    except Exception:
        pass


async def get_resource_schema(
    url: str,
    fmt: str | None,
    sample_rows: int = SCHEMA_SAMPLE_ROWS,
) -> dict[str, Any]:
    """Return column names, inferred types, and a small value sample per column.

    Cheap reconnaissance step before deciding which tool to call next.
    """
    kind = classify_format(fmt)
    if kind is None:
        return {"error": f"Format '{fmt}' not supported"}

    path: Path | None = None
    try:
        path, n_bytes, truncated = await _fetch_to_temp(url, kind)
        con = _new_con()
        _table_from_file(con, path, kind)

        # Column metadata via DESCRIBE.
        described = con.execute("DESCRIBE data").fetchall()
        columns_meta = [
            {"name": row[0], "type": row[1], "nullable": row[2] == "YES"}
            for row in described
        ]

        # Row count (may be partial if file was truncated).
        row_count = con.execute("SELECT COUNT(*) FROM data").fetchone()[0]

        # Per-column small sample of distinct non-null values.
        col_names = [c["name"] for c in columns_meta]
        samples: dict[str, list[Any]] = {}
        n = min(int(sample_rows), 1000)
        for cname in col_names:
            quoted = '"' + cname.replace('"', '""') + '"'
            try:
                vals = con.execute(
                    f"SELECT DISTINCT {quoted} FROM data "
                    f"WHERE {quoted} IS NOT NULL LIMIT 5"
                ).fetchall()
                samples[cname] = [v[0] for v in vals]
            except duckdb.Error:
                samples[cname] = []

        for col in columns_meta:
            col["sample_values"] = samples.get(col["name"], [])

        return {
            "source_url": url,
            "format": kind,
            "bytes_downloaded": n_bytes,
            "download_truncated": truncated,
            "row_count_in_download": row_count,
            "column_count": len(columns_meta),
            "columns": columns_meta,
        }
    except duckdb.Error as e:
        return {"error": f"DuckDB error: {e}"}
    except httpx.HTTPError as e:
        return {"error": f"Network error downloading resource: {e}"}
    finally:
        if path is not None:
            _safe_unlink(path)


def _column_stats(
    con: duckdb.DuckDBPyConnection,
    col_name: str,
    col_type: str,
    top_n: int,
) -> dict[str, Any]:
    quoted = '"' + col_name.replace('"', '""') + '"'
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

    # Top values: useful for low-cardinality columns regardless of type.
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
    """Auto-generated profile of a resource.

    Returns row count, per-column type/nulls/distinct/min/max/mean/top-values.
    Designed to give the LLM enough context to decide which aggregations or
    filters to apply next, without sending raw rows.
    """
    kind = classify_format(fmt)
    if kind is None:
        return {"error": f"Format '{fmt}' not supported"}

    top_n = min(max(int(max_categorical_top_n), 1), SUMMARIZE_MAX_TOP_N)

    path: Path | None = None
    try:
        path, n_bytes, truncated = await _fetch_to_temp(url, kind)
        con = _new_con()
        _table_from_file(con, path, kind)

        described = con.execute("DESCRIBE data").fetchall()
        columns_meta = [{"name": row[0], "type": row[1]} for row in described]
        row_count = con.execute("SELECT COUNT(*) FROM data").fetchone()[0]

        column_stats = [
            _column_stats(con, c["name"], c["type"], top_n) for c in columns_meta
        ]

        return {
            "source_url": url,
            "format": kind,
            "bytes_downloaded": n_bytes,
            "download_truncated": truncated,
            "row_count_in_download": row_count,
            "column_count": len(columns_meta),
            "columns": column_stats,
        }
    except duckdb.Error as e:
        return {"error": f"DuckDB error: {e}"}
    except httpx.HTTPError as e:
        return {"error": f"Network error downloading resource: {e}"}
    finally:
        if path is not None:
            _safe_unlink(path)

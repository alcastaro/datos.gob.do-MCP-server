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
import csv
import functools
import hashlib
import logging
import os
import re
import shutil
import tempfile
import unicodedata
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal, TypeVar

import duckdb
import httpx

from . import archive, pagelink, reachability
from .cache import CacheLockError, LocalDiskCache, build_cache_key, get_cache
from .download import (
    ANALYTICS_MAX_BYTES,
    classify_format,
    direct_download_url,
    download_to_file,
    err_text,
    headers_for,
    looks_like_html,
    looks_like_text_table,
    sniff_container,
)
from .netguard import NetGuardError, guard_request_hook

logger = logging.getLogger(__name__)

SCHEMA_SAMPLE_ROWS = 1000
# What `get_resource_schema` returns when the caller does not ask for more.
# It used to default to the 1000-value ceiling, which made the tool the server
# itself recommends calling *first* also the most expensive thing it can do:
# measured against the real catalog, a single reply reached 352 KB — roughly
# 88k tokens of an assistant's context spent on recognising column names. Six
# distinct values identify a column; enumerating a category is a deliberate
# request, not a default.
SCHEMA_SAMPLE_DEFAULT = 6
SUMMARIZE_MAX_TOP_N = 50
FILTER_MAX_LIMIT = 1000
AGGREGATE_MAX_LIMIT = 1000

# Identifier guard. The real protection is that every identifier is emitted
# inside double quotes with embedded quotes doubled; this allowlist is the
# second line of defence, plus a denylist of SQL-comment sequences and
# statement terminators.
#
# The character set is wider than "word chars + dot + space" because real
# Dominican government spreadsheets use punctuation in their headers. A
# catalog sweep (2026-08-07) turned up, among others:
#     "Sueldo Bruto (RD$)"   "% Abastecimiento de la Demanda"
#     "RANGO DE EDAD 60 - 70"   "FECHA DE REGISTRO / ADQUISICIÓN"
#     "ALIMÉNTATE-COMER ES PRIMERO (PCP)"
# The old class rejected all of them, so every tool call against those files
# failed — a false positive that blocked legitimate public data.
#
# \A…\Z (not ^…$): in Python, $ also matches just before a trailing newline, so
# `^[...]+$` would accept an identifier like "col\n". \Z anchors the true end.
# Embedded newlines/tabs are normalized to spaces before this runs (headers
# spanning two spreadsheet lines are common), so they never reach the class.
_IDENT_OK = re.compile(r"\A[\w .\-()%/,:#&+'°ºª¡¿?!@*\[\]À-ſ$]+\Z", re.UNICODE)
_IDENT_FORBIDDEN_SUBSTR = ("--", "/*", "*/", ";")
# Anything in the C0/C1 control ranges is rejected outright: no legitimate
# column name contains one, and they are the only characters that could do
# something surprising inside a quoted identifier.
_IDENT_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_IDENT_WHITESPACE = re.compile(r"\s+")

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

# POSIX prefixes compare exact-case: /Etc is legitimately a different
# directory from /etc on a case-sensitive filesystem.
_FORBIDDEN_DEST_POSIX = (
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

# Windows system directories, matched case-folded with slashes normalised,
# because that filesystem is case-insensitive and paths arrive in both
# spellings. Stored without a drive letter: the first version of this list
# hard-coded `c:`, and on a machine with Windows installed on another drive the
# protection did not exist at all.
_FORBIDDEN_DEST_WINDOWS = (
    "/windows",
    "/program files",
    "/program files (x86)",
    "/programdata",
)

# `C:/…` — a drive letter followed by a separator, which is what makes the
# Windows list applicable. A Linux directory literally named /windows is not a
# system path, so the list is never applied without a drive.
_DRIVE_PREFIX = re.compile(r"^[a-z]:(?=/)")


def _forbidden_posix(raw: str) -> bool:
    # Exact case: /Etc is legitimately a different directory from /etc.
    return raw.replace("\\", "/").startswith(_FORBIDDEN_DEST_POSIX)


def _forbidden_windows(raw: str) -> bool:
    """True if this spelling names a Windows system directory.

    Four spellings reached `C:\\Windows` past the first version of this check,
    found by testing on Windows rather than reasoning about it:

    * `\\\\?\\C:\\Windows\\…` and `//?/C:/Windows/…` — the extended-length
      prefix. Python writes through it, and `Path.resolve()` *keeps* it, so the
      "check the raw path and the resolved path" strategy that catches
      /etc → /private/etc on macOS does not help here: both candidates carry
      the prefix.
    * `\\\\localhost\\C$\\Windows\\…` and `\\\\127.0.0.1\\ADMIN$\\…` — the
      administrative shares, which reach the same directory over UNC.
    * `D:\\Windows\\…` — any drive that is not C.
    """
    lowered = raw.replace("\\", "/").lower()
    # Extended-length and device namespaces: \\?\ and \\.\ are prefixes, not
    # locations, so strip them before deciding anything about what follows.
    if lowered.startswith(("//?/", "//./")):
        lowered = lowered[4:]
    # UNC, including the \\?\UNC\server\share form. Refused wholesale rather
    # than by share name: an admin share is not the only way to reach a system
    # directory on another host, and this tool exports a CSV for a person to
    # read — a remote share is not a destination it needs to support. A Windows
    # user whose home really is a network share gets a clear refusal here
    # instead of a silent write to a machine they did not name.
    if lowered.startswith("//") or lowered.startswith("unc/"):
        return True
    if _DRIVE_PREFIX.match(lowered):
        return lowered[2:].startswith(_FORBIDDEN_DEST_WINDOWS)
    return False


def _is_unc_dest(raw: str) -> bool:
    """True if this names a network location rather than a local path.

    Separated from the system-path check so the refusal can say what it is. A
    Windows tester was told `\\\\servidor\\equipo\\salida.csv` was a "system
    path", which it is not: it is a network share, refused by a different
    decision for a different reason, and a message that misnames it sends the
    reader looking in the wrong place.
    """
    lowered = raw.replace("\\", "/").lower()
    if lowered.startswith(("//?/", "//./")):
        lowered = lowered[4:]
    return lowered.startswith("//") or lowered.startswith("unc/")


def _is_forbidden_dest(*candidates: str) -> bool:
    """True if any spelling of the destination sits under a system path."""
    return any(_forbidden_posix(raw) or _forbidden_windows(raw) for raw in candidates)


# Windows refuses paths over 260 characters unless the machine has long paths
# enabled, which is off by default. The OS message says the name is too long and
# stops there; a person who has just been handed a generated filename needs to be
# told which half of the problem is theirs.
_WINDOWS_LONG_PATH = 206  # ERROR_FILENAME_EXCED_RANGE


def _dest_open_error(e: OSError, dest_path: Path) -> dict[str, Any]:
    """Turn a failed open() into something the caller can act on."""
    result: dict[str, Any] = {"error": f"Cannot open destination for writing: {e}"}
    if getattr(e, "winerror", None) == _WINDOWS_LONG_PATH or "too long" in str(e).lower():
        result["hint"] = (
            f"The destination is {len(str(dest_path))} characters. Windows caps paths at 260 "
            "unless long paths are enabled: shorten the folder or file name, or set "
            "LongPathsEnabled=1 under "
            "HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem and restart."
        )
    return result


class AnalyticsError(RuntimeError):
    pass


class PageInsteadOfDataError(AnalyticsError):
    """The URL served a page. Carries whatever data files that page linked.

    The candidates travel on the exception because the whole point is that a
    caller who cannot be given the file can still be given the choice. An error
    string would drop them.
    """

    def __init__(self, message: str, candidates: list[dict] | None = None) -> None:
        super().__init__(message)
        self.candidates = candidates or []


# Every failure a tool can plausibly hit while talking to a government portal or
# to DuckDB. Anything in this tuple becomes an `{"error": ...}` result the model
# can read and act on; anything outside it is a bug in this server and should
# surface as a real traceback rather than be silently swallowed.
#
# CacheLockError belongs here for a reason worth recording: the cache lock used
# to fail as `OSError: [Errno 36]`, which this tuple already caught, so a
# contended index came back as a readable `{"error": ...}`. Giving that failure a
# clearer type moved it *out* of the tuple and turned a handled result into a
# traceback — a message improvement that made the behaviour worse. Only on
# Windows, because POSIX `flock` queues and never times out, and therefore only
# on the platform the clearer message was written for.
_ENVELOPE_ERRORS = (
    httpx.HTTPError,
    AnalyticsError,
    duckdb.Error,
    NetGuardError,
    OSError,
    CacheLockError,
)


_T = TypeVar("_T", bound=Callable[..., Awaitable[dict[str, Any]]])


def _tool_envelope(fn: _T) -> _T:
    """Return handled failures as `{"error": ...}` instead of raising.

    Individual tools also catch around `ensure_cached` to attach context like
    "Could not load resource". This decorator is the backstop for everything
    *after* that point: a dead-DNS host, an exotic column name, a malformed
    file that only blows up at query time. Before it existed, those escaped as
    unhandled exceptions and the MCP client saw a protocol error with a
    traceback instead of a sentence the assistant could relay to the user.
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return await fn(*args, **kwargs)
        except _ENVELOPE_ERRORS as e:
            logger.warning("%s failed: %s: %s", fn.__name__, type(e).__name__, e)
            return {"error": err_text(e)}

    return wrapper  # type: ignore[return-value]


def _match_column(name: str, available: list[str]) -> str | None:
    """Find the real column a caller meant, or None.

    Exact first, then case- and whitespace-insensitive, because a model reading
    a schema reply routinely writes `Año` where the file says `AÑO`.
    """
    if name in available:
        return name
    wanted = _normalize_header(name).casefold()
    for actual in available:
        if _normalize_header(actual).casefold() == wanted:
            return actual
    return None


def _quote_ident(name: str, available: list[str] | None = None) -> str:
    """Quote a column identifier safely.

    When `available` is given — the columns the open view actually has — the
    name is resolved against it and the *matched* name is escaped. Membership
    in a list DuckDB itself produced is a stronger guarantee than any character
    allowlist, and it lets a file with an odd header stay queryable instead of
    being refused wholesale. It also turns a typo into "Column not found, here
    are the real ones" rather than a SQL error.

    Without `available` the strict path below applies. That is what aggregation
    aliases use: they are invented by the model and have nothing to match.

    Layers of defence, outermost first:
        1. Control characters rejected outright.
        2. Allowlist regex on the remaining chars (see `_IDENT_OK`).
        3. Denylist of forbidden substrings (--, /*, */, ;) so a name that
           somehow passes the regex still can't smuggle SQL syntax.
        4. The identifier is emitted double-quoted with embedded quotes
           doubled — the actual guarantee; 1-3 are belt and braces.

    Anything that fails a check raises AnalyticsError. Note the caller passes
    the *original* column name from the file; this function only validates and
    quotes it, so callers that need the DuckDB-visible name unchanged still get
    it verbatim inside the quotes.
    """
    if available is not None:
        hit = _match_column(name, available)
        if hit is not None:
            return _raw_quote(hit)
        shown = ", ".join(repr(c) for c in available[:15])
        raise AnalyticsError(f"Column not found: {name!r}. Columns are: {shown}")
    if not name or _IDENT_CONTROL.search(name):
        raise AnalyticsError(f"Invalid column identifier: {name!r}")
    if not _IDENT_OK.match(name):
        raise AnalyticsError(f"Invalid column identifier: {name!r}")
    for bad in _IDENT_FORBIDDEN_SUBSTR:
        if bad in name:
            raise AnalyticsError(f"Forbidden substring in identifier: {name!r}")
    return '"' + name.replace('"', '""') + '"'


def _normalize_header(name: str) -> str:
    """Clean a column name read from a file so it stays usable.

    Two problems, both from real files:

    Spreadsheet headers wrap across lines ("Presupuesto\\nAprobado") or carry
    trailing tabs, and DuckDB keeps those characters in the column name.

    Worse, they carry *invisible* characters — the catalog audit found
    "Cod.Capí\\xadtulo", where \\xad is a soft hyphen. Nothing about that name
    looks wrong to a person reading it, so a rejection was impossible to act
    on: the user is told to fix a column name that already looks correct.
    Unicode format characters (category Cf, which includes soft hyphen, zero
    width space and the bidi marks) carry no meaning in a field name and are
    dropped rather than rejected.
    """
    cleaned = "".join(c for c in name if unicodedata.category(c) != "Cf")
    return _IDENT_WHITESPACE.sub(" ", cleaned).strip()


def _column_names(value: list[Any] | None) -> list[str] | None:
    """Accept a column list written either as strings or as {"col": ...} objects.

    Three of the four list parameters on these tools (`filters`, `order_by`,
    `having`) take objects keyed by `col`, and one (`group_by` / `columns`)
    takes bare strings. Models generalise from the majority and write
    `group_by: [{"col": "Año"}]` — in the directed battery that single shape
    error accounted for 190 of 487 calls, and every one of them failed at
    schema validation before the tool ran, so the caller got a Pydantic
    traceback instead of an answer. Accepting both spellings is cheaper than
    being right about which one we prefer.
    """
    if value is None:
        return None
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            name = item.get("col") or item.get("column") or item.get("name")
            if not isinstance(name, str):
                raise AnalyticsError(
                    f"Column entry must be a name or {{'col': name}}, got {item!r}"
                )
            out.append(name)
        else:
            raise AnalyticsError(f"Column entry must be a string, got {item!r}")
    return out


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


# Operator-set resource limits (env). Values are validated before reaching SQL.
_MEM_LIMIT_RE = re.compile(r"\A\d+(\.\d+)?\s*(KB|MB|GB|TB|KiB|MiB|GiB|TiB)?\Z", re.IGNORECASE)

# DuckDB refuses a single JSON value over 16 MB by default, and these portals
# serve the whole table as one object — `{"data": [ … 69,097 records … ]}`. That
# default was rejecting seven resources for a reason that has nothing to do with
# the file: a JSON object cannot be larger than the download that carried it, so
# the only ceiling worth having is the download cap. Set just above it so this
# limit never fires before the one the operator can see.
JSON_MAX_OBJECT_BYTES = ANALYTICS_MAX_BYTES + 8 * 1024 * 1024


def _extract_single_member(archive_path: Path, member: str) -> Path:
    """Unpack the one data file inside a ZIP archive, next to the archive.

    Only ever called with a member name that `sniff_container` already found to be
    the single data file in the container, so there is no choice to get wrong. The
    name is not used to build the output path — a zip entry can be called
    `../../etc/passwd`, and a reader that trusts it writes wherever the archive
    says. Only the extension travels.
    """
    suffix = member.rsplit(".", 1)[-1].lower()
    target = archive_path.with_name(archive_path.name + ".unpacked." + suffix)
    with zipfile.ZipFile(archive_path) as z, z.open(member) as src, target.open("wb") as out:
        shutil.copyfileobj(src, out, length=1024 * 1024)
    return target


def _json_unwrap_sql(con: duckdb.DuckDBPyConnection, src: str, dst: str) -> str | None:
    """SQL that reads the records inside a single-key JSON envelope, or None.

    `{"data": [ {...}, {...} ]}` is one JSON value, so DuckDB reads it as one row
    with one LIST column. The call *succeeds* — which is the dangerous part, and
    why `ensure_cached` already warns about a large file that parses into one
    column. Measured on the national payroll (MAP, 21 MB): one row and one column
    named `data` becomes **69,097 rows** with `Nombre`, `Departamento`, `Función`,
    `Sueldo_Bruto` once unnested.

    Deliberately narrow. It fires only on exactly one top-level column whose type
    is a list of structs — the shape that cannot be anything but an envelope. A
    JSON file that is genuinely one record, or one that has several top-level
    keys, is left alone: guessing which key holds "the data" is how a reader
    starts inventing datasets.
    """
    try:
        described = con.execute(
            f"DESCRIBE SELECT * FROM read_json_auto('{src}', "
            f"maximum_object_size={JSON_MAX_OBJECT_BYTES})"
        ).fetchall()
    except duckdb.Error:
        return None
    if len(described) != 1:
        return None
    name, col_type = str(described[0][0]), str(described[0][1]).upper()
    if not (col_type.startswith("STRUCT(") and col_type.endswith(")[]")):
        return None
    logger.info("JSON envelope: unnesting the single list column %r", name)
    quoted = name.replace('"', '""')
    return (
        f'COPY (SELECT unnest("{quoted}", recursive := true) '
        f"FROM read_json_auto('{src}', maximum_object_size={JSON_MAX_OBJECT_BYTES})) "
        f"TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )


def _new_con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    for ext in ("httpfs", "excel"):
        try:
            con.execute(f"LOAD {ext}")
        except duckdb.Error:
            pass
    # Resource ceilings — one tenant/query must not be able to OOM the process.
    mem = os.environ.get("DATOSGOBDO_DUCKDB_MEMORY", "2GB").strip()
    if not _MEM_LIMIT_RE.match(mem):
        logger.warning("Ignoring invalid DATOSGOBDO_DUCKDB_MEMORY=%r", mem)
        mem = "2GB"
    threads = os.environ.get("DATOSGOBDO_DUCKDB_THREADS", "4").strip()
    if not threads.isdigit() or int(threads) < 1:
        logger.warning("Ignoring invalid DATOSGOBDO_DUCKDB_THREADS=%r", threads)
        threads = "4"
    try:
        con.execute(f"SET memory_limit='{mem}'")
        con.execute(f"SET threads={int(threads)}")
    except duckdb.Error as e:  # pragma: no cover — defensive, never seen in practice
        logger.warning("Could not apply DuckDB resource limits: %s", e)
    return con


def _execute_guarded(con: duckdb.DuckDBPyConnection, sql: str) -> duckdb.DuckDBPyConnection:
    """Execute with a wall-clock timeout (DATOSGOBDO_QUERY_TIMEOUT seconds).

    0 / unset = no timeout (local default). On expiry con.interrupt() makes
    DuckDB abort the query with an error instead of running forever — the
    backstop for free-form SQL in hosted deployments.
    """
    import threading

    raw = os.environ.get("DATOSGOBDO_QUERY_TIMEOUT", "0").strip() or "0"
    try:
        timeout = float(raw)
    except ValueError:
        logger.warning("Ignoring invalid DATOSGOBDO_QUERY_TIMEOUT=%r", raw)
        timeout = 0.0
    if timeout <= 0:
        return con.execute(sql)
    timer = threading.Timer(timeout, con.interrupt)
    timer.start()
    try:
        return con.execute(sql)
    finally:
        timer.cancel()


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


_REPAIR_DELIMS = (";", ",", "\t", "|")
_REPAIR_SAMPLE_LINES = 50


def _fields(line: str, delim: str) -> list[str]:
    try:
        return next(csv.reader([line], delimiter=delim))
    except (csv.Error, StopIteration):
        return [line]


def _strip_padding(line: str, sniffed: str) -> str | None:
    """Drop the empty columns Excel pads a line with; return the real record.

    Returns None when the line does not have the shape this repair targets —
    that is, when something other than padding survives. A field containing the
    sniffed delimiter (`Vejez, Discapacidad y ...`) is rejoined rather than
    treated as a second column, since that comma is exactly why the sniffer
    guessed wrong in the first place.
    """
    fields = _fields(line, sniffed)
    while fields and not fields[-1].strip():
        fields.pop()
    if not fields:
        return None
    return sniffed.join(fields)


def _repair_csv_text(path: Path) -> Path:
    """Rewrite a CSV whose real structure the sniffer cannot see. Else no-op.

    Two shapes from this catalog defeat DuckDB's auto-detection, and both look
    the same from the outside: the whole record ends up inside one field.

    1. Excel exports a semicolon file with five empty trailing comma columns
       (`Seguro;Cantidad;Mes;Año,,,,,`). Commas are the most consistent
       separator in the file, so the sniffer picks them and the real record
       becomes column one.
    2. A file where every line was quoted as a single field
       (`"ISBN,""EDITOR"",..."`). Parsing it is correct, and yields exactly one
       column whose values are themselves CSV lines.

    The repair is only attempted when the header, read under the delimiter the
    sniffer would choose, collapses to one usable field while another delimiter
    splits it into three or more — so a legitimately single-column file is left
    alone.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            head = [next(f) for _ in range(_REPAIR_SAMPLE_LINES)]
    except StopIteration:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
            head = f.readlines()
    if not head:
        return path
    header = head[0].rstrip("\r\n")

    # What the sniffer sees: the delimiter with the most fields on the header.
    sniffed = max(_REPAIR_DELIMS, key=lambda d: len(_fields(header, d)))
    record = _strip_padding(header, sniffed)
    if record is None:
        return path

    best = max(_REPAIR_DELIMS, key=lambda d: len(_fields(record, d)))
    width = len(_fields(record, best))
    # Only rewrite when the repair actually changes the table's shape. A file
    # the sniffer already reads correctly reaches this point too, and rewriting
    # it would be a pointless copy.
    if width < 3 or width == len(_fields(header, sniffed)):
        return path

    # Confirm on the body: a one-off odd header is not worth rewriting a file.
    body = [x.rstrip("\r\n") for x in head[1:] if x.strip()]
    if body:
        agree = 0
        for line in body:
            rec = _strip_padding(line, sniffed)
            if rec is not None and len(_fields(rec, best)) == width:
                agree += 1
        if agree < len(body) * 0.8:
            return path

    logger.info("repairing CSV structure: sniffed %r, real %r", sniffed, best)
    out = path.with_suffix(path.suffix + ".fixed")
    with (
        path.open("r", encoding="utf-8", errors="replace", newline="") as src,
        out.open("w", encoding="utf-8", newline="") as dst,
    ):
        writer = csv.writer(dst, delimiter=best, lineterminator="\n")
        for line in src:
            line = line.rstrip("\r\n")
            if not line:
                continue
            rec = _strip_padding(line, sniffed)
            if rec is None:
                continue
            writer.writerow(_fields(rec, best))
    return out


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _computation(con: duckdb.DuckDBPyConnection, sql: str) -> dict[str, Any]:
    """What ran, and over how many rows, so the figure can be re-derived.

    A number that arrives in structuredContent was computed; a number the
    model retypes into a table may not survive the trip — measured on a real
    session, a top-10 entry came back 300 million above what the assistant's
    own script had produced. Together with the source digest this makes every
    reply checkable by a third party: same bytes, same SQL, same figure. The
    SQL names only the `data` view, never a server path.
    """
    row = con.execute("SELECT count(*) FROM data").fetchone()
    return {"sql": sql, "rows_scanned": int(row[0]) if row else 0}


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


def _load_error(e: BaseException, url: str | None = None) -> dict[str, Any]:
    """Turn a failure to load a resource into a reply the caller can act on.

    When the URL served a page, whatever data files that page linked travel
    back with the error. A caller told only "the URL returned HTML" has nowhere
    to go; one handed three candidate files can pick the right one and ask
    again — and it holds the user's actual question, which this server does not.

    For a network refusal the status code alone is not the story. A 403 with
    ``cf-mitigated: challenge`` means a browser would succeed where no client
    can, and a caller that is not told the difference has no way to choose
    between reporting the resource as blocked and asking the user for the file.
    Only the response headers are read: the body is streamed, so touching it
    here would need an await this function does not have.
    """
    out: dict[str, Any] = {"error": f"Could not load resource: {err_text(e)}"}
    linked = getattr(e, "candidates", None)
    if linked:
        out["linked_files"] = linked
        out["next_step"] = (
            "This URL is a page. Call the same tool again with the `url` of "
            "whichever linked file answers your question."
        )
        return out

    response = getattr(e, "response", None)
    if response is not None or isinstance(e, httpx.HTTPError):
        status = getattr(response, "status_code", None)
        headers = dict(getattr(response, "headers", {}) or {})
        kind = reachability.classify(status, headers)
        found = archive.lookup(url) if url else None
        out.update(reachability.explain(kind, found[1] if found else None))
    return out


async def _head_metadata(url: str) -> tuple[str | None, str | None]:
    """Fetch ETag + Last-Modified via HEAD. Used as cache version tag.

    The SSRF guard belongs here as much as on the download. P2a installed it
    "on downloads", and a HEAD is not a download — so this request went out
    unguarded, ahead of the guarded one, to whatever host the caller named. A
    HEAD against 127.0.0.1 reached a local service and returned its ETag while
    the download that followed was correctly refused, which makes the server a
    blind network-probe primitive: existence and liveness of internal addresses
    are observable through the timing and through the version tag reaching the
    cache key. It also let `strict` mode be bypassed, since the hostname was
    never checked on this path.
    """
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=15.0,
            headers=headers_for(direct_download_url(url)),
            event_hooks={"request": [guard_request_hook]},
        ) as client:
            r = await client.head(direct_download_url(url))
            return r.headers.get("etag"), r.headers.get("last-modified")
    except httpx.HTTPError:
        return None, None


_ODS_NS_TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
_ODS_NS_TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_ODS_TABLE = f"{{{_ODS_NS_TABLE}}}table"
_ODS_ROW = f"{{{_ODS_NS_TABLE}}}table-row"
_ODS_CELL = f"{{{_ODS_NS_TABLE}}}table-cell"
_ODS_P = f"{{{_ODS_NS_TEXT}}}p"
_ODS_REPEAT_COLS = f"{{{_ODS_NS_TABLE}}}number-columns-repeated"
_ODS_REPEAT_ROWS = f"{{{_ODS_NS_TABLE}}}number-rows-repeated"
# ODS pads sheets to the spreadsheet grid with repeat counts in the millions.
# Those repeats are always empty, so they are dropped rather than expanded;
# this cap only bounds repeats of *non-empty* content.
_ODS_MAX_REPEAT = 4096


def _ods_to_csv(src: Path) -> Path:
    """Convert the first sheet of an ODS file to CSV. Returns sibling .csv path.

    DuckDB has no native ODS reader as of 1.x, so the file is transcoded once on
    the cold path and the CSV pipeline takes it from there.

    This parses `content.xml` as a stream. The obvious implementation —
    `odf.opendocument.load()` — builds the whole document as a Python object
    tree first, which a 2026-08-07 catalog sweep measured at roughly **580x the
    file size in RAM**: a 0.7 MB spreadsheet peaked at 0.41 GB, and since the
    download cap is 100 MB the worst case was tens of gigabytes. It also pinned
    a core for minutes with no way to interrupt it. ODS is a third of this
    catalog, so that was not an edge case. Streaming keeps memory proportional
    to one row.
    """
    import csv as _csv
    import zipfile
    from xml.etree import ElementTree as ET

    csv_path = src.with_suffix(src.suffix + ".csv")
    try:
        archive = zipfile.ZipFile(src)
    except zipfile.BadZipFile as e:
        raise AnalyticsError(f"Not a readable ODS file: {e}") from e

    with archive as z:
        try:
            content = z.open("content.xml")
        except KeyError as e:
            raise AnalyticsError("ODS file has no content.xml") from e

        with content, csv_path.open("w", encoding="utf-8", newline="") as f:
            writer = _csv.writer(f)
            table_elem = None  # the first sheet; cleared as rows are consumed
            depth = 0
            saw_table = False

            for event, elem in ET.iterparse(content, events=("start", "end")):
                if event == "start":
                    if elem.tag == _ODS_TABLE:
                        depth += 1
                        if depth == 1:
                            table_elem, saw_table = elem, True
                    continue

                if elem.tag == _ODS_TABLE:
                    depth -= 1
                    if depth == 0:
                        break  # first sheet only, as before
                    continue

                if elem.tag != _ODS_ROW or depth != 1:
                    continue

                row: list[str] = []
                blanks = 0  # empty cells held back so trailing padding is dropped
                for cell in elem.iterfind(_ODS_CELL):
                    reps = _ods_repeat(cell.get(_ODS_REPEAT_COLS))
                    text = "".join("".join(p.itertext()) for p in cell.iterfind(_ODS_P))
                    if text:
                        row.extend([""] * blanks)
                        blanks = 0
                        row.extend([text] * reps)
                    else:
                        blanks += reps

                if row:
                    for _ in range(_ods_repeat(elem.get(_ODS_REPEAT_ROWS))):
                        writer.writerow(row)
                elif blanks:
                    writer.writerow([])  # a genuinely blank row inside the data

                # Drop what has been consumed; without this the parent keeps
                # every row alive and the streaming gains nothing.
                if table_elem is not None:
                    table_elem.clear()

            if not saw_table:
                raise AnalyticsError("ODS file has no tables")

    return csv_path


def _ods_repeat(raw: str | None) -> int:
    """Parse an ODS repeat attribute, clamped to a sane range."""
    try:
        return max(1, min(int(raw), _ODS_MAX_REPEAT)) if raw else 1
    except (TypeError, ValueError):
        return 1


def warm_parquet_for(url: str) -> Path | None:
    """Return the cached Parquet for this URL, or None — never touches the network.

    Lets a caller ask "have we already read this?" without paying for a HEAD
    request, which is the difference between reusing a copy and asking the
    portal about it again.
    """
    try:
        hit = get_cache().get_by_url(url)
    except OSError:  # pragma: no cover — unreadable cache dir
        return None
    return hit[0] if hit else None


async def _ensure_cached_live(
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
            # Whatever had to be declared about this copy on the cold path has
            # to be declared here too. The warm path serves every call but the
            # first, so provenance that lives only in the download branch is
            # provenance the caller hears once and never again.
            return cached_path, {"cache": "hit", "key": key, **cache.provenance(key)}

    # Cold path (or forced refresh): HEAD to compute version key.
    etag, last_mod = await _head_metadata(url)
    key = build_cache_key(url, etag=etag, last_modified=last_mod)
    cached = cache.get(key)
    if cached is not None:
        logger.info("cache HIT key=%s size=%d", key, cached.stat().st_size)
        return cached, {"cache": "hit", "key": key, **cache.provenance(key)}

    logger.info("cache MISS key=%s — downloading %s", key, url)
    fd, tmp_path_str = tempfile.mkstemp(prefix="dgd-dl-", suffix="." + fmt)
    import os

    os.close(fd)
    raw = Path(tmp_path_str)
    raw_ods: Path | None = None  # declared here so finally block can always reference it
    raw_archive: Path | None = None  # the container, when the bytes were a ZIP archive
    resolved_from: dict[str, str] | None = None
    format_corrected: dict[str, str] | None = None
    try:
        bytes_written, truncated = await download_to_file(url, raw, max_bytes=ANALYTICS_MAX_BYTES)
        if bytes_written == 0:
            raise AnalyticsError("Downloaded zero bytes")

        with raw.open("rb") as fh:
            head = fh.read(2048)
        if looks_like_html(head):
            # A page is not necessarily a dead end. Often it is the download
            # page and the file is one link away, so try to resolve it before
            # giving up — and when the links cannot be told apart, hand them to
            # the caller instead of refusing. See pagelink.py.
            page = raw.read_bytes().decode("utf-8", errors="replace")
            target, found = pagelink.resolve(page, url, fmt)
            if target is None:
                raise PageInsteadOfDataError(pagelink.describe(page), found)
            logger.info("page resolved to a linked file: %s -> %s", url, target)
            bytes_written, truncated = await download_to_file(
                target, raw, max_bytes=ANALYTICS_MAX_BYTES
            )
            if bytes_written == 0:
                raise AnalyticsError("The linked file downloaded zero bytes")
            with raw.open("rb") as fh:
                # One hop only. A page that links another page is not a file,
                # and following the chain would eventually follow the site's
                # navigation into something that merely parses.
                if looks_like_html(fh.read(2048)):
                    raise PageInsteadOfDataError(
                        "The URL returned a page, and the file it links is itself a "
                        "page. Only one hop is followed.",
                        found,
                    )
            resolved_from = {"page": url, "followed": target}

        # What the bytes are, whatever the catalog says they are. The first
        # version of this check went one way only — a `PK` signature under a
        # `.csv` declaration became XLSX — and both halves of that were wrong.
        # `PK` is also how every ODS starts, so an ODS registered as CSV went to
        # `read_xlsx` and came back as "No [Content_Types].xml found in xlsx
        # file": a sentence about our internals, about a file whose own name ended
        # in `.ods`. And the reverse case was not handled at all — a CSV
        # registered as ODS was refused for not starting like a spreadsheet, which
        # describes the declaration and not the file. 24 resources in the sibling
        # corpus, and trusting the bytes while declaring the correction is the
        # same bargain as numeric coercion: do the useful thing, and say so.
        sniffed, refusal = sniff_container(raw)
        if sniffed is None and fmt in ("xlsx", "xls", "xlsm", "ods"):
            with raw.open("rb") as fh:
                sniffed = looks_like_text_table(fh.read(4096))
        zip_member: str | None = None
        if sniffed is not None and sniffed != fmt:
            if sniffed.startswith("zip:"):
                zip_member = sniffed[4:]
                sniffed = classify_format(zip_member.rsplit(".", 1)[-1]) or "xlsx"
            logger.info("declared %s but the bytes are %s: %s", fmt, sniffed, url)
            format_corrected = {
                "declared": fmt,
                "actual": sniffed,
                "detected_from": (
                    f"the single member {zip_member!r} inside a ZIP archive"
                    if zip_member
                    else "the file's own signature"
                ),
                "note": (
                    f"The catalog declares this resource as {fmt.upper()}, and the bytes "
                    f"are {sniffed.upper()}. It was read as {sniffed.upper()}. The wrong "
                    "format in the catalog is a finding about the publisher, not about "
                    "the data."
                ),
            }
            fmt = sniffed
        elif refusal is not None:
            # Recognisable and unsupported, or a broken container: say which.
            raise AnalyticsError(refusal)
        elif fmt in ("xlsx", "xlsm", "ods") and sniffed is None:
            # Nothing identifiable. Portals answer a gated or moved download with a
            # page carrying the original filename and HTTP 200, and that page is not
            # always shaped like the HTML the guard above recognises. This turns
            # "IO Error: Failed to open zip for reading" into a sentence about the
            # file.
            raise AnalyticsError(
                f"The file is not a valid {fmt.upper()} — it does not start "
                "like a spreadsheet. The portal most likely served a web "
                "page or an error document under a spreadsheet filename."
            )

        # The digest of the bytes exactly as they will be parsed — after
        # following a page to its file, before any transcoding — so any figure
        # computed from this entry can be re-checked against an independent
        # capture. A truncated download carries no digest: hashing part of a
        # file and presenting it as the file's digest would be precisely the
        # false confidence this field exists to kill.
        source_sha256: str | None = None
        if not truncated:
            source_sha256 = await asyncio.to_thread(_sha256_file, raw)

        # Unpacking happens after the digest on purpose: the digest has to name
        # what the portal served, so an independent capture can be compared
        # against it. Whoever re-downloads this URL gets the archive, not what was
        # inside it.
        if zip_member is not None:
            raw_archive = raw  # the finally block has to remove both files
            raw = await asyncio.to_thread(_extract_single_member, raw, zip_member)

        effective_fmt = fmt
        if fmt == "ods":
            raw_ods = raw
            # Transcoding and encoding detection are CPU-bound and synchronous.
            # Left on the event loop they freeze the whole server for the
            # duration — and, worse, block the timers that are supposed to cut
            # a long operation short. A thread keeps the loop answering.
            raw_csv = await asyncio.to_thread(_ods_to_csv, raw)
            raw = raw_csv
            effective_fmt = "csv"

        if effective_fmt in ("csv", "tsv"):
            usable = await asyncio.to_thread(_normalize_csv_encoding, raw)
            usable = await asyncio.to_thread(_repair_csv_text, usable)
        else:
            usable = raw
        parquet_path = cache.put_path(key)

        src = str(usable).replace("'", "''")
        dst = str(parquet_path).replace("'", "''")
        fallback_sql: str | None = None
        if effective_fmt in ("csv", "tsv"):
            # `null_padding` is not a nicety, it is what keeps a ragged line from
            # destroying the whole file. One row with a field count the sniffer
            # does not expect — line 1,423 of the DGP passport series has one —
            # and `IGNORE_ERRORS` makes DuckDB fall back to a *single column*
            # named after the entire header row: `Provincia,Cantidad_Pasaportes_
            # Emitidos,Mes,Ano`. The call succeeds, 1,434 rows come back, and
            # every one of them is a single string. That is the failure this
            # server exists to prevent — a wrong answer, delivered confidently.
            # With padding the same file reads as its four real columns and the
            # short row is filled with NULLs, which is a fact about the row rather
            # than a verdict on the file.
            copy_sql = (
                f"COPY (SELECT * FROM read_csv_auto('{src}', "
                f"SAMPLE_SIZE=-1, IGNORE_ERRORS=TRUE, null_padding=true)) "
                f"TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
            # A header wrapped across two lines inside a quoted field is legal
            # CSV, and a real price series in this catalog has one — but the
            # sniffer refuses the whole file over it. Relaxing strict mode reads
            # it correctly (153 columns) at the cost of a laxer dialect guess,
            # which is only paid by files that already failed.
            fallback_sql = (
                f"COPY (SELECT * FROM read_csv_auto('{src}', "
                f"SAMPLE_SIZE=-1, IGNORE_ERRORS=TRUE, null_padding=true, strict_mode=false)) "
                f"TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
        elif effective_fmt in ("xlsx", "xls", "xlsm"):
            copy_sql = (
                f"COPY (SELECT * FROM read_xlsx('{src}')) "
                f"TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
            # Government workbooks routinely put a numeric column's total,
            # a footnote or a "#REF!" thousands of rows below the data, after
            # DuckDB has already inferred DOUBLE from the top of the column.
            # Rather than lose the file, retry with every column as text: worse
            # types beat no data, and the sweep showed this recovers ~6% of the
            # catalog that otherwise failed outright.
            fallback_sql = (
                f"COPY (SELECT * FROM read_xlsx('{src}', all_varchar=true)) "
                f"TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
        elif effective_fmt == "json":
            copy_sql = (
                f"COPY (SELECT * FROM read_json_auto('{src}', "
                f"maximum_object_size={JSON_MAX_OBJECT_BYTES})) "
                f"TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
            # JSON Lines served under a `.json` name. One file per format is the
            # rule in this catalog, so the cheap second attempt is worth more than
            # a sniffer.
            fallback_sql = (
                f"COPY (SELECT * FROM read_json_auto('{src}', format='newline_delimited', "
                f"maximum_object_size={JSON_MAX_OBJECT_BYTES})) "
                f"TO '{dst}' (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
        else:
            raise AnalyticsError(f"Format '{fmt}' not supported")

        def _convert() -> None:
            con = _new_con()
            try:
                first = copy_sql
                if effective_fmt == "json":
                    first = _json_unwrap_sql(con, src, dst) or copy_sql
                try:
                    _execute_guarded(con, first)
                except duckdb.Error:
                    if fallback_sql is None:
                        raise
                    logger.info("typed read failed, retrying as all-text: %s", url)
                    _execute_guarded(con, fallback_sql)
            finally:
                con.close()

        # Same reasoning as the transcode above: parsing a 100 MB spreadsheet is
        # seconds to minutes of blocking work, and _execute_guarded's interrupt
        # timer can only fire if something else is free to run.
        try:
            await asyncio.to_thread(_convert)
        except duckdb.Error as e:
            if not truncated:
                raise
            # "Malformed JSON … unexpected end of data" is true and blames the
            # wrong party: we are the ones who stopped reading. Five resources in
            # this catalog are single JSON objects of ~115 MB against a 100 MB
            # cap, and a publisher told their file is malformed will go looking
            # for a defect that is not there. A container format cannot be parsed
            # from a prefix, so this is a limit to state, not a failure to fix.
            cap_mb = ANALYTICS_MAX_BYTES // (1024 * 1024)
            raise AnalyticsError(
                f"The file is larger than the {cap_mb} MB this server downloads, so it was "
                f"cut short — and {effective_fmt.upper()} cannot be read from a partial "
                "file. The data is not malformed; this server declined to fetch all of it. "
                "Ask the publisher for a split or paginated version, or fetch the file "
                "directly outside this server."
            ) from e

        # A megabyte of data that parses into one column and a handful of rows
        # did not parse. It happens with JSON arrays DuckDB folds into a single
        # value, and with spreadsheets whose real table sits behind a cover
        # sheet. The call *succeeds*, which is the dangerous part: the assistant
        # reports one cell as though it were the dataset. Measured over 1,926
        # readable resources it hits 12 — six of them JSON.
        #
        # It warns rather than refuses. A one-column file is legal, and blocking
        # it would trade a rare wrong answer for a certain lost one; the caller
        # gets the data and a reason to distrust it.
        warning = None
        if bytes_written > _SUSPICIOUS_SOURCE_BYTES:
            shape_con = _new_con()
            try:
                cols = len(
                    shape_con.execute(f"DESCRIBE SELECT * FROM read_parquet('{dst}')").fetchall()
                )
                fila = shape_con.execute(f"SELECT count(*) FROM read_parquet('{dst}')").fetchone()
                n = fila[0] if fila else 0
            except duckdb.Error:  # pragma: no cover - the file just converted
                cols, n = 2, 2
            finally:
                shape_con.close()
            if cols <= 1 and n <= _SUSPICIOUS_ROWS:
                warning = (
                    f"This file is {bytes_written:,} bytes but parsed into {n} row(s) "
                    f"and {cols} column(s). That is almost certainly a parse failure, "
                    "not the shape of the data — a JSON array read as one value, or a "
                    "spreadsheet whose table sits behind a cover sheet. Treat the "
                    "result as unreliable and inspect with download_resource_preview."
                )
                logger.warning("suspicious parse shape for %s: %d rows x %d cols", url, n, cols)

        # The caller asked for a URL and got data from another one, or a large
        # file parsed into a shape that cannot be right. Saying so is not
        # optional in an audit tool — and it is stored, not just returned,
        # because every later call reads this entry from the warm path.
        provenance: dict[str, Any] = {}
        if resolved_from:
            provenance["resolved_from"] = resolved_from
        if warning:
            provenance["parse_warning"] = warning
        if format_corrected:
            provenance["format_corrected"] = format_corrected
        if source_sha256:
            provenance["source_sha256"] = source_sha256
        cache.finalize(key, url=url, provenance=provenance)  # URL for warm-path lookups
        logger.info(
            "cache STORE key=%s parquet=%d source=%d",
            key,
            parquet_path.stat().st_size,
            bytes_written,
        )
        return parquet_path, {
            "cache": "miss",
            "key": key,
            **provenance,
            "source_bytes": bytes_written,
            "source_truncated": truncated,
            "parquet_bytes": parquet_path.stat().st_size,
        }
    finally:
        _safe_unlink(raw)
        if raw_ods is not None:
            _safe_unlink(raw_ods)
        if raw_archive is not None:
            _safe_unlink(raw_archive)


async def ensure_cached(
    url: str,
    fmt: str,
    cache: LocalDiskCache | None = None,
    force_refresh: bool = False,
) -> tuple[Path, dict[str, Any]]:
    """Get the resource, falling back to an archived copy if one is configured.

    The portal is always tried first. The archive exists because a resource that
    reads today may not read tomorrow — this catalog has 15 links already dead
    and 99 institutions whose sites grew rules that refuse programmatic access —
    and because a figure cited in a report should still be recomputable years
    later against the same digest.

    It is off unless `DATOSGOBDO_ARCHIVE_DIR` is set, and it is never silent:
    the reply says the answer came from an archive, when it was captured and why
    the origin was not used. An audit tool that quietly serves yesterday's copy
    as today's has stopped being one.
    """
    try:
        return await _ensure_cached_live(url, fmt, cache=cache, force_refresh=force_refresh)
    except (httpx.HTTPError, AnalyticsError, NetGuardError, duckdb.Error) as e:
        hit = archive.lookup(url)
        if hit is None:
            raise
        parquet, entry = hit
        logger.info("origin failed (%s) — serving the archived copy of %s", err_text(e), url)
        return parquet, {
            "cache": "archive",
            "provenance": archive.provenance(url, entry, err_text(e)),
        }


def _raw_quote(name: str) -> str:
    """Quote a name that came from DuckDB itself, not from the model.

    `_quote_ident` validates because its input is model- or user-supplied. The
    column names DuckDB reports for a file it just parsed are neither, so they
    only need escaping — and they must NOT be rejected, or an odd header would
    make the file unreadable.
    """
    return '"' + name.replace('"', '""') + '"'


def _select_list(con: duckdb.DuckDBPyConnection, parquet: Path) -> str:
    """Build a SELECT list that renames headers needing whitespace normalization.

    Returns `*` when every column name is already clean, so the common case
    stays a plain passthrough.
    """
    p = str(parquet).replace("'", "''")
    names = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{p}')").fetchall()]
    if all(_normalize_header(n) == n for n in names):
        return "*"

    parts, seen = [], set()
    for n in names:
        clean = _normalize_header(n) or n
        # Normalization can collide two headers ("A\nB" and "A B"); keep the
        # later one distinct rather than silently dropping a column.
        while clean in seen:
            clean += "_"
        seen.add(clean)
        parts.append(_raw_quote(n) if clean == n else f"{_raw_quote(n)} AS {_raw_quote(clean)}")
    return ", ".join(parts)


def _open_view(con: duckdb.DuckDBPyConnection, parquet: Path) -> list[str]:
    """Open the resource as view `data`. Returns its column names.

    Callers hand those names to `_quote_ident` so a column reference is
    checked against what the file actually has rather than against a
    character class.
    """
    p = str(parquet).replace("'", "''")
    cols = _select_list(con, parquet)
    con.execute(f"CREATE OR REPLACE VIEW data AS SELECT {cols} FROM read_parquet('{p}')")
    return [r[0] for r in con.execute("DESCRIBE data").fetchall()]


def _open_sandboxed(con: duckdb.DuckDBPyConnection, parquet: Path) -> list[str]:
    """Materialize the resource into an in-memory table, then revoke external access.

    Used by query_resource, whose SQL is model-supplied. Without this, a SELECT
    could call DuckDB table functions (read_text/read_csv/read_blob/glob) to read
    arbitrary local files or reach the network — the keyword denylist in
    _validate_sql does not cover those. We materialize FIRST (reading the local
    Parquet is itself "external access") and only then disable it, so the user
    query runs entirely against the in-memory `data` table.
    """
    p = str(parquet).replace("'", "''")
    cols = _select_list(con, parquet)
    con.execute(f"CREATE TABLE data AS SELECT {cols} FROM read_parquet('{p}')")
    names = [r[0] for r in con.execute("DESCRIBE data").fetchall()]
    con.execute("SET enable_external_access=false")
    con.execute("SET lock_configuration=true")
    return names


# ─── Public analytics tools ───────────────────────────────────────────────────


@_tool_envelope
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
        return _load_error(e, url)

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
            # These names came from DuckDB's own DESCRIBE of a file it just
            # parsed, not from the model, so they are escaped rather than
            # validated. Validating them was the reason a header the publisher
            # mangled ("A¤o") made every tool refuse the whole file.
            quoted = _raw_quote(col["name"])
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
    # col_name is a DESCRIBE result, not caller input: escape, do not validate.
    quoted = _raw_quote(col_name)
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


@_tool_envelope
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
        return _load_error(e, url)

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


_COMPARISON_OPS = {"=", "!=", "<>", "<", "<=", ">", ">="}


def _filter_measure(
    col: str,
    quoted: str,
    op: str,
    val: Any,
    types: dict[str, str] | None,
    notes: list[dict[str, Any]] | None,
) -> str:
    """Decide how a comparison should read a column, and say so.

    Aggregations already read a text-stored number as a number. Filters did not,
    which left two ways to be wrong on the same column. Comparing against an
    integer raised a DuckDB binder error the caller could do nothing with; the
    obvious workaround — comparing against `"0"` — succeeded and compared
    *strings*, so `"00" > "0"` was true where the number is not. That one is
    worse, because it looks like it worked.

    A numeric operand on a text column is coerced. A string operand is left
    alone: `=` and `in` against text codes is a legitimate question, and
    quietly turning it into arithmetic would answer a different one. It is
    flagged instead.
    """
    if types is None or op not in _COMPARISON_OPS:
        return quoted
    declared = types.get(col) or types.get(_match_column(col, list(types)) or "", "")
    if _is_numeric_type(declared):
        return quoted

    if isinstance(val, bool):  # bool is an int in Python; not a measurement
        return quoted
    if isinstance(val, (int, float)):
        if notes is not None:
            notes.append(
                {
                    "column": col,
                    "coerced": True,
                    "where": "filter",
                    "note": (
                        f"'{col}' is stored as text in this file and was compared as a "
                        "number. Values that do not read as numbers do not match."
                    ),
                }
            )
        return _as_number(quoted)

    if isinstance(val, str) and notes is not None:
        stripped = val.strip()
        try:
            float(stripped.replace(",", ""))
        except ValueError:
            return quoted
        notes.append(
            {
                "column": col,
                "coerced": False,
                "where": "filter",
                "comparison": "lexicographic",
                "note": (
                    f"'{col}' is stored as text and was compared against the string "
                    f"'{val}', so the comparison was alphabetical, not numeric — "
                    f"'00' sorts after '0'. Pass {stripped} as a number to compare "
                    "numerically."
                ),
            }
        )
    return quoted


def _build_filter_clause(
    f: dict[str, Any],
    available: list[str] | None = None,
    types: dict[str, str] | None = None,
    notes: list[dict[str, Any]] | None = None,
) -> str:
    col = f.get("col")
    op = f.get("op", "=")
    val = f.get("val")
    unknown = sorted(set(f) - {"col", "op", "val"})
    if unknown:
        raise AnalyticsError(
            f"Unknown key(s) in filter: {', '.join(unknown)}. Expected col, op and val — "
            'for example {"col": "Año", "op": "=", "val": 2026}.'
        )
    if not isinstance(col, str):
        raise AnalyticsError("filter.col must be a string")
    if op not in ALLOWED_OPS:
        raise AnalyticsError(
            f"Operator not allowed: {op}. Valid operators: {', '.join(sorted(ALLOWED_OPS))}."
        )
    q = _quote_ident(col, available)
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
    measured = _filter_measure(col, q, op, val, types, notes)
    return f"{measured} {cmp_op} {_quote_literal(val)}"


def _build_where(
    filters: list[dict] | None,
    available: list[str] | None = None,
    types: dict[str, str] | None = None,
    notes: list[dict[str, Any]] | None = None,
) -> str:
    if not filters:
        return ""
    parts = [_build_filter_clause(f, available, types, notes) for f in filters]
    return "WHERE " + " AND ".join(parts)


def _with_filter_notes(result: dict[str, Any], notes: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold what the filters had to do to the data into the same field the
    aggregations use.

    One place to look. A caller that has learned to read `numeric_coercion`
    should not have to learn a second field to find out that its WHERE clause
    silently compared text alphabetically.
    """
    if notes:
        existing = result.get("numeric_coercion") or []
        result["numeric_coercion"] = [*existing, *notes]
    return result


def _column_types(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    """Declared type per column of the open view."""
    return {r[0]: r[1] for r in con.execute("DESCRIBE data").fetchall()}


def _build_order_by(order_by: list[dict] | None, available: list[str] | None = None) -> str:
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
        parts.append(f"{_quote_ident(col, available)} {direction.upper()}")
    return "ORDER BY " + ", ".join(parts)


_NUMERIC_FN_ON_TEXT = re.compile(
    r"No function matches the given name and argument types '"
    r"(sum|avg|median|stddev|variance|min|max)\(VARCHAR\)",
    re.IGNORECASE,
)


# What a failed CAST looks like from DuckDB. The values that trip it in this
# catalog are stereotyped: thousands separators, a non-breaking space glued to
# a number, and placeholders standing in for "no data".
_CAST_FAILED = re.compile(r"Could not convert string '([^']*)' to", re.IGNORECASE)
_COLUMN_NOT_FOUND = re.compile(
    r'Referenced column "([^"]+)" (?:not found|was not found)', re.IGNORECASE
)


def _duckdb_error(
    e: duckdb.Error,
    sql: str | None = None,
    available: list[str] | None = None,
) -> dict[str, Any]:
    """Turn a DuckDB message into one the caller can act on.

    The common case by far: a spreadsheet mixes text into a numeric column
    ("N/D", a footnote, a thousands separator), so the whole column loads as
    text and `sum` fails. The DuckDB message says the function does not exist,
    which is true and useless — the fix is a cast, and the caller has no way to
    know that from the text alone.
    """
    out: dict[str, Any] = {"error": f"DuckDB: {e}"}
    conv = _CAST_FAILED.search(str(e))
    if conv:
        bad = conv.group(1)
        out["error"] = (
            f"The cast failed on the value {bad!r}. Columns in this catalog mix "
            "thousands separators, non-breaking spaces and placeholders "
            '("N/A", "-", "#REF!", "PROCESO CANCELADO") into otherwise numeric '
            "data, so a plain CAST stops at the first one."
        )
        out["hint"] = (
            "Use TRY_CAST, which yields NULL instead of failing, and strip the "
            "separators first: "
            "TRY_CAST(REPLACE(REPLACE(\"columna\", ',', ''), CHR(160), '') AS DOUBLE). "
            "Wrap it in an aggregate to skip the NULLs."
        )
        return out
    if available:
        missing = _COLUMN_NOT_FOUND.search(str(e))
        if missing:
            shown = ", ".join(repr(c) for c in available[:20])
            out["error"] = (
                f"Column {missing.group(1)!r} does not exist in this resource. Columns are: {shown}"
            )
            return out
    m = _NUMERIC_FN_ON_TEXT.search(str(e))
    if m:
        fn = m.group(1).lower()
        out["error"] = (
            f"The column is stored as text, so {fn.upper()} cannot be applied to it "
            "directly — the file mixes non-numeric values (a total, a footnote, "
            '"N/D", or a thousands separator) into a numeric column, so it was '
            "loaded as text."
        )
        out["hint"] = (
            "Use query_resource with an explicit cast, e.g. "
            "SELECT SUM(CAST(REPLACE(\"columna\", ',', '') AS DOUBLE)) FROM data — "
            "or call get_resource_schema first to see which columns are VARCHAR."
        )
    if sql is not None:
        out["sql"] = sql
    return out


def _build_agg_expr(
    agg: dict,
    available: list[str] | None = None,
    numeric: Callable[[str], str] | None = None,
) -> str:
    """Build one aggregate expression.

    `numeric` maps a column name to the SQL that reads it as a number. The
    counting functions never use it: COUNT over a text column is a legitimate
    question about text, and coercing there would silently answer a different
    one.
    """

    def measure(name: str) -> str:
        return numeric(name) if numeric else _quote_ident(name, available)

    # The obvious names for these keys are `column` and `function`, and getting
    # them wrong used to surface as "Aggregation not allowed: " with nothing
    # after the colon — an error that names neither what arrived nor what was
    # wanted. Say both before looking at the value.
    unknown = sorted(set(agg) - {"col", "fn", "alias"})
    if unknown:
        raise AnalyticsError(
            f"Unknown key(s) in aggregation: {', '.join(unknown)}. "
            "Expected col, fn and alias — for example "
            '{"col": "Sueldo Bruto", "fn": "sum", "alias": "total"}.'
        )
    col = agg.get("col")
    fn = (agg.get("fn") or "").lower()
    alias = agg.get("alias") or f"{fn}_{col or 'all'}"
    if fn not in ALLOWED_AGG_FNS:
        raise AnalyticsError(
            f"Aggregation not allowed: {fn or '(missing fn)'}. "
            f"Valid functions: {', '.join(sorted(ALLOWED_AGG_FNS))}."
        )
    if fn == "count" and col in (None, "*"):
        expr = "COUNT(*)"
    elif fn == "count":
        if not isinstance(col, str):
            raise AnalyticsError("count requires col to be a string")
        expr = f"COUNT({_quote_ident(col, available)})"
    elif fn == "count_distinct":
        if not isinstance(col, str):
            raise AnalyticsError("count_distinct requires col")
        expr = f"COUNT(DISTINCT {_quote_ident(col, available)})"
    elif fn in ("avg", "mean"):
        if not isinstance(col, str):
            raise AnalyticsError(f"{fn} requires col")
        expr = f"AVG({measure(col)})"
    elif fn == "median":
        if not isinstance(col, str):
            raise AnalyticsError("median requires col")
        expr = f"MEDIAN({measure(col)})"
    elif fn in ("sum", "min", "max", "stddev", "variance"):
        if not isinstance(col, str):
            raise AnalyticsError(f"{fn} requires col")
        sql_fn = "STDDEV" if fn == "stddev" else ("VAR_SAMP" if fn == "variance" else fn.upper())
        expr = f"{sql_fn}({measure(col)})"
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

# ─── Numbers stored as text ───────────────────────────────────────────────────
#
# The single largest failure class in this catalog: 202 of 284 errors from the
# directed battery, and 90 columns across 54 of the readable files. A payroll
# publishes `SUELDO BRUTO (RD$)` as VARCHAR because one cell says `N/A`, and
# every SUM and AVG over it fails — the column is a real measure, held hostage
# by a handful of placeholder cells.
#
# The cleanup below was chosen by measurement, not by intuition. Over 1,133
# VARCHAR columns in the mirror it rescues **41** that a plain cast cannot read
# at all (under 50% parseable) and that become fully parseable (≥90%): payroll
# (`Sueldo bruto`, `AFP`, `ISR`, `NETO`), water quality
# (`INDICE_POTABILIDAD_(%)`, `CLORO_RESIDUAL_(Mg/l)`), production volumes. In
# aggregate the gain looks tiny — 15.0% to 16.3% of all text values — because
# most VARCHAR columns are genuinely names and categories. The per-column view
# is the one that matters.
#
# A variant that also strips spaces was measured and rejected: it rescued one
# more column and would silently read `10 20 30` as 102030. Removing separators
# a number cannot contain is safe; removing a character that separates values
# is not.
_NUMERIC_TEXT_CLEAN = (
    "NULLIF(TRIM(REPLACE(REPLACE(REPLACE(REPLACE({c}, ',', ''), CHR(160), ''),"
    " 'RD$', ''), '$', '')), '')"
)

# Below this share of parseable values the column is treated as text, not as a
# damaged number. Coercing a column that is 60% numbers would answer a question
# about a measure using an arbitrary subset of the rows, which is worse than
# refusing: the caller gets a number and no reason to doubt it.
_COERCION_MIN_RATIO = 0.9

# How many distinct unparseable values to name back. Enough to recognise the
# pattern (`N/A`, `-`, `#REF!`, `PROCESO CANCELADO`), not enough to flood the
# assistant's context with one row per typo.
_COERCION_EXAMPLES = 8

# Umbrales del aviso de parseo sospechoso. Ver ensure_cached.
_SUSPICIOUS_SOURCE_BYTES = 100_000
_SUSPICIOUS_ROWS = 5


def _as_number(quoted: str) -> str:
    """SQL reading a text column as a number, NULL where it cannot."""
    return f"TRY_CAST({_NUMERIC_TEXT_CLEAN.format(c=quoted)} AS DOUBLE)"


def _and_where(where: str, condition: str) -> str:
    """Append a condition to a possibly-empty WHERE clause."""
    return f"{where} AND {condition}" if where else f"WHERE {condition}"


def _is_numeric_type(sql_type: str) -> bool:
    return any(t in (sql_type or "").lower() for t in _NUMERIC_TYPE_FRAGMENTS)


def _numeric_ref(
    con: duckdb.DuckDBPyConnection,
    col_name: str,
    types: dict[str, str],
    where: str = "",
) -> tuple[str, dict[str, Any] | None]:
    """SQL to read a column as a number, and an account of what that cost.

    Returns `(expression, report)`. `report` is None when the column is already
    numeric and nothing was done to it. Otherwise it says how many values were
    used, how many were dropped and which ones — because this is an audit tool.
    Absorbing a publisher's defect silently would make the server the last place
    the defect is visible, and the caller would have no way to know the average
    it just received excluded 37 rows.
    """
    quoted = _raw_quote(col_name)
    if _is_numeric_type(types.get(col_name, "")):
        return quoted, None

    expr = _as_number(quoted)
    row = con.execute(f"SELECT count({quoted}), count({expr}) FROM data {where}").fetchone()
    non_null, usable = (row or (0, 0))[0], (row or (0, 0))[1]
    if not non_null or usable / non_null < _COERCION_MIN_RATIO:
        return quoted, {
            "column": col_name,
            "coerced": False,
            "values_present": non_null,
            "values_numeric": usable,
            "note": (
                f"'{col_name}' is stored as text and only {usable} of {non_null} values "
                "read as numbers, too few to treat it as a damaged numeric column. "
                "Use query_resource with an explicit CAST if you know better."
            ),
        }

    dropped = con.execute(
        f"SELECT CAST({quoted} AS VARCHAR) AS v, count(*) FROM data "
        f"{_and_where(where, f'{quoted} IS NOT NULL AND {expr} IS NULL')} "
        f"GROUP BY 1 ORDER BY 2 DESC LIMIT {_COERCION_EXAMPLES}"
    ).fetchall()
    report: dict[str, Any] = {
        "column": col_name,
        "coerced": True,
        "values_used": usable,
        "values_excluded": non_null - usable,
    }
    if dropped:
        report["excluded_values"] = [{"value": v, "count": n} for v, n in dropped]
        report["note"] = (
            f"'{col_name}' is stored as text in this file. Values were read as numbers "
            f"where possible; {non_null - usable} could not be and were excluded from "
            "this result."
        )
    else:
        report["note"] = (
            f"'{col_name}' is stored as text in this file but every value read as a "
            "number. Nothing was excluded."
        )
    return expr, report


@_tool_envelope
async def quantiles_resource(
    url: str,
    fmt: str | None,
    columns: list[Any] | None = None,
    percentiles: list[float] | None = None,
    filters: list[dict] | None = None,
) -> dict[str, Any]:
    """Percentile distribution of numeric columns in a cached resource."""
    columns = _column_names(columns)
    kind = classify_format(fmt)
    if kind is None:
        return {"error": f"Format '{fmt}' not supported"}

    if percentiles is None:
        percentiles = [0.25, 0.5, 0.75, 0.90, 0.95, 0.99]
    for p in percentiles:
        # 0 and 1 are the min and max, which DuckDB computes happily. Rejecting
        # them only sent callers who wrote [0, 0.5, 1] away empty-handed.
        if not (0 <= p <= 1):
            return {"error": f"Percentile {p} must be between 0 and 1"}
    pctile_keys_check = [f"p{int(round(p * 100))}" for p in percentiles]
    if len(set(pctile_keys_check)) != len(pctile_keys_check):
        return {
            "error": "Duplicate percentile values after rounding (e.g., 0.904 and 0.905 both map to p90). Use distinct values."
        }

    try:
        parquet, meta = await ensure_cached(url, kind)
    except (httpx.HTTPError, AnalyticsError, duckdb.Error) as e:
        return _load_error(e, url)

    con = _new_con()
    try:
        available = _open_view(con, parquet)
        described = con.execute("DESCRIBE data").fetchall()
        row_count = con.execute("SELECT COUNT(*) FROM data").fetchone()[0]  # type: ignore[index]

        types = {row[0]: row[1] for row in described}
        all_numeric = [(n, t) for n, t in types.items() if _is_numeric_type(t)]

        filter_notes: list[dict[str, Any]] = []
        try:
            where = _build_where(filters, available, _column_types(con), filter_notes)
        except AnalyticsError as e:
            return {"error": str(e)}

        # Columns the caller named are inspected even when stored as text; the
        # rest of the file is not, because probing every VARCHAR column of a
        # wide file to see whether it is secretly a number costs a scan per
        # column and usually finds names.
        coercion: list[dict[str, Any]] = []
        exprs: dict[str, str] = {}
        if columns is not None:
            resolved = []
            for c in columns:
                hit = _match_column(c, available)
                if hit is None:
                    return {"error": f"Column '{c}' not found in resource"}
                resolved.append(hit)
            selected = []
            for n in resolved:
                if _is_numeric_type(types.get(n, "")):
                    selected.append((n, types.get(n, "")))
                    continue
                expr, report = _numeric_ref(con, n, types, where)
                if report and report.get("coerced"):
                    selected.append((n, types.get(n, "")))
                    exprs[n] = expr
                    coercion.append(report)
                elif report:
                    coercion.append(report)
        else:
            selected = all_numeric

        if not selected:
            out: dict[str, Any] = {
                "error": (
                    "No numeric columns found (or none of the requested columns hold "
                    "numbers, even read as text)"
                )
            }
            if coercion:
                out["numeric_coercion"] = coercion
            return _with_filter_notes(out, filter_notes)

        pctile_arr = "[" + ", ".join(repr(float(p)) for p in percentiles) + "]"
        pctile_keys = [f"p{int(round(p * 100))}" for p in percentiles]

        col_results = []
        for col_name, col_type in selected:
            quoted = exprs.get(col_name) or _raw_quote(col_name)
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

    quantile_result: dict[str, Any] = {
        "source_url": url,
        "format": kind,
        "cache": meta,
        "row_count": row_count,
        "percentiles": percentiles,
        "columns": col_results,
    }
    if coercion:
        quantile_result["numeric_coercion"] = coercion
    return _with_filter_notes(quantile_result, filter_notes)


@_tool_envelope
async def find_duplicates_resource(
    url: str,
    fmt: str | None,
    columns: list[Any] | None = None,
    filters: list[dict] | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Find rows duplicated on the specified columns (or all columns)."""
    columns = _column_names(columns)
    kind = classify_format(fmt)
    if kind is None:
        return {"error": f"Format '{fmt}' not supported"}

    limit = min(max(int(limit), 1), 500)

    try:
        parquet, meta = await ensure_cached(url, kind)
    except (httpx.HTTPError, AnalyticsError, duckdb.Error) as e:
        return _load_error(e, url)

    con = _new_con()
    try:
        available = _open_view(con, parquet)
        filter_notes: list[dict[str, Any]] = []
        try:
            where = _build_where(filters, available, _column_types(con), filter_notes)
        except AnalyticsError as e:
            return {"error": str(e)}

        if columns is None:
            columns = list(available)

        try:
            group_cols = ", ".join(_quote_ident(c, available) for c in columns)
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
            return _duckdb_error(e)

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
            return _duckdb_error(e)
        col_names = [d[0] for d in rs.description]
        rows = rs.fetchall()
    finally:
        con.close()

    return _with_filter_notes(
        {
            "source_url": url,
            "format": kind,
            "cache": meta,
            "columns_checked": columns,
            "duplicate_groups_found": duplicate_groups,
            "groups_returned": len(rows),
            "total_duplicate_rows": total_dup_rows,
            "columns": col_names,
            "rows": [list(r) for r in rows],
        },
        filter_notes,
    )


@_tool_envelope
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

    if not isinstance(column, str) or not column:
        return {"error": "column must be a non-empty string"}

    try:
        parquet, meta = await ensure_cached(url, kind)
    except (httpx.HTTPError, AnalyticsError, duckdb.Error) as e:
        return _load_error(e, url)

    con = _new_con()
    try:
        available = _open_view(con, parquet)
        filter_notes: list[dict[str, Any]] = []
        try:
            where = _build_where(filters, available, _column_types(con), filter_notes)
        except AnalyticsError as e:
            return {"error": str(e)}

        quoted = _quote_ident(column, available)
        types = {r[0]: r[1] for r in con.execute("DESCRIBE data").fetchall()}
        resolved = _match_column(column, available) or column
        coercion_report = None
        if not _is_numeric_type(types.get(resolved, "")):
            expr, coercion_report = _numeric_ref(con, resolved, types, where)
            if coercion_report and coercion_report.get("coerced"):
                quoted = expr
            else:
                return {
                    "error": (
                        f"Column '{column}' is stored as text and does not hold numbers, "
                        "so it has no quartiles."
                    ),
                    "numeric_coercion": [coercion_report] if coercion_report else [],
                }

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
            # Not a failure. The question "which values are outliers?" has a
            # correct answer here — none — and the column being flat is itself
            # the finding. Reporting it as an error made a working tool look
            # broken on 13 of 113 real columns during the catalog audit, and
            # left the assistant with nothing to tell the user.
            return {
                "outliers": [],
                "column": column,
                "q1": q1,
                "q3": q3,
                "iqr": 0,
                "note": (
                    f"Column '{column}' has no spread: the first and third quartiles are "
                    f"both {q1}, so no value can be an outlier. This usually means the "
                    f"column holds a constant, a year, or only a handful of repeated values."
                ),
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
            return _duckdb_error(e)
    finally:
        con.close()

    outliers_result = {
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
    return _with_filter_notes(
        {
            **outliers_result,
            **({"numeric_coercion": [coercion_report]} if coercion_report else {}),
        },
        filter_notes,
    )


@_tool_envelope
async def save_query_to_csv(
    url: str,
    fmt: str | None,
    dest: str | None = None,
    sql: str | None = None,
    filters: list[dict] | None = None,
    columns: list[Any] | None = None,
    limit: int = 10_000,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Run a filter or SQL query against a cached resource and write the result to CSV."""
    import csv as _csv
    import datetime
    import re

    columns = _column_names(columns)
    filter_notes: list[dict[str, Any]] = []
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
        expanded = Path(dest).expanduser()
        dest_path = expanded.resolve()
        if dest_path.suffix not in (".csv", ".tsv"):
            return {"error": "Destination must end in .csv or .tsv"}
        # The OS per-user temp dir is writable scratch space. On macOS it lives under
        # /private/var/folders/…, which would otherwise trip the /private/var denylist
        # entry below, so that exception is deliberate. It is not extended to a temp
        # dir that is itself a Windows system path: TEMP is C:\Windows\Temp for the
        # SYSTEM account and some services, and honouring the exception there would
        # switch the denylist off exactly where it matters most.
        tmp_root = Path(tempfile.gettempdir()).resolve()
        in_scratch = tmp_root in dest_path.parents and not _forbidden_windows(str(tmp_root))
        if not in_scratch:
            # Network locations are refused, and told apart from system paths so the
            # message names the actual policy. Checked first because a UNC path to a
            # system directory is both, and "network" is the more useful thing to say.
            if _is_unc_dest(dest) or _is_unc_dest(str(dest_path)):
                return {
                    "error": f"Network paths are not a supported destination: {dest}",
                    "hint": (
                        "This tool writes a CSV for a person to open, so it only writes to "
                        "local paths — a UNC or mapped-network destination is refused rather "
                        "than written to a host you did not name. Write it locally and copy "
                        "it to the share afterwards."
                    ),
                }
            # Check both the raw path and the resolved path (macOS resolves /etc → /private/etc).
            if _is_forbidden_dest(dest, str(dest_path)):
                return {"error": f"Cannot write to system path: {dest}"}
        # A relative destination is not a destination here. An MCP server launched
        # by a client inherits an undefined working directory — `/` on macOS — so
        # `resolve()` above sends "export.csv" to the filesystem root, where the
        # write fails with an OS error nobody can act on (macOS: read-only volume)
        # or, worse, succeeds somewhere the user will never look. This check runs
        # after the denylist so a Windows-style path stays reported as a system path
        # on POSIX, where it is not absolute.
        if not expanded.is_absolute():
            example = Path.home() / "Downloads" / (dest_path.name or "export.csv")
            return {
                "error": (
                    f"Destination must be an absolute path, got {dest!r}. This server runs "
                    "with no meaningful working directory, so a relative path does not land "
                    f"where you expect. Use e.g. {example}, or omit `dest` to write to "
                    "~/Downloads/datosgobdo-exports/."
                )
            }

    if dest_path.exists() and not overwrite:
        return {"error": f"File already exists: {dest_path}. Pass overwrite=True to replace."}

    try:
        parquet, meta = await ensure_cached(url, kind)
    except (httpx.HTTPError, AnalyticsError, duckdb.Error) as e:
        return _load_error(e, url)

    con = _new_con()
    try:
        if sql is not None:
            try:
                cleaned = _validate_sql(sql)
            except AnalyticsError as e:
                return {"error": str(e)}
            available = _open_sandboxed(con, parquet)
            wrapped = f"SELECT * FROM ({cleaned}) AS _q LIMIT {limit}"
            try:
                # Same wall-clock backstop as query_resource — this is the
                # other free-form SQL entry point.
                rs = _execute_guarded(con, wrapped)
            except duckdb.Error as e:
                return _duckdb_error(e, available=available)
        else:
            available = _open_view(con, parquet)
            select_clause = "*"
            if columns:
                try:
                    select_clause = ", ".join(_quote_ident(c, available) for c in columns)
                except AnalyticsError as e:
                    return {"error": str(e)}
            try:
                where = _build_where(filters, available, _column_types(con), filter_notes)
            except AnalyticsError as e:
                return {"error": str(e)}
            try:
                rs = con.execute(f"SELECT {select_clause} FROM data {where} LIMIT {limit}".strip())
            except duckdb.Error as e:
                return _duckdb_error(e)

        col_names = [d[0] for d in rs.description]
        rows = rs.fetchall()
    finally:
        con.close()

    try:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        # O_NOFOLLOW closes the TOCTOU window: a symlink swapped in between the
        # earlier path checks and this write would otherwise be followed when
        # overwrite=True. Raises ELOOP instead of writing through the link.
        open_flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(dest_path), open_flags, 0o644)
    except OSError as e:
        return _dest_open_error(e, dest_path)
    with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
        writer = _csv.writer(f)
        writer.writerow(col_names)
        writer.writerows(rows)

    bytes_written = dest_path.stat().st_size
    return _with_filter_notes(
        {
            "path": str(dest_path),
            "rows_written": len(rows),
            "columns": col_names,
            "bytes_written": bytes_written,
            "cache": meta,
        },
        filter_notes,
    )


@_tool_envelope
async def filter_resource(
    url: str,
    fmt: str | None,
    filters: list[dict] | None = None,
    columns: list[Any] | None = None,
    order_by: list[dict] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Typed WHERE/SELECT/ORDER BY/LIMIT against a cached resource."""
    columns = _column_names(columns)
    kind = classify_format(fmt)
    if kind is None:
        return {"error": f"Format '{fmt}' not supported"}
    try:
        parquet, meta = await ensure_cached(url, kind)
    except (httpx.HTTPError, AnalyticsError, duckdb.Error) as e:
        return _load_error(e, url)

    limit = min(max(int(limit), 1), FILTER_MAX_LIMIT)
    offset = max(int(offset), 0)

    con = _new_con()
    try:
        available = _open_view(con, parquet)
        select_clause = "*"
        if columns:
            select_clause = ", ".join(_quote_ident(c, available) for c in columns)
        filter_notes: list[dict[str, Any]] = []
        try:
            where = _build_where(filters, available, _column_types(con), filter_notes)
            order = _build_order_by(order_by, available)
        except AnalyticsError as e:
            return {"error": str(e)}

        sql = (
            f"SELECT {select_clause} FROM data {where} {order} LIMIT {limit} OFFSET {offset}"
        ).strip()
        try:
            rs = con.execute(sql)
        except duckdb.Error as e:
            return _duckdb_error(e, sql)
        col_names = [d[0] for d in rs.description]
        rows = rs.fetchall()
        # Estimate total matching rows (separate count query).
        try:
            total = con.execute(f"SELECT COUNT(*) FROM data {where}".strip()).fetchone()[0]  # type: ignore[index]
        except duckdb.Error:
            total = None

    finally:
        con.close()

    return _with_filter_notes(
        {
            "source_url": url,
            "format": kind,
            "cache": meta,
            "matching_rows_total": total,
            "rows_returned": len(rows),
            "columns": col_names,
            "limit": limit,
            "offset": offset,
            "rows": [list(r) for r in rows],
        },
        filter_notes,
    )


@_tool_envelope
async def aggregate_resource(
    url: str,
    fmt: str | None,
    aggregations: list[dict],
    group_by: list[Any] | None = None,
    filters: list[dict] | None = None,
    having: list[dict] | None = None,
    order_by: list[dict] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Typed GROUP BY + aggregations + optional HAVING."""
    group_by = _column_names(group_by)
    kind = classify_format(fmt)
    if kind is None:
        return {"error": f"Format '{fmt}' not supported"}
    if not aggregations:
        return {"error": "aggregations cannot be empty"}
    try:
        parquet, meta = await ensure_cached(url, kind)
    except (httpx.HTTPError, AnalyticsError, duckdb.Error) as e:
        return _load_error(e, url)

    limit = min(max(int(limit), 1), AGGREGATE_MAX_LIMIT)

    con = _new_con()
    try:
        available = _open_view(con, parquet)
        types = {r[0]: r[1] for r in con.execute("DESCRIBE data").fetchall()}
        # Resolve each measure once, so a column aggregated three ways is
        # inspected once and reported once.
        coercion: dict[str, dict[str, Any]] = {}

        def measure(name: str) -> str:
            resolved = _match_column(name, available)
            if resolved is None:
                return _quote_ident(name, available)  # raises, naming the real columns
            if resolved not in coercion:
                expr, report = _numeric_ref(con, resolved, types, "")
                coercion[resolved] = {"expr": expr, "report": report}
            return coercion[resolved]["expr"]

        try:
            agg_parts = [_build_agg_expr(a, available, measure) for a in aggregations]
        except AnalyticsError as e:
            return {"error": str(e)}

        group_parts: list[str] = []
        if group_by:
            try:
                group_parts = [_quote_ident(c, available) for c in group_by]
            except AnalyticsError as e:
                return {"error": str(e)}

        select_clause = ", ".join([*group_parts, *agg_parts])
        # ORDER BY and HAVING may legitimately name an aggregation alias, which
        # is not a column of the file, so they see the columns plus the aliases
        # this query just defined.
        aliases = [
            a.get("alias") or f"{(a.get('fn') or '').lower()}_{a.get('col') or 'all'}"
            for a in aggregations
        ]
        selectable = available + [a for a in aliases if isinstance(a, str)]
        filter_notes: list[dict[str, Any]] = []
        try:
            where = _build_where(filters, available, _column_types(con), filter_notes)
            order = _build_order_by(order_by, selectable)
        except AnalyticsError as e:
            return {"error": str(e)}
        group_clause = "GROUP BY " + ", ".join(group_parts) if group_parts else ""

        # HAVING uses the same filter syntax but column refs are agg aliases.
        having_clause = ""
        if having:
            try:
                having_clause = "HAVING " + " AND ".join(
                    _build_filter_clause(h, selectable) for h in having
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
            return _duckdb_error(e, sql)
        col_names = [d[0] for d in rs.description]
        rows = rs.fetchall()
        reports = [v["report"] for v in coercion.values() if v["report"]]
        computation = _computation(con, sql)
    finally:
        con.close()

    result: dict[str, Any] = {
        "source_url": url,
        "format": kind,
        "cache": meta,
        "groups_returned": len(rows),
        "columns": col_names,
        "limit": limit,
        "rows": [list(r) for r in rows],
        "computation": computation,
    }
    if reports:
        result["numeric_coercion"] = reports
    # Asking for ten groups without saying which ten returns ten arbitrary ones,
    # and the reply is shaped exactly like a top ten. Warn only when the cut
    # actually happened: an unordered query that fit under the limit lost
    # nothing, and warning on every one of those is noise that trains the
    # caller to ignore the field.
    if not order_by and len(rows) == limit:
        result["warning"] = (
            f"Returned {limit} group(s) with no order_by, so these are an arbitrary "
            "slice of the groups, not the largest ones. Add order_by, e.g. "
            f'[{{"col": "{aliases[0] if aliases else "alias"}", "dir": "desc"}}].'
        )
    return _with_filter_notes(result, filter_notes)


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


@_tool_envelope
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
        return _load_error(e, url)

    limit = min(max(int(limit), 1), SQL_MAX_LIMIT)
    wrapped = f"SELECT * FROM ({cleaned}) AS _user_q LIMIT {limit}"

    con = _new_con()
    try:
        available = _open_sandboxed(con, parquet)
        try:
            rs = _execute_guarded(con, wrapped)
        except duckdb.Error as e:
            return _duckdb_error(e, wrapped, available)
        col_names = [d[0] for d in rs.description]
        rows = rs.fetchall()
        computation = _computation(con, wrapped)
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
        "computation": computation,
    }


# ─── Cache management tool ────────────────────────────────────────────────────


def get_cache_stats() -> dict[str, Any]:
    return get_cache().stats()


def clear_cache() -> dict[str, Any]:
    """Empty the cache, or say why it could not be emptied.

    `_tool_envelope` only wraps coroutines and this is synchronous, so the
    envelope tuple does not cover it — the one caller-facing path that takes the
    index lock and is allowed to raise needs its own catch. Without it a
    contended index reaches the client as a protocol-level traceback.
    """
    try:
        removed = get_cache().clear()
    except CacheLockError as e:
        logger.warning("clear_cache failed: %s", e)
        return {"error": str(e)}
    return {"removed_entries": removed}

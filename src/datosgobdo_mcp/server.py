"""MCP server for datos.gob.do.

Exposes the Dominican Republic's open government data as MCP tools, resources
and prompts. Works with Claude Desktop, Claude Code, Antigravity, Codex,
OpenCode and any other MCP client.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Annotated, Any, Literal

from mcp import types
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__, ckan, reachability
from .analytics import (
    SCHEMA_SAMPLE_DEFAULT,
)
from .analytics import (
    aggregate_resource as _aggregate_resource,
)
from .analytics import (
    clear_cache as _clear_cache,
)
from .analytics import (
    detect_outliers_resource as _detect_outliers_resource,
)
from .analytics import (
    filter_resource as _filter_resource,
)
from .analytics import (
    find_duplicates_resource as _find_duplicates_resource,
)
from .analytics import (
    get_cache_stats as _get_cache_stats,
)
from .analytics import (
    get_resource_schema as _get_resource_schema,
)
from .analytics import (
    quantiles_resource as _quantiles_resource,
)
from .analytics import (
    query_resource as _query_resource,
)
from .analytics import (
    save_query_to_csv as _save_query_to_csv,
)
from .analytics import (
    summarize_resource as _summarize_resource,
)
from .models import (
    AggregateResult,
    CacheStatsResult,
    ClearCacheResult,
    DuplicatesResult,
    FilterResult,
    OutliersResult,
    PreviewResult,
    QuantilesResult,
    QueryResult,
    ReachabilityResult,
    SaveCsvResult,
    SchemaResult,
    SummaryResult,
)
from .preview import preview_resource_data

# Per MCP spec: stdio servers MUST NOT write to stdout (interferes with protocol).
# stderr is captured by the host and surfaced in Claude Desktop's mcp-server-*.log.
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("datosgobdo-mcp")

REPOSITORY_URL = "https://github.com/alcastaro/datos.gob.do-MCP-server"

# Server-level guidance. Clients pass this to the model once, at connect, so it
# costs nothing per call and reaches every agent — which is the point: the same
# habits had to be taught prompt by prompt in three separate client sessions,
# and an assistant that never reads the prompts still reads this.
INSTRUCTIONS = """\
Open data from the Dominican Republic's official portal, datos.gob.do: 1,061 \
datasets from 266 government institutions.

Three habits this catalog demands:

1. Roughly half of the catalog's files cannot be downloaded — the publishing \
institution's own site refuses programmatic access. Use check_resources before \
recommending a source. If a file cannot be read, say so; never answer with a \
different file, from a different institution or period, as if it were the one \
requested.

2. Distinguish what the catalog claims from what you read. Catalog replies \
carry source: catalog_metadata, analytics replies source: file_contents. A \
dataset's description is not evidence about its contents.

3. Report what the numbers cost. Government spreadsheets store numbers as text \
and leave headers inside the data, so results carry numeric_coercion naming \
what was excluded, source_sha256 for the bytes read, and computation with the \
SQL that ran. Pass those on: they are what make a figure checkable.

Start with search_datasets, then get_resource_schema before analysing.\
"""

mcp = FastMCP(
    "datosgobdo-mcp",
    instructions=INSTRUCTIONS,
    website_url=REPOSITORY_URL,
)
# FastMCP has no `version` constructor arg; the low-level server falls back to
# the installed mcp SDK version, so clients saw the SDK version in serverInfo.
mcp._mcp_server.version = __version__
# `Implementation.title` exists in the schema but this SDK version never fills
# it in, so setting it here would be dead code that reads like a feature.


# ─── Tool annotations ─────────────────────────────────────────────────────────
# Anthropic Directory review criteria require title + readOnlyHint, plus
# destructiveHint for any mutating tool. None of these tools write to the
# portal; the only mutation is clearing the local Parquet cache.


def _ro(title: str) -> ToolAnnotations:
    """Read-only tool that reaches the network (live datos.gob.do / file URLs)."""
    return ToolAnnotations(title=title, readOnlyHint=True, openWorldHint=True)


def _ro_local(title: str) -> ToolAnnotations:
    """Read-only tool that touches only local state (no network)."""
    return ToolAnnotations(title=title, readOnlyHint=True, openWorldHint=False)


# ─── Search and discovery ────────────────────────────────────────────────


@mcp.tool(annotations=_ro("Search datasets"))
async def search_datasets(
    query: Annotated[
        str | None,
        Field(
            description=(
                "Free-text search term, e.g. 'presupuesto', 'salud publica', "
                "'educacion'. Omit to list everything."
            )
        ),
    ] = None,
    organization: Annotated[
        str | None,
        Field(
            description=(
                "Institution slug, e.g. 'ministerio-de-salud-publica'. Slugs are "
                "the full registered name, so acronyms rarely work on their own — "
                "use autocomplete(kind='organization') when unsure."
            )
        ),
    ] = None,
    tag: Annotated[
        str | None,
        Field(description="Topic tag, e.g. 'finanzas', 'poblacion'."),
    ] = None,
    group: Annotated[
        str | None,
        Field(description="Thematic group, e.g. 'economia', 'salud'."),
    ] = None,
    limit: Annotated[int, Field(description="Results (1-50)", ge=1, le=50)] = 10,
    offset: Annotated[int, Field(description="Offset for paging", ge=0)] = 0,
) -> dict[str, Any]:
    """Search datasets on datos.gob.do (Dominican Republic open data).

    Filter by keyword, institution, tag or thematic group. Returns summary
    metadata: title, publishing institution, available formats, URL.
    """
    return await ckan.search_datasets(
        query=query,
        organization=organization,
        tag=tag,
        group=group,
        limit=limit,
        offset=offset,
    )


@mcp.tool(annotations=_ro("Get dataset metadata"))
async def get_dataset(
    id: Annotated[
        str,
        Field(
            description=(
                "Dataset UUID or slug, e.g. 'nomina-general' or "
                "'40bc3980-625b-4d7b-9ccc-304878126f8f'."
            )
        ),
    ],
) -> dict[str, Any]:
    """Full metadata for one dataset, including every downloadable resource.

    Returns title, description, institution, licence, and the complete resource
    list (CSV/XLSX/PDF/… files) with direct download URLs.
    """
    return await ckan.get_dataset(id)


@mcp.tool(annotations=_ro("List recent datasets"))
async def list_recent_datasets(
    limit: Annotated[int, Field(description="How many (1-30)", ge=1, le=30)] = 10,
) -> dict[str, Any]:
    """Most recently modified datasets on datos.gob.do.

    For monitoring portal updates. Returns hydrated metadata, not raw
    activity records.
    """
    return await ckan.list_recent_datasets(limit=limit)


# ─── Resources ─────────────────────────────────────────────────────────────────


@mcp.tool(annotations=_ro("Get resource metadata"))
async def get_resource(
    id: Annotated[str, Field(description="Resource UUID.")],
) -> dict[str, Any]:
    """Metadata for one resource (file): download URL, format, size."""
    return await ckan.get_resource(id)


@mcp.tool(annotations=_ro("Search resources"))
async def search_resources(
    query: Annotated[str, Field(description="Resource name, or part of it.")],
    limit: Annotated[int, Field(description="Results (1-50)", ge=1, le=50)] = 10,
) -> dict[str, Any]:
    """Search individual files by name. Returns download URLs, with the dataset and institution behind each one."""
    return await ckan.search_resources(query=query, limit=limit)


@mcp.tool(annotations=_ro("Preview resource data"))
async def download_resource_preview(
    url: Annotated[
        str,
        Field(
            description=(
                "Direct URL to the file (CKAN resource 'url' field). Supports CSV, TSV, XLSX, JSON."
            )
        ),
    ],
    format: Annotated[
        str,
        Field(
            description=(
                "Format declared in CKAN ('format' field). Accepts: csv, tsv, xlsx, xls, json."
            )
        ),
    ],
    rows: Annotated[
        int,
        Field(description="Rows to return (1-200). Default 20.", ge=1, le=200),
    ] = 20,
    sample: Annotated[
        Literal["head", "tail", "random"],
        Field(
            description=(
                "Which slice to return: 'head' (first N), 'tail' (last N of "
                "downloaded portion), or 'random' (uniform sample). For large "
                "files, prefer summarize_resource or aggregate_resource."
            )
        ),
    ] = "head",
) -> PreviewResult:
    """Download a resource and return N rows with their column headers.

    The datos.gob.do portal has no DataStore (no SQL), so this tool downloads
    the file and parses it client-side. 5 MB cap to avoid huge files. Useful
    for inspecting the structure of the data before deciding how to query it.
    For analytical queries on big files, use get_resource_schema +
    summarize_resource (v0.2) or aggregate_resource (v0.3+).
    """
    return PreviewResult(
        **await preview_resource_data(url=url, fmt=format, rows=rows, sample=sample)
    )


@mcp.tool(annotations=_ro("Check whether resources can be downloaded"))
async def check_resources(
    urls: Annotated[
        list[str],
        Field(description=(f"Direct file URLs (a resource's 'url'). Max {reachability.MAX_URLS}.")),
    ],
) -> ReachabilityResult:
    """Ask whether each file can actually be downloaded, without downloading it.

    The catalog says a resource exists; it does not say the file is still
    there. Call this before recommending a source — recommending a dataset
    whose files all refuse sends the user to a dead end, and nothing else here
    tells you in advance. Returns a class, not a yes/no: ok, challenge (a
    browser passes, no client can), waf_rule, not_found, server_error,
    html_page, head_not_supported, network.
    """
    results = await reachability.check(urls)
    return ReachabilityResult(
        checked=len(results),
        reachable=sum(1 for r in results if r.get("reachability") == reachability.OK),
        resources=results,  # type: ignore[arg-type]
    )


@mcp.tool(annotations=_ro("Get resource schema"))
async def get_resource_schema(
    url: Annotated[
        str,
        Field(description="Direct URL to the file (CKAN resource 'url' field)."),
    ],
    format: Annotated[
        str,
        Field(description="Format declared in CKAN. Accepts: csv, tsv, xlsx, json."),
    ],
    sample_rows: Annotated[
        int,
        Field(
            description=(
                "Distinct sample values to show per column (1-1000). The default of 6 "
                "is enough to recognise what a column holds; raise it only when you "
                "need to enumerate a category's values, and expect a much larger reply."
            ),
            ge=1,
            le=1000,
        ),
    ] = SCHEMA_SAMPLE_DEFAULT,
) -> SchemaResult:
    """Return column names, inferred types, and sample values for a resource.

    Cheap reconnaissance step. Downloads file (up to 100 MB), opens it in
    DuckDB, and runs DESCRIBE + per-column DISTINCT sampling. Does NOT return
    raw rows. Use this before summarize_resource or aggregate_resource so the
    model knows column names and types.
    """
    return SchemaResult(**await _get_resource_schema(url=url, fmt=format, sample_rows=sample_rows))


@mcp.tool(annotations=_ro("Summarize resource"))
async def summarize_resource(
    url: Annotated[
        str,
        Field(description="Direct URL to the file (CKAN resource 'url' field)."),
    ],
    format: Annotated[
        str,
        Field(description="Format declared in CKAN. Accepts: csv, tsv, xlsx, json."),
    ],
    max_categorical_top_n: Annotated[
        int,
        Field(
            description="Top-N most-frequent values per categorical column (1-50).",
            ge=1,
            le=50,
        ),
    ] = 10,
) -> SummaryResult:
    """Auto-generated profile: row count, types, nulls, distinct, min/max/mean, top values.

    Downloads file (up to 100 MB), runs DuckDB COUNT/DISTINCT/AGG queries per
    column. Returns one compact dict per column with stats. The model uses this
    to decide which filters and aggregations to apply next, without any raw
    rows in its context. For columns with many distinct values (e.g. names),
    'top_values' is omitted; only counts are returned.
    """
    return SummaryResult(
        **await _summarize_resource(
            url=url, fmt=format, max_categorical_top_n=max_categorical_top_n
        )
    )


@mcp.tool(annotations=_ro("Filter resource rows"))
async def filter_resource(
    url: Annotated[str, Field(description="Direct URL to the file (CKAN resource 'url' field).")],
    format: Annotated[
        str,
        Field(description="Format declared in CKAN. Accepts: csv, tsv, xlsx, json."),
    ],
    filters: Annotated[
        list[dict] | None,
        Field(
            description=(
                "Optional list of filter conditions, AND-combined. Each item is "
                "{col, op, val}. Valid ops: =, !=, <, <=, >, >=, in, not_in, "
                "contains, starts_with, ends_with, is_null, is_not_null. "
                'Example: [{"col":"Año","op":"=","val":2026},{"col":"Mes","op":"=","val":"Abril"}].'
            )
        ),
    ] = None,
    columns: Annotated[
        list[str | dict] | None,
        Field(description="Columns to SELECT. None = all columns."),
    ] = None,
    order_by: Annotated[
        list[dict] | None,
        Field(
            description=(
                'List of {col, dir} where dir is "asc" or "desc". '
                'Example: [{"col":"Sueldo Bruto","dir":"desc"}].'
            )
        ),
    ] = None,
    limit: Annotated[int, Field(description="Max rows to return (1-1000).", ge=1, le=1000)] = 100,
    offset: Annotated[int, Field(description="Rows to skip (for paginating).", ge=0)] = 0,
) -> FilterResult:
    """Run a typed WHERE/SELECT/ORDER BY/LIMIT against a cached resource.

    First call downloads the file (up to 100 MB) and caches it as Parquet at
    ~/.cache/datosgobdo-mcp/. Subsequent calls hit cache (<1s). Returns
    requested columns + matching rows (capped at limit) plus the total count
    of matching rows. Use this when you need actual records, not aggregates.
    """
    return FilterResult(
        **await _filter_resource(
            url=url,
            fmt=format,
            filters=filters,
            columns=columns,
            order_by=order_by,
            limit=limit,
            offset=offset,
        )
    )


@mcp.tool(annotations=_ro("Aggregate resource"))
async def aggregate_resource(
    url: Annotated[str, Field(description="Direct URL to the file (CKAN resource 'url' field).")],
    format: Annotated[
        str,
        Field(description="Format declared in CKAN. Accepts: csv, tsv, xlsx, json."),
    ],
    aggregations: Annotated[
        list[dict],
        Field(
            description=(
                "List of {col, fn, alias}. Valid fns: count, count_distinct, "
                "sum, avg, mean, median, min, max, stddev, variance. col=null "
                "or col='*' means COUNT(*). "
                'Example: [{"col":null,"fn":"count","alias":"empleados"},'
                '{"col":"Sueldo Bruto","fn":"sum","alias":"masa_salarial"}].'
            )
        ),
    ],
    group_by: Annotated[
        list[str | dict] | None,
        Field(description='Columns to GROUP BY. Example: ["Estatus","Mes"].'),
    ] = None,
    filters: Annotated[
        list[dict] | None,
        Field(description="Same syntax as filter_resource.filters. Applied before grouping."),
    ] = None,
    having: Annotated[
        list[dict] | None,
        Field(
            description=(
                "Post-aggregation filter on aggregation aliases. "
                'Example: [{"col":"empleados","op":">","val":10}].'
            )
        ),
    ] = None,
    order_by: Annotated[
        list[dict] | None,
        Field(
            description="Same syntax as filter_resource.order_by. Refs aggregation aliases or group cols."
        ),
    ] = None,
    limit: Annotated[int, Field(description="Max groups to return (1-1000).", ge=1, le=1000)] = 100,
) -> AggregateResult:
    """Run GROUP BY + aggregations against a cached resource without writing SQL.

    Typed wrapper that builds safe DuckDB queries from JSON. Example usage:
    \"How many employees by status in April 2026?\" →
        aggregations=[{col: null, fn: count, alias: empleados}],
        group_by=["Estatus"],
        filters=[{col:"Año",op:"=",val:2026},{col:"Mes",op:"=",val:"Abril"}],
        order_by=[{col:"empleados",dir:"desc"}].

    First call downloads + caches the file. Subsequent calls reuse the cache.
    Returns one row per group with the aggregation values.
    """
    return AggregateResult(
        **await _aggregate_resource(
            url=url,
            fmt=format,
            aggregations=aggregations,
            group_by=group_by,
            filters=filters,
            having=having,
            order_by=order_by,
            limit=limit,
        )
    )


@mcp.tool(annotations=_ro("Quantile distribution of numeric columns"))
async def quantiles_resource(
    url: Annotated[str, Field(description="Direct URL to the file (CKAN resource 'url' field).")],
    format: Annotated[
        str, Field(description="Format declared in CKAN. Accepts: csv, tsv, xlsx, json.")
    ],
    columns: Annotated[
        list[str | dict] | None,
        Field(description="Numeric columns to analyze. None = all numeric columns."),
    ] = None,
    percentiles: Annotated[
        list[float] | None,
        Field(
            description=(
                "Percentiles to compute (0–1 exclusive). "
                "Default: [0.25, 0.5, 0.75, 0.90, 0.95, 0.99]."
            )
        ),
    ] = None,
    filters: Annotated[
        list[dict] | None,
        Field(description="Same filter syntax as filter_resource. Applied before computing."),
    ] = None,
) -> QuantilesResult:
    """Percentile distribution (p25/p50/p75/p90/p95/p99) of numeric columns.

    Fills the gap left by aggregate_resource, which only exposes median.
    First call downloads + caches the file. Subsequent calls reuse the cache.
    Useful for salary analysis, budget distributions, and statistical profiling.
    """
    return QuantilesResult(
        **await _quantiles_resource(
            url=url, fmt=format, columns=columns, percentiles=percentiles, filters=filters
        )
    )


@mcp.tool(annotations=_ro("Find duplicate rows"))
async def find_duplicates_resource(
    url: Annotated[str, Field(description="Direct URL to the file (CKAN resource 'url' field).")],
    format: Annotated[
        str, Field(description="Format declared in CKAN. Accepts: csv, tsv, xlsx, json.")
    ],
    columns: Annotated[
        list[str | dict] | None,
        Field(
            description=(
                "Columns to check for duplication. None = all columns. "
                "Example: ['Nombre', 'Cedula'] checks for rows with same name and ID."
            )
        ),
    ] = None,
    filters: Annotated[
        list[dict] | None,
        Field(description="Same filter syntax as filter_resource. Applied before duplicate check."),
    ] = None,
    limit: Annotated[
        int, Field(description="Max duplicate groups to return (1–500).", ge=1, le=500)
    ] = 50,
) -> DuplicatesResult:
    """Find rows that appear more than once on the given columns (or all columns).

    Returns duplicate groups sorted by frequency descending. Useful for detecting
    data-quality issues in payroll, census, and registry datasets.
    First call downloads + caches. Subsequent calls reuse the cache.
    """
    return DuplicatesResult(
        **await _find_duplicates_resource(
            url=url, fmt=format, columns=columns, filters=filters, limit=limit
        )
    )


@mcp.tool(annotations=_ro("Detect outliers in a numeric column"))
async def detect_outliers_resource(
    url: Annotated[str, Field(description="Direct URL to the file (CKAN resource 'url' field).")],
    format: Annotated[
        str, Field(description="Format declared in CKAN. Accepts: csv, tsv, xlsx, json.")
    ],
    column: Annotated[str, Field(description="Numeric column to check. One column per call.")],
    filters: Annotated[
        list[dict] | None,
        Field(
            description="Same filter syntax as filter_resource. Applied before outlier detection."
        ),
    ] = None,
    limit: Annotated[
        int, Field(description="Max outlier rows to return (1–500).", ge=1, le=500)
    ] = 100,
) -> OutliersResult:
    """Find rows where a numeric column falls outside the IQR fence.

    Uses the standard IQR method: outliers are values below Q1 - 1.5*IQR or
    above Q3 + 1.5*IQR. Returns rows sorted by distance from the median.
    Useful for detecting data-entry errors in salary, budget, or census data.
    First call downloads + caches. Subsequent calls reuse the cache.
    """
    return OutliersResult(
        **await _detect_outliers_resource(
            url=url, fmt=format, column=column, filters=filters, limit=limit
        )
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Save query result to CSV",
        readOnlyHint=False,
        destructiveHint=False,
        openWorldHint=True,
    )
)
async def save_query_to_csv(
    url: Annotated[str, Field(description="Direct URL to the file (CKAN resource 'url' field).")],
    format: Annotated[
        str, Field(description="Format declared in CKAN. Accepts: csv, tsv, xlsx, json.")
    ],
    dest: Annotated[
        str | None,
        Field(
            description=(
                "Absolute path for the output file (.csv or .tsv). "
                "If None, saves to ~/Downloads/datosgobdo-exports/<slug>-<timestamp>.csv. "
                "Must not contain '..'. Cannot write to system paths (/etc, /usr, /bin, ...)."
            )
        ),
    ] = None,
    sql: Annotated[
        str | None,
        Field(
            description=(
                "Read-only SQL query against table 'data' (same rules as query_resource). "
                "If provided, takes precedence over filters/columns."
            )
        ),
    ] = None,
    filters: Annotated[
        list[dict] | None,
        Field(description="Same filter syntax as filter_resource. Used if sql is None."),
    ] = None,
    columns: Annotated[
        list[str | dict] | None,
        Field(description="Columns to include. None = all. Ignored if sql is provided."),
    ] = None,
    limit: Annotated[
        int,
        Field(description="Max rows to write (1–100000). Default 10000.", ge=1, le=100000),
    ] = 10000,
    overwrite: Annotated[
        bool, Field(description="Overwrite dest if it already exists. Default False.")
    ] = False,
) -> SaveCsvResult:
    """Write a query or filter result to a local CSV file.

    Export endpoint for analysis workflows — run your filter or SQL, then save
    the result to open in Excel or another tool. Returns the file path and row count.
    First call downloads + caches the source file. Subsequent calls reuse the cache.
    """
    if _hosted_mode():
        return SaveCsvResult(**_HOSTED_DISABLED)
    return SaveCsvResult(
        **await _save_query_to_csv(
            url=url,
            fmt=format,
            dest=dest,
            sql=sql,
            filters=filters,
            columns=columns,
            limit=limit,
            overwrite=overwrite,
        )
    )


@mcp.tool(annotations=_ro("Query resource (read-only SQL)"))
async def query_resource(
    url: Annotated[str, Field(description="Direct URL to the file (CKAN resource 'url' field).")],
    format: Annotated[
        str,
        Field(description="Format declared in CKAN. Accepts: csv, tsv, xlsx, xls, json, ods."),
    ],
    sql: Annotated[
        str,
        Field(
            description=(
                "Read-only SQL query against table 'data'. Only SELECT/WITH "
                "allowed; DDL/DML rejected. The query is wrapped in "
                "'SELECT * FROM (<your sql>) LIMIT <limit>' so a row cap is "
                "always enforced. "
                'Example: "SELECT Estatus, COUNT(*) c FROM data WHERE Año=2026 '
                "AND Mes='Abril' GROUP BY Estatus ORDER BY c DESC\""
            )
        ),
    ],
    limit: Annotated[
        int, Field(description="Hard cap on returned rows (1-1000).", ge=1, le=1000)
    ] = 200,
) -> QueryResult:
    """Run an ad-hoc read-only SQL query against a cached resource via DuckDB.

    Power-user escape hatch when filter_resource / aggregate_resource don't
    cover the case. The cached resource is exposed as the in-memory table
    'data'. SQL is DuckDB dialect — see https://duckdb.org/docs/sql/introduction.
    Supports CSV, TSV, XLSX, XLS, JSON, and ODS (auto-converted to CSV).

    Safety:
      - Only SELECT/WITH statements (CTEs allowed); multi-statement blocked.
      - DDL/DML keywords (INSERT/UPDATE/DELETE/DROP/CREATE/ALTER/COPY/EXPORT/
        IMPORT/TRUNCATE/GRANT/REVOKE/PRAGMA/SET/LOAD/INSTALL/ATTACH/DETACH/
        VACUUM/ANALYZE) rejected outright.
      - Sandboxed: the resource is materialized in memory and external access
        is disabled, so table functions (read_text/read_csv/glob/...) cannot
        read local files or reach the network.
      - Row cap always applied via outer wrapper.
    """
    return QueryResult(**await _query_resource(url=url, fmt=format, sql=sql, limit=limit))


def _hosted_mode() -> bool:
    """True when serving remote clients (streamable HTTP). Local-filesystem and
    shared-destructive tools are disabled in this mode: the filesystem belongs
    to the server host, not the user, and the Parquet cache is shared across
    tenants. Read per-call so tests (and runtime reconfig) see env changes."""
    import os

    return os.environ.get("DATOSGOBDO_TRANSPORT", "stdio").strip().lower() == "streamable-http"


_HOSTED_DISABLED: dict[str, Any] = {
    "error": "This tool is disabled in hosted mode",
    "hint": "It touches the server's local filesystem / shared cache. "
    "Run the server locally (stdio) to use it.",
}


@mcp.tool(annotations=_ro_local("Get cache stats"))
def get_cache_stats() -> CacheStatsResult:
    """Return cache stats (entries, bytes) plus server identity: version, security mode."""
    stats = _get_cache_stats()
    if _hosted_mode():
        # Don't leak server-side paths to remote clients.
        stats.pop("cache_dir", None)
    # The server's version travels in the initialize handshake, which clients
    # read and never hand to the model — a tester asking "which version is
    # running?" got the CKAN portal's version back, because that was the only
    # version any tool exposed. This is the only tool that describes the
    # server rather than the catalog, so identity rides here instead of
    # costing the tool-list budget a 25th entry.
    stats["server"] = {
        "name": "datosgobdo-mcp",
        "version": __version__,
        "netguard_mode": os.environ.get("DATOSGOBDO_NETGUARD", "public-only"),
        "transport": "streamable-http" if _hosted_mode() else "stdio",
    }
    return CacheStatsResult(**stats)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Clear analytics cache",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clear_cache() -> ClearCacheResult:
    """Remove all cached Parquet files. Returns the count removed."""
    if _hosted_mode():
        return ClearCacheResult(**_HOSTED_DISABLED)
    return ClearCacheResult(**_clear_cache())


# ─── Organizaciones ───────────────────────────────────────────────────────────


@mcp.tool(annotations=_ro("List organizations"))
async def list_organizations(
    limit: Annotated[int, Field(description="Maximum (1-200)", ge=1, le=200)] = 50,
) -> list[dict[str, Any]]:
    """List the government institutions publishing on datos.gob.do.

    Ministries, autonomous agencies, municipalities and so on, with a dataset
    count each. No long descriptions.
    """
    return await ckan.list_organizations(limit=limit)


@mcp.tool(annotations=_ro("Get organization"))
async def get_organization(
    id: Annotated[
        str,
        Field(
            description=(
                "Institution UUID or slug, e.g. 'ministerio-de-hacienda'. Slugs are the "
                "full registered name, so an acronym alone usually fails: INDOTEL is "
                "'instituto-dominicano-de-las-telecomunicaciones-indotel'."
            )
        ),
    ],
) -> dict[str, Any]:
    """Details for one institution: description, dataset count, URL."""
    return await ckan.get_organization(id)


# ─── Groups and tags ────────────────────────────────────────────────────────────


@mcp.tool(annotations=_ro("List groups"))
async def list_groups() -> list[dict[str, Any]]:
    """Thematic categories on datos.gob.do (economy, health, public administration, …)."""
    return await ckan.list_groups()


@mcp.tool(annotations=_ro("List tags"))
async def list_tags(
    query: Annotated[str | None, Field(description="Prefix to filter tags by.")] = None,
    limit: Annotated[int, Field(description="Maximum (1-100)", ge=1, le=100)] = 20,
) -> list[str]:
    """List available tags, optionally filtered by prefix."""
    return await ckan.list_tags(query=query, limit=limit)


# ─── Autocomplete ─────────────────────────────────────────────────────────────


@mcp.tool(annotations=_ro("Autocomplete entities"))
async def autocomplete(
    kind: Annotated[
        Literal["dataset", "organization", "group", "tag"],
        Field(description="Entity kind to autocomplete."),
    ],
    query: Annotated[str, Field(description="Partial text to complete.")],
    limit: Annotated[int, Field(description="Suggestions (1-30)", ge=1, le=30)] = 10,
) -> list[dict[str, Any]]:
    """Autocomplete dataset / organization / group / tag names.

    Resolves a slug when the user gives only part of a name. Slugs here are the
    full registered name, so acronyms rarely match on their own:
    kind='organization', query='indotel' →
    'instituto-dominicano-de-las-telecomunicaciones-indotel'.
    """
    return await ckan.autocomplete(kind=kind, query=query, limit=limit)


# ─── Stats ────────────────────────────────────────────────────────────────────


@mcp.tool(annotations=_ro("Get site stats"))
async def get_site_stats() -> dict[str, Any]:
    """Portal-wide totals for datos.gob.do: datasets, institutions, groups, tags."""
    return await ckan.get_site_stats()


# ─── Resources: the catalog as read-only context ─────────────────────────────
# The protocol has three primitives and they differ by who decides when to use
# them: the model calls tools, the application attaches resources, the user
# picks prompts. This server advertised all three capabilities and served only
# tools — a client opening its resources or prompts panel found the empty
# sections the handshake had promised. Found with the MCP Inspector.
#
# Resources here are the small, stable facts an application can attach as
# context without spending a tool call on them.


@mcp.resource(
    "datosgobdo://catalog/overview",
    name="Resumen del catálogo",
    description="Totales del portal: datasets, instituciones, grupos y etiquetas.",
    mime_type="application/json",
)
async def catalog_overview() -> dict[str, Any]:
    """Portal-wide totals, cheap enough to attach to any conversation."""
    return ckan.with_provenance(await ckan.get_site_stats())


@mcp.resource(
    "datosgobdo://catalog/institutions",
    name="Instituciones publicadoras",
    description="Las instituciones que publican en datos.gob.do, con su número de datasets.",
    mime_type="application/json",
)
async def catalog_institutions() -> dict[str, Any]:
    """Who publishes here. The answer to "which institution?" before any query."""
    return ckan.with_provenance({"organizations": await ckan.list_organizations(limit=500)})


@mcp.resource(
    "datosgobdo://guia/verificacion",
    name="Cómo verificar una cifra",
    description="Los cuatro campos que hacen comprobable un número, y qué hacer si faltan.",
    mime_type="text/markdown",
)
def verification_guide() -> str:
    """The checking protocol, as attachable context.

    A resource rather than a prompt because it is not a request: it is the
    half-page a journalist keeps open beside the conversation. It exists
    because the measured failure was never a fabricated number — it was a
    correct number nobody could check, and a substituted source named once in
    passing.
    """
    return """\
# Cómo verificar una cifra de este servidor

Cuatro campos. Si los cuatro cuadran, la cifra es citable; si falta alguno, no
salió de aquí.

## 1. `source` — ¿de dónde sale esto?

- `file_contents` → se leyó el archivo. La cifra es del dato.
- `catalog_metadata` → viene de la ficha del portal. Es lo que **el catálogo
  dice** del archivo, no lo que el archivo contiene. Sirve para describir, no
  para citar cifras.

## 2. `source_sha256` — ¿es el mismo archivo?

Huella de los bytes exactos que se leyeron. Dos lecturas con la misma huella
vieron el mismo archivo, aunque estén separadas por meses o por personas
distintas. Si el ministerio republica el archivo, la huella cambia — y así te
enteras de que tu cifra envejeció.

Una descarga truncada **no trae huella**: media huella presentada como entera
sería peor que ninguna.

## 3. `computation` — ¿cómo se calculó?

El SQL que se ejecutó y cuántas filas leyó. Con esto y la huella, cualquiera
reproduce el número. Si alguien discute la cifra, esto es lo que se le enseña.

## 4. `numeric_coercion` — ¿qué quedó fuera?

Las hojas de cálculo del gobierno guardan números como texto y dejan
encabezados metidos entre los datos. Este bloque dice cuántos valores entraron,
cuántos no, y cuáles fueron. **Una suma sin este bloque declarado no es una
suma verificada.**

## Señales de alarma

- Una cifra **sin** estos campos: la escribió el modelo, no el servidor.
- Una respuesta sobre un archivo que **no se pudo descargar**: cerca de la
  mitad del catálogo no se descarga. Si la fuente que pediste falló y aun así
  te dan un número, pregunta de qué archivo salió.
- Un archivo **de otra institución o de otro periodo** que el pedido: es
  sustitución de fuente, y ya pasó en pruebas reales — con un millón de
  personas de diferencia.
"""


@mcp.resource(
    "datosgobdo://dataset/{dataset_id}",
    name="Dataset del catálogo",
    description="Metadatos de un dataset por id o slug, incluidos sus recursos.",
    mime_type="application/json",
)
async def dataset_resource(dataset_id: str) -> dict[str, Any]:
    """A named dataset as attachable context.

    Carries `source: catalog_metadata`, like every catalog reply: what the
    catalog says about a file is not what the file contains, and a resource
    attached silently into a prompt is exactly where that distinction gets
    lost.
    """
    return ckan.with_provenance(await ckan.get_dataset(dataset_id))


# ─── Prompts: how to use this server well ────────────────────────────────────
# The specification describes prompts as a way to "showcase how to best use the
# MCP server", and this one needs it: 24 tools is a lot of surface for someone
# who has never seen the catalog, and the live sessions showed assistants
# recommending sources they never checked and citing figures without saying
# what was excluded. Each prompt below encodes a habit that was learned the
# hard way.


@mcp.prompt(
    name="empezar_aqui",
    title="Empezar aquí — qué hay y qué puedo preguntar",
    description="Panorama del portal y tres preguntas concretas para arrancar.",
)
def empezar_aqui() -> str:
    """The first thing someone should open, and the only prompt with no
    arguments. Twenty-four tools is not an invitation — a person who has
    never seen this catalog does not know that payrolls, budget execution and
    public investment are the three things it covers best."""
    return (
        "Preséntame el portal de datos abiertos de República Dominicana como si "
        "no supiera nada de él.\n\n"
        "1. Usa get_site_stats y list_organizations para decirme cuántos "
        "datasets hay, de cuántas instituciones, y cuáles publican más.\n"
        "2. Explícame en dos frases qué tipo de preguntas puedo responder con "
        "esto — nóminas públicas, ejecución presupuestaria e inversión pública "
        "por provincia son lo más completo del catálogo.\n"
        "3. Propón tres preguntas concretas que podría hacerte a continuación, "
        "y dime qué me daría cada una.\n\n"
        "Advertencia que debes darme desde el principio: cerca de la mitad de "
        "los archivos del catálogo no se pueden descargar porque el sitio de la "
        "propia institución rechaza el acceso automático. Comprueba con "
        "check_resources antes de recomendarme una fuente."
    )


@mcp.prompt(
    name="serie_temporal",
    title="Cómo cambió algo en el tiempo",
    description="Serie por año declarando el periodo real y sin tratar el año como medida.",
)
def serie_temporal(tema: str) -> str:
    return (
        f"Muéstrame cómo ha cambiado {tema} en el tiempo, según los datos del "
        "portal dominicano.\n\n"
        "Reglas para que la serie sirva:\n"
        "- El año es una **dimensión, no una medida**: agrupa por año, nunca lo "
        "sumes ni lo promedies.\n"
        "- Dime el periodo que el archivo cubre de verdad, no el que sugiere su "
        "título; suelen no coincidir.\n"
        "- Si el último periodo está incompleto (un año en curso, un mes a "
        "medias), dilo antes de comparar: una caída al final casi siempre es "
        "eso y no una tendencia.\n"
        "- Ordena por año de forma numérica. Ordenar meses o años como texto "
        "pone MAYO por encima de JUNIO, y ya produjo una conclusión equivocada "
        "en una prueba real.\n\n"
        "Cierra con la cifra por periodo y el sha256 del archivo."
    )


@mcp.prompt(
    name="auditar_nomina",
    title="Auditar la nómina de una institución",
    description="Suma, promedio y distribución salarial declarando filas excluidas y procedencia.",
)
def auditar_nomina(institucion: str) -> str:
    return (
        f"Audita la nómina pública de {institucion} en datos.gob.do.\n\n"
        "Procedimiento:\n"
        "1. Busca el dataset con search_datasets y elige el recurso más completo.\n"
        "2. Antes de citarlo, comprueba con check_resources que el archivo se "
        "descarga; si no, dilo y no lo sustituyas por otro.\n"
        "3. Usa get_resource_schema y luego aggregate_resource para el total y "
        "el promedio del sueldo bruto.\n\n"
        "Al responder, indica siempre: cuántos valores entraron en el cálculo y "
        "cuántos quedaron fuera (bloque numeric_coercion), el sha256 del "
        "archivo y el SQL ejecutado (bloque computation). Si una columna "
        "numérica está guardada como texto, dilo: es un defecto del publicador "
        "y cambia cómo hay que leer la cifra."
    )


@mcp.prompt(
    name="verificar_fuente",
    title="Verificar una fuente antes de citarla",
    description="Comprueba alcance, procedencia y forma de un recurso antes de usarlo.",
)
def verificar_fuente(url: str) -> str:
    return (
        f"Verifica esta fuente antes de que la cite en un trabajo publicable:\n{url}\n\n"
        "1. check_resources sobre la URL: ¿se descarga, o el sitio la rechaza?\n"
        "2. Si se descarga, get_resource_schema: forma, columnas y tipos.\n"
        "3. Informa el sha256 y la fecha de captura.\n\n"
        "Si el archivo no se puede bajar, di exactamente por qué (regla del "
        "sitio, desafío de navegador, enlace roto, página en vez de archivo) y "
        "**no respondas con otro archivo**: una fuente parecida presentada como "
        "la pedida es peor que ninguna respuesta."
    )


@mcp.prompt(
    name="explorar_institucion",
    title="Explorar qué publica una institución",
    description="Inventario de datasets de una institución con su estado real de descarga.",
)
def explorar_institucion(institucion: str) -> str:
    return (
        f"Dime qué datos publica {institucion} en datos.gob.do y cuáles sirven "
        "de verdad.\n\n"
        "Usa search_datasets o get_organization para el inventario, y "
        "check_resources sobre los recursos antes de recomendarlos. Separa "
        "explícitamente lo que el catálogo dice del archivo (campo description, "
        "source: catalog_metadata) de lo que has leído del archivo "
        "(source: file_contents). En este portal cerca de la mitad de los "
        "recursos no se descargan, así que un inventario sin esa comprobación "
        "es una lista de fuentes que no existen."
    )


@mcp.prompt(
    name="cruzar_fuentes",
    title="Cruzar dos fuentes con sus reservas",
    description="Cruce entre dos recursos declarando unidades, periodos y límites.",
)
def cruzar_fuentes(tema: str) -> str:
    return (
        f"Cruza dos fuentes del portal dominicano sobre {tema}.\n\n"
        "Antes de concluir nada, declara: qué mide exactamente cada archivo, "
        "qué años cubre cada uno, y si las unidades son comparables. "
        "Inversión de capital no es gasto corriente; presupuesto asignado no es "
        "ejecutado. Si el cruce no es válido, dilo y explica qué haría falta "
        "para hacerlo bien — un cruce inválido presentado como hallazgo es peor "
        "que no responder."
    )


# ─── Failed calls are marked as failed ───────────────────────────────────────


def _mark_domain_errors(server: FastMCP) -> None:
    """Set `isError` on replies that carry an error, keeping their payload.

    This server answers a failed call with a normal result whose body is
    `{"error": ..., "hint": ...}`. That was deliberate and it serves an
    assistant well: a structured hint is something it can act on, where a
    raised exception would arrive as bare prose. What it does not do is tell
    anything *other* than a model that the call failed — measured with the MCP
    Inspector, an unknown tool exits 5 while our own "Column not found" exits
    0, so a CI pipeline chaining on `&&` walks straight past a failure.

    The SDK offers no way to have both: its success path hardcodes
    `isError=False`, and its error path builds a fresh result that drops
    `structuredContent` entirely. So the reply is amended after the fact —
    same content, same structured payload, correct flag. The test suite pins
    the assumption, so an SDK that changes shape fails loudly here rather than
    silently reverting the behaviour.
    """
    handlers = server._mcp_server.request_handlers
    original = handlers.get(types.CallToolRequest)
    if original is None:  # pragma: no cover — registered by FastMCP at init
        return

    async def handler(req: types.CallToolRequest) -> types.ServerResult:
        result = await original(req)
        call = result.root
        if not isinstance(call, types.CallToolResult) or call.isError:
            return result
        body = call.structuredContent
        if isinstance(body, dict) and body.get("error"):
            return types.ServerResult(
                types.CallToolResult(
                    content=call.content,
                    structuredContent=body,
                    isError=True,
                )
            )
        return result

    handlers[types.CallToolRequest] = handler


_mark_domain_errors(mcp)


# ─── GCP pipeline tools (optional, requires dominican-open-data-mcp[gcp]) ────
from .gcp import register_gcp_tools  # noqa: E402

_GCP_TOOLS_REGISTERED = register_gcp_tools(mcp)
if _GCP_TOOLS_REGISTERED:
    logger.info("GCP pipeline tools registered (google-cloud libraries found)")


# ─── Entry point ──────────────────────────────────────────────────────────────


def main() -> (
    None
):  # pragma: no cover — blocking server loop, exercised by CI entry-point smoke test
    import os

    transport = os.environ.get("DATOSGOBDO_TRANSPORT", "stdio").strip().lower()
    logger.info(
        "datosgobdo-mcp starting (CKAN endpoint: %s, transport: %s)", ckan.BASE_URL, transport
    )
    try:
        if transport == "streamable-http":
            # Hosted mode: HTTP transport, stateless so instances can scale
            # horizontally. save_query_to_csv / clear_cache are auto-disabled
            # (see _hosted_mode) and cache stats omit server paths.
            mcp.settings.host = os.environ.get("DATOSGOBDO_HOST", "127.0.0.1")
            mcp.settings.port = int(os.environ.get("DATOSGOBDO_PORT", "8000"))
            mcp.settings.stateless_http = True
            mcp.run(transport="streamable-http")
        elif transport == "stdio":
            mcp.run()
        else:
            raise SystemExit(
                f"Invalid DATOSGOBDO_TRANSPORT={transport!r}; use 'stdio' or 'streamable-http'"
            )
    except Exception:
        logger.exception("Fatal error in MCP server")
        raise
    finally:
        import asyncio

        try:
            asyncio.run(ckan.close_client())
        except RuntimeError:
            pass
        logger.info("datosgobdo-mcp shut down")


if __name__ == "__main__":
    main()

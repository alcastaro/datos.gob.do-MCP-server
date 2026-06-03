"""Servidor MCP para datos.gob.do.

Expone los datos abiertos del gobierno dominicano como herramientas MCP.
Compatible con Claude Desktop, Claude Code y cualquier cliente MCP.
"""

from __future__ import annotations

import logging
import sys
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from . import ckan
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
from .preview import preview_resource_data

# Per MCP spec: stdio servers MUST NOT write to stdout (interferes with protocol).
# stderr is captured by the host and surfaced in Claude Desktop's mcp-server-*.log.
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("datosgobdo-mcp")

mcp = FastMCP("datosgobdo-mcp")


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


# ─── Búsqueda y descubrimiento ────────────────────────────────────────────────


@mcp.tool(annotations=_ro("Search datasets"))
async def search_datasets(
    query: Annotated[
        str | None,
        Field(
            description=(
                "Término de búsqueda en texto libre. "
                "Ej: 'presupuesto', 'salud pública', 'educación'. "
                "Omitir para listar todos."
            )
        ),
    ] = None,
    organization: Annotated[
        str | None,
        Field(
            description=(
                "Slug de la institución gubernamental. "
                "Ej: 'ministerio-de-salud-publica', 'bcrd', 'digepres'. "
                "Usar 'autocomplete' con kind='organization' si no estás seguro del slug."
            )
        ),
    ] = None,
    tag: Annotated[
        str | None,
        Field(description="Etiqueta temática. Ej: 'finanzas', 'poblacion'."),
    ] = None,
    group: Annotated[
        str | None,
        Field(description="Grupo o categoría. Ej: 'economia', 'salud'."),
    ] = None,
    limit: Annotated[int, Field(description="Resultados (1-50)", ge=1, le=50)] = 10,
    offset: Annotated[int, Field(description="Offset para paginación", ge=0)] = 0,
) -> dict:
    """Busca datasets en datos.gob.do (datos abiertos de República Dominicana).

    Filtra por palabra clave, organización, tag o grupo temático. Devuelve
    metadatos resumidos: título, organización, formatos disponibles, URL.
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
                "ID UUID o slug del dataset. "
                "Ej: 'nomina-general', '40bc3980-625b-4d7b-9ccc-304878126f8f'."
            )
        ),
    ],
) -> dict:
    """Obtiene metadatos completos de un dataset, incluyendo todos sus recursos descargables.

    Devuelve: título, descripción, organización, licencia, lista completa de recursos
    (archivos CSV/XLSX/PDF/etc) con URLs de descarga directa.
    """
    return await ckan.get_dataset(id)


@mcp.tool(annotations=_ro("List recent datasets"))
async def list_recent_datasets(
    limit: Annotated[int, Field(description="Cantidad (1-30)", ge=1, le=30)] = 10,
) -> dict:
    """Datasets modificados más recientemente en datos.gob.do.

    Útil para monitorear actualizaciones del portal gubernamental.
    Devuelve metadatos hidratados, no actividades crudas.
    """
    return await ckan.list_recent_datasets(limit=limit)


# ─── Recursos ─────────────────────────────────────────────────────────────────


@mcp.tool(annotations=_ro("Get resource metadata"))
async def get_resource(
    id: Annotated[str, Field(description="UUID del recurso.")],
) -> dict:
    """Metadatos de un recurso (archivo) específico: URL de descarga, formato, tamaño."""
    return await ckan.get_resource(id)


@mcp.tool(annotations=_ro("Search resources"))
async def search_resources(
    query: Annotated[str, Field(description="Nombre o parte del nombre del recurso.")],
    limit: Annotated[int, Field(description="Resultados (1-50)", ge=1, le=50)] = 10,
) -> dict:
    """Busca recursos (archivos individuales) por nombre. Devuelve URLs de descarga."""
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
) -> dict:
    """Download a resource and return N rows with their column headers.

    The datos.gob.do portal has no DataStore (no SQL), so this tool downloads
    the file and parses it client-side. 5 MB cap to avoid huge files. Useful
    for inspecting the structure of the data before deciding how to query it.
    For analytical queries on big files, use get_resource_schema +
    summarize_resource (v0.2) or aggregate_resource (v0.3+).
    """
    return await preview_resource_data(url=url, fmt=format, rows=rows, sample=sample)


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
            description="Distinct values per column to include as samples (1-1000).",
            ge=1,
            le=1000,
        ),
    ] = 1000,
) -> dict:
    """Return column names, inferred types, and sample values for a resource.

    Cheap reconnaissance step. Downloads file (up to 100 MB), opens it in
    DuckDB, and runs DESCRIBE + per-column DISTINCT sampling. Does NOT return
    raw rows. Use this before summarize_resource or aggregate_resource so the
    model knows column names and types.
    """
    return await _get_resource_schema(url=url, fmt=format, sample_rows=sample_rows)


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
) -> dict:
    """Auto-generated profile: row count, types, nulls, distinct, min/max/mean, top values.

    Downloads file (up to 100 MB), runs DuckDB COUNT/DISTINCT/AGG queries per
    column. Returns one compact dict per column with stats. The model uses this
    to decide which filters and aggregations to apply next, without any raw
    rows in its context. For columns with many distinct values (e.g. names),
    'top_values' is omitted; only counts are returned.
    """
    return await _summarize_resource(
        url=url, fmt=format, max_categorical_top_n=max_categorical_top_n
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
        list[str] | None,
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
) -> dict:
    """Run a typed WHERE/SELECT/ORDER BY/LIMIT against a cached resource.

    First call downloads the file (up to 100 MB) and caches it as Parquet at
    ~/.cache/datosgobdo-mcp/. Subsequent calls hit cache (<1s). Returns
    requested columns + matching rows (capped at limit) plus the total count
    of matching rows. Use this when you need actual records, not aggregates.
    """
    return await _filter_resource(
        url=url,
        fmt=format,
        filters=filters,
        columns=columns,
        order_by=order_by,
        limit=limit,
        offset=offset,
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
        list[str] | None,
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
) -> dict:
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
    return await _aggregate_resource(
        url=url,
        fmt=format,
        aggregations=aggregations,
        group_by=group_by,
        filters=filters,
        having=having,
        order_by=order_by,
        limit=limit,
    )


@mcp.tool(annotations=_ro("Quantile distribution of numeric columns"))
async def quantiles_resource(
    url: Annotated[
        str, Field(description="Direct URL to the file (CKAN resource 'url' field).")
    ],
    format: Annotated[
        str, Field(description="Format declared in CKAN. Accepts: csv, tsv, xlsx, json.")
    ],
    columns: Annotated[
        list[str] | None,
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
) -> dict:
    """Percentile distribution (p25/p50/p75/p90/p95/p99) of numeric columns.

    Fills the gap left by aggregate_resource, which only exposes median.
    First call downloads + caches the file. Subsequent calls reuse the cache.
    Useful for salary analysis, budget distributions, and statistical profiling.
    """
    return await _quantiles_resource(
        url=url, fmt=format, columns=columns, percentiles=percentiles, filters=filters
    )


@mcp.tool(annotations=_ro("Find duplicate rows"))
async def find_duplicates_resource(
    url: Annotated[
        str, Field(description="Direct URL to the file (CKAN resource 'url' field).")
    ],
    format: Annotated[
        str, Field(description="Format declared in CKAN. Accepts: csv, tsv, xlsx, json.")
    ],
    columns: Annotated[
        list[str] | None,
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
) -> dict:
    """Find rows that appear more than once on the given columns (or all columns).

    Returns duplicate groups sorted by frequency descending. Useful for detecting
    data-quality issues in payroll, census, and registry datasets.
    First call downloads + caches. Subsequent calls reuse the cache.
    """
    return await _find_duplicates_resource(
        url=url, fmt=format, columns=columns, filters=filters, limit=limit
    )


@mcp.tool(annotations=_ro("Detect outliers in a numeric column"))
async def detect_outliers_resource(
    url: Annotated[
        str, Field(description="Direct URL to the file (CKAN resource 'url' field).")
    ],
    format: Annotated[
        str, Field(description="Format declared in CKAN. Accepts: csv, tsv, xlsx, json.")
    ],
    column: Annotated[
        str, Field(description="Numeric column to check. One column per call.")
    ],
    filters: Annotated[
        list[dict] | None,
        Field(description="Same filter syntax as filter_resource. Applied before outlier detection."),
    ] = None,
    limit: Annotated[
        int, Field(description="Max outlier rows to return (1–500).", ge=1, le=500)
    ] = 100,
) -> dict:
    """Find rows where a numeric column falls outside the IQR fence.

    Uses the standard IQR method: outliers are values below Q1 - 1.5*IQR or
    above Q3 + 1.5*IQR. Returns rows sorted by distance from the median.
    Useful for detecting data-entry errors in salary, budget, or census data.
    First call downloads + caches. Subsequent calls reuse the cache.
    """
    return await _detect_outliers_resource(
        url=url, fmt=format, column=column, filters=filters, limit=limit
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
    url: Annotated[
        str, Field(description="Direct URL to the file (CKAN resource 'url' field).")
    ],
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
        list[str] | None,
        Field(description="Columns to include. None = all. Ignored if sql is provided."),
    ] = None,
    limit: Annotated[
        int,
        Field(description="Max rows to write (1–100000). Default 10000.", ge=1, le=100000),
    ] = 10000,
    overwrite: Annotated[
        bool, Field(description="Overwrite dest if it already exists. Default False.")
    ] = False,
) -> dict:
    """Write a query or filter result to a local CSV file.

    Export endpoint for analysis workflows — run your filter or SQL, then save
    the result to open in Excel or another tool. Returns the file path and row count.
    First call downloads + caches the source file. Subsequent calls reuse the cache.
    """
    return await _save_query_to_csv(
        url=url,
        fmt=format,
        dest=dest,
        sql=sql,
        filters=filters,
        columns=columns,
        limit=limit,
        overwrite=overwrite,
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
) -> dict:
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
    return await _query_resource(url=url, fmt=format, sql=sql, limit=limit)


@mcp.tool(annotations=_ro_local("Get cache stats"))
def get_cache_stats() -> dict:
    """Return on-disk Parquet cache stats: entry count, total bytes, max bytes."""
    return _get_cache_stats()


@mcp.tool(
    annotations=ToolAnnotations(
        title="Clear analytics cache",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def clear_cache() -> dict:
    """Remove all cached Parquet files. Returns the count removed."""
    return _clear_cache()


# ─── Organizaciones ───────────────────────────────────────────────────────────


@mcp.tool(annotations=_ro("List organizations"))
async def list_organizations(
    limit: Annotated[int, Field(description="Máximo (1-200)", ge=1, le=200)] = 50,
) -> list[dict]:
    """Lista instituciones gubernamentales que publican en datos.gob.do.

    Devuelve ministerios, organismos autónomos, municipios, etc.,
    con conteo de datasets por institución. Sin descripciones largas.
    """
    return await ckan.list_organizations(limit=limit)


@mcp.tool(annotations=_ro("Get organization"))
async def get_organization(
    id: Annotated[
        str,
        Field(
            description=(
                "ID o slug de la organización. Ej: 'ministerio-de-hacienda', 'bcrd', 'indotel'."
            )
        ),
    ],
) -> dict:
    """Información detallada de una institución: descripción, número de datasets, URL."""
    return await ckan.get_organization(id)


# ─── Grupos y tags ────────────────────────────────────────────────────────────


@mcp.tool(annotations=_ro("List groups"))
async def list_groups() -> list[dict]:
    """Categorías temáticas en datos.gob.do (economía, salud, gestión pública, etc.)."""
    return await ckan.list_groups()


@mcp.tool(annotations=_ro("List tags"))
async def list_tags(
    query: Annotated[str | None, Field(description="Prefijo para filtrar tags.")] = None,
    limit: Annotated[int, Field(description="Máximo (1-100)", ge=1, le=100)] = 20,
) -> list[str]:
    """Lista etiquetas disponibles, opcionalmente filtradas por prefijo."""
    return await ckan.list_tags(query=query, limit=limit)


# ─── Autocomplete ─────────────────────────────────────────────────────────────


@mcp.tool(annotations=_ro("Autocomplete entities"))
async def autocomplete(
    kind: Annotated[
        Literal["dataset", "organization", "group", "tag"],
        Field(description="Tipo de entidad a autocompletar."),
    ],
    query: Annotated[str, Field(description="Texto parcial a completar.")],
    limit: Annotated[int, Field(description="Sugerencias (1-30)", ge=1, le=30)] = 10,
) -> list:
    """Autocompleta nombres de datasets / organizaciones / grupos / tags.

    Útil para resolver slugs cuando el usuario sólo da nombre parcial.
    Ej: kind='organization', query='hacienda' → sugiere 'ministerio-de-hacienda'.
    """
    return await ckan.autocomplete(kind=kind, query=query, limit=limit)


# ─── Stats ────────────────────────────────────────────────────────────────────


@mcp.tool(annotations=_ro("Get site stats"))
async def get_site_stats() -> dict:
    """Estadísticas generales del portal datos.gob.do.

    Devuelve: total de datasets, organizaciones, grupos, tags.
    """
    return await ckan.get_site_stats()


# ─── Entry point ──────────────────────────────────────────────────────────────


def main() -> None:
    logger.info("datosgobdo-mcp starting (CKAN endpoint: %s)", ckan.BASE_URL)
    try:
        mcp.run()
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

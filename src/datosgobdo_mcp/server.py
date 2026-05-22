"""Servidor MCP para datos.gob.do.

Expone los datos abiertos del gobierno dominicano como herramientas MCP.
Compatible con Claude Desktop, Claude Code y cualquier cliente MCP.
"""

from __future__ import annotations

import logging
import sys
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from . import ckan
from .analytics import (
    aggregate_resource as _aggregate_resource,
    clear_cache as _clear_cache,
    filter_resource as _filter_resource,
    get_cache_stats as _get_cache_stats,
    get_resource_schema as _get_resource_schema,
    query_resource as _query_resource,
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


# ─── Búsqueda y descubrimiento ────────────────────────────────────────────────


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
async def list_recent_datasets(
    limit: Annotated[int, Field(description="Cantidad (1-30)", ge=1, le=30)] = 10,
) -> dict:
    """Datasets modificados más recientemente en datos.gob.do.

    Útil para monitorear actualizaciones del portal gubernamental.
    Devuelve metadatos hidratados, no actividades crudas.
    """
    return await ckan.list_recent_datasets(limit=limit)


# ─── Recursos ─────────────────────────────────────────────────────────────────


@mcp.tool()
async def get_resource(
    id: Annotated[str, Field(description="UUID del recurso.")],
) -> dict:
    """Metadatos de un recurso (archivo) específico: URL de descarga, formato, tamaño."""
    return await ckan.get_resource(id)


@mcp.tool()
async def search_resources(
    query: Annotated[str, Field(description="Nombre o parte del nombre del recurso.")],
    limit: Annotated[int, Field(description="Resultados (1-50)", ge=1, le=50)] = 10,
) -> dict:
    """Busca recursos (archivos individuales) por nombre. Devuelve URLs de descarga."""
    return await ckan.search_resources(query=query, limit=limit)


@mcp.tool()
async def download_resource_preview(
    url: Annotated[
        str,
        Field(
            description=(
                "Direct URL to the file (CKAN resource 'url' field). "
                "Supports CSV, TSV, XLSX, JSON."
            )
        ),
    ],
    format: Annotated[
        str,
        Field(
            description=(
                "Format declared in CKAN ('format' field). "
                "Accepts: csv, tsv, xlsx, xls, json."
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
async def filter_resource(
    url: Annotated[
        str, Field(description="Direct URL to the file (CKAN resource 'url' field).")
    ],
    format: Annotated[
        str, Field(description="Format declared in CKAN. Accepts: csv, tsv, xlsx, json.")
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
    limit: Annotated[
        int, Field(description="Max rows to return (1-1000).", ge=1, le=1000)
    ] = 100,
    offset: Annotated[
        int, Field(description="Rows to skip (for paginating).", ge=0)
    ] = 0,
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


@mcp.tool()
async def aggregate_resource(
    url: Annotated[
        str, Field(description="Direct URL to the file (CKAN resource 'url' field).")
    ],
    format: Annotated[
        str, Field(description="Format declared in CKAN. Accepts: csv, tsv, xlsx, json.")
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
        Field(description="Same syntax as filter_resource.order_by. Refs aggregation aliases or group cols."),
    ] = None,
    limit: Annotated[
        int, Field(description="Max groups to return (1-1000).", ge=1, le=1000)
    ] = 100,
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


@mcp.tool()
async def query_resource(
    url: Annotated[
        str, Field(description="Direct URL to the file (CKAN resource 'url' field).")
    ],
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
                "Example: \"SELECT Estatus, COUNT(*) c FROM data WHERE Año=2026 "
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
    cover the case. The cached resource is exposed as the view 'data'.
    Supports CSV, TSV, XLSX, XLS, JSON, and ODS (auto-converted to CSV).

    Safety:
      - Only SELECT/WITH statements (CTEs allowed).
      - Multi-statement queries blocked.
      - Keywords INSERT/UPDATE/DELETE/DROP/CREATE/ALTER/COPY/EXPORT/IMPORT/
        TRUNCATE/GRANT/REVOKE/PRAGMA/SET/LOAD/INSTALL/ATTACH/DETACH/VACUUM/
        ANALYZE rejected outright.
      - Row cap always applied via outer wrapper.
    """
    return await _query_resource(url=url, fmt=format, sql=sql, limit=limit)


@mcp.tool()
def get_cache_stats() -> dict:
    """Return on-disk Parquet cache stats: entry count, total bytes, max bytes."""
    return _get_cache_stats()


@mcp.tool()
def clear_cache() -> dict:
    """Remove all cached Parquet files. Returns the count removed."""
    return _clear_cache()


# ─── Organizaciones ───────────────────────────────────────────────────────────


@mcp.tool()
async def list_organizations(
    limit: Annotated[int, Field(description="Máximo (1-200)", ge=1, le=200)] = 50,
) -> list[dict]:
    """Lista instituciones gubernamentales que publican en datos.gob.do.

    Devuelve ministerios, organismos autónomos, municipios, etc.,
    con conteo de datasets por institución. Sin descripciones largas.
    """
    return await ckan.list_organizations(limit=limit)


@mcp.tool()
async def get_organization(
    id: Annotated[
        str,
        Field(
            description=(
                "ID o slug de la organización. "
                "Ej: 'ministerio-de-hacienda', 'bcrd', 'indotel'."
            )
        ),
    ],
) -> dict:
    """Información detallada de una institución: descripción, número de datasets, URL."""
    return await ckan.get_organization(id)


# ─── Grupos y tags ────────────────────────────────────────────────────────────


@mcp.tool()
async def list_groups() -> list[dict]:
    """Categorías temáticas en datos.gob.do (economía, salud, gestión pública, etc.)."""
    return await ckan.list_groups()


@mcp.tool()
async def list_tags(
    query: Annotated[
        str | None, Field(description="Prefijo para filtrar tags.")
    ] = None,
    limit: Annotated[int, Field(description="Máximo (1-100)", ge=1, le=100)] = 20,
) -> list[str]:
    """Lista etiquetas disponibles, opcionalmente filtradas por prefijo."""
    return await ckan.list_tags(query=query, limit=limit)


# ─── Autocomplete ─────────────────────────────────────────────────────────────


@mcp.tool()
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


@mcp.tool()
async def get_site_stats() -> dict:
    """Estadísticas generales del portal datos.gob.do.

    Devuelve: total de datasets, organizaciones, grupos, tags.
    """
    return await ckan.get_site_stats()


# ─── Entry point ──────────────────────────────────────────────────────────────


def _tool_count() -> int | None:
    """Best-effort count of registered tools (uses private FastMCP attr)."""
    try:
        return len(mcp._tool_manager._tools)  # type: ignore[attr-defined]
    except Exception:
        return None


def main() -> None:
    logger.info("datosgobdo-mcp starting (CKAN endpoint: %s)", ckan.BASE_URL)
    count = _tool_count()
    if count is not None:
        logger.info("Registered %d tools", count)
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

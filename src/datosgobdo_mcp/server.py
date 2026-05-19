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
                "URL directa al archivo (campo 'url' del recurso). "
                "Soporta CSV, TSV, XLSX, JSON."
            )
        ),
    ],
    format: Annotated[
        str,
        Field(
            description=(
                "Formato declarado en CKAN (campo 'format'). "
                "Acepta: csv, tsv, xlsx, xls, json."
            )
        ),
    ],
    rows: Annotated[
        int,
        Field(description="Filas a devolver (1-200). Default 20.", ge=1, le=200),
    ] = 20,
) -> dict:
    """Baja un recurso y devuelve las primeras N filas con sus columnas.

    El portal datos.gob.do no tiene DataStore (no hay SQL), así que esta tool descarga
    el archivo y lo parsea cliente-side. Tope de 5MB para evitar archivos enormes.
    Útil para ver la estructura real de los datos antes de bajar el archivo completo.
    """
    return await preview_resource_data(url=url, fmt=format, rows=rows)


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

"""Optional GCP pipeline: load datos.gob.do resources into BigQuery.

The server fills the slot Google's own BigQuery MCP does not cover —
discovery + ingestion of Dominican open data. These tools download a resource
(reusing the existing Parquet cache), upload the Parquet to GCS and expose it
to BigQuery, by default as an **External Table** (zero-ETL: BigQuery reads the
Parquet in place; you pay only for queries).

Everything Google-related imports lazily so the base install never needs the
GCP SDKs. Install with:  pip install 'dominican-open-data-mcp[gcp]'

The google-cloud client libraries are synchronous — all calls run in a worker
thread (``asyncio.to_thread``) so the MCP event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import re
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.types import ToolAnnotations
from pydantic import Field

from .analytics import ensure_cached
from .download import classify_format

logger = logging.getLogger(__name__)

_BUCKET_ENV = "DATOSGOBDO_GCS_BUCKET"
_BLOB_PREFIX = "datosgobdo"

_NOT_INSTALLED = {
    "error": "GCP support not installed",
    "hint": "pip install 'dominican-open-data-mcp[gcp]' (adds google-cloud-storage + google-cloud-bigquery)",
}


def gcp_available() -> bool:
    """True when both google-cloud libraries are importable."""
    try:
        # find_spec raises ModuleNotFoundError when a PARENT package ("google")
        # is absent — it only returns None for a missing leaf.
        return (
            importlib.util.find_spec("google.cloud.bigquery") is not None
            and importlib.util.find_spec("google.cloud.storage") is not None
        )
    except ModuleNotFoundError:
        return False


def _auto_table_name(url: str) -> str:
    """URL stem → valid BigQuery table id: [a-z0-9_], no leading digit, ≤60 chars."""
    stem = Path(url.split("?")[0]).stem.lower()
    name = re.sub(r"[^a-z0-9_]+", "_", stem).strip("_") or "resource"
    if name[0].isdigit():
        name = f"t_{name}"
    return name[:60]


def _upload_to_gcs(parquet_path: Path, bucket_name: str, blob_name: str) -> str:
    """Sync: upload the Parquet file; returns the gs:// URI. Idempotent overwrite."""
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(str(parquet_path))
    return f"gs://{bucket_name}/{blob_name}"


def _create_bq_table(
    gcs_uri: str, project: str, dataset: str, table: str, mode: str
) -> dict[str, Any]:
    """Sync: external table (default) or load job. Returns table metadata."""
    from google.cloud import bigquery

    client = bigquery.Client(project=project)
    table_ref = f"{project}.{dataset}.{table}"

    if mode == "external":
        ext_config = bigquery.ExternalConfig("PARQUET")
        ext_config.source_uris = [gcs_uri]
        bq_table = bigquery.Table(table_ref)
        bq_table.external_data_configuration = ext_config
        client.create_table(bq_table, exists_ok=True)
        return {"table": table_ref, "rows": None}

    job_config = bigquery.LoadJobConfig(source_format="PARQUET")
    job = client.load_table_from_uri(gcs_uri, table_ref, job_config=job_config)
    job.result()
    loaded = client.get_table(table_ref)
    return {"table": table_ref, "rows": loaded.num_rows}


async def load_resource_to_bigquery(
    url: str,
    fmt: str | None,
    project: str,
    dataset: str,
    table: str | None = None,
    gcs_bucket: str | None = None,
    mode: Literal["external", "load"] = "external",
) -> dict[str, Any]:
    """Download a resource (via the Parquet cache), upload to GCS, expose in BigQuery."""
    if not gcp_available():
        return dict(_NOT_INSTALLED)

    kind = classify_format(fmt)
    if kind is None:
        return {"error": f"Format '{fmt}' not supported"}
    if mode not in ("external", "load"):
        return {"error": f"mode must be 'external' or 'load', got {mode!r}"}

    bucket = gcs_bucket or os.environ.get(_BUCKET_ENV)
    if not bucket:
        return {
            "error": "No GCS bucket specified",
            "hint": f"Pass gcs_bucket=... or set the {_BUCKET_ENV} env var.",
        }

    try:
        parquet_path, cache_meta = await ensure_cached(url, kind)
    except Exception as e:  # httpx/Analytics/duckdb — same envelope as analytics tools
        return {"error": f"Could not load resource: {e}"}

    table_name = table or _auto_table_name(url)
    blob_name = f"{_BLOB_PREFIX}/{table_name}.parquet"

    try:
        gcs_uri = await asyncio.to_thread(_upload_to_gcs, parquet_path, bucket, blob_name)
        result = await asyncio.to_thread(
            _create_bq_table, gcs_uri, project, dataset, table_name, mode
        )
    except Exception as e:
        return {"error": f"GCP: {e}"}

    out: dict[str, Any] = {
        "table": result["table"],
        "gcs_uri": gcs_uri,
        "mode": mode,
        "hint": f"Use a BigQuery MCP/SQL client to query: SELECT * FROM `{result['table']}` LIMIT 100",
        "cache": cache_meta,
    }
    if result.get("rows") is not None:
        out["rows"] = result["rows"]
    return out


def _list_tables(project: str, dataset: str) -> list[dict[str, Any]]:
    from google.cloud import bigquery

    client = bigquery.Client(project=project)
    return [
        {
            "table": f"{project}.{dataset}.{t.table_id}",
            "type": t.table_type,
            "created": str(t.created) if t.created else None,
        }
        for t in client.list_tables(dataset)
    ]


async def list_bigquery_exports(project: str, dataset: str) -> dict[str, Any]:
    """List tables in a BigQuery dataset (the ones this server exported, and any others)."""
    if not gcp_available():
        return dict(_NOT_INSTALLED)
    try:
        tables = await asyncio.to_thread(_list_tables, project, dataset)
    except Exception as e:
        return {"error": f"GCP: {e}"}
    return {"dataset": f"{project}.{dataset}", "tables": tables, "count": len(tables)}


def _table_info(project: str, dataset: str, table: str) -> dict[str, Any]:
    from google.cloud import bigquery

    client = bigquery.Client(project=project)
    t = client.get_table(f"{project}.{dataset}.{table}")
    info: dict[str, Any] = {
        "table": f"{project}.{dataset}.{table}",
        "num_rows": t.num_rows,
        "schema": [{"name": f.name, "type": f.field_type} for f in t.schema],
    }
    ext = getattr(t, "external_data_configuration", None)
    if ext is not None:
        info["external_source_uris"] = list(ext.source_uris)
    return info


async def get_bigquery_table_info(project: str, dataset: str, table: str) -> dict[str, Any]:
    """Schema, row count and (for external tables) source URIs of a BigQuery table."""
    if not gcp_available():
        return dict(_NOT_INSTALLED)
    try:
        return await asyncio.to_thread(_table_info, project, dataset, table)
    except Exception as e:
        return {"error": f"GCP: {e}"}


def register_gcp_tools(mcp_instance: Any) -> bool:
    """Register the GCP tools on a FastMCP instance — only when the SDKs are
    installed, so the base install's tool surface is unchanged."""
    if not gcp_available():
        logger.info("GCP libraries not found — pipeline tools not registered")
        return False

    @mcp_instance.tool(
        annotations=ToolAnnotations(
            title="Load resource to BigQuery",
            readOnlyHint=False,
            destructiveHint=False,
            openWorldHint=True,
        )
    )
    async def load_resource_to_bigquery_tool(
        url: Annotated[
            str, Field(description="Direct URL to the file (CKAN resource 'url' field).")
        ],
        format: Annotated[
            str, Field(description="Format declared in CKAN: csv, tsv, xlsx, xls, json, ods.")
        ],
        project: Annotated[str, Field(description="GCP project id.")],
        dataset: Annotated[str, Field(description="BigQuery dataset id (must exist).")],
        table: Annotated[
            str | None,
            Field(description="BigQuery table name. Auto-derived from the URL when omitted."),
        ] = None,
        gcs_bucket: Annotated[
            str | None,
            Field(description=f"GCS bucket for the Parquet. Default: {_BUCKET_ENV} env var."),
        ] = None,
        mode: Annotated[
            Literal["external", "load"],
            Field(
                description="'external' (default): External Table over the Parquet in GCS, "
                "zero ingestion cost. 'load': copy into BigQuery storage (faster repeat queries)."
            ),
        ] = "external",
    ) -> dict:
        """Download a datos.gob.do resource and expose it as a BigQuery table.

        Pipeline: resource → local Parquet cache → GCS upload → BigQuery
        External Table (or Load Job). Pair with a BigQuery MCP for SQL over the
        result — JOINs across datasets that local DuckDB cannot do.
        """
        return await load_resource_to_bigquery(
            url=url,
            fmt=format,
            project=project,
            dataset=dataset,
            table=table,
            gcs_bucket=gcs_bucket,
            mode=mode,
        )

    @mcp_instance.tool(
        annotations=ToolAnnotations(
            title="List BigQuery exports", readOnlyHint=True, openWorldHint=True
        )
    )
    async def list_bigquery_exports_tool(
        project: Annotated[str, Field(description="GCP project id.")],
        dataset: Annotated[str, Field(description="BigQuery dataset id.")],
    ) -> dict:
        """List tables in a BigQuery dataset (exports made by this server included)."""
        return await list_bigquery_exports(project=project, dataset=dataset)

    @mcp_instance.tool(
        annotations=ToolAnnotations(
            title="Get BigQuery table info", readOnlyHint=True, openWorldHint=True
        )
    )
    async def get_bigquery_table_info_tool(
        project: Annotated[str, Field(description="GCP project id.")],
        dataset: Annotated[str, Field(description="BigQuery dataset id.")],
        table: Annotated[str, Field(description="BigQuery table name.")],
    ) -> dict:
        """Schema, row count and source URIs of a BigQuery table created by this server."""
        return await get_bigquery_table_info(project=project, dataset=dataset, table=table)

    return True

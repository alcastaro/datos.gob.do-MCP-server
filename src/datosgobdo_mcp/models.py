"""Pydantic response models for typed MCP output (outputSchema / structuredContent).

These wrap the dict returns of the data-producing tools so FastMCP can emit a
real `outputSchema` and `structuredContent`, letting hosts validate results.

Design:
    - Every model uses `extra="allow"` so dynamic keys (e.g. quantile p-values,
      JSON-preview variants) pass through into structuredContent even though they
      are not declared in the schema. Zero data loss vs. the raw dict.
    - Every data field is optional with a default, because each tool may instead
      return `{"error": ..., "hint": ...}` on failure. The schema therefore marks
      only `error`/`hint` as always-present-capable and nothing as required.
    - The internal analytics/ckan functions still return plain dicts; the server
      layer constructs these models from those dicts (`Model(**result_dict)`).

Modelled tools (data payloads): get_resource_schema, summarize_resource,
filter_resource, aggregate_resource, query_resource, quantiles_resource,
find_duplicates_resource, detect_outliers_resource, get_cache_stats,
clear_cache, download_resource_preview.

NOT modelled (navigational CKAN metadata, low validation value, variable nested
shape): search_datasets, get_dataset, get_resource, list_recent_datasets,
get_organization, list_organizations, list_groups, list_tags, autocomplete,
get_site_stats. These keep returning dict/list and emit no outputSchema.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class _Result(BaseModel):
    """Base: every tool result may carry an error + recovery hint instead of data."""

    model_config = ConfigDict(extra="allow")
    error: str | None = None
    hint: str | None = None


class _AnalyticsResult(_Result):
    """Base for DuckDB-backed tools: common provenance + cache metadata."""

    source_url: str | None = None
    format: str | None = None
    cache: dict[str, Any] | None = None


# ─── Schema / summarize (column-stat payloads) ────────────────────────────────


class SchemaColumn(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    type: str
    nullable: bool | None = None
    sample_values: list[Any] | None = None


class SchemaResult(_AnalyticsResult):
    row_count: int | None = None
    column_count: int | None = None
    columns: list[SchemaColumn] = []


class SummaryColumn(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    type: str
    non_null_count: int | None = None
    null_count: int | None = None
    distinct_count: int | None = None
    min: Any | None = None
    max: Any | None = None
    mean: float | None = None
    median: float | None = None
    top_values: list[dict[str, Any]] | None = None


class SummaryResult(_AnalyticsResult):
    row_count: int | None = None
    column_count: int | None = None
    columns: list[SummaryColumn] = []


class QuantileColumn(BaseModel):
    # Percentile keys (p25, p50, …) are dynamic → preserved via extra="allow".
    model_config = ConfigDict(extra="allow")
    name: str
    type: str
    non_null_count: int | None = None
    null_count: int | None = None
    min: Any | None = None
    max: Any | None = None
    mean: float | None = None


class QuantilesResult(_AnalyticsResult):
    row_count: int | None = None
    percentiles: list[float] | None = None
    columns: list[QuantileColumn] = []


# ─── Tabular payloads (columns = names, rows = arrays) ─────────────────────────


class FilterResult(_AnalyticsResult):
    matching_rows_total: int | None = None
    rows_returned: int | None = None
    columns: list[str] = []
    limit: int | None = None
    offset: int | None = None
    rows: list[list[Any]] = []


class AggregateResult(_AnalyticsResult):
    groups_returned: int | None = None
    columns: list[str] = []
    limit: int | None = None
    rows: list[list[Any]] = []


class QueryResult(_AnalyticsResult):
    sql_executed: str | None = None
    rows_returned: int | None = None
    columns: list[str] = []
    rows: list[list[Any]] = []


class DuplicatesResult(_AnalyticsResult):
    columns_checked: list[str] | None = None
    duplicate_groups_found: int | None = None
    groups_returned: int | None = None
    total_duplicate_rows: int | None = None
    columns: list[str] = []
    rows: list[list[Any]] = []


class OutliersResult(_AnalyticsResult):
    column: str | None = None
    method: str | None = None
    q1: float | None = None
    q3: float | None = None
    iqr: float | None = None
    lower_fence: float | None = None
    upper_fence: float | None = None
    outlier_count_estimate: int | None = None
    rows_returned: int | None = None
    columns: list[str] = []
    rows: list[list[Any]] = []


# ─── Cache management ──────────────────────────────────────────────────────────


class CacheStatsResult(_Result):
    cache_dir: str | None = None
    entries: int | None = None
    total_bytes: int | None = None
    max_bytes: int | None = None


class ClearCacheResult(_Result):
    removed_entries: int | None = None


class SaveCsvResult(_Result):
    path: str | None = None
    rows_written: int | None = None
    columns: list[str] | None = None
    bytes_written: int | None = None
    cache: dict[str, Any] | None = None


# ─── Resource preview (CSV/XLSX/JSON variants → extra="allow" covers them) ─────


class PreviewResult(_Result):
    format: str | None = None
    source_url: str | None = None
    columns: list[Any] | None = None
    total_rows_in_download: int | None = None
    rows_returned: int | None = None
    sample_mode: str | None = None
    bytes_downloaded: int | None = None
    download_truncated: bool | None = None
    rows: list[Any] | None = None

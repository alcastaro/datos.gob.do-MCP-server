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


def _strip_noise(node: Any) -> Any:
    """Drop the parts of a generated JSON Schema that tell a reader nothing.

    Every conversation pays for these 23 schemas before the user asks anything.
    Measured: 43,582 bytes, of which the prose descriptions are only 6,083 — the
    weight is in the schemas themselves, and a third of *that* is noise. Pydantic
    titles every field, so `non_null_count` arrives with `"title": "Non Null
    Count"`, and stamps `"default": null` on every optional one. The property key
    already carries the name, and an absent optional field is absent.

    Removing them costs no meaning and returns roughly 6 KB per conversation.
    The prose stays: the campaign's largest failure mode was malformed calls, and
    trimming the guidance that prevents them to save bytes would be a bad trade.
    """
    if isinstance(node, dict):
        return {
            k: _strip_noise(v)
            for k, v in node.items()
            if not (k == "title" or (k == "default" and v is None))
        }
    if isinstance(node, list):
        return [_strip_noise(v) for v in node]
    return node


class _Lean(BaseModel):
    """Base that emits its schema without the generated boilerplate."""

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema: Any, handler: Any) -> Any:
        return _strip_noise(handler(core_schema))


class _Result(_Lean):
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


class SchemaColumn(_Lean):
    model_config = ConfigDict(extra="allow")
    name: str
    type: str
    nullable: bool | None = None
    sample_values: list[Any] | None = None


class SchemaResult(_AnalyticsResult):
    row_count: int | None = None
    column_count: int | None = None
    columns: list[SchemaColumn] = []


class SummaryColumn(_Lean):
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


class QuantileColumn(_Lean):
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

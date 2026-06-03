# Changelog

All notable changes to this project are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [0.6.1] — 2026-06-03

### Fixed

- **`ensure_cached` crash on zero-byte download** — `UnboundLocalError: raw_ods` when
  the server returned an empty body. `raw_ods` was declared inside the `try` block after
  the zero-byte check; moved before `try` so the `finally` cleanup always works.

### Changed

- **Coverage floor raised 75% → 80%** (actual 83%).
- Added hermetic tests for XLSX, JSON, ODS, Latin-1 encoding, and zero-byte error path
  in `analytics.py`; 171 → 184 tests.

## [0.6.0] — 2026-06-03

### Added

- **Typed `outputSchema` / `structuredContent`** for the 12 data-producing tools
  (schema, summarize, filter, aggregate, query, quantiles, find_duplicates,
  detect_outliers, preview, save_query_to_csv, get_cache_stats, clear_cache).
  New `models.py` with Pydantic response models — hosts can now validate tool output.
  Models use `extra="allow"` so dynamic keys (quantile p-values, JSON-preview variants)
  pass through with zero data loss. Navigational CKAN-metadata tools keep dict returns.
- **`Tutorial.md` + `Tutorial_es.md`** — bilingual educational guide: how the server
  works, how to use it, and a step-by-step recipe for building your own MCP server.

### Fixed

- README tool count corrected to **23** (was "17" in EN / "12" in ES).

## [0.5.0] — 2026-06-03

### Added

- **`quantiles_resource`** — percentile distribution (p25/p50/p75/p90/p95/p99 by default) of numeric columns. Fills the gap `aggregate_resource` leaves (only exposes `median`).
- **`find_duplicates_resource`** — find rows duplicated on specified columns (or all columns), sorted by frequency. Essential for payroll and census data-quality checks.
- **`detect_outliers_resource`** — IQR method outlier detection on a single numeric column. Returns outlier rows sorted by distance from the median.
- **`save_query_to_csv`** — export any filter or SQL result to a local CSV file. Defaults to `~/Downloads/datosgobdo-exports/`; supports explicit `dest` (validated, no traversal, no system paths). `overwrite=False` by default.

### Fixed

- **Warm cache no longer issues a HEAD request** on every call. The cache index now stores URL→key mappings; warm-path reads skip the network entirely. Cached data survives a portal outage. `ensure_cached()` gains a `force_refresh=False` parameter for explicit invalidation.
- **ckan.py error model unified** — all public functions now return `{"error": ..., "hint": ...}` on failures instead of raising `RuntimeError`. The model can read the hint and try a recovery tool. Consistent with the `analytics.py` pattern.

### Tools count

19 → 23.

## [0.4.2] — 2026-06-03

### Added

- **ruff + mypy + pytest-cov** gates in CI and `pyproject.toml`. Coverage floor 75% (omitting the HTTP adapter layer covered by live tests).
- **Python 3.13** classifier and CI leg.
- **Dependabot** (pip + GitHub Actions, weekly).
- **CodeQL** SAST workflow (weekly + on push).
- **CONTRIBUTING.md** with dev setup, PR checklist, and security reporting pointer.
- **`pre-commit`** config (ruff lint+format + mypy) for local enforcement.

### Changed

- `mcp` dependency floor raised from `>=1.2.0` to `>=1.9.0` (tested minimum).
- Added `Changelog` and `Bug Tracker` URLs to `pyproject.toml`.

### Fixed

- `_new_con()` no longer runs `INSTALL httpfs/excel` on every call — extensions are bundled in DuckDB ≥1.0 and `LOAD` suffices. Eliminates the network round-trip and the silent-failure risk on cold starts.
- `USER_AGENT` unified to a single source-of-truth (`__init__.py`) across `ckan.py`, `download.py`, and `analytics.py` — previously drifted at `0.1` / `0.2` / `0.3`.
- Removed `_tool_count()` which accessed a private FastMCP attribute (`_tool_manager._tools`) brittle against version upgrades.
- mypy errors in `analytics.py`: `fetchone()` null-safety guards + `_build_agg_expr` / `_build_order_by` now validate `col` is `str` before passing to `_quote_ident`.

## [0.4.1] — 2026-06-02

### Security

- **`query_resource` sandbox (HIGH).** Model-supplied SQL could call DuckDB
  table functions (`read_text` / `read_csv` / `read_blob` / `glob`) to read
  arbitrary local files or reach the network — the keyword denylist did not
  cover them. The resource is now materialized into an in-memory table and the
  connection is locked down (`enable_external_access=false`,
  `lock_configuration=true`) before the query runs. Added adversarial tests
  proving local-file access is blocked while legitimate queries still work.
- Added `SECURITY.md` (disclosure process + threat model).

### Fixed

- **`get_resource_schema.sample_rows` had no effect.** The per-column distinct
  sample was hardcoded to `LIMIT 5`; the documented `sample_rows` parameter is
  now honored.

### Added

- **Tool annotations on all tools** (`title`, `readOnlyHint`, `openWorldHint`,
  and `destructiveHint` on `clear_cache`) — satisfies the Anthropic Directory
  review criteria and lets hosts auto-approve read-only calls.

## [0.4.0] — 2026-05-24

### Added

- Raw read-only SQL escape hatch (`query_resource`).
- ODS support (auto-converted to CSV on the cold path).
- pytest suite (now 139 hermetic tests) + hardened `_quote_ident` against SQL
  comments.

## [0.3.0]

### Added

- Parquet on-disk cache with LRU eviction (`cache.py`).
- Typed `aggregate_resource` and `filter_resource` (GROUP BY / WHERE without SQL).

## [0.2.0]

### Added

- DuckDB-backed `get_resource_schema` and `summarize_resource`.

## [0.1.0]

### Added

- Initial release: CKAN discovery, resource, catalog, and autocomplete tools.

[0.4.1]: https://github.com/alcastaro/datos.gob.do-MCP-server/releases/tag/v0.4.1
[0.4.0]: https://github.com/alcastaro/datos.gob.do-MCP-server/releases/tag/v0.4.0

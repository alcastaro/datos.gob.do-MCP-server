# Changelog

All notable changes to this project are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [0.7.1] — 2026-08-07

Install-breakage hotfix. Anyone who ran `uvx dominican-open-data-mcp` or
installed fresh after 2026-07-28 hit one of the two failures below.

### Fixed

- **`ModuleNotFoundError: No module named 'mcp.server.fastmcp'` on fresh
  installs.** The MCP Python SDK released **v2.0.0** on 2026-07-28 (the
  `2026-07-28` protocol revision), which renamed `FastMCP` to `MCPServer` and
  removed the old import path with no compatibility shim. Our dependency was
  pinned `mcp>=1.9.0` with no upper bound, so any fresh resolution pulled 2.x
  and the server failed at import. Now pinned `mcp>=1.9.0,<2`, with a
  regression test. Migration to the v2 SDK is tracked separately; the SDK v2
  serves older protocol revisions, so there is no client-side urgency.
  Workaround for anyone on an older release:
  `uvx --with "mcp<2" --from dominican-open-data-mcp datosgobdo-mcp`.
- **`uvx dominican-open-data-mcp` failed with "An executable named
  dominican-open-data-mcp is not provided by package".** The distribution
  shipped only the short `datosgobdo-mcp` console script, but the MCP Registry
  entry (`runtimeHint: uvx` + the PyPI identifier) implies a command matching
  the distribution name — which is also what third-party install pages print.
  A `dominican-open-data-mcp` alias entry point now exists; both names launch
  the same server. Pre-existing bug, unrelated to the SDK break.
- **`serverInfo.version` reported the MCP SDK version instead of the package
  version** (clients saw e.g. `1.27.1`). `FastMCP` accepts no `version`
  argument, so the low-level server fell back to the installed SDK version.

### Changed

- Test suite verified against both `mcp` 1.27.1 and 1.29.0 (316 tests, 88%
  coverage, no omits).

## [0.7.0] — 2026-06-10

### Added (hosted readiness, experimental)

- **`DATOSGOBDO_TRANSPORT=streamable-http`**: serve MCP over stateless HTTP
  (`DATOSGOBDO_HOST`/`DATOSGOBDO_PORT`). In hosted mode `save_query_to_csv` and
  `clear_cache` are disabled (server filesystem / shared cache) and
  `get_cache_stats` omits server paths.
- **DuckDB resource ceilings**: `DATOSGOBDO_DUCKDB_MEMORY` (default 2GB),
  `DATOSGOBDO_DUCKDB_THREADS` (default 4), and `DATOSGOBDO_QUERY_TIMEOUT`
  (wall-clock interrupt for free-form SQL; off by default locally).
- **Cache hardening**: atomic `_index.json` writes (tmp + rename), cross-process
  `flock` around finalize/eviction/clear (no-op on Windows), deterministic LRU
  tie-break.

### Added

- **SSRF guard (`netguard.py`)** wired into every resource download via an httpx
  request hook — validates the initial URL **and each redirect hop**. Default mode
  `public-only`: http/https only, every resolved address must be globally routable
  (cloud metadata `169.254.169.254`, loopback, RFC-1918, link-local, IPv6 ULA all
  blocked). `DATOSGOBDO_NETGUARD=strict|off`, `DATOSGOBDO_ALLOW_HOSTS` for
  operator-trusted hosts. Adversarial tests incl. redirect-to-private.
- **Optional GCP pipeline (`gcp.py`, `pip install 'dominican-open-data-mcp[gcp]'`)** —
  3 tools that register only when the google-cloud libraries are installed:
  `load_resource_to_bigquery` (Parquet cache → GCS → BigQuery External Table or
  Load Job), `list_bigquery_exports`, `get_bigquery_table_info`. Pairs with
  Google's BigQuery MCP: this server ingests, theirs queries. Base install keeps
  exactly 23 tools.
- Full-package coverage discipline: no coverage omits, floor 85% (actual ~88%),
  `ckan.py` and `server.py` at 100% via hermetic tests. macOS CI job added.

### Fixed

- `search_resources` interpolated the raw user query into CKAN's
  `resource_search` `name:{query}` — `:`/`"` are now sanitized out.
- `list_tags` / `autocomplete` no longer fail silently: degraded `[]` returns now
  log a warning.

## [0.6.2] — 2026-06-09

### Added

- **Version-drift guard** (`tests/test_version_sync.py`): CI now fails if
  `pyproject.toml`, `server.json`, `__init__.__version__` or `USER_AGENT` disagree.
- **Symlink hardening in `save_query_to_csv`**: final write uses `O_NOFOLLOW`, closing
  the TOCTOU window where a symlink swapped in after path validation (with
  `overwrite=True`) could redirect the write.

### Changed

- `download_resource_preview` ODS rejection now hints at the analytics tools (which DO
  support ODS) instead of suggesting a manual download.

### Fixed

- **`save_query_to_csv` rejected legitimate writes to the OS temp dir on macOS.** The
  `/private/var` system-path denylist entry also matched `/private/var/folders/…`, which
  is the macOS per-user temp dir (`$TMPDIR`). Writes there — including the entire pytest
  `tmp_path` suite — were blocked. The hermetic suite was therefore **red on macOS while
  green on Linux CI** (where temp lives in `/tmp`). Now paths under
  `tempfile.gettempdir()` are allowed before the denylist runs; real system subtrees such
  as `/private/var/db` stay blocked (regression test added).
- **`_quote_ident` allowed a trailing newline in column identifiers.** The allowlist
  regex was anchored with `^…$`; in Python `$` also matches just before a trailing
  newline, so `"col\n"` passed an allowlist meant to reject control characters. Re-anchored
  with `\A…\Z`. Embedded-newline and trailing-CR cases added to the test matrix.

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

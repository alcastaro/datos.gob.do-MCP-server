# Changelog

All notable changes to this project are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [0.7.7] — 2026-08-08

### Changed

- **`SUM` over a text column now says why and what to do instead.** The single
  largest remaining class of failure in the directed battery (23 of 487 calls):
  a spreadsheet mixes a footnote, a total or `"N/D"` into a numeric column, the
  whole column loads as text, and DuckDB answers that `sum(VARCHAR)` does not
  exist — true, and useless to a caller with no way to know the fix is a cast.
  The reply now names the cause and hands over a working `query_resource` query.
  Other DuckDB messages pass through unchanged.

## [0.7.6] — 2026-08-08

Found while scaling the protocol run from 129 to 500 datasets.

### Fixed

- **A header wrapped across two lines inside a quoted field killed the file.**
  That is legal CSV, and a live price series publishes one; DuckDB's sniffer
  refuses the whole resource over it. A `strict_mode=false` retry — paid only by
  files that already failed — reads it correctly: 153 columns instead of an
  error.
- **"IO Error: Failed to open zip for reading."** Portals answer a gated or
  moved download with a login page carrying the original filename and HTTP 200,
  and not every such page is shaped like the HTML the existing guard
  recognises. Zip-container formats (XLSX/XLSM/ODS) now check the magic bytes
  first, so the caller is told the portal served a web page instead of being
  handed a message that reads like a bug in this server.

## [0.7.5] — 2026-08-08

### Fixed

- **Eleven tools returned no `structuredContent` at all.** An unparameterised
  `-> dict` return annotation makes FastMCP skip the `outputSchema`, and without
  one the tool answers with text only. Every discovery and catalog tool was in
  that state — `search_datasets`, `get_dataset`, `get_resource`,
  `search_resources`, `list_recent_datasets`, `list_organizations`,
  `get_organization`, `list_groups`, `list_tags`, `autocomplete`,
  `get_site_stats` — which is the entire entry point of a conversation. The
  analytics tools were unaffected because they return Pydantic models. Caught by
  fetching catalog metadata through the protocol instead of through CKAN
  directly; a test now asserts all 23 tools declare an output schema.
- **Encoding detection regression from 0.7.4.** Letting chardet's
  low-confidence guess win outright was worse than the problem it fixed:
  Latin-1 bytes decode without error as macroman, cp1250, cp874 and even cp424,
  and chardet volunteers those at ~5% confidence, so `Año` came back as `AÒo`,
  `AŃO` or `A๑o`. The guess now only competes if it is a codepage this catalog
  could plausibly contain, and the scorer penalises Greek, Cyrillic, CJK,
  box-drawing and maths blocks — the characters a DOS-codepage misreading
  produces (`A±o`, `investigaci≤n`). Across the 37 non-UTF-8 files in the
  mirror, 37 now decode cleanly; before this pass, 8 did not.

## [0.7.4] — 2026-08-07

The second pass of the same protocol audit, this time driving the tools with
calls written by analyst agents that had only seen each file's schema — the
closest thing to how an assistant actually uses this server. 129 files, 487
calls. Two failure modes accounted for most of it, and neither was the data.

### Fixed

- **`group_by` and `columns` rejected `[{"col": "Año"}]`.** Three of the four
  list parameters on these tools (`filters`, `order_by`, `having`) take objects
  keyed by `col`, and one takes bare strings. Models generalise from the
  majority: **190 of 487 directed calls** were written the object way, and every
  one of them died in schema validation *before the tool ran*, so the caller got
  a Pydantic traceback instead of an answer. Both spellings are now accepted.
- **Column names were validated against a character class instead of being
  resolved against the file.** A header the publisher had mangled (`A¤o`) made
  every tool refuse the whole resource. Names supplied by the caller are now
  matched against the columns the open view actually has — case- and
  whitespace-insensitively, so `año` finds `AÑO` — and a name that matches
  nothing returns "Column not found, columns are: …" instead of a SQL error.
  Names that came from DuckDB's own `DESCRIBE` are escaped, never validated.
  Aggregation aliases, which are invented by the model and have nothing to
  match, keep the strict path.
- **`Año` reached users as `A¤o`.** These files are CP850/CP437 — the DOS
  codepages Excel still emits in Latin America, where `0xA4` is `ñ`. chardet
  identified them correctly but at ~5% confidence, under the 0.7 threshold, so
  the guess was discarded for a blind CP1252 fallback. Candidate decodings are
  now scored for characters that would be extraordinary in Spanish text and the
  cleanest one wins. Five files in the sample were affected.
- **Two structurally broken CSVs are now readable.** One is a semicolon file
  Excel padded with five empty comma columns, so commas were the most consistent
  separator and the real record became column one. The other had every line
  quoted as a single field. Both are detected — the table collapses to one
  usable field under the sniffed delimiter while another delimiter splits it
  into three or more — and rewritten before parsing. An 18,235-row book registry
  went from 1 unusable column to 12; a complaints series from 6 to 4.
- **`quantiles_resource` refused percentiles 0 and 1**, which are the minimum
  and maximum and which DuckDB computes without complaint.

## [0.7.3] — 2026-08-07

Findings from running every tool over the MCP protocol — a real stdio client
session, not in-process calls — against 129 files from the live catalog.
Measured over the same 1,121 calls before and after: success **91.5% → 94.0%**,
errors **51 → 10**, total response payload **4.69 MB → 1.29 MB**.

### Fixed

- **`get_resource_schema` returned up to 352 KB.** `sample_rows` defaulted to
  its own 1000 ceiling, so the tool the server tells the model to call *first*
  ("cheap reconnaissance step") was also the most expensive thing it could do —
  roughly 88k tokens of an assistant's context spent learning column names. The
  default is now 6 distinct values per column, enough to recognise what a column
  holds; the 1000 ceiling remains available on request. Largest reply in the
  same benchmark fell to **7.4 KB**.
- **`download_resource_preview` never used the cache.** It called the download
  path directly, so it re-fetched the file on every call: **20-25× slower than
  every other tool** (median 0.77 s against 0.03 s, worst 11.25 s) and a fresh
  request to a government portal each time an assistant glanced at a file it had
  already read. It now reads from the Parquet cache when the resource is already
  there. Median **0.77 s → 0.017 s**.
- **`download_resource_preview` refused ODS**, which is about a third of this
  catalog, while the analytics tools read the same files without trouble — 27 of
  129 resources failed for this reason alone. ODS now goes through the cached
  path. Success rate **79% → 100%**.
- **Column names were rejected for characters nobody can see.** A real header
  read `Cod.Capí\xadtulo`, where `\xad` is a soft hyphen. The name looks correct
  on screen, so the error was impossible to act on. Unicode format characters
  (soft hyphen, zero-width space, bidi marks) are now stripped rather than
  rejected.
- **`detect_outliers_resource` reported an error when a column had no spread.**
  "Which values are outliers?" has a correct answer there — none — and the
  column being flat is itself worth knowing. It returned an error on 13 of 113
  real columns (years, constants, small repeated sets), leaving the assistant
  with nothing to report. It now returns an empty result with an explanation.

## [0.7.2] — 2026-08-07

Robustness fixes found by running the analytics pipeline against a sample of
the real datos.gob.do catalog rather than against test fixtures. All three
defects were invisible to the hermetic suite because the fixtures are
well-formed and the real catalog is not.

### Fixed

- **Handled failures escaped as unhandled exceptions.** `NetGuardError` — most
  often raised for a resource whose host no longer resolves in DNS, which is
  common in a catalog spanning 266 institutions — was caught nowhere, so the
  MCP client received a protocol-level traceback instead of a readable error.
  The same applied to any failure occurring *after* a tool's `ensure_cached`
  call, such as identifier validation. Every analytics tool is now wrapped in
  a single error envelope covering `httpx.HTTPError`, `AnalyticsError`,
  `duckdb.Error`, `NetGuardError` and `OSError`; `download_resource_preview`
  handles `NetGuardError` too.
- **Real government column names were rejected as invalid identifiers.** The
  allowlist accepted only word characters, dots and spaces, so headers like
  `Sueldo Bruto (RD$)`, `% Abastecimiento de la Demanda`,
  `RANGO DE EDAD 60 - 70` and `FECHA DE REGISTRO / ADQUISICIÓN` failed
  validation and made the entire file unqueryable. The character class now
  covers the punctuation that actually appears in these files; the substring
  denylist (`--`, `/*`, `*/`, `;`), the control-character rejection and the
  double-quote escaping that provides the real protection are unchanged.
  Headers that wrap across spreadsheet lines are whitespace-normalized when
  the file is opened, and a single unusable column name now degrades that
  column's samples instead of failing the whole call.
- **A single ODS file could exhaust the machine's memory and hang the server
  indefinitely.** ODS was read with `odf.opendocument.load()`, which builds the
  entire document as a Python object tree. Measured on a real catalog file: a
  **0.70 MB spreadsheet peaked at 0.41 GB of RSS** — roughly 580x — and took
  8–12 s. Since the download cap is 100 MB, the worst case was tens of
  gigabytes; the sweep hit exactly that, reaching **9.3 GB RSS with a core
  pinned at 100% for over 15 minutes** before being killed. DuckDB's
  `memory_limit` does not apply (this is pure Python), and because the parse
  ran synchronously on the event loop, no timeout could fire — the timers
  themselves were blocked. ODS is about a third of this catalog, so this was
  not an edge case.

  `content.xml` is now parsed as a stream, keeping memory proportional to one
  row. On the same file: **0.4 s and 0.053 GB**, byte-identical CSV output
  (11,788 lines). Grid-padding repeat counts are dropped rather than expanded.
- **Blocking work moved off the event loop.** ODS transcoding, encoding
  detection and the DuckDB→Parquet conversion now run in worker threads
  (`asyncio.to_thread`), so the server keeps responding during a cold-path
  load and the query-timeout interrupt can actually fire. The Parquet
  conversion also goes through `_execute_guarded`, so
  `DATOSGOBDO_QUERY_TIMEOUT` now bounds ingestion, not just user SQL.
- **HTML error pages were parsed as data.** Several portals answer a dead or
  gated download link with a styled web page and **HTTP 200**. Read as CSV,
  such a page became a one-column table named `<!DOCTYPE html>` that the
  assistant would relay to the user as real data. Downloads are now checked
  for HTML markup before parsing, in both the analytics and preview paths, and
  rejected with an explanation. A wrong answer is worse than a failed one.

- **Spreadsheets were lost to a single stray cell.** Government workbooks put
  totals, footnotes or `#REF!` thousands of rows below the data, after DuckDB
  has already inferred `DOUBLE` from the top of the column; the load then
  failed outright. A failed typed read now retries with every column as text.
  Worse types beat no data — this recovers about 6% of the sampled catalog.
- **Some errors said nothing at all.** Several httpx timeout classes carry an
  empty message, so a real failure surfaced as `Could not load resource:` with
  nothing after the colon. Error text now falls back to the exception class
  name.

### Added

- `sweep/` (development only, not shipped): a catalog sweep harness that walks
  datos.gob.do through this server's own pipeline and records per-resource
  outcomes. It found every defect listed above.

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

# Changelog

All notable changes to this project are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

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

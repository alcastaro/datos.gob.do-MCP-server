# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.4.x   | ✅        |
| < 0.4   | ❌        |

Always run the latest release. Security fixes land on the newest minor only.

## Reporting a vulnerability

Please report security issues **privately** — do not open a public issue.

- Open a [GitHub private security advisory](https://github.com/alcastaro/datos.gob.do-MCP-server/security/advisories/new), or
- Email the maintainer (see the `authors` field in `pyproject.toml` / the GitHub profile).

Include: affected version, a reproduction, and the impact you observed. Expect
an acknowledgement within a few days. Coordinated disclosure is appreciated.

## Threat model

This is a **local stdio MCP server**. It runs on the user's machine with the
user's privileges and is driven by an LLM. The design assumptions:

- **Tool arguments are LLM-generated and may be influenced by untrusted data.**
  Datasets fetched from the portal can carry prompt-injection payloads. Any
  argument that reaches SQL or the filesystem is treated as hostile.
- **No write path to the portal.** Every portal-facing tool is read-only
  (annotated `readOnlyHint: true`). The only mutation is clearing the local
  Parquet cache (`clear_cache`, annotated `destructiveHint: true`).
- **The portal API and resource files are external.** Network-facing tools are
  annotated `openWorldHint: true`.

### Controls in place

- **Solr injection** — every user value entering a CKAN `fq` filter passes
  through `_escape_solr` / `_fq_term` (`ckan.py`).
- **DuckDB identifier injection** — all column identifiers go through
  `_quote_ident` (`analytics.py`): an allowlist regex **plus** a denylist of
  comment/terminator substrings (`--`, `/*`, `*/`, `;`), then double-quoting.
- **Raw SQL (`query_resource`)** — two layers:
  1. `_validate_sql` rejects anything that is not a single read-only
     `SELECT`/`WITH`, and blocks DDL/DML keywords.
  2. **Sandbox**: the resource is materialized into an in-memory table, then
     `enable_external_access=false` + `lock_configuration=true` are set before
     the user query runs. DuckDB table functions (`read_text`, `read_csv`,
     `read_blob`, `glob`, …) therefore cannot read local files or reach the
     network — closing the gap the keyword denylist alone would leave open.
- **Download caps** — remote fetches are byte-capped (5 MB preview / 100 MB
  analytics) and streamed, bounding memory and decompression-bomb exposure.

### Known limitations (tracked)

- **SSRF for remote/hosted deployments**: the local server fetches resource
  URLs as given. A hosted deployment must add a host allowlist and block
  private/link-local ranges. Tracked for the hosted (`v0.6`) milestone.

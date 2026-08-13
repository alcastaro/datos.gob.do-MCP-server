# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.13.x  | ✅        |
| < 0.13  | ❌        |

Always run the latest release. Security fixes land on the newest minor only.

**Two past releases fixed issues you should not stay behind:**

- **≤ 0.7.0 is broken on fresh installs**, not merely outdated: those releases pinned `mcp>=1.9.0` with no upper bound, and MCP Python SDK 2.0 removed the `mcp.server.fastmcp` import path.
- **≤ 0.7.10 had an unguarded HEAD request.** `analytics._head_metadata` probed a resource URL *before* the guarded download, so the SSRF guard and `strict` mode were both bypassed on that path. Demonstrated against a loopback service that returned its ETag. Fixed in 0.7.11.

## Reporting a vulnerability

Please report security issues **privately** — do not open a public issue.

- Open a [GitHub private security advisory](https://github.com/alcastaro/datos.gob.do-MCP-server/security/advisories/new), or
- Email the maintainer (see the `authors` field in `pyproject.toml` / the GitHub profile).

Include: affected version, a reproduction, and the impact you observed. Expect
an acknowledgement within a few days. Coordinated disclosure is appreciated.

## Threat model

This is a **local stdio MCP server** by default. It runs on the user's machine
with the user's privileges and is driven by an LLM. The design assumptions:

- **Tool arguments are LLM-generated and may be influenced by untrusted data.**
  Datasets fetched from the portal can carry prompt-injection payloads. Any
  argument that reaches SQL or the filesystem is treated as hostile.
- **Downloaded content is untrusted input.** Resources come from 273 distinct
  third-party hosts — ministry web servers, buckets, CDNs — not from
  `datos.gob.do` alone. A response body is parsed defensively: HTML served with
  HTTP 200 is rejected before parsing rather than read as a one-column table.
- **No write path to the portal.** Every portal-facing tool is read-only
  (annotated `readOnlyHint: true`). The only mutation is clearing the local
  Parquet cache (`clear_cache`, annotated `destructiveHint: true`).
- **The portal API and resource files are external.** Network-facing tools are
  annotated `openWorldHint: true`.
- **Annotations are hints, not enforcement.** Per the MCP specification, clients
  must treat tool annotations as untrusted unless the server is trusted. The
  guarantees below are enforced in code, not by the annotations.

### The three server primitives, security-wise

| Primitive | Exposure |
|---|---|
| **Tools** (24, +3 optional) | The whole attack surface. Everything below applies to them. |
| **Resources** (3 + 1 template) | Read-only reads of portal metadata, returned as JSON or Markdown. No filesystem access, no user-supplied path, no write path. The one templated URI takes a dataset id, which reaches only a CKAN metadata lookup. |
| **Prompts** (6) | Static text templates with arguments interpolated into the returned string. They perform no I/O and call no tool. A prompt argument is data the *user* supplied and the model then acts on — so it carries no privilege the user did not already have. |

### Controls in place

- **Solr injection** — every user value entering a CKAN `fq` filter passes
  through `_escape_solr` / `_fq_term` (`ckan.py`).
- **DuckDB identifier injection** — all column identifiers go through
  `_quote_ident` (`analytics.py`): an allowlist regex **plus** a denylist of
  comment/terminator substrings (`--`, `/*`, `*/`, `;`), then double-quoting.
  Control characters are rejected; invisible Unicode (soft hyphen) is stripped.
- **Raw SQL (`query_resource`)** — two layers:
  1. `_validate_sql` rejects anything that is not a single read-only
     `SELECT`/`WITH`, and blocks DDL/DML keywords.
  2. **Sandbox**: the resource is materialized into an in-memory table, then
     `enable_external_access=false` + `lock_configuration=true` are set before
     the user query runs. DuckDB table functions (`read_text`, `read_csv`,
     `read_blob`, `glob`, …) therefore cannot read local files or reach the
     network — closing the gap the keyword denylist alone would leave open.
- **Download caps** — remote fetches are byte-capped (5 MB preview / 100 MB
  analytics) and streamed, bounding memory and decompression-bomb exposure. A
  single value over DuckDB's 16 MB limit fails the parse rather than the process.
- **SSRF guard (`netguard.py`)** — every outbound resource request validates the
  URL *and each redirect hop* (httpx request hook). **This covers the metadata
  HEAD probe as well as the download**, since 0.7.11:
  - schemes restricted to http/https;
  - every address the hostname resolves to must be globally routable — cloud
    metadata (`169.254.169.254`), loopback, RFC-1918, link-local and IPv6 ULA
    ranges are blocked (`public-only`, the default mode);
  - `DATOSGOBDO_NETGUARD=strict` additionally restricts hosts to
    `datos.gob.do` / `*.datos.gob.do`; `off` disables the guard;
  - `DATOSGOBDO_ALLOW_HOSTS` (comma-separated, `*.` wildcards) names
    operator-trusted hosts — the escape hatch for forks pointing at other
    portals.
  The default is deliberately *not* a host allowlist: CKAN resources
  legitimately live on ministry sites, S3 buckets and CDNs.
- **Page→file resolution is bounded.** 37 catalog URLs answer with an HTML page
  instead of a file. When the server follows a link found in such a page, the
  followed URL passes the same SSRF guard, only same-origin/HTTP(S) data-file
  candidates are considered, and the response declares the substitution in
  `cache.resolved_from` rather than presenting it as the requested URL.
- **Fetch context headers are fixed, not impersonated.** Resource requests carry
  a stable `User-Agent` and `Sec-Fetch-*` set. This was isolated with controls:
  the User-Agent is not what site rules discriminate on, so **nothing here
  attempts to pass as a browser** or to evade a site's access decision. A 403 is
  reported as a 403.
- **Archive fallback cannot fabricate provenance.** The archived-copy path
  (`DATOSGOBDO_ARCHIVE_DIR`, off by default) always tries the origin first, and
  every archive-served answer carries `cache.provenance` with the capture date,
  `sha256`, licence and the reason the origin was not used.
- **Cache poisoning across parser versions** — the Parquet cache key includes
  the parser build (package version + DuckDB's, which decides column types), so
  an upgrade cannot serve types inferred by the previous build. The warm path
  (`get_by_url`) validates against a build stamp for the same reason.
- **Filesystem writes (`save_query_to_csv`)** — destination must end in
  `.csv`/`.tsv`, may not contain `..`, may not target system paths (OS temp
  dir excepted), and the final write uses `O_NOFOLLOW` so a symlink swapped in
  after validation is not followed.
- **Hosted mode drops local-filesystem tools.** With
  `DATOSGOBDO_TRANSPORT=streamable-http`, `save_query_to_csv` and `clear_cache`
  return a disabled result instead of executing, and cache statistics omit
  server paths.

### Known limitations (tracked)

- **DNS rebind window** — *not fixed as of 0.13.0*. The SSRF guard resolves DNS
  to validate, then httpx resolves again to connect; a fast-flux rebind between
  the two lookups is not blocked. Full mitigation (pinning the validated IP at
  the transport layer) remains tracked for the hosted milestone. Exposure is
  bounded by what a request can do: an unauthenticated GET whose body is then
  parsed as tabular data.
- **Multi-tenant hosting** — partially addressed. `save_query_to_csv` and
  `clear_cache` are disabled under the hosted transport, but the Parquet cache is
  still shared process-wide and the transport itself has not been exercised under
  real concurrent load. Treat hosted mode as experimental and do not expose it
  to untrusted tenants.
- **Prompt injection via dataset content is not solved, and cannot be by this
  server alone.** A downloaded file can contain text aimed at the model reading
  it. The mitigations here are architectural rather than a filter: no write path
  to the portal, no credentials to steal, sandboxed SQL, a filesystem write that
  is path-validated and requires an explicit destination, and responses that
  declare their own provenance so a human can check them. Keep a human in the
  loop for anything consequential, as the MCP specification recommends.
- **GCP pipeline tools are preview.** The three optional BigQuery/GCS tools have
  not been exercised against a live project and are outside the stability
  promise; they write to *your* cloud project when used.

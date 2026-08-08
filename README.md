<!-- mcp-name: io.github.alcastaro/datos.gob.do-MCP-server -->

**[English](README.md) · [Español](README.es.md)**

---

# datosgobdo-mcp

**A [Model Context Protocol](https://modelcontextprotocol.io) server that exposes the Dominican Republic's open government data ([datos.gob.do](https://datos.gob.do)) as tools consumable by any AI assistant.**

It turns the official Dominican open-data portal into a native integration for Claude Desktop, Claude Code, Cursor, ChatGPT Desktop or any MCP-compatible client. The model can search, read, analyze, and preview the 1,053+ datasets published by the country's 266 government institutions, all from within a conversation.

> **Official source.** The canonical repository is
> [`alcastaro/datos.gob.do-MCP-server`](https://github.com/alcastaro/datos.gob.do-MCP-server).
> The only official distributions are the PyPI package
> [`dominican-open-data-mcp`](https://pypi.org/project/dominican-open-data-mcp/)
> and the MCP Registry entry `io.github.alcastaro/datos.gob.do-MCP-server`.
> Copies published elsewhere are not maintained by the author and may be
> outdated or modified — verify against this repository before installing.

> 📚 **New here?** Read the [Tutorial](Tutorial.md) — how the server works, how to
> use it, and how to build your own MCP server like it. ([Español](Tutorial_es.md))

---

## What problem does it solve?

[datos.gob.do](https://datos.gob.do) publishes thousands of CSV, XLSX, and JSON files with public data: payrolls, budgets, crime statistics, health indicators, electoral data, and more. Today that information is only accessible to people who know how to navigate the CKAN portal and download files manually.

This MCP closes that gap. Anyone can ask their assistant:

- *"How much does the Judicial Branch spend on salaries?"*
- *"Compare FONDOMARENA's approved vs. executed budget over the last three years."*
- *"List the 10 institutions that publish the most data."*
- *"What columns does the Ministry of Interior's vehicle-theft dataset have?"*

…and the model — without the user having to write code, navigate URLs, or download files — runs the actual queries against the portal, downloads the data, parses it, and analyzes it.

## Who is it for?

- **Data journalists** wanting to explore official sources without writing scrapers.
- **Researchers and academics** needing programmatic access to Dominican government data.
- **Transparency activists and civil society** monitoring budget execution, procurement, and public administration.
- **Developers and data scientists** prototyping dashboards or analyses on public data.
- **Public officials** wanting to query what their own (or other) institutions already publish.
- **Anyone with civic curiosity** about how the government operates.

## What is MCP?

[Model Context Protocol](https://modelcontextprotocol.io) is an open standard (created by Anthropic, adopted by OpenAI and others) that lets language models connect securely to external data sources and tools. An "MCP server" exposes a collection of typed functions; the model decides when to invoke them, with what arguments, and how to combine the results.

This project is an MCP server specialized in `datos.gob.do`.

## What is datos.gob.do?

The official open-data portal of the Dominican government, operated by OGTIC (the country's IT and communications office). It runs on **CKAN 2.11.3**, the same open-data software used by portals like data.gov (USA), data.gov.uk, and many other Latin American governments.

As of May 2026 it contains approximately:

- **1,053 datasets** published
- **266 organizations** publishing (ministries, municipalities, autonomous agencies, etc.)
- **11 thematic categories** (Economy, Health, Education, Public Management…)
- **852 tags**

Each dataset bundles one or more "resources" (downloadable files) in formats such as CSV, XLSX, ODS, PDF, or JSON.

This MCP is inspired by [`datagouv-mcp`](https://github.com/datagouv/datagouv-mcp) (France), but datos.gob.do runs a different platform (CKAN, not udata), so the implementation is its own.

---

## Tools exposed

23 typed functions, grouped into five categories. The data-producing tools
(analytics + preview + cache) return typed `outputSchema` / `structuredContent`,
so MCP hosts can validate results; navigational metadata tools return JSON.

### Discovery

| Tool | What it does |
|---|---|
| `search_datasets` | Search datasets by keyword, organization, tag, or group. Combinable filters, pagination. |
| `get_dataset` | Return full metadata for a dataset: title, description, license, author, and the complete list of its resources with direct download URLs. |
| `list_recent_datasets` | Datasets sorted by most-recent modification. Useful for monitoring portal updates. |
| `get_site_stats` | Portal-wide counts (totals of datasets, organizations, groups, tags). |

### Resources (files)

| Tool | What it does |
|---|---|
| `get_resource` | Metadata for a single resource (URL, format, size, date). |
| `search_resources` | Search resources by name. |
| `download_resource_preview` | Download a file and return N rows. CSV, TSV, XLSX, XLS, JSON. 5 MB cap. Sample mode: head / tail / random. |

### Analytics (v0.2+)

DuckDB-backed analytics over a persistent Parquet cache. First call per resource downloads + caches (up to 100 MB). Subsequent calls are sub-second.

| Tool | What it does |
|---|---|
| `get_resource_schema` | Column names, inferred types, sample values per column. Cheap reconnaissance step before any aggregation. |
| `summarize_resource` | Auto-generated profile: row count, per-column nulls/distinct, min/max/mean on numerics, top-N values on categoricals. |
| `filter_resource` | Typed WHERE / SELECT / ORDER BY / LIMIT. Ops: `=`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `not_in`, `contains`, `starts_with`, `ends_with`, `is_null`, `is_not_null`. |
| `aggregate_resource` | Typed GROUP BY + aggregations + HAVING + ORDER BY. Fns: `count`, `count_distinct`, `sum`, `avg`, `mean`, `median`, `min`, `max`, `stddev`, `variance`. |
| `query_resource` | Power-user escape hatch: read-only SQL against table `data`. SELECT/WITH only; DDL/DML/COPY/PRAGMA/ATTACH/LOAD rejected. Sandboxed — the resource is materialized in memory with external access disabled, so table functions cannot read local files or reach the network. |
| `quantiles_resource` | Percentile distribution (p25/p50/p75/p90/p95/p99) of numeric columns. Use before `aggregate_resource` for statistical profiling. |
| `find_duplicates_resource` | Find rows duplicated on specified columns (or all columns). Essential for payroll and census data-quality checks. |
| `detect_outliers_resource` | Find rows outside the IQR fence on a numeric column. Returns rows sorted by distance from median. |
| `save_query_to_csv` | Write a filter or SQL result to a local CSV file. Default destination: `~/Downloads/datosgobdo-exports/`. |
| `get_cache_stats` | On-disk Parquet cache stats. |
| `clear_cache` | Wipe the local Parquet cache. |

### Catalog

| Tool | What it does |
|---|---|
| `list_organizations` | All publishing institutions, with a dataset count per institution. |
| `get_organization` | Detail for a single institution (description, dataset count, URL). |
| `list_groups` | Thematic categories with dataset counts. |
| `list_tags` | Available tags, optionally filtered by prefix. |

### Autocomplete

| Tool | What it does |
|---|---|
| `autocomplete` | Resolve partial names for datasets, organizations, groups, or tags. Useful when the user only gives a partial name. |

### GCP pipeline (optional — `pip install 'dominican-open-data-mcp[gcp]'`)

Three extra tools register automatically when the google-cloud libraries are
installed. They turn the server into the *ingestion* half of a BigQuery
pipeline: discover datasets here, load them to BigQuery, then query them with
Google's official BigQuery MCP (cross-dataset JOINs local DuckDB cannot do).

| Tool | What it does |
|---|---|
| `load_resource_to_bigquery` | Resource → Parquet cache → GCS upload → BigQuery External Table (default, zero-ETL) or Load Job. |
| `list_bigquery_exports` | List tables in a BigQuery dataset. |
| `get_bigquery_table_info` | Schema, row count and source URIs of a table. |

Set `DATOSGOBDO_GCS_BUCKET` to avoid passing the bucket on every call.

### Security: outbound download guard

Every resource download is validated by an SSRF guard (initial URL **and**
each redirect hop): http/https only, and every address the host resolves to
must be publicly routable (cloud metadata, loopback and private ranges are
blocked). Environment knobs:

| Variable | Values | Meaning |
|---|---|---|
| `DATOSGOBDO_NETGUARD` | `public-only` (default) / `strict` / `off` | `strict` restricts hosts to `datos.gob.do`; `off` disables the guard. |
| `DATOSGOBDO_ALLOW_HOSTS` | comma-separated, `*.` wildcards | Operator-trusted hosts (e.g. forks pointing at another CKAN portal). |

### Archived copies (optional)

Government links rot. A census of the whole catalog on 2026-08-08 found 15
resource URLs already dead and 99 institutions whose sites had grown rules that
refuse programmatic access, so a figure you cite today may be uncheckable next
year.

Point `DATOSGOBDO_ARCHIVE_DIR` at a directory holding a `manifest.json` and its
Parquet files, and when a portal cannot be reached the server answers from the
archived copy instead. It is off by default, the portal is always tried first,
and **the reply always says so** — `cache.provenance` carries the capture date,
the `sha256`, the licence and why the origin was not used. A tool that quietly
returned yesterday's copy as though it were today's would stop being useful for
an audit.

An archive only holds what could be downloaded, so it does not contain the
resources a portal refuses. That is the natural assumption and it is wrong.

| Variable | Default | Meaning |
|---|---|---|
| `DATOSGOBDO_ARCHIVE_DIR` | unset (off) | Directory with `manifest.json` + Parquet copies to fall back on. |

### Hosted mode (experimental)

`DATOSGOBDO_TRANSPORT=streamable-http` serves MCP over HTTP (stateless, for
horizontal scaling) instead of stdio. In this mode `save_query_to_csv` and
`clear_cache` are disabled (they touch the server's filesystem / shared cache)
and cache stats omit server paths.

| Variable | Default | Meaning |
|---|---|---|
| `DATOSGOBDO_TRANSPORT` | `stdio` | `streamable-http` for hosted deployments. |
| `DATOSGOBDO_HOST` / `DATOSGOBDO_PORT` | `127.0.0.1` / `8000` | HTTP bind address. |
| `DATOSGOBDO_DUCKDB_MEMORY` | `2GB` | DuckDB memory ceiling per connection. |
| `DATOSGOBDO_DUCKDB_THREADS` | `4` | DuckDB thread cap. |
| `DATOSGOBDO_QUERY_TIMEOUT` | `0` (off) | Wall-clock seconds before a DuckDB run is interrupted. Covers both `query_resource` SQL and the conversion of a freshly downloaded file into the Parquet cache. |

---

## Installation and configuration

### Option A — Via `uvx` from PyPI (recommended)

Package: [`dominican-open-data-mcp`](https://pypi.org/project/dominican-open-data-mcp/).

```bash
uvx dominican-open-data-mcp
```

The package also ships a short binary, `datosgobdo-mcp` — both commands launch the same server:

```bash
uvx --from dominican-open-data-mcp datosgobdo-mcp
```

`uvx` downloads the package, creates an isolated venv, and runs the server. First run takes a few seconds; subsequent runs are instant.

> **Upgrading from ≤ 0.7.0?** Those releases pinned `mcp>=1.9.0` with no upper bound, and the MCP Python SDK 2.0 (released 2026-07-28) removed the `mcp.server.fastmcp` import path — so a fresh install fails with `ModuleNotFoundError`. Install 0.7.1 or later, or pin the SDK yourself: `uvx --with "mcp<2" --from dominican-open-data-mcp datosgobdo-mcp`.

### Option B — Via `uvx` from GitHub (latest dev version)

```bash
uvx --from git+https://github.com/alcastaro/datos.gob.do-MCP-server.git datosgobdo-mcp
```

Prerequisite: [`uv`](https://docs.astral.sh/uv/) installed. On macOS:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Option C — Local clone (for development)

```bash
git clone https://github.com/alcastaro/datos.gob.do-MCP-server.git
cd datos.gob.do-MCP-server
uv sync
uv run datosgobdo-mcp   # starts the server on stdio (Ctrl+C to exit)
```

> **macOS note:** avoid cloning inside `~/Library/CloudStorage/GoogleDrive-*` or similar paths. macOS blocks executing binaries from cloud-synced paths (TCC restriction). Use `~/code/` or equivalent.

### Claude Desktop configuration

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "datosgobdo": {
      "command": "/Users/YOUR_USERNAME/.local/bin/uvx",
      "args": ["dominican-open-data-mcp"]
    }
  }
}
```

Use the absolute path to `uvx` — Claude Desktop does not inherit your shell `PATH`. To track the development version instead, replace the args with `["--from", "git+https://github.com/alcastaro/datos.gob.do-MCP-server.git", "datosgobdo-mcp"]`.

Restart Claude Desktop completely (Cmd+Q, not just closing the window). Settings → Developer → Local MCP servers should show `datosgobdo` in **running** state.

### Claude Code configuration

```bash
claude mcp add datosgobdo -- uvx dominican-open-data-mcp
```

Development version: `claude mcp add datosgobdo -- uvx --from git+https://github.com/alcastaro/datos.gob.do-MCP-server.git datosgobdo-mcp`

### Cursor / other clients

Same principle: register `uvx` as the command with `--from git+... datosgobdo-mcp` as args. Consult each client's docs for the location of its configuration file.

---

## Usage examples

Once configured, you can ask the model:

### Basic exploration

> *Use the datosgobdo MCP and tell me how many datasets are on the datos.gob.do portal.*

→ Invokes `get_site_stats`. Reply: 1,053 datasets, 266 organizations.

### Search with analysis

> *Find the 5 most relevant datasets about budget on datos.gob.do and summarize which institution publishes each one.*

→ Invokes `search_datasets(query="presupuesto", limit=5)` and the model writes the summary.

### Name resolution + detail

> *Find the slug for the Ministry of Finance and tell me how many datasets it has published.*

→ `autocomplete(kind="organization", query="hacienda")` → `get_organization(id="ministerio-de-hacienda")`.

### Real data analysis

> *Show me the first 20 rows of the Judicial Branch budget CSV and tell me the three largest line items.*

→ `search_datasets(query="poder judicial")` → `get_dataset("presupuesto-poder-judicial")` → `download_resource_preview(url=..., format="csv", rows=20)` → the model identifies the largest items.

### Big-file analytics (v0.2+)

> *How many active employees are there at the Ministry of Agriculture in April 2026, broken down by employment status?*

The Agricultura nómina CSV has 826,000 rows and 94 MB — too big for the preview tool. The analytics workflow:

→ `search_datasets(query="nomina agricultura")` → `get_dataset(...)` → `get_resource_schema(url, "csv")` to see columns (Nombre, Departamento, Función, Estatus, Sueldo Bruto, Mes, Año) → `aggregate_resource(...)` with `group_by=["Estatus"]`, filters on `Año=2026, Mes='Abril'`, and `count_distinct` on `Nombre`.

Result: 6 status types, total ~8,915 employees. Cold first call: ~14 s (download + Parquet conversion). Subsequent calls on the same file: <0.5 s (cache hit).

### Monitoring

> *List the 10 most recently updated datasets on the portal.*

→ `list_recent_datasets(limit=10)`.

---

## Architecture

```
src/datosgobdo_mcp/
  server.py        FastMCP server + tool definitions (Pydantic typed)
  ckan.py          CKAN client: requests, Solr escaping, formatters
  preview.py       Capped file download + parsers for CSV/XLSX/JSON
```

### Design decisions

- **FastMCP instead of the low-level SDK**: tools are functions decorated with `@mcp.tool()` and typed via Pydantic. Less boilerplate, automatic argument validation.
- **Reused `httpx.AsyncClient`**: a single persistent connection, no TCP-handshake overhead per request.
- **Solr escaping**: CKAN `fq` filters use Solr/Lucene syntax. User-supplied values go through `_escape_solr()`, which escapes the 13 reserved characters (`+ - & | ! ( ) { } [ ] ^ " ~ * ? : \ /`). Without this, a tag containing a quote would break the query.
- **Defensive truncation**: long descriptions (some institutions publish 5+ KB of text per organization) are truncated to 300 chars in list responses. Without this, a single call could burn thousands of tokens of model context.
- **`list_recent_datasets` reoriented**: CKAN's API exposes `recently_changed_packages_activity_list`, but it returns "activities" with raw, un-hydrated metadata — the model would receive `{object_id: "uuid", activity_type: "changed package"}` with no way to know which dataset it refers to. We use `package_search?sort=metadata_modified+desc` to return already-formatted datasets in a single call.
- **DataStore not available**: the datos.gob.do portal does not have the DataStore extension installed, so there is no `datastore_search` endpoint or SQL queries against resource contents. The workaround is `download_resource_preview`: we download the file (5 MB cap) and parse it client-side with `csv` (stdlib) or `openpyxl`. Enough for the model to understand the structure.
- **Encoding fallback**: many published files are in CP1252 or Latin-1 (not UTF-8). The parser tries UTF-8 → UTF-8-sig → Latin-1 → CP1252 → UTF-8 with `errors=replace`.
- **stderr logging**: per the [MCP debugging guide](https://modelcontextprotocol.io/docs/tools/debugging), stdio servers must never write to stdout (it breaks the protocol). All logs go to stderr and are captured by the client in `~/Library/Logs/Claude/mcp-server-datosgobdo.log` (macOS).

### Technical stack

- [`mcp`](https://pypi.org/project/mcp/) — Anthropic's official Python SDK (FastMCP)
- [`httpx`](https://www.python-httpx.org/) — async HTTP client
- [`openpyxl`](https://openpyxl.readthedocs.io/) — read-only streaming XLSX reader
- `csv`, `json` — stdlib for other formats

---

## Known limitations

- **Preview tool limited to 5 MB** (the portal's largest files have hundreds of MB). For larger files use the analytics tools (`get_resource_schema`, `summarize_resource`, `filter_resource`, `aggregate_resource`, `query_resource`) which raise the cap to 100 MB and parse via DuckDB.
- **PDF not supported**: PDF files are exposed via their direct download URL only.
- **Read-only**: the MCP does not write to the portal (no authentication, no `package_create`, `resource_create` endpoints, etc.). By design.
- **XLSX with non-header preamble rows**: some published XLSX files have title rows above the actual header. DuckDB's `read_xlsx` 1.x has no skip-rows option, so the auto-detected schema is garbled for those files. Workaround: use `download_resource_preview` to inspect, then `query_resource` with explicit column projections.
- **Exotic encodings**: chardet fallback handles UTF-8 / UTF-8-sig / Latin-1 / CP1252 transparently; files with truly unusual encoding may still show replacement characters.

---

## Development

### Local setup

```bash
git clone https://github.com/alcastaro/datos.gob.do-MCP-server.git
cd datos.gob.do-MCP-server
uv sync
```

### Test with the MCP Inspector

[MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector) is the official tool for testing MCP servers in isolation:

```bash
npx @modelcontextprotocol/inspector uv run datosgobdo-mcp
```

Opens `http://localhost:6274` with a form builder to invoke tools manually and see raw request/response JSON.

### Logs

In Claude Desktop (macOS): `tail -f ~/Library/Logs/Claude/mcp-server-datosgobdo.log`

The server logs to stderr:
- Startup (endpoint, number of registered tools)
- Fatal errors with full traceback
- Shutdown

### Iteration

When you edit code:

1. Commit + push to `main` on GitHub.
2. Clear the `uvx` cache to force a refresh: `uv cache clean dominican-open-data-mcp` (the cache is keyed on the distribution name, not the binary name).
3. Restart the MCP client.

For faster iteration, configure the client to point to your local clone instead of the GitHub repo: `command: /path/to/clone/.venv/bin/datosgobdo-mcp`.

### Manual tests against the live API

```bash
uv run python -c "
import asyncio
from datosgobdo_mcp import ckan
print(asyncio.run(ckan.get_site_stats()))
asyncio.run(ckan.close_client())
"
```

---

## Contributing

Pull requests welcome. Obvious areas for improvement:

- Automated tests with `pytest-httpx` (mocking CKAN).
- `summarize_csv` tool with aggregate statistics (count, min, max, distinct values per column).
- Preview support for ODS and Parquet.
- Local cache of frequent responses (organizations, groups, tags change rarely).
- `find_dataset_about` tool that combines `autocomplete` + `search_datasets` with semantic ranking.

---

## Credits

Developed by **Alberto Castillo Aroca** ([@alcastaro](https://github.com/alcastaro)) with contributions from **Juana Casique** ([@juanacasique](https://github.com/juanacasique)).

Data published by the institutions of the Dominican State via [datos.gob.do](https://datos.gob.do), a portal operated by OGTIC.

Inspired by [`datagouv-mcp`](https://github.com/datagouv/datagouv-mcp) (Etalab, Government of France).

## License

MIT. See [LICENSE](LICENSE) if present, otherwise assume standard MIT terms.

Data accessed through this MCP is subject to the license under which each Dominican institution publishes it on datos.gob.do (typically **Open Data Commons Open Database License — ODbL**).

<!-- mcp-name: io.github.alcastaro/datos.gob.do-MCP-server -->

**[English](Tutorial.md) · [Español](Tutorial_es.md)**

---

# Tutorial — How `datosgobdo-mcp` works, and how to build one like it

This is a teaching document. Part 1 shows you how to **use** the server. Part 2
explains **how it's built**, module by module. Part 3 is a step-by-step recipe for
**building your own** MCP server over an open-data portal (or any HTTP API).

> The canonical repo is [`alcastaro/datos.gob.do-MCP-server`](https://github.com/alcastaro/datos.gob.do-MCP-server).
> Everything below references real code in that repo — open it alongside this tutorial.

---

## 0. The one-paragraph mental model

An **MCP server** is a small program that exposes *typed functions* (called **tools**)
to an AI assistant. The assistant decides when to call them, with what arguments, and
how to combine the results. This server's tools wrap the Dominican Republic's open-data
portal ([datos.gob.do](https://datos.gob.do), which runs **CKAN**). It holds **no data
of its own** — every call fetches live from the government portal, analyzes it locally
with **DuckDB**, and returns a slice to the model.

---

## Part 1 — Using the server

### 1.1 Install

```bash
# Any MCP client (Claude Desktop, Claude Code, Cursor, …) runs it via uvx:
uvx --from dominican-open-data-mcp datosgobdo-mcp
```

Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "datosgobdo": {
      "command": "uvx",
      "args": ["--from", "dominican-open-data-mcp", "datosgobdo-mcp"]
    }
  }
}
```

Restart the client. The 23 tools appear automatically.

### 1.2 The five tool categories

| Category | Tools | What they're for |
|---|---|---|
| **Discovery** | `search_datasets`, `get_dataset`, `list_recent_datasets`, `get_site_stats` | Find datasets |
| **Resources** | `get_resource`, `search_resources`, `download_resource_preview` | Inspect individual files |
| **Analytics** | `get_resource_schema`, `summarize_resource`, `filter_resource`, `aggregate_resource`, `query_resource`, `quantiles_resource`, `find_duplicates_resource`, `detect_outliers_resource`, `save_query_to_csv`, `get_cache_stats`, `clear_cache` | Query the actual data |
| **Catalog** | `list_organizations`, `get_organization`, `list_groups`, `list_tags` | Browse the institution/topic catalog |
| **Autocomplete** | `autocomplete` | Resolve partial names → exact slugs |

### 1.3 The typical workflow (and why it's ordered that way)

A good analytical conversation walks *down* a funnel, spending the least context first:

```
search_datasets        →  find the dataset            (cheap: metadata only)
get_dataset            →  get its resource URLs        (cheap: metadata only)
get_resource_schema    →  see columns + types          (downloads + caches once)
summarize_resource     →  per-column stats             (no raw rows in context)
aggregate_resource     →  the actual answer            (GROUP BY, no SQL needed)
filter_resource        →  pull specific records        (when you need rows)
save_query_to_csv      →  export for Excel             (the endpoint)
```

The model is taught (via tool descriptions) to do reconnaissance before pulling rows,
so it never floods its own context window with a 800,000-row payroll file.

### 1.4 Example: "How many employees by status, April 2026?"

The model translates that into a single `aggregate_resource` call — **no SQL**:

```json
{
  "url": "https://datos.gob.do/.../nomina.csv",
  "format": "csv",
  "aggregations": [{"col": null, "fn": "count", "alias": "empleados"}],
  "group_by": ["Estatus"],
  "filters": [
    {"col": "Año", "op": "=", "val": 2026},
    {"col": "Mes", "op": "=", "val": "Abril"}
  ],
  "order_by": [{"col": "empleados", "dir": "desc"}]
}
```

First call downloads the file once (capped at 100 MB) and caches it as Parquet.
Every later call against the same URL is sub-second.

### 1.5 The escape hatch: `query_resource`

When the typed tools don't fit, the model can write raw read-only SQL against a table
named `data`:

```sql
SELECT Estatus, COUNT(*) c FROM data WHERE Año=2026 GROUP BY Estatus ORDER BY c DESC
```

This is **sandboxed** (see §2.5) — it physically cannot read your local files or hit
the network, even though it's free-form SQL.

---

## Part 2 — How it's built

### 2.1 The shape: local stdio server

This is a **local stdio MCP server**: it runs on the user's machine, launched by the
client, communicating over stdin/stdout. The single most important rule:

> **Never `print()` to stdout.** stdout is the MCP protocol channel. All logs go to
> stderr. (See `server.py` — `logging.basicConfig(stream=sys.stderr, ...)`.)

### 2.2 Module map

```
src/datosgobdo_mcp/
├── server.py     FastMCP entry — 23 @mcp.tool decorators (the public surface)
├── ckan.py       Async CKAN HTTP client + Solr-escaping + JSON formatters
├── download.py   Capped streaming download + encoding detection
├── preview.py    CSV/TSV/XLSX/JSON parsers for the preview tool
├── analytics.py  DuckDB engine: schema/summarize/filter/aggregate/query/…
├── cache.py      Parquet-on-disk cache with LRU eviction + URL→key lookup
└── models.py     Pydantic response models → typed outputSchema
```

Each file has **one responsibility**. `server.py` is a thin layer of tool decorators
that delegate to the real logic — so the tools stay readable and the logic stays testable.

### 2.3 The tool decorator pattern (FastMCP)

A tool is a decorated, type-annotated function. The annotations *are* the schema the
model sees:

```python
@mcp.tool(annotations=_ro("Search datasets"))   # title + readOnlyHint + openWorldHint
async def search_datasets(
    query: Annotated[str | None, Field(description="Free-text search term…")] = None,
    limit: Annotated[int, Field(description="Results (1-50)", ge=1, le=50)] = 10,
) -> dict:
    """Search datasets in datos.gob.do. Filters by keyword, org, tag, or group."""
    return await ckan.search_datasets(query=query, limit=limit)
```

Three things the model reads: the **docstring** (what it does), each parameter's
**`Field(description=…)`** with constraints (`ge`/`le`/enum), and the **annotations**
(`readOnlyHint` lets a host auto-approve; `destructiveHint` triggers a confirm dialog).

### 2.4 Why DuckDB + Parquet (the analytics engine)

datos.gob.do has **no DataStore** — there's no server-side SQL. So the server downloads
the file once and runs analytics locally:

```
download → transcode to UTF-8 → DuckDB writes Parquet (ZSTD) to ~/.cache → query
```

DuckDB reads CSV/XLSX/JSON natively and runs full SQL. Parquet caching means the
expensive download+parse happens once; repeat queries hit a columnar file in
milliseconds. The cache is keyed by **URL + ETag/Last-Modified**, so it auto-invalidates
when the portal updates a file (`cache.py: build_cache_key`).

### 2.5 Security: the two injection surfaces

A server that takes model-generated input into SQL and HTTP has two attack surfaces.
Both are closed deliberately:

**(a) Solr injection** (CKAN search filters). Every user value entering a CKAN `fq`
filter passes through `_escape_solr` / `_fq_term` in `ckan.py`. Never interpolate raw.

**(b) SQL injection + local-file exfiltration** (`query_resource`). DuckDB's file access
lives in *table functions* (`read_text`, `read_csv`, `glob`) — a keyword denylist
doesn't catch them. The real fix is a **sandbox** (`analytics.py: _open_sandboxed`):

```python
con.execute(f"CREATE TABLE data AS SELECT * FROM read_parquet('{p}')")  # materialize
con.execute("SET enable_external_access=false")   # kills file + network reads
con.execute("SET lock_configuration=true")        # can't be re-enabled mid-query
# now run the user's SELECT — `data` is in memory, no filesystem reachable
```

Materialize **first** (reading the parquet is itself "external access"), then lock down.
Column identifiers everywhere else go through `_quote_ident` (an allowlist regex + a
denylist of `--`, `/*`, `;` + double-quoting). This is the lesson: **defense in depth,
and never trust that a denylist is complete.**

### 2.6 Encoding: the real-world data problem

Government CSVs are often Windows-1252, not UTF-8. DuckDB requires UTF-8. So
`download.py: _detect_encoding` tries UTF-8 → chardet → CP1252, and `analytics.py`
transcodes non-UTF-8 files to a `.utf8` sidecar before parsing. Real open data is messy;
budget for encoding, delimiter sniffing, and header-detection edge cases.

### 2.7 Typed output (the `models.py` layer)

Returning a bare `dict` gives the host no schema. Returning a **Pydantic model** makes
FastMCP emit a real `outputSchema` + `structuredContent`, so hosts can validate. The
trick for variable data: `model_config = ConfigDict(extra="allow")` keeps dynamic keys
(like `p25`/`p50` percentiles) flowing through while still typing the known envelope.

### 2.8 Testing: hermetic by default

171 tests run with **no network** — `pytest-httpx` mocks the CKAN responses and a tiny
in-memory CSV fixture exercises the whole download→DuckDB→Parquet stack. A handful of
live tests hit the real API only when `RUN_LIVE_TESTS=1`. The security guards have
**adversarial tests**: `test_query_resource_blocks_file_access` proves `read_text('/etc/passwd')`
returns an error, not your password file.

---

## Part 3 — Build your own MCP server (recipe)

You want to wrap an open-data portal (or any HTTP API) for an AI assistant. Here's the
path this project took, generalized.

### Step 1 — Decide the shape

- **Local stdio** (this project): easiest to prototype, runs on the user's machine, ships via PyPI + `uvx`. Good for personal/civic tools.
- **Remote HTTP**: one deployment serves everyone, handles OAuth, pushes updates. The right choice for a hosted product.

Start local; the analytics layer ports directly to remote later.

### Step 2 — Scaffold with FastMCP

```python
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("my-server")

@mcp.tool(annotations={"title": "Search", "readOnlyHint": True, "openWorldHint": True})
async def search(query: str) -> dict:
    """Search the catalog. Returns up to 10 results."""
    return await my_client.search(query)

def main():
    mcp.run()   # stdio transport
```

`pyproject.toml` exposes the entry point:

```toml
[project.scripts]
my-server = "my_package.server:main"
```

### Step 3 — Design tools the model can actually use

Read the tool-design rules baked into this project:

1. **Tight schemas.** `Field(ge=1, le=50)`, `Literal["head","tail","random"]` — every
   constraint is one fewer bad call.
2. **Descriptions are the contract.** Say what it does, what it returns, and *what it
   doesn't* (so the model picks the right sibling tool).
3. **Required annotations.** `readOnlyHint`, `destructiveHint`, `title` — these are
   pass/fail for the Anthropic Directory and drive auto-approval UX.
4. **Read/write split.** A tool is either read-only or it mutates — never both.
5. **≤ ~30 tools.** Each schema costs context tokens every turn. Past ~30, switch to a
   `search_actions` + `execute_action` pair.

### Step 4 — Don't dump raw data into context

The killer pattern for data tools: give the model **reconnaissance tools** (schema,
summarize) so it understands the data *before* pulling rows, and **typed query tools**
(filter, aggregate) so it gets answers, not megabytes. Truncate long fields; report
when you do (`"Showing 10 of 847…"`).

### Step 5 — Cache the expensive step

If each call re-downloads + re-parses, the server is slow and rude to the upstream API.
Cache the transformed artifact (here: Parquet), key it by a version tag (ETag), and skip
the network entirely on a warm hit.

### Step 6 — Close the injection surfaces

- Escape every user value going into a query language (Solr, SQL, shell).
- For free-form SQL, **sandbox the engine**, don't rely on keyword filtering.
- Cap downloads (byte limits) to bound memory and decompression bombs.
- Write a `SECURITY.md` and **adversarial tests** that prove the guards work.

### Step 7 — Make it shippable

- Hermetic tests (mock the network) + a CI matrix across Python versions.
- Lint + format + type-check gates (ruff + mypy).
- `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`.
- Publish to **PyPI** (`uv build && uv publish`) and the **MCP Registry**
  (`mcp-publisher publish` with a `server.json`).

### Step 8 — Generalize across sources (optional)

Once one portal works, an **adapter pattern** lets one server cover many. CKAN portals
across Latin America share an API — a single client covers Argentina, Chile, México,
Uruguay, Ecuador, and the DR with only a base-URL change. That's the path from
`datosgobdo-mcp` to a regional `opendata-latam-mcp`.

---

## Where to look in the code

| To learn… | Read… |
|---|---|
| Tool definitions + annotations | `src/datosgobdo_mcp/server.py` |
| HTTP client + injection escaping | `src/datosgobdo_mcp/ckan.py` |
| DuckDB analytics + the SQL sandbox | `src/datosgobdo_mcp/analytics.py` |
| Caching strategy | `src/datosgobdo_mcp/cache.py` |
| Typed output | `src/datosgobdo_mcp/models.py` |
| Hermetic + adversarial tests | `tests/` |
| Threat model | `SECURITY.md` |

Clone it, run `uv sync --extra dev && uv run pytest -v`, and start changing things.
That's the fastest way to learn.

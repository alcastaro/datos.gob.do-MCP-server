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
uvx dominican-open-data-mcp
```

Claude Desktop config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "datosgobdo": {
      "command": "uvx",
      "args": ["dominican-open-data-mcp"]
    }
  }
}
```

Settings go in an `"env"` object next to `"args"` — `{"env": {"DATOSGOBDO_NETGUARD": "strict"}}` — **not** in your shell. A stdio server inherits only a limited subset of the environment from the client, so an `export` in your terminal never reaches it. Same reason: use absolute paths everywhere, because the working directory of a client-launched server is undefined (`/` on macOS). Both facts come from the [MCP debugging guide](https://modelcontextprotocol.io/docs/tools/debugging), and both cost real debugging time when you build your own server — see Step 7.

Restart the client. Everything the server offers appears automatically: **24 tools, 3 resources with 1 URI template, and 6 prompts**. This part of the tutorial walks the tools first because they do the work; §1.7 covers the other two, which is where most people should actually start.

### 1.2 The five tool categories

Note the naming collision, since it trips people up: MCP has a primitive called *resources* (§1.7), and this catalog calls its downloadable files *resources* too. The category below is the second meaning — CKAN's files.

| Category | Tools | What they're for |
|---|---|---|
| **Discovery** | `search_datasets`, `get_dataset`, `list_recent_datasets`, `get_site_stats` | Find datasets |
| **Resource files** | `get_resource`, `search_resources`, `download_resource_preview`, `check_resources` | Inspect individual files, and ask whether they can be downloaded at all |
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

### 1.6 Seeing it without an assistant: the MCP Inspector

The [MCP Inspector](https://modelcontextprotocol.io/docs/2026-07-28/tools/inspector) is
the protocol's own developer tool. It speaks MCP directly, so it shows you what the
server actually exposes — with no model in between deciding what to mention. Requires
Node 22.19+; nothing to install.

```bash
npx -y @modelcontextprotocol/inspector uvx dominican-open-data-mcp
```

That prints a URL with a one-time token. Open it and you get four panels worth
exploring, one per protocol primitive:

- **Tools** — all 24, with their input and output schemas and their annotations.
  Call `aggregate_resource` here and you see the raw `structuredContent`: the figure,
  the `numeric_coercion` block naming what was excluded, `source_sha256`, and the SQL
  in `computation`. This is the same payload a model receives, unedited.
- **Resources** — the catalog as read-only context.
- **Prompts** — the six templates, which double as a guide to what to ask; see §1.7.
- **Monitoring** — the JSON-RPC traffic, live. Useful when something looks wrong and
  you want to know whether the server or the client caused it.

For scripting and CI there is a CLI mode that runs one request and exits:

```bash
npx -y @modelcontextprotocol/inspector --cli uvx dominican-open-data-mcp \
  --method tools/list --format json | jq -r '.result.tools[].name'
```

Exit codes are meaningful: `0` success, `3` needs auth, `4` unreachable, `5` the tool
returned an error. That last one works because this server flags failed calls with
`isError` — see the 0.13.0 entry in the changelog for why that took a deliberate fix.

**Testing a local build instead of the published package.** The Inspector parses
everything after the server command as its own flags, so `uvx --from ./dist/….whl …`
fails with `Connection closed` — it swallows `--from`. Wrap the launch in a script,
which is also exactly what a real client config does:

```bash
mkdir -p ~/bin
cat > ~/bin/datosgobdo-server.sh <<'EOF'
#!/bin/sh
exec uvx --from "$HOME/path/to/dominican_open_data_mcp-X.Y.Z-py3-none-any.whl" \
  dominican-open-data-mcp
EOF
chmod +x ~/bin/datosgobdo-server.sh

npx -y @modelcontextprotocol/inspector ~/bin/datosgobdo-server.sh
```

The repository ships `scripts/inspector.sh`, which does both: the published package by
default, or a local wheel when you pass one.

### 1.7 Prompts, resources and templates — the other two-thirds of MCP

Almost every MCP tutorial covers tools and stops. That leaves out the two primitives
that decide *who is in control*, which is the part worth understanding:

| Primitive | Who decides when it is used | Spec |
|---|---|---|
| **Tools** | the **model**, mid-conversation | [server/tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) |
| **Resources** | the **application**, by attaching a URI | [server/resources](https://modelcontextprotocol.io/specification/2026-07-28/server/resources) |
| **Prompts** | the **user**, deliberately | [server/prompts](https://modelcontextprotocol.io/specification/2026-07-28/server/prompts) |

#### Prompts: the user-controlled entry point

A prompt is a template you invoke on purpose — normally a slash command. It is not a
tool the model may choose, and that is exactly the point: it lets the *server author*
ship a method, not just capabilities.

This server has six. Start here:

```
/empezar_aqui
```

The other five take one argument each — `/serie_temporal` (a topic), `/auditar_nomina`
(an institution), `/verificar_fuente` (a URL), `/explorar_institucion` (an institution),
`/cruzar_fuentes` (a topic). In a client that presents prompts as a menu rather than
slash commands, you pick from that menu instead; support is optional per client, so
check the [clients directory](https://modelcontextprotocol.io/clients).

**Why these six exist is the lesson.** Each encodes a mistake this catalog invites. A
dataset titled `2020-2025` may hold only 2022, so `serie_temporal` declares the real
period before plotting anything. A payroll column stops being numeric because 37 cells
say `#REF!`, so `auditar_nomina` reports excluded rows next to any total. Half the
catalog cannot be downloaded, so `empezar_aqui` says so before you get attached to a
question. Twenty-four tools cannot teach that; a prompt can.

In code it is one decorator returning a string:

```python
@mcp.prompt(
    name="serie_temporal",
    title="Serie temporal — declarando el periodo real",
    description="Serie por año declarando el periodo real y sin tratar el año como medida.",
)
def serie_temporal(tema: str) -> str:
    return f"Arma una serie anual sobre {tema}. …"
```

The parameter name becomes the prompt's argument, and its presence makes it required.
No I/O happens here — a prompt returns text, and the model does the work.

#### Resources: application-controlled context

A resource is data the *host application* can attach, addressed by URI. Nothing is
called; nothing has side effects. Reach for one when a fact is small, stable, and
wasteful to spend a tool call rediscovering:

```
datosgobdo://catalog/overview       application/json   portal totals
datosgobdo://catalog/institutions   application/json   who publishes, with counts
datosgobdo://guide/verification     text/markdown      how to make a figure checkable
```

The third is the interesting design call. It could have been a prompt, and it should
not be: it is not a request to act, it is reference text worth having in context while
you work. **Prompt = "do this". Resource = "know this".**

```python
@mcp.resource(
    "datosgobdo://catalog/overview",
    name="Resumen del catálogo",
    description="Totales del portal: datasets, instituciones, grupos y etiquetas.",
    mime_type="application/json",
)
async def catalog_overview() -> dict[str, Any]:
    return ckan.with_provenance(await ckan.get_site_stats())
```

#### Resource templates: one definition, every dataset

A [resource template](https://modelcontextprotocol.io/specification/2026-07-28/server/resources#resource-templates)
is a URI with a parameter, so you do not have to enumerate 1,061 static resources — one
per dataset in the catalog — to make each one addressable:

```
datosgobdo://dataset/{dataset_id}       →  datosgobdo://dataset/nomina-poder-judicial
```

In FastMCP the placeholder in the URI and the function parameter simply have to match:

```python
@mcp.resource("datosgobdo://dataset/{dataset_id}", ...)
async def dataset_resource(dataset_id: str) -> dict[str, Any]: ...
```

Templates are listed by `resources/templates/list`, **not** `resources/list` — a common
source of "my template isn't showing up".

#### Seeing all three for real

The Inspector is the honest view, because it shows the protocol rather than a client's
interpretation of it:

```bash
npx -y @modelcontextprotocol/inspector --cli uvx dominican-open-data-mcp \
  --method prompts/list --format json
npx -y @modelcontextprotocol/inspector --cli uvx dominican-open-data-mcp \
  --method resources/list --format json
npx -y @modelcontextprotocol/inspector --cli uvx dominican-open-data-mcp \
  --method resources/templates/list --format json
```

In the UI, the **Prompts** panel renders a prompt's expanded text before anything
reaches a model — the fastest way to see what a slash command will actually ask for.

#### What this server does not implement

MCP also defines client-side primitives — [sampling](https://modelcontextprotocol.io/specification/2026-07-28/client/sampling)
(the server asks the client for a model completion), [elicitation](https://modelcontextprotocol.io/specification/2026-07-28/client/elicitation)
(the server asks the user for input mid-call), and [roots](https://modelcontextprotocol.io/specification/2026-07-28/client/roots)
(the client tells the server which directories are in scope). None are used here, and
the capability declaration says so rather than claiming them:

```json
"capabilities": {
  "tools":     {"listChanged": false},
  "resources": {"subscribe": false, "listChanged": false},
  "prompts":   {"listChanged": false}
}
```

`listChanged: false` is a promise, not a gap: the lists are fixed at startup, so a
client can cache them.

---

## Part 2 — How it's built

### 2.1 The shape: local stdio server

This is a **local stdio MCP server**: it runs on the user's machine, launched by the
client, communicating over stdin/stdout. The single most important rule:

> **Never `print()` to stdout.** stdout is the MCP protocol channel. All logs go to
> stderr. (See `server.py` — `logging.basicConfig(stream=sys.stderr, ...)`.)

The client captures that stderr and writes it to a file — `~/Library/Logs/Claude/mcp-server-datosgobdo.log` for Claude Desktop on macOS, `%APPDATA%\Claude\logs\mcp*.log` on Windows. Two things worth knowing:

- **The protocol has its own logging channel and you should not use it.** `notifications/message` is [deprecated as of spec `2026-07-28`](https://modelcontextprotocol.io/docs/tools/debugging); stderr is what the specification now recommends. This server never used it, so there is nothing to migrate — the point is not to add it.
- **Over Streamable HTTP nobody captures stderr for you.** The stdio convenience is a property of the transport, not of MCP. A hosted server needs its own log collection or [OpenTelemetry](https://opentelemetry.io/).

### 2.2 Module map

```
src/datosgobdo_mcp/
├── server.py     FastMCP entry — 24 @mcp.tool decorators (the public surface)
├── ckan.py       Async CKAN HTTP client + Solr-escaping + JSON formatters
├── download.py   Capped streaming download + encoding detection
├── preview.py    CSV/TSV/XLSX/JSON parsers for the preview tool
├── analytics.py  DuckDB engine: schema/summarize/filter/aggregate/query/…
├── cache.py      Parquet-on-disk cache with LRU eviction + URL→key lookup
├── models.py     Pydantic response models → typed outputSchema
├── netguard.py   SSRF guard: validates every download URL + redirect hop
└── gcp.py        Optional BigQuery pipeline (registers only with [gcp] extra)
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

A concrete trap from this codebase: the allowlist regex was originally anchored with
`^…$`. In Python, `$` also matches *just before* a trailing newline — so `"col\n"`
slipped through an allowlist meant to reject control characters. Anchor with `\A…\Z`
(not `^…$`) whenever a regex is a security boundary, not just a format check.

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

The hermetic suite (180+ tests) runs with **no network** — `pytest-httpx` mocks the CKAN responses and a tiny
in-memory CSV fixture exercises the whole download→DuckDB→Parquet stack. A handful of
live tests hit the real API only when `RUN_LIVE_TESTS=1`. The security guards have
**adversarial tests**: `test_query_resource_blocks_file_access` proves `read_text('/etc/passwd')`
returns an error, not your password file.

One subtle trap worth internalizing: a path-safety denylist that blocked `/private/var`
passed on Linux CI (where temp files live in `/tmp`) but **failed on macOS**, where the
per-user temp dir *is* `/private/var/folders/…`. Green CI is not green everywhere — test
on the platforms you actually ship to, and scope security denylists narrowly enough that
they don't swallow legitimate user-writable space.

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

### Step 3b — Ship a method, not only capabilities

Tools are what your server *can* do; prompts are what you think someone *should* do
first. If your domain has traps — a title that lies about its date range, a total that
is wrong unless you check what was excluded — a tool description cannot carry that
reliably, because the model only reads it once it has already chosen the tool.

So: after the tools work, write two or three prompts (§1.7). One with no arguments that
orients a newcomer, and one per workflow you would be annoyed to see done wrong. Then add
resources for the standing facts a conversation should not spend a tool call on, and a
resource template if your domain has one obvious addressable entity (a dataset, a repo, a
ticket). This is the cheapest quality lever in the whole server: ~30 lines that stop the
most common misuse of the other 3,000.

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

**Assume nothing about the environment you are launched into.** Your server works in your terminal and then behaves differently under a client, for two reasons the [MCP debugging guide](https://modelcontextprotocol.io/docs/tools/debugging) states plainly: the client passes only a limited subset of environment variables, and the working directory is undefined (`/` on macOS). Both bit this project:

- A user who sets a **security** variable in their shell gets the default. Document the `env` block, not `export`.
- A relative path resolves against a directory nobody chose. `DATOSGOBDO_ARCHIVE_DIR=mi-archivo` silently disabled the archive fallback; a relative `dest` for `save_query_to_csv` sent the write to the filesystem root, failing with `[Errno 30] Read-only file system`.

The lesson generalizes: **for any path-valued input, require an absolute path and say why in the error; for any configuration you cannot find, log that you looked.** A feature that is off because it was misconfigured must not look identical to a feature that was never asked for.

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
| SSRF guard (scheme/IP checks, redirect hops) | `src/datosgobdo_mcp/netguard.py` |
| Optional-dependency tool registration | `src/datosgobdo_mcp/gcp.py` |
| Hermetic + adversarial tests | `tests/` |
| Threat model | `SECURITY.md` |

Clone it, run `uv sync --extra dev && uv run pytest -v`, and start changing things.
That's the fastest way to learn.

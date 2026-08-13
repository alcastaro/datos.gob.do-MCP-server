<!-- mcp-name: io.github.alcastaro/datos.gob.do-MCP-server -->

**[English](README.md) · [Español](README.es.md)**

---

# datosgobdo-mcp

**Ask an AI assistant a question about Dominican public data, and get an answer traced back to the government file it came from.**

This is a [Model Context Protocol](https://modelcontextprotocol.io) server for [datos.gob.do](https://datos.gob.do), the Dominican Republic's official open-data portal. It plugs into Claude Desktop, Claude Code, Cursor, ChatGPT Desktop or any MCP-compatible client, and lets the model search the catalog, download the actual files, parse them, and run real analysis — without you writing code, opening a URL, or downloading a spreadsheet.

> **Official source.** The canonical repository is
> [`alcastaro/datos.gob.do-MCP-server`](https://github.com/alcastaro/datos.gob.do-MCP-server).
> The only official distributions are the PyPI package
> [`dominican-open-data-mcp`](https://pypi.org/project/dominican-open-data-mcp/)
> and the MCP Registry entry `io.github.alcastaro/datos.gob.do-MCP-server`.
> Copies published elsewhere are not maintained by the author and may be
> outdated or modified — verify against this repository before installing.

This README is written to be read in order. **Part 1 needs no technical knowledge.** Part 2 teaches what MCP actually is, using this server as the worked example. Parts 3 to 6 are the reference and the engineering detail. If you want the same material as a walkthrough, read the [Tutorial](Tutorial.md) ([Español](Tutorial_es.md)).

---

## Contents

**Part 1 — Start here (no technical knowledge needed)**
1. [What this is, in plain words](#1-what-this-is-in-plain-words)
2. [Quick start](#2-quick-start)
3. [The six guided prompts — start with `/empezar_aqui`](#3-the-six-guided-prompts--start-with-empezar_aqui)
4. [What you can ask](#4-what-you-can-ask)
5. [Read this before you quote a number](#5-read-this-before-you-quote-a-number)

**Part 2 — Understanding MCP (educational)**

6. [What is MCP? Tools, resources and prompts](#6-what-is-mcp-tools-resources-and-prompts)
7. [What is datos.gob.do?](#7-what-is-datosgobdo)

**Part 3 — What this server exposes**

8. [Tools (24, plus 3 optional)](#8-tools-24-plus-3-optional)
9. [Resources (3) and one resource template](#9-resources-3-and-one-resource-template)
10. [Prompts (6)](#10-prompts-6)
11. [Which primitive to reach for](#11-which-primitive-to-reach-for)

**Part 4 — Why this server exists**

12. [How it compares with other CKAN MCP servers](#12-how-it-compares-with-other-ckan-mcp-servers)

**Part 5 — Technical reference**

13. [Installation and client configuration](#13-installation-and-client-configuration)
14. [What the answers tell you about themselves](#14-what-the-answers-tell-you-about-themselves)
15. [Security and environment variables](#15-security-and-environment-variables)
16. [Architecture](#16-architecture)
17. [Measured limitations](#17-measured-limitations)

**Part 6 — Development**

18. [Development, testing and the MCP Inspector](#18-development-testing-and-the-mcp-inspector)
19. [Contributing, credits, how to cite, licence](#19-contributing-credits-how-to-cite-licence)

---
---

# Part 1 — Start here

## 1. What this is, in plain words

The Dominican government publishes thousands of files: public payrolls, budget execution, hospital activity, migration flows, procurement contracts, electricity losses, forest fires. It is all public. Almost nobody reads it, because reading it means knowing which of 266 institutions published what, finding the file, downloading a spreadsheet with the header on row 3, and knowing what to do next.

This server hands that whole job to your AI assistant. You ask in your own words. The assistant finds the dataset, downloads the file from the institution's own server, figures out the columns, runs the sum or the average, and tells you the answer **along with where it came from and what it had to leave out**.

Three things worth knowing up front, because they shape everything else:

- **It only reads.** Nothing here can modify the portal or publish anything. There is no login and no password.
- **It runs on your computer**, alongside your assistant. Your questions do not pass through a server owned by this project.
- **It tells you when the data is bad.** Roughly half the catalog cannot be downloaded programmatically at all, and the tools say so instead of inventing a number. That is the point of the whole design, not a caveat buried at the bottom.

## 2. Quick start

You need [`uv`](https://docs.astral.sh/uv/), a small tool that runs Python programs without you installing anything else. On macOS or Linux, paste this into a terminal:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

On Windows, follow the [uv installation page](https://docs.astral.sh/uv/getting-started/installation/).

Then tell your assistant about the server.

**Claude Desktop.** Open `Settings → Developer → Edit Config`, or edit the file directly:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Paste this, replacing `YOUR_USERNAME`:

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

Use the **full path** to `uvx` — Claude Desktop does not read your shell's `PATH`. Then quit Claude Desktop completely (Cmd+Q on macOS, not just closing the window) and reopen it. Under `Settings → Developer` you should see `datosgobdo` **running**.

Nothing else is required. If you later want to change a setting — the network guard, the cache directory — it goes in an `"env"` block **inside this file**, not in your shell: see [§13](#13-installation-and-client-configuration).

**Claude Code.** One line:

```bash
claude mcp add datosgobdo -- uvx dominican-open-data-mcp
```

**Anything else.** Same idea: register `uvx` as the command with `dominican-open-data-mcp` as its argument. The [MCP clients directory](https://modelcontextprotocol.io/clients) lists which clients support which features. Full options — dev versions, local clones, hosted mode — are in [§13](#13-installation-and-client-configuration).

## 3. The six guided prompts — start with `/empezar_aqui`

Twenty-four tools is not an invitation. Someone who has never seen this catalog has no way to know that payrolls, budget execution and public investment are the three things it covers best.

So the server ships **six prompts**: ready-made questions, written to encode the habits that took a full catalog audit to learn. In Claude Code and Claude Desktop they appear as slash commands. Type:

```
/empezar_aqui
```

and the assistant will introduce you to the portal, tell you what it covers well, propose three concrete questions you could ask next, and warn you up front about what cannot be downloaded.

The other five take one argument each:

| Prompt | You give it | What it does |
|---|---|---|
| `/empezar_aqui` | — | Portrait of the portal and three questions to start with. |
| `/serie_temporal` | a topic | Builds a year-by-year series, declaring the real period covered and refusing to treat the year column as a measure. |
| `/auditar_nomina` | an institution | Sum, average and salary distribution of a public payroll, declaring how many rows were excluded and why. |
| `/verificar_fuente` | a resource URL | Checks scope, provenance and shape of a file **before** you rely on it. |
| `/explorar_institucion` | an institution | Inventory of everything that institution publishes, with the real download status of each file. |
| `/cruzar_fuentes` | a topic | Crosses two resources, declaring units, periods and the limits of the join. |

If your client does not show prompts as slash commands, see its entry in the [MCP clients directory](https://modelcontextprotocol.io/clients) — prompt support is [optional for clients](https://modelcontextprotocol.io/specification/2026-07-28/server/prompts), and the [MCP Inspector](#18-development-testing-and-the-mcp-inspector) can always show and run them.

## 4. What you can ask

Plain questions, in Spanish or English. Some that work today:

> *How many datasets are on the datos.gob.do portal, and which institutions publish the most?*

> *Find the five most relevant budget datasets and tell me which institution publishes each one.*

> *How much does the Judicial Branch spend on salaries?*

> *How many active employees does the Ministry of Agriculture have in April 2026, broken down by employment status?*

That last one is worth pausing on, because it is the kind of question the whole analytics layer exists for. The Agriculture payroll is a CSV with **826,000 rows and 94 MB** — far too big to paste into a conversation. The server downloads it once, converts it to a columnar cache, and answers with a grouped aggregation: 6 status types, roughly 8,915 employees. The first call takes about 14 seconds; every later question about the same file answers in under half a second.

> *Compare FONDOMARENA's approved versus executed budget over the last three years.*

> *What columns does the Ministry of Interior's vehicle-theft dataset have?*

> *List the ten most recently updated datasets.*

**Who this tends to be useful for:** data journalists who would otherwise write a scraper; researchers who need programmatic access; transparency groups tracking budget execution and procurement; developers prototyping on public data; public officials checking what their own institution already publishes; and anyone curious about how the state actually operates.

## 5. Read this before you quote a number

This catalog has real defects, and they were measured — a census of the whole thing on **2026-08-08**, one resource per dataset, 1,056 resources over real MCP sessions. Four findings change how you should read any figure you get from here:

**About half the catalog cannot be downloaded by a program.** 540 of 1,056 resources could be read. The largest single cause is not this server and no version of it can fix it: **360 resources across 98 institutions** sit behind a site configuration that refuses programmatic downloads of the files those same institutions publish as open data. From the same address, 21 other government hosts behind the same CDN answer normally — so it is per-site configuration, not our network. A further 15 links are dead, 37 return a web page instead of a file, and 8 files are unreadable.

**One in three multi-format datasets contradicts itself.** Of 528 datasets whose formats could be compared, **176 disagree** on row count or column count. One example: the Treasury's `recaudaciones-sirite-2021-2025` has 971,818 rows as CSV and 197,338 as ODS. A citizen downloading the ODS and a journalist downloading the CSV would quote different numbers from the same official dataset. **Practical rule: check more than one format before you publish a total.**

**Numbers are often stored as text.** 93 of the 540 readable resources hold numeric columns as text, usually because a handful of cells say `N/A` or `#REF!`. The tools read such a column as numbers where each value permits it and **report what that cost** — see [§14](#14-what-the-answers-tell-you-about-themselves). Read `values_excluded` before quoting the total.

**No dataset declares how often it is updated.** The `periodicidad` field is empty in all 1,056. A dataset labelled "2018-2026" may have been fed last month or frozen two years ago; you have to infer freshness from the last period that actually has data.

None of this is a reason not to use the catalog. It is a reason to cite it precisely — which is what `/verificar_fuente` and the self-describing response fields are for.

---
---

# Part 2 — Understanding MCP

## 6. What is MCP? Tools, resources and prompts

[Model Context Protocol](https://modelcontextprotocol.io) is an open standard — created by Anthropic, now adopted across the industry — for connecting language models to outside data and capabilities. Instead of every application inventing its own plugin format, a model-facing app (the **client**, e.g. Claude Desktop) talks to any number of **servers** over one protocol.

A server can offer three kinds of thing. The distinction matters, because it determines *who decides* when something is used:

| Primitive | Controlled by | What it is | In this server |
|---|---|---|---|
| **[Tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)** | the **model** | Functions the model may call, with typed arguments. The model picks when and with what. | 24 functions: search, download, aggregate, query… |
| **[Resources](https://modelcontextprotocol.io/specification/2026-07-28/server/resources)** | the **application** | Data the app can attach as context, addressed by URI. No side effects, no cost to reason about. | 3 documents + 1 URI template |
| **[Prompts](https://modelcontextprotocol.io/specification/2026-07-28/server/prompts)** | the **user** | Templates the user invokes deliberately, usually as slash commands. | 6 guided workflows |

The protocol also defines client-side primitives — [sampling](https://modelcontextprotocol.io/specification/2026-07-28/client/sampling), [elicitation](https://modelcontextprotocol.io/specification/2026-07-28/client/elicitation), [roots](https://modelcontextprotocol.io/specification/2026-07-28/client/roots) — which this server does not use.

Concept guides: [tools](https://modelcontextprotocol.io/docs/concepts/tools), [resources](https://modelcontextprotocol.io/docs/concepts/resources), [prompts](https://modelcontextprotocol.io/docs/concepts/prompts). If you want to build one, start with [Build a server](https://modelcontextprotocol.io/docs/develop/build-server), and read [Part 3 of our Tutorial](Tutorial.md#part-3--build-your-own-mcp-server-recipe) for what this project learned doing it.

What this server declares on connection, verified over a live session on 2026-08-12:

```json
{
  "serverInfo": { "name": "datosgobdo-mcp", "version": "0.14.0" },
  "protocolVersion": "2025-11-25",
  "capabilities": {
    "tools":     { "listChanged": false },
    "resources": { "subscribe": false, "listChanged": false },
    "prompts":   { "listChanged": false }
  }
}
```

`listChanged: false` and `subscribe: false` are honest declarations, not omissions: the tool list is fixed at startup, and no resource here changes often enough to be worth a subscription.

> **On the two version numbers.** The spec links above point to `2026-07-28`, the current specification, because that is what you should read. The server negotiates **`2025-11-25`** because it pins `mcp>=1.9.0,<2` — SDK 2.0 renamed `FastMCP` to `MCPServer` and dropped the old import path with no shim. What 2026-07-28 adds and this server therefore does not implement: [`server/discover`](https://modelcontextprotocol.io/specification/2026-07-28/server/discover), the per-request `_meta` fields, and per-request log levels. Migration is tracked, not accidental.

## 7. What is datos.gob.do?

The Dominican government's official open-data portal, operated by OGTIC. It runs **CKAN 2.11.3** — the same platform behind data.gov (USA), data.gov.uk and much of Latin America.

**What the portal declares** (queried live on 2026-08-12):

| | |
|---|---|
| Datasets | **1,061** |
| Registered organizations | **266** |
| Thematic groups | **11** |
| Tags | **874** |
| CKAN extensions loaded | `activity`, `datosgobdo_theme` |

**What the 2026-08-08 audit measured**, which is a different thing and the gap is instructive:

| | |
|---|---|
| Resources (files) in the catalog | **3,826** |
| Organizations that actually own a dataset | **261** of the 266 registered |
| Resources tested (one per dataset) | **1,056** |
| Machine-readable | **540** (51.1 %) |
| Rows downloaded and cached | **13,371,601** |
| Resources hosted on `datos.gob.do` itself | **66** — the rest live on **273 other domains** |

That last row is the structural fact behind most of this project. **The portal is a catalogue of links, not a repository.** Each institution keeps its own files on its own web server, so availability, format hygiene and access rules are decided in 273 places the portal does not control.

Note also what the extension list does *not* include: **CKAN's DataStore is not installed here.** That single fact is why this server looks the way it does — see [§12](#12-how-it-compares-with-other-ckan-mcp-servers).

This project was inspired by [`datagouv-mcp`](https://github.com/datagouv/datagouv-mcp) (Etalab, France), but datos.gob.do runs CKAN rather than udata, so the implementation is its own.

---
---

# Part 3 — What this server exposes

## 8. Tools (24, plus 3 optional)

Typed functions, grouped in five families. The data-producing tools (analytics, preview, cache) return typed `outputSchema` / `structuredContent` so hosts can validate results; navigational metadata tools return JSON. Every portal-facing tool is annotated `readOnlyHint: true`; network-facing ones `openWorldHint: true`.

**Every tool answers with one object.** Listings name what they carry and count it — `{organizations, count, limit_reached}`, `{tags, count, limit_reached}`, `{groups, count}`, `{suggestions, count, kind, query}`. `limit_reached` matters because the caps are lower than the catalog: 200 institutions against 266, and any tag listing without a `query` is a sample of 874.

### Discovery

| Tool | What it does |
|---|---|
| `search_datasets` | Search datasets by keyword, organization, tag, or group. Combinable filters, pagination. |
| `get_dataset` | Full metadata for a dataset: title, description, licence, author, and every resource with its direct download URL. |
| `list_recent_datasets` | Datasets sorted by most-recent modification. Useful for monitoring portal updates. |
| `get_site_stats` | Portal-wide counts (datasets, organizations, groups, tags). |

### Resource files

| Tool | What it does |
|---|---|
| `get_resource` | Metadata for a single resource (URL, format, size, date). |
| `search_resources` | Search resources by name. |
| `download_resource_preview` | Download a file and return N rows. CSV, TSV, XLSX, XLS, ODS, JSON. 5 MB cap. Sample mode: head / tail / random. |
| `check_resources` | Ask up to 25 URLs whether their files can actually be downloaded, without downloading them. Returns a class per URL — reachable, browser challenge, site rule, dead link, no answer — because a catalog entry is not evidence the file is still there. |

### Analytics

DuckDB over a persistent Parquet cache. The first call per resource downloads and caches (up to 100 MB); later calls are sub-second. The cache is worth roughly **44×** on measured medians.

| Tool | What it does |
|---|---|
| `get_resource_schema` | Column names, inferred types, sample values. The cheap reconnaissance step before any aggregation. |
| `summarize_resource` | Auto profile: row count, per-column nulls and distinct counts, min/max/mean on numerics, top-N on categoricals. |
| `filter_resource` | Typed WHERE / SELECT / ORDER BY / LIMIT. Ops: `=`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `not_in`, `contains`, `starts_with`, `ends_with`, `is_null`, `is_not_null`. |
| `aggregate_resource` | Typed GROUP BY + aggregations + HAVING + ORDER BY. Fns: `count`, `count_distinct`, `sum`, `avg`, `mean`, `median`, `min`, `max`, `stddev`, `variance`. |
| `quantiles_resource` | Percentile distribution (p25/p50/p75/p90/p95/p99) of numeric columns. |
| `find_duplicates_resource` | Rows duplicated on given columns (or all). Essential for payroll and census quality checks. |
| `detect_outliers_resource` | Rows outside the IQR fence on a numeric column, sorted by distance from the median. |
| `query_resource` | Power-user escape hatch: read-only SQL against table `data`. SELECT/WITH only; DDL/DML/COPY/PRAGMA/ATTACH/LOAD rejected, and sandboxed (see [§15](#15-security-and-environment-variables)). |
| `save_query_to_csv` | Write a filter or SQL result to a local CSV. Absolute destination, or the default `~/Downloads/datosgobdo-exports/`. Disabled in hosted mode. |
| `get_cache_stats` | On-disk Parquet cache statistics, plus the server's own identity and effective security mode. `total_bytes` is disk usage, not index usage: `orphan_entries` counts Parquet files the index does not list — written by a call whose bookkeeping lost the cache lock, or by a process that died before recording them — and a non-zero value there means contention rather than a healthy cache. |
| `clear_cache` | Wipe the local Parquet cache. The only non-read-only tool in the server (`destructiveHint: true`). Disabled in hosted mode. |

### Catalog

| Tool | What it does |
|---|---|
| `list_organizations` | Publishing institutions with a dataset count each. |
| `get_organization` | Detail for one institution (description, dataset count, URL). |
| `list_groups` | Thematic categories with counts. |
| `list_tags` | Tags, optionally filtered by prefix. |

### Autocomplete

| Tool | What it does |
|---|---|
| `autocomplete` | Resolve partial names for datasets, organizations, groups or tags — for when the user only gives part of a name. |

### GCP pipeline (optional)

Installed with `pip install 'dominican-open-data-mcp[gcp]'`; three extra tools register automatically when the Google Cloud libraries are present, taking the count to 27. They make this server the *ingestion* half of a BigQuery pipeline: discover here, load to BigQuery, then query with Google's own BigQuery MCP for the cross-dataset JOINs a local DuckDB cache cannot do.

| Tool | What it does |
|---|---|
| `load_resource_to_bigquery` | Resource → Parquet cache → GCS upload → BigQuery external table (default, zero-ETL) or load job. |
| `list_bigquery_exports` | List tables in a BigQuery dataset. |
| `get_bigquery_table_info` | Schema, row count and source URIs of a table. |

Set `DATOSGOBDO_GCS_BUCKET` to avoid passing the bucket on every call. **Preview status:** these three are outside the stability promise and have not been exercised against a live project.

## 9. Resources (3) and one resource template

[Resources](https://modelcontextprotocol.io/specification/2026-07-28/server/resources) are addressed by URI and read by the *application*, not called by the model. They exist here for the facts that are small, stable, and wasteful to spend a tool call on. All three are read-only and free of side effects.

| URI | Type | What it holds |
|---|---|---|
| `datosgobdo://catalog/overview` | `application/json` | Portal totals: datasets, institutions, groups, tags. |
| `datosgobdo://catalog/institutions` | `application/json` | Every publishing institution with its dataset count — the answer to "which institution?" before any query. |
| `datosgobdo://guide/verification` | `text/markdown` | The four fields that make a number checkable, and what to do when they are missing. |

That last one is a resource rather than a prompt on purpose: it is not a request to act, it is reference text worth having in context while you work.

One [resource template](https://modelcontextprotocol.io/specification/2026-07-28/server/resources#resource-templates) — a URI pattern with a parameter, so one definition addresses every dataset in the catalog:

| Template | Fill in | Returns |
|---|---|---|
| `datosgobdo://dataset/{dataset_id}` | a dataset id or slug | That dataset's metadata as attachable context. |

Example: `datosgobdo://dataset/nomina-poder-judicial`.

**How to use them.** In Claude Desktop, resources appear in the attachment menu of a conversation with the server connected. In other clients, check the [clients directory](https://modelcontextprotocol.io/clients) — resource support is optional. In every client, the Inspector's **Resources** panel lists them and shows the raw payload, including expanding the template.

## 10. Prompts (6)

[Prompts](https://modelcontextprotocol.io/specification/2026-07-28/server/prompts) are user-controlled: nothing invokes them but you. Each one here encodes a habit learned the hard way during the catalog audit — which is why they are worth using even when you know the tools well.

| Prompt | Argument | The habit it encodes |
|---|---|---|
| `empezar_aqui` | — | Orientation before exploration, and the download warning stated up front rather than discovered later. |
| `serie_temporal` | `tema` (required) | Declare the **real** period the data covers, not the one in the title; never treat the year column as a measure. |
| `auditar_nomina` | `institucion` (required) | Report excluded rows and their provenance alongside any salary total. |
| `verificar_fuente` | `url` (required) | Check scope, provenance and shape **before** relying on a file. |
| `explorar_institucion` | `institucion` (required) | Inventory with each file's real download status, not just its catalog entry. |
| `cruzar_fuentes` | `tema` (required) | State units, periods and join limits before crossing two sources. |

**How to invoke them.** In Claude Code and Claude Desktop, as slash commands: `/empezar_aqui`, or `/serie_temporal` and then the topic when asked. Some clients present them in a menu instead. In the Inspector, the **Prompts** panel lists each one with its arguments and renders the expanded text before anything is sent to a model — the most reliable way to see exactly what a prompt does.

## 11. Which primitive to reach for

| You want to… | Use | Why |
|---|---|---|
| Answer a specific question about data | a **tool**, via ordinary conversation | The model chooses and combines them. |
| Start from zero, or follow a rigorous method | a **prompt** | Six workflows with the caveats already built in. |
| Give the assistant standing background | a **resource** | Attach it once; no tool call, no tokens spent deciding. |
| Pin one dataset as context | the **resource template** | `datosgobdo://dataset/{id}`. |
| Do something the typed tools do not cover | `query_resource` | Read-only SQL, sandboxed. The escape hatch, not the first move. |

---
---

# Part 4 — Why this server exists

## 12. How it compares with other CKAN MCP servers

CKAN powers hundreds of government portals, so a generic CKAN MCP server is an obvious idea and a good one. The most developed is **[`ondata/ckan-mcp-server`](https://github.com/ondata/ckan-mcp-server)** (MIT, TypeScript, adopted by [AgID](https://github.com/AgID/ckan-mcp-server), Italy's digital agency): dataset search with full Solr syntax, organizations and groups, discovery across ~950 portals, and tabular access through CKAN's DataStore API. It points at any portal via a `server_url` argument. If your portal has DataStore populated, **use it** — it is broader than this project and more actively released.

The difference is not quality, it is where the data lives. Verified live on 2026-08-12:

```
GET /api/3/action/status_show   → extensions: ["activity", "datosgobdo_theme"]
GET /api/3/action/datastore_search
  → 400  "Action name not known: datastore_search"
resources with datastore_active: 0 / 254 sampled
```

**datos.gob.do has no DataStore.** There is no `datastore_search`, no SQL endpoint, and not one resource is loaded into it. A generic CKAN MCP server pointed here can search metadata perfectly well and cannot read a single row of data. That is not a flaw in it — the extension is optional in CKAN and this portal never enabled it.

So the two projects divide along a real line:

| | Portals **with** DataStore | Portals that are **file catalogs** |
|---|---|---|
| Where the data is | Loaded into CKAN, queryable by API | Files on 273 institutional web servers |
| How to read it | `datastore_search_sql` | Download, sniff the encoding, parse, cache, query |
| Best tool | [`ondata/ckan-mcp-server`](https://github.com/ondata/ckan-mcp-server) | this one |

Everything that makes this codebase larger than a CKAN API wrapper exists because of that right-hand column: encoding detection scored by the Spanish it recovers, streaming ODS parsing (loading the full DOM multiplied memory by ~580×), a Parquet cache keyed on the parser build, numeric coercion that declares what it excluded, page→file resolution for the 37 URLs that answer with HTML, an SSRF guard for downloads reaching 273 third-party hosts, and an optional archived-copy fallback that always says when it fired.

**If you are building for another Latin American portal**, check `status_show` first. If DataStore is absent — as it is in the Dominican Republic — the file-reading pipeline in this repository is the part you will need, and the [Tutorial](Tutorial.md) documents it so it can be reused.

---
---

# Part 5 — Technical reference

## 13. Installation and client configuration

### Option A — `uvx` from PyPI (recommended)

Package: [`dominican-open-data-mcp`](https://pypi.org/project/dominican-open-data-mcp/).

```bash
uvx dominican-open-data-mcp
```

A short alias binary ships too — both launch the same server:

```bash
uvx --from dominican-open-data-mcp datosgobdo-mcp
```

`uvx` downloads the package, builds an isolated venv and runs it. First run takes seconds; later runs are instant.

> **Upgrading from ≤ 0.7.0?** Those releases pinned `mcp>=1.9.0` with no upper bound, and MCP Python SDK 2.0 (2026-07-28) removed the `mcp.server.fastmcp` import path — a fresh install fails with `ModuleNotFoundError`. Install 0.7.1 or later, or pin it yourself: `uvx --with "mcp<2" --from dominican-open-data-mcp datosgobdo-mcp`.

### Option B — `uvx` from GitHub (development version)

```bash
uvx --from git+https://github.com/alcastaro/datos.gob.do-MCP-server.git datosgobdo-mcp
```

### Option C — local clone (for development)

```bash
git clone https://github.com/alcastaro/datos.gob.do-MCP-server.git
cd datos.gob.do-MCP-server
uv sync
uv run datosgobdo-mcp   # stdio; Ctrl+C to exit
```

> **macOS note:** do not clone inside `~/Library/CloudStorage/GoogleDrive-*` or similar. macOS blocks executing binaries from cloud-synced paths (a TCC restriction). Use `~/code/` or equivalent.

### Client configuration

Claude Desktop and Claude Code are covered in [§2](#2-quick-start). To track the development version in Claude Desktop, replace the args with `["--from", "git+https://github.com/alcastaro/datos.gob.do-MCP-server.git", "datosgobdo-mcp"]`; in Claude Code, `claude mcp add datosgobdo -- uvx --from git+https://github.com/alcastaro/datos.gob.do-MCP-server.git datosgobdo-mcp`.

For Cursor and others, the principle is identical — register `uvx` as the command. Each client's config file location is in its own docs; the [MCP clients directory](https://modelcontextprotocol.io/clients) is the index.

### Passing settings to the server: the `env` block

Every `DATOSGOBDO_*` variable in this README goes in an `"env"` object inside the client's config:

```json
{
  "mcpServers": {
    "datosgobdo": {
      "command": "/Users/YOUR_USERNAME/.local/bin/uvx",
      "args": ["dominican-open-data-mcp"],
      "env": {
        "DATOSGOBDO_NETGUARD": "strict",
        "DATOSGOBDO_CACHE_DIR": "/Users/YOUR_USERNAME/.cache/datosgobdo-mcp"
      }
    }
  }
}
```

**`export DATOSGOBDO_NETGUARD=strict` in your shell does not reach the server.** A stdio MCP server launched by a client inherits only a limited, platform-dependent subset of the environment — [MCP debugging guidance](https://modelcontextprotocol.io/docs/tools/debugging) is explicit about it. Set the variable in your shell and the server starts in the default mode while you believe it is locked down. This matters most for `DATOSGOBDO_NETGUARD`, which is a security control ([§15](#15-security-and-environment-variables)).

Two consequences of the same fact, both worth knowing before you file a bug:

- **Use absolute paths for every path-valued setting.** The working directory of a client-launched server is undefined — `/` on macOS. `DATOSGOBDO_ARCHIVE_DIR=mi-archivo` resolves nowhere, and the server now logs `is not a directory … Archive fallback stays off` rather than going quiet. Same for `DATOSGOBDO_CACHE_DIR` and for the `dest` argument of `save_query_to_csv`, which refuses a relative path outright.
- **`uv run datosgobdo-mcp` in a terminal behaves differently** — there the working directory is wherever you ran it, and your shell environment does apply. A bug that only appears under the client is usually this.

For Claude Code, pass them with `-e`: `claude mcp add datosgobdo -e DATOSGOBDO_NETGUARD=strict -- uvx dominican-open-data-mcp`.

### Hosted mode (experimental)

`DATOSGOBDO_TRANSPORT=streamable-http` serves MCP over HTTP (stateless, for horizontal scaling) instead of stdio. In this mode `save_query_to_csv` and `clear_cache` are disabled — they touch the server's filesystem and shared cache — and cache statistics omit server paths.

**Logs are your problem in this mode.** Under stdio the client captures the server's stderr and writes it to a file you can tail; over Streamable HTTP it does not. Collect stderr yourself, or wire up [OpenTelemetry](https://opentelemetry.io/), and use ordinary HTTP tooling (`curl`, a browser's Network panel) to inspect requests and SSE streams.

| Variable | Default | Meaning |
|---|---|---|
| `DATOSGOBDO_TRANSPORT` | `stdio` | `streamable-http` for hosted deployments. |
| `DATOSGOBDO_HOST` / `DATOSGOBDO_PORT` | `127.0.0.1` / `8000` | HTTP bind address. |
| `DATOSGOBDO_DUCKDB_MEMORY` | `2GB` | DuckDB memory ceiling per connection. |
| `DATOSGOBDO_DUCKDB_THREADS` | `4` | DuckDB thread cap. |
| `DATOSGOBDO_QUERY_TIMEOUT` | `0` (off) | Wall-clock seconds before a DuckDB run is interrupted. Covers both `query_resource` SQL and the conversion of a freshly downloaded file into Parquet. |

## 14. What the answers tell you about themselves

Three fields appear in responses when the server had to do something the caller did not ask for. Each exists because a tool used for auditing must not quietly paper over a defect in the data.

**`numeric_coercion`** — a column stored as text was read as numbers.

The most common defect in this catalog: **93 of 540 readable resources** hold numeric columns as text, because a handful of cells say `N/A` or `#REF!` and that is enough to make a whole payroll column non-numeric. `aggregate_resource`, `quantiles_resource` and `detect_outliers_resource` read such a column as numbers where each value permits it, and report what it cost:

```json
"numeric_coercion": [{
  "column": "SUELDO BRUTO (RD$)", "coerced": true,
  "values_used": 21469, "values_excluded": 37,
  "excluded_values": [{"value": "N/A", "count": 21}, {"value": "#REF!", "count": 16}]
}]
```

**Read `values_excluded` before quoting the total.** A column less than 90 % parseable is left as text and the reply says why, rather than answering a question about a measure from an arbitrary subset of rows. `count` and `count_distinct` are never coerced.

**`linked_files`** — the URL served a page, and the page linked data files.

37 catalog resources answer with a web page instead of a file. When one linked file clearly matches the request it is fetched, and `cache.resolved_from` records `{page, followed}` — you asked for one URL and received data from another, which the reply says rather than hides. When several candidates are indistinguishable they come back as `linked_files` with names and scores, for you to choose and call again. Files named `clss.csv` and `xls.csv` both exist in this catalog; guessing between them would be inventing.

A file the page opens from JavaScript counts as linked. Some portals put the address in `onclick="window.location.assign('…')"` and nowhere else — the Tribunal Constitucional publishes all three of its formats that way — so reading only anchors reported "no data file on it" about a page anyone can download from in one click.

**`cache.format_corrected`** — the catalog's declared format was wrong, and the reply says which way.

The format in the catalog is a claim about the file, and 83 of 1,595 sibling resources have it wrong in both directions: a spreadsheet registered as CSV, and a CSV registered as ODS. The container is identified from what is inside it — the `mimetype` member for ODS, a workbook part for XLSX — never from the signature alone, because `PK` is how both start. A ZIP holding exactly one data file is unpacked and `detected_from` names the member; a ZIP holding several is left alone, because deciding which one is "the data" would be inventing. `source_sha256` always covers what the portal served, so a re-download can be compared against it even when what was parsed came from inside an archive.

A pre-2007 `.xls` (BIFF/OLE2) cannot be read at all and says so, with what to ask the publisher for. It is the worst-served format in the catalog: 12 of 22 readable.

**A note on the CSV `save_query_to_csv` writes.** It is UTF-8 with CRLF line endings and **no BOM**. That is a correct CSV, and Excel on a Spanish-language Windows will still open it as cp1252 and show `AÃ±o` for `Año`, because without a BOM that is what Excel assumes. The file is fine; the tool most of this audience will open it with is the problem. Two ways around it: open it through Excel's `Data → From Text/CSV`, which asks for the encoding, or use LibreOffice, which detects UTF-8. Measured on Windows 11: `4E 6F 6D 62 72 65 2C 41 C3 B1 6F 0D` — `Nombre,Año\r`, valid UTF-8, no `EF BB BF`.

**`cache.provenance`** — the answer came from an archived copy rather than the portal.

Government links rot: the 2026-08-08 census found 15 resource URLs already dead and 98 institutions whose sites refuse programmatic access, so a figure you cite today may be uncheckable next year. Point `DATOSGOBDO_ARCHIVE_DIR` at a directory holding a `manifest.json` and its Parquet files, and when a portal cannot be reached the server answers from the archived copy. It is off by default, the portal is always tried first, and **the reply always says so** — `cache.provenance` carries the capture date, the `sha256`, the licence and why the origin was not used. A tool that quietly returned yesterday's copy as today's would stop being useful for an audit.

An archive only holds what could be downloaded, so it does not contain the resources a portal refuses. That is the natural assumption and it is wrong.

| Variable | Default | Meaning |
|---|---|---|
| `DATOSGOBDO_ARCHIVE_DIR` | unset (off) | **Absolute** path to a directory with `manifest.json` + Parquet copies to fall back on. |

Set it in the client's `env` block ([§13](#13-installation-and-client-configuration)), with an absolute path. If the directory does not exist the server logs a warning and leaves the fallback off — it will not pretend to be armed.

## 15. Security and environment variables

Full policy, threat model and reporting process: **[SECURITY.md](SECURITY.md)**. In brief:

- **Read-only toward the portal.** No authentication, no `package_create`, no `resource_create`. The only mutating tool is `clear_cache`, over the local cache.
- **Two injection surfaces, both closed.** User values entering CKAN `fq` filters pass through Solr escaping; every column identifier reaching DuckDB passes an allowlist regex plus a denylist of comment and terminator substrings, then is double-quoted.
- **`query_resource` is sandboxed.** Beyond validating that the statement is a single read-only SELECT/WITH, the resource is materialized into an in-memory table and then `enable_external_access=false` + `lock_configuration=true` are set before the user's SQL runs — so DuckDB table functions (`read_text`, `read_csv`, `glob`, …) cannot reach the filesystem or the network.
- **SSRF guard on every download**, initial URL and each redirect hop: http/https only, and every address the hostname resolves to must be globally routable. Cloud metadata (`169.254.169.254`), loopback, RFC-1918, link-local and IPv6 ULA are blocked. The guarded path covers the metadata HEAD probe as well as the download itself.
- **Byte caps** on remote fetches (5 MB preview, 100 MB analytics), streamed — bounding memory and decompression-bomb exposure.
- **`save_query_to_csv`** requires an **absolute** `.csv`/`.tsv` destination, rejects `..` and system paths, and writes with `O_NOFOLLOW`.

| Variable | Values | Meaning |
|---|---|---|
| `DATOSGOBDO_NETGUARD` | `public-only` (default) / `strict` / `off` | `strict` restricts hosts to `datos.gob.do` and subdomains; `off` disables the guard. |
| `DATOSGOBDO_ALLOW_HOSTS` | comma-separated, `*.` wildcards | Operator-trusted hosts — the escape hatch for forks pointing at another CKAN portal. |

> **Set these in the client's `env` block, not in your shell** — [§13](#13-installation-and-client-configuration) shows the exact JSON. A stdio server inherits only a limited subset of the environment, so `export DATOSGOBDO_NETGUARD=strict` leaves the server running with the default guard. There is no warning for this, because from the server's side nothing happened. To check: `get_cache_stats` reports the mode actually in force as `server.netguard_mode`, and the startup line in the client's log records the effective mode.

The default is deliberately **not** a host allowlist: as [§7](#7-what-is-datosgobdo) shows, legitimate resources live on 273 ministry sites, buckets and CDNs.

**On the new primitives:** prompts here are static templates with arguments interpolated into text — they perform no I/O. Resources are read-only reads of portal metadata. Neither adds a write path.

## 16. Architecture

```
src/datosgobdo_mcp/
  server.py        FastMCP server: 24 tools, 3 resources, 1 template, 6 prompts
  ckan.py          CKAN client: requests, Solr escaping, formatters, provenance
  analytics.py     DuckDB layer: typed query builders, coercion, SQL validation
  download.py      Capped streaming download, fetch headers, encoding detection
  cache.py         Parquet cache + index, keyed on source and parser build
  preview.py       Row-level preview parsers (CSV/TSV/XLSX/XLS/ODS/JSON)
  pagelink.py      Resolves a page URL to the data file it links
  archive.py       Archived-copy fallback with declared provenance
  reachability.py  check_resources: classifies why a URL cannot be read
  netguard.py      SSRF guard for URLs and every redirect hop
  models.py        Pydantic output models (typed outputSchema)
  gcp.py           Optional BigQuery/GCS pipeline tools
```

### Design decisions

- **FastMCP over the low-level SDK.** Tools are functions decorated with `@mcp.tool()` and typed via Pydantic: less boilerplate, automatic argument validation.
- **DuckDB + Parquet instead of pandas.** Columnar cache, SQL engine, streaming from disk. A 94 MB payroll answers grouped aggregations in under a second warm, and memory stays bounded.
- **The cache key includes the parser build** — package version plus DuckDB's, because DuckDB's sniffer decides column types. A parser upgrade must not serve types inferred by the old one.
- **DataStore is absent, so files are parsed client-side.** See [§12](#12-how-it-compares-with-other-ckan-mcp-servers). This is the single decision the rest of the architecture follows from.
- **Encoding is scored, not guessed.** Candidate decodings are ranked by the Spanish they recover, rather than trusting a confidence number — the fix for live mojibake like `A¤o` for `Año`.
- **ODS is parsed by streaming `content.xml`.** Loading the full DOM turned a 0.70 MB file into 0.41 GB of RSS; ODS is roughly a third of this catalog, so the naive path was untenable.
- **Blocking work runs in `asyncio.to_thread`** (ODS transcode, encoding detection, Parquet COPY) so a long parse never stalls the event loop.
- **Defensive truncation.** Long descriptions — some institutions publish 5+ KB per organization — are cut to 300 characters in list responses, so one call cannot burn thousands of tokens of context.
- **`list_recent_datasets` is reoriented.** CKAN exposes `recently_changed_packages_activity_list`, but it returns un-hydrated activities (`{object_id: "uuid", activity_type: "changed package"}`) the model cannot interpret. We use `package_search?sort=metadata_modified+desc` and return formatted datasets in one call.
- **All logging to stderr, and none over the protocol.** Per the [MCP debugging guidance](https://modelcontextprotocol.io/docs/tools/debugging), a stdio server must never write to stdout — it corrupts the protocol stream. The protocol's own logging channel (`notifications/message`) was never used here, and as of spec `2026-07-28` it is deprecated: stderr is now what the specification recommends. Nothing to migrate — but do not "improve" this by adding protocol logging.

### Stack

[`mcp`](https://pypi.org/project/mcp/) (official Python SDK, FastMCP) · [`duckdb`](https://duckdb.org/) · [`httpx`](https://www.python-httpx.org/) · [`openpyxl`](https://openpyxl.readthedocs.io/) (read-only streaming XLSX) · [`pydantic`](https://docs.pydantic.dev/) · stdlib `csv`, `json`, `xml.etree` (streaming ODS).

## 17. Measured limitations

Measured against the whole catalog on 2026-08-08 — 1,056 resources, one per dataset, over real MCP sessions — not estimated.

**Not everything published is reachable.** 540 of 1,056 resources could be read. The largest cause is not this server: **360 resources across 98 institutions** sit behind a site configuration that refuses programmatic downloads. From one address, 21 other government hosts behind the same CDN serve us normally, so it is per-site configuration rather than our network. No version of this server can change that. A further 85 failed at transport level (cause not attributable), 37 serve a web page, 15 links are dead, 8 files are unreadable, 6 have a CDN whose origin does not answer, and 5 hit portal errors.

What is **established**: those sites refuse *programmatic* access to their own open data from the address measured. What is **not established**: that a person with a browser in Santo Domingo is refused. That test needs a Dominican residential vantage point and has not been run.

**Formats.** CSV, XLSX and ODS all read at roughly 93 % of what downloads. Two are weaker: legacy `.xls`, and JSON — DuckDB's `read_json_auto` rejects several catalog files as malformed, making JSON the least reliable format here. **PDF is not parsed**; only its download URL is exposed.

**Size.** `download_resource_preview` caps at 5 MB; analytics tools at 100 MB. A single value larger than 16 MB exceeds DuckDB's limit and the file cannot be parsed.

**Shape.** 41 resources put a title or logo above the real header row, which garbles the auto-detected schema — inspect with `download_resource_preview` and project columns explicitly. 25 come back with generic column names (`column00`, unnamed). 93 hold numbers as text, handled and declared per [§14](#14-what-the-answers-tell-you-about-themselves).

**Formats can disagree with each other.** 176 of 528 comparable multi-format datasets differ in row or column count, and in 11 cases one format is empty while another carries the full table. Reading a single format is not evidence of what the dataset contains.

**Encoding** is effectively solved: one file in 540 still shows damaged accents, and that file is encoded in two codepages at once, so no single reading is correct for it.

**Freshness cannot be read from metadata.** `periodicidad` is empty for all 1,056 datasets.

**Windows: tested on 2026-08-13, and here is exactly how far.** Windows 11 (build 26200), Python 3.13, Defender's real-time protection on, a non-administrator account. The suite runs green — 518 passed, 5 skipped, the one skip being a POSIX-only `O_NOFOLLOW` test. Encoding holds end to end: a cp1252 payroll comes back with `Año` and `UREÑA` intact, 135 of 200 institution names carry non-ASCII and none arrive mangled, and paths with accents and spaces work. An aggregation over a 108,038-row payroll matched an independent `Decimal` recomputation to the cent. Defender cost nothing measurable — the 40 MB cold read is dominated by the publisher's ~1 MB/s, and repeated raw downloads varied more between themselves than Windows differed from macOS.

What is **still not tested on Windows**, and therefore not claimed: a user profile that is itself accented (`C:\Users\José Pérez\`, common in the Dominican Republic — only accented sub-folders were exercised), a Downloads folder redirected into OneDrive, Claude Desktop as the client (the transport was driven by a different MCP client), Windows installed on a drive other than `C:`, and a Defender exclusion measured before-and-after, which needs administrator rights. The Windows-only branch of the cache lock is likewise still awaiting a run on Windows: its retry policy is tested, its four-line `msvcrt` shim is not.

**Untested, and therefore not claimed:** the hosted `streamable-http` transport under real load, the three GCP tools against a live project, and concurrent use beyond four processes.

---
---

# Part 6 — Development

## 18. Development, testing and the MCP Inspector

### Local setup

```bash
git clone https://github.com/alcastaro/datos.gob.do-MCP-server.git
cd datos.gob.do-MCP-server
uv sync
uv run pytest          # hermetic by default: no network required
```

### The MCP Inspector

The [MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector) is the protocol's own developer tool. It speaks MCP directly, so it shows what the server actually exposes with no model in between — the best way to see tools, resources, templates and prompts as the protocol sees them. Requires Node 22.19+ and installs nothing permanent:

```bash
# The published package — no clone needed
npx -y @modelcontextprotocol/inspector uvx dominican-open-data-mcp
```

It prints a URL carrying a one-time token. Open it for four panels:

- **Tools** — all 24 with their schemas. Call one and read the raw `structuredContent`, including `numeric_coercion`, `source_sha256` and `computation`.
- **Resources** — the three URIs and the `datosgobdo://dataset/{dataset_id}` template, with raw payloads.
- **Prompts** — the six, with their arguments, rendered to their expanded text before anything reaches a model.
- **Monitoring** — live JSON-RPC traffic in both directions.

From a clone, `scripts/inspector.sh` wraps both cases:

```bash
./scripts/inspector.sh                                        # published package
./scripts/inspector.sh dist/dominican_open_data_mcp-*.whl     # a local build
./scripts/inspector.sh --cli --method tools/list --format json
```

The local-build path needs that wrapper: the Inspector reads everything after the server command as its own flags, so `uvx --from ./dist/….whl …` fails with `Connection closed` because `--from` never reaches `uvx`.

CLI mode exits with meaningful codes — `0` success, `3` needs auth, `4` unreachable, `5` the tool returned an error — so it drops straight into CI:

```bash
npx -y @modelcontextprotocol/inspector --cli uvx dominican-open-data-mcp \
  --method tools/list  --format json | jq -r '.result.tools[].name'
npx -y @modelcontextprotocol/inspector --cli uvx dominican-open-data-mcp \
  --method prompts/list --format json | jq -r '.result.prompts[].name'
npx -y @modelcontextprotocol/inspector --cli uvx dominican-open-data-mcp \
  --method resources/templates/list --format json
```

### Logs

Claude Desktop writes one log file per server, plus its own:

```bash
tail -f ~/Library/Logs/Claude/mcp-server-datosgobdo.log   # macOS — this server
tail -n 20 -F ~/Library/Logs/Claude/mcp*.log              # macOS — all servers + the client
```

```powershell
type "$env:AppData\Claude\logs\mcp*.log"                  # Windows
```

The server logs startup (endpoint, transport, network-guard mode, archive on or off), cache hits and misses, page→file substitutions, suspicious parse shapes, misconfigured environment variables, fatal errors with traceback, and shutdown — all to stderr, which the client captures. Under `DATOSGOBDO_TRANSPORT=streamable-http` it does not: see [§13](#13-installation-and-client-configuration).

Logs contain resource URLs, cache keys and destination paths. They contain no credentials — the server holds none for the portal — and the optional GCP tools authenticate through your own ADC, which is never logged.

When the client itself is the suspect rather than the server, Claude Desktop can open Chrome DevTools: write `{"allowDevTools": true}` to `~/Library/Application Support/Claude/developer_settings.json`, then `Cmd-Option-I`. The Console panel shows client-side errors, the Network panel shows message payloads and timing.

### Iteration

1. Commit and push to `main`.
2. Clear the `uvx` cache to force a refresh: `uv cache clean dominican-open-data-mcp` (keyed on the distribution name, not the binary name).
3. Restart the MCP client.

For faster loops, point the client at your clone: `command: /path/to/clone/.venv/bin/datosgobdo-mcp`.

### Manual check against the live API

```bash
uv run python -c "
import asyncio
from datosgobdo_mcp import ckan
print(asyncio.run(ckan.get_site_stats()))
asyncio.run(ckan.close_client())
"
```

## 19. Contributing, credits, how to cite, licence

### Contributing

Pull requests welcome. Areas where help would land well:

- **Header detection.** 41 resources put a banner above the real header row. In XLSX this can cost the whole file: `precios_productos_primera_necesidad` (PROCONSUMIDOR) carries 890 rows in a sheet declaring `dimension A1:K890`, and reads as 1 column and 0 rows because cell A1 is a title. The CSV sibling recovers all 890 rows but names them `column00`…`column10`. Detecting and skipping the banner would recover real data.
- **Cross-format reconciliation.** Given a dataset with several formats, pick the trustworthy one rather than the first one listed.
- **JSON parsing**, the weakest format here.
- **Generalizing `ckan_endpoint`** so the same file-reading pipeline serves other DataStore-less portals in the region.
- **Windows testing**, currently unclaimed.

### Credits

Developed by **Alberto Castillo Aroca** ([@alcastaro](https://github.com/alcastaro)) with contributions from **Juana Casique** ([@juanacasique](https://github.com/juanacasique)).

Data published by the institutions of the Dominican State via [datos.gob.do](https://datos.gob.do), a portal operated by OGTIC.

Inspired by [`datagouv-mcp`](https://github.com/datagouv/datagouv-mcp) (Etalab, Government of France). For CKAN portals that do have DataStore enabled, [`ondata/ckan-mcp-server`](https://github.com/ondata/ckan-mcp-server) is the reference implementation and worth using instead — see [§12](#12-how-it-compares-with-other-ckan-mcp-servers).

### How to cite

If you use this server — or a figure obtained through it — in an article, report, dataset or talk, please cite it. GitHub's **"Cite this repository"** button reads [`CITATION.cff`](CITATION.cff) and offers APA and BibTeX directly.

> Castillo Aroca, A. (2026). *dominican-open-data-mcp: an MCP server for
> datos.gob.do* [Computer software]. OLDS — Observatorio Latinoamericano de
> Desarrollo Sostenible. https://github.com/alcastaro/datos.gob.do-MCP-server

This is a request, not a licence condition: the MIT terms are unmodified, so nothing here restricts your use. Citation matters for a different reason — figures from this catalog carry caveats (what a numeric coercion excluded, which files could not be downloaded at all), and a citation is how a reader gets back to them.

**Cite the institution too.** This server reads data; it does not produce it. Every figure belongs to the Dominican government body that published it, and `get_dataset` returns that institution's name for exactly this purpose.

### Licence

MIT. See [LICENSE](LICENSE).

Data accessed through this MCP is subject to the licence under which each Dominican institution publishes it on datos.gob.do. Verified across the catalog: **1,020 datasets are ODbL**, 15 CC-BY, 6 PDDL, 3 other public-domain terms, and **12 declare no licence at all** — those twelve should stay out of any redistribution.

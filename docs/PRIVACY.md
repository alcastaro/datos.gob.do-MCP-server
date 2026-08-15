# Privacy Policy — `dominican-open-data-mcp`

**Effective:** 2026-08-15 · **Applies to:** `dominican-open-data-mcp`
(`datosgobdo-mcp`), every version · **Operator:** OLDS — Observatorio
Latinoamericano de Desarrollo Sostenible · **Contact:** ai@olds2030.org

Spanish version: [`PRIVACIDAD.md`](PRIVACIDAD.md).

---

## Summary

- **No accounts, no credentials, no telemetry.** The server has no user
  registry and sends nothing to us.
- **Your search terms do leave your machine.** They travel to the
  `datos.gob.do` API, because that is where the search runs. This is the one
  thing worth reading twice.
- **Everything this server reads is public government data.** No private API,
  no scraped source.
- **Files are cached, questions are not.** Downloaded public files are cached
  as Parquet; prompts and results are never stored.

The server runs in two modes and the answers differ, so each section below
distinguishes them:

| Mode | Who runs it | Where data sits |
|---|---|---|
| **Local (stdio)** | you, on your own machine | your disk |
| **Hosted (streamable HTTP)** | OLDS, on rented infrastructure | ephemeral container disk |

---

## 1. What we collect

Nothing. There is no account system, no API key of ours, no analytics, no
crash reporting, no usage beacon. The server does not phone home in either
mode.

## 2. What leaves your machine, and to whom

Three outbound flows exist. All three are the ordinary work of reading an open
data portal — none is a side channel.

1. **Catalog requests → `https://datos.gob.do/api/3/action`.** Searching,
   listing and reading metadata sends your query to the portal's CKAN API.
   **If you search for `presupuesto salud 2026`, that string reaches
   datos.gob.do.** The portal is operated by OGTIC (Dominican Government) under
   its own privacy terms; we do not control its logs.
2. **File downloads → the publishing institution's own host.** Analytics tools
   fetch the resource file from wherever the institution published it, which is
   frequently *not* datos.gob.do. See the inventory in §3.
3. **Optional Google Cloud tools → your own GCP project.** Only if you
   installed the `[gcp]` extra. See §6.

Every outbound request identifies the tool in its `User-Agent`:

```
datosgobdo-mcp/<version> (MCP Server)
```

Publishing institutions can therefore see in their access logs that a request
came from this server. That is deliberate — it is how a portal operator can
distinguish a documented client from anonymous scraping.

## 3. Which systems this server connects to

The question "which databases is this thing touching?" deserves a real answer,
not a category. This inventory was measured over the catalog census of
**August 2026** — one resource per dataset, **1,056 resources across 258
publishing institutions**. It is a snapshot: institutions move files, and the
list is regenerable from the catalog at any time.

**One API host, and many file hosts.**

| Layer | Host | Notes |
|---|---|---|
| Catalog API | `datos.gob.do` | every search, listing and metadata read |
| Resource files | **273 distinct hosts** | wherever each institution published the file |

Of the 1,056 census resources, **1,033 (97.8 %) live on Dominican domains** —
`gob.do` (932), `mil.do` (48), `edu.do` (25), `com.do` (15), `gov.do` (10),
`tse.do` (3).
Frequent file hosts include `deepblue.simv.gob.do`, `www.fondomarena.gob.do`,
`descargas.one.gob.do`, `sb.gob.do`, `condei.gob.do`, `ambiente.gob.do` and
`cnss.gob.do`; 66 resources are hosted on `datos.gob.do` itself.

**The 23 resources that are not on a Dominican domain matter more for privacy,
because a foreign cloud provider sees the request.** The complete list:

| Resources | Host | Provider |
|---|---|---|
| 9 | `drive.google.com` | Google |
| 4 | `mopcstrapistorage.blob.core.windows.net` | Microsoft Azure |
| 4 | `institucionesestatales04-my.sharepoint.com` | Microsoft |
| 2 | `opencncc.web.app` | Google Firebase |
| 2 | `view.officeapps.live.com` | Microsoft |
| 1 | `uteco-my.sharepoint.com` | Microsoft |
| 1 | `tribunalsitestorage.blob.core.windows.net` | Microsoft Azure |

When you analyse one of those resources, your request reaches Google or
Microsoft, under their terms — not ours. We neither chose those hosts nor can
change them: they are where the Dominican institution decided to publish.

## 4. What is stored, where, and for how long

### Local mode (stdio) — the default

| What | Where | Retention |
|---|---|---|
| Parquet cache of downloaded public files | `~/.cache/datosgobdo-mcp` (override with `DATOSGOBDO_CACHE_DIR`) | until evicted by `DATOSGOBDO_CACHE_MAX_BYTES`, or until you run `clear_cache` |
| CSV exports | **only the path you pass** to `save_query_to_csv` | yours; we never touch it |
| Operational logs | your client's stderr log (e.g. Claude Desktop's `mcp-server-*.log`) | your client's policy |

Nothing in local mode is transmitted to OLDS. Your prompts, your results and
your files stay on your machine.

### Hosted mode (streamable HTTP)

| What | Where | Retention |
|---|---|---|
| Parquet cache of downloaded public files | ephemeral container disk | lost when the instance recycles |
| Prompts, questions, tool results | **not stored** | — |
| Operational logs (stderr) | operator's log stream; include the URLs fetched | short-lived operational retention |

Two honest disclosures about hosted mode:

- **Filesystem tools are disabled, not merely discouraged.** `clear_cache` and
  `save_query_to_csv` refuse to run, because the filesystem belongs to the
  server host and the cache is shared. `get_cache_stats` omits server paths.
- **The infrastructure provider sees connection metadata.** Whoever hosts the
  endpoint (CDN, container platform) processes IP addresses and request
  metadata under its own privacy policy. We do not control that layer and do
  not claim otherwise.

There are no accounts in hosted mode, so there is nothing to link a request to
a person on our side.

## 5. Personal data inside the public datasets

This server reads what Dominican institutions chose to publish. **Some public
datasets contain personal data** — payroll listings, staff registers, aid
beneficiaries. The server does not alter, enrich, cross-reference or retain
that data beyond the file cache described in §4, and it applies no special
treatment to it.

If you believe a published dataset should not contain the personal data it
contains, the responsible party is the publishing institution, not this
server. Contact them, or OGTIC as portal operator.

## 6. Optional Google Cloud tools

The `[gcp]` extra adds three preview tools that export a resource to your own
Google Cloud Storage bucket and BigQuery dataset. If you install and configure
it:

- Authentication uses **your own** Application Default Credentials. We never
  see them.
- Data lands in **your own** project, bucket and dataset.
- Google processes it under Google Cloud's terms.

These tools are not installed by default and are absent from the hosted
deployment.

## 7. Security controls that bear on privacy

- **SSRF guard on every outbound fetch.** Default mode `public-only`: only
  http/https, and every resolved IP must be globally routable — cloud metadata
  endpoints, loopback and private ranges are refused. `strict` mode confines
  fetches to `datos.gob.do` plus an operator allowlist.
- **Read-only SQL.** Analytical queries run against a Parquet copy with
  DuckDB's filesystem and network access disabled, so a query cannot read local
  files or reach the network.
- **Hosted hardening.** Local-filesystem and shared-destructive tools are
  disabled, and server paths are not returned to remote clients.

## 8. Third parties, in one place

| Third party | When it sees a request | Under whose policy |
|---|---|---|
| OGTIC / `datos.gob.do` | every catalog search and metadata read | OGTIC |
| Publishing institutions (273 hosts) | when you read or analyse their file | each institution |
| Google, Microsoft | for the 23 resources listed in §3 | Google / Microsoft |
| Hosting provider (hosted mode only) | every connection to the hosted endpoint | that provider |
| Google Cloud (`[gcp]` extra only) | when you export | Google Cloud |

We sell nothing, share nothing, and have nothing to share.

## 9. Your choices

- Run in **local mode** and nothing reaches OLDS at any point.
- Delete the cache at any time: `clear_cache`, or remove
  `~/.cache/datosgobdo-mcp`.
- Set `DATOSGOBDO_NETGUARD=strict` to confine downloads to `datos.gob.do`.
- Don't install the `[gcp]` extra if you don't want the export path to exist.

## 10. Children

Not directed at children. It collects nothing from anyone, so it collects
nothing from a child either.

## 11. Changes

Material changes are recorded in `CHANGELOG.md` and dated at the top of this
file. The version history of this document is the repository's own git history.

## 12. Contact

**ai@olds2030.org** — privacy questions, data concerns, security reports.
Security issues may also follow [`SECURITY.md`](../SECURITY.md).

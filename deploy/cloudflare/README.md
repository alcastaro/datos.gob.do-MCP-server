# Hosted deployment — Cloudflare (Workers + Containers)

**Status: written, never deployed.** Nothing here has run against Cloudflare.
Treat the files as a reviewed starting point, not as a verified deployment.
What *has* been verified is why it is shaped this way — see
["Why a container and not a Worker"](#why-a-container-and-not-a-worker).

The image itself is not Cloudflare-specific. [`../../Dockerfile`](../../Dockerfile)
runs the same on Cloud Run, Fly.io or any container host; only this directory
is Cloudflare.

## What this gives you

One HTTPS endpoint — say `https://mcp.olds2030.org/mcp` — that three clients
can use without anyone reviewing anything:

| Client | How the user adds it |
|---|---|
| Claude (web / desktop) | custom connector, paste the URL |
| ChatGPT | connector, paste the URL |
| Antigravity | `serverUrl` in `mcp_config.json` |

A directory listing adds discovery on top of that later. It is not what makes
the server installable.

## Why a container and not a Worker

Two independent reasons, both measured rather than assumed:

1. **DuckDB has no Pyodide wheel.** Python Workers run on Pyodide/Wasm and
   accept pure-Python or PyEmscripten wheels. DuckDB 1.5.5 publishes wheels for
   macOS, manylinux and Windows — none for Emscripten. Without DuckDB the whole
   analytics half of this server disappears: `query_resource`,
   `aggregate_resource`, `quantiles_resource`, `summarize_resource`,
   `filter_resource`, `find_duplicates_resource`, `detect_outliers_resource`.
2. **No listening socket.** `mcp.run(transport="streamable-http")` starts
   uvicorn. Workers has nowhere to put it.

The Worker still earns its place — as the front door. It terminates TLS, owns
the domain, and answers `/.well-known/openai-apps` for OpenAI's domain
verification, which must be served from the same host as the MCP endpoint.

## Files

| File | What it is |
|---|---|
| [`../../Dockerfile`](../../Dockerfile) | the image; base install, **no `[gcp]` extra** |
| `wrangler.jsonc` | container class, instance size, Durable Object binding |
| `src/index.js` | the front-door Worker |
| `package.json` | `@cloudflare/containers` + `wrangler` |

The dependency versions in `package.json` are placeholders. Run
`npm install @cloudflare/containers wrangler` once and let npm write the
versions that actually resolve.

## Prerequisites

- Workers **Paid** plan ($5/month) — Containers is not on the free plan
- Docker running locally: `wrangler deploy` builds the image on your machine
  and pushes it to Cloudflare's registry
- The domain on Cloudflare DNS (`mcp.olds2030.org`)

## Deploy

```bash
cd deploy/cloudflare
npm install
npx wrangler deploy
```

First deploy takes several minutes to provision. Then set the OpenAI token as a
secret — only needed when you get to the ChatGPT submission:

```bash
npx wrangler secret put OPENAI_VERIFICATION_TOKEN
```

## Verify before telling anyone the URL

Six checks. The first four confirm the hosted hardening actually engaged; the
fifth is the one a Worker could never have passed.

1. **Handshake and tool list.** MCP Inspector against the production URL: 24
   tools. If the count is 27, the `[gcp]` extra got into the image — rebuild.
2. **`get_cache_stats`** reports `transport: streamable-http` and **no**
   `cache_dir` key. Server paths must not reach remote clients.
3. **`clear_cache`** returns the hosted-disabled error rather than running.
4. **`save_query_to_csv`** does the same. It writes to the server's filesystem
   and has its own guard; it is the one people forget.
5. **A real analytical query end to end** — `query_resource` against a
   downloadable resource. This is DuckDB alive inside the container.
6. **Reachable from Anthropic's IP ranges**, a directory requirement.

## Sizing

Instance types: `lite` 256 MiB · `basic` 1 GiB · `standard-1` 4 GiB ·
`standard-2` 6 GiB. `basic` is the floor here — DuckDB plus one government XLSX
in the Parquet cache does not fit in 256 MiB.

`DATOSGOBDO_DUCKDB_MEMORY` and `DATOSGOBDO_DUCKDB_THREADS` are set well under
the instance size on purpose: DuckDB will use what you give it, and an OOM kill
looks to the client like a server that randomly drops connections.

## The cache, and what it does and does not mean

In hosted mode the Parquet cache is shared across everyone using that instance,
which is why the filesystem tools are disabled. Each container instance has its
own ephemeral disk, so nothing leaks between instances — but the cache also
starts cold on every new instance. If that shows up in latency measurements,
the place for a warm shared cache is R2, not the container disk. Not in the
first version.

## Not done here

- **OAuth.** Public government data, no accounts. Both Anthropic and OpenAI
  accept a no-auth server; see the internal distribution plan.
- **Rate limiting.** The Worker is where it would go if the endpoint gets
  abused.
- **A second region.** One is enough until it is not.

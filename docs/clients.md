# Using this server from any MCP client

Every configuration below is the same three facts spelled in that client's
syntax: run `uvx`, give it the package name `dominican-open-data-mcp`, talk
stdio. If your client is not listed, those three facts are the whole recipe.

Two rules save most support requests:

1. **Use the absolute path to `uvx`** when the client is a desktop app.
   GUI applications do not inherit your shell's `PATH`, so a bare `uvx`
   resolves in your terminal and fails inside the app. Find it with
   `which uvx` (macOS/Linux) or `where.exe uvx` (Windows).
2. **Restart the client completely** after changing configuration. MCP
   servers are negotiated when a session starts; editing configuration
   mid-conversation changes nothing until the next launch. On Windows and
   macOS, "close the window" is not "quit" — use the tray/dock menu.

## Claude Desktop

`claude_desktop_config.json` — macOS:
`~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\`.

```json
{
  "mcpServers": {
    "datosgobdo": {
      "command": "/Users/you/.local/bin/uvx",
      "args": ["dominican-open-data-mcp"]
    }
  }
}
```

On Windows use the full path to `uvx.exe` and either doubled backslashes
(`C:\\Users\\you\\.local\\bin\\uvx.exe`) or forward slashes. Non-technical
Windows users can run the one-line installer instead:

```powershell
irm https://raw.githubusercontent.com/alcastaro/datos.gob.do-MCP-server/main/scripts/instalar-windows.ps1 | iex
```

## Claude Code

```bash
claude mcp add --scope user datosgobdo -- uvx dominican-open-data-mcp
```

**`--scope user` is not optional if you want the server everywhere.** The
default scope is `local`, which binds the server to the directory you ran the
command in — launch `claude` anywhere else and the server silently does not
exist. This is a measured failure, not a hypothetical: a real session
installed with the default scope, then answered every data question with
hand-written Python because the tools never loaded.

## Antigravity

Settings → MCP servers, or the JSON config:

```json
{
  "mcpServers": {
    "datos-gob-do": {
      "command": "uvx",
      "args": ["dominican-open-data-mcp"],
      "env": {}
    }
  }
}
```

Antigravity does not load tool schemas into the prompt up front (it reads
them on demand), so the server costs it almost no context.

For the CLI (`agy`) there is a plugin that carries the same config:

```bash
git clone https://github.com/alcastaro/datos.gob.do-MCP-server.git
agy plugin install datos.gob.do-MCP-server/packaging/antigravity
```

`agy plugin install` takes a **directory** — there is no install-from-URL form,
so the clone is part of the recipe rather than a suggestion. See
[`../packaging/antigravity/README.md`](../packaging/antigravity/README.md).

The config paths, if you prefer writing them yourself, are
`~/.gemini/config/mcp_config.json` (global) or `.agents/mcp_config.json`
(workspace).

## Codex CLI

`~/.codex/config.toml`:

```toml
[mcp_servers.datosgobdo]
command = "uvx"
args = ["dominican-open-data-mcp"]
```

## OpenCode

`opencode.json` in your project or `~/.config/opencode/`:

```json
{
  "mcp": {
    "datosgobdo": {
      "type": "local",
      "command": ["uvx", "dominican-open-data-mcp"]
    }
  }
}
```

## Kiro / Pi / anything else that speaks MCP over stdio

The generic shape, whatever the surrounding syntax:

```
command: uvx
args:    ["dominican-open-data-mcp"]
transport: stdio
```

Optional environment variables (all clients accept an `env` block):
`DATOSGOBDO_CACHE_DIR`, `DATOSGOBDO_CACHE_MAX_BYTES`, `DATOSGOBDO_NETGUARD`
(`public-only` default | `strict` | `off`), `DATOSGOBDO_ARCHIVE_DIR`. See the
README for the full table.

## The 30-second smoke test, same for every client

Do not ask "is the MCP installed?" — clients answer that question badly, and
an assistant that cannot reach the tools will often answer your data question
anyway, by other means, without saying so. Ask this instead:

```
Suma el sueldo bruto de toda la nómina histórica del Ministerio de Trabajo
de República Dominicana y dime exactamente qué filas quedaron fuera del
cálculo y por qué.
```

- **The tools ran** if the answer arrives in seconds and says
  **RD$ 4,497,116,317.02** over 106,729 rows with **exactly one value
  excluded** — the literal string `Sueldo Bruto (RD$)`, a header the
  publisher left inside the data. Only this server reports that exclusion;
  it comes from the `numeric_coercion` block.
- **The tools were bypassed** if the answer takes minutes, the client shows
  Python or shell executions, or files appear in your working directory.
  The figure may even be right — the session downloaded the 40 MB file and
  computed it — but nothing in that path declares exclusions, verifies TLS,
  or caches the download. Fix the configuration and ask again.

Since 0.12.0 there is a sharper check: every analytics reply carries
`cache.source_sha256` (the digest of the bytes the figure was computed from)
and aggregate/query replies carry `computation` (the SQL that ran and the
rows it scanned). A figure quoted without those fields did not come from this
server.

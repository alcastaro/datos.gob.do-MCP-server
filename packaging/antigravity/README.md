# Antigravity plugin

Packages this MCP server as a plugin for Google's **Antigravity CLI** (`agy`),
the successor to Gemini CLI since June 2026.

## Install

```bash
git clone https://github.com/alcastaro/datos.gob.do-MCP-server.git
agy plugin install datos.gob.do-MCP-server/packaging/antigravity
```

**The clone is not decoration.** `agy plugin install` takes a directory — pass
it anything else and it answers `install target must be a directory`. There is
no install-from-URL form. Verified against `agy` 1.1.12 on 2026-08-15.

Check it landed:

```bash
agy plugin list
agy plugin validate packaging/antigravity
```

`validate` on this directory reports `mcpServers: 1 processed` and skips
skills, agents, commands and hooks — this plugin is one MCP server and nothing
else.

## Without the plugin

The plugin is a convenience. The same server works by writing the config
directly:

- global: `~/.gemini/config/mcp_config.json`
- workspace: `.agents/mcp_config.json`

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

## Remote (hosted) variant

Once a hosted endpoint exists, the same file points at it instead:

```json
{
  "mcpServers": {
    "datosgobdo": {
      "serverUrl": "https://mcp.olds2030.org/mcp/"
    }
  }
}
```

**The key is `serverUrl`.** Antigravity documents `url` and `httpUrl` as legacy
fields that are not supported — a config using them fails in a way that looks
like the server being down.

## Files

| File | What it is |
|---|---|
| `plugin.json` | the manifest; only `name` is required |
| `mcp_config.json` | the server definition, stdio variant |

## Open

- **How a plugin gets published.** `agy plugin install` accepts
  `plugin@marketplace`, and `agy plugin link <marketplace> <target>` exists, but
  Antigravity's public docs describe neither the marketplace format nor a
  submission path. Until that is clear, distribution is: clone the repo,
  install the directory.
- **Where plugins actually land.** The docs say
  `~/.gemini/antigravity-cli/plugins/`; `agy` 1.1.12 writes to
  `~/.gemini/config/plugins/`. Trust the binary.

# datosgobdo-mcp

Servidor [MCP](https://modelcontextprotocol.io) para [datos.gob.do](https://datos.gob.do) — el portal de datos abiertos del Gobierno de la República Dominicana.

Permite que cualquier LLM compatible con MCP (Claude Desktop, Claude Code, etc.) consulte y analice los datos publicados por instituciones gubernamentales dominicanas: ministerios, organismos autónomos, municipios.

Inspirado en [datagouv-mcp](https://github.com/datagouv/datagouv-mcp) (Francia), pero contra una plataforma distinta: `datos.gob.do` corre sobre **CKAN 2.11**, mientras que data.gouv.fr usa udata.

## Tools expuestas

| Tool | Qué hace |
|---|---|
| `search_datasets` | Busca datasets por palabra clave, organización, tag o grupo. |
| `get_dataset` | Metadatos completos de un dataset + todos sus recursos. |
| `list_recent_datasets` | Datasets modificados más recientemente. |
| `get_resource` | Metadatos de un recurso (archivo) individual. |
| `search_resources` | Busca recursos por nombre. |
| `download_resource_preview` | **Baja primeras N filas de un CSV/XLSX/JSON.** Cliente-side parsing (no hay DataStore en el portal). |
| `list_organizations` | Lista instituciones publicadoras con conteo de datasets. |
| `get_organization` | Detalle de una institución. |
| `list_groups` | Categorías temáticas (economía, salud, gestión pública…). |
| `list_tags` | Etiquetas disponibles. |
| `autocomplete` | Autocompleta nombres de datasets / organizaciones / grupos / tags. |
| `get_site_stats` | Conteos globales del portal. |

## Instalación

Requiere Python 3.10+.

### Con [uv](https://docs.astral.sh/uv/) (recomendado)

```bash
uv sync
uv run datosgobdo-mcp
```

### Con pip

```bash
pip install -e .
datosgobdo-mcp
```

## Configuración en Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

```json
{
  "mcpServers": {
    "datosgobdo": {
      "command": "uvx",
      "args": ["--from", "/ruta/absoluta/al/repo", "datosgobdo-mcp"]
    }
  }
}
```

O si ya está instalado con pip:

```json
{
  "mcpServers": {
    "datosgobdo": {
      "command": "datosgobdo-mcp"
    }
  }
}
```

## Configuración en Claude Code

```bash
claude mcp add datosgobdo -- uvx --from /ruta/absoluta/al/repo datosgobdo-mcp
```

## Ejemplo de uso

Una vez configurado, podés pedirle al LLM cosas como:

- "Busca datasets sobre presupuesto del gobierno dominicano"
- "¿Qué publica el Ministerio de Salud en datos.gob.do?"
- "Muéstrame las primeras 30 filas del CSV de nómina del Ministerio de Agricultura"
- "Listá las 10 organizaciones con más datasets publicados"

## Notas técnicas

- **API**: CKAN 2.11.3 — `https://datos.gob.do/api/3/action/`
- **DataStore**: NO está instalado en el portal. Por eso `download_resource_preview` baja el archivo y lo parsea cliente-side (tope de 5 MB).
- **Formatos soportados en preview**: CSV, TSV, XLSX, XLS, JSON. (ODS y PDF no se previsualizan; URL de descarga manual.)
- **Solr escape**: filtros `fq` escapan correctamente los caracteres reservados de Solr/Lucene.
- **Truncado**: descripciones largas se truncan a 300 chars en respuestas-listado para no quemar contexto del LLM.

## Stack

- [`mcp`](https://pypi.org/project/mcp/) — Python SDK oficial de Anthropic
- [`httpx`](https://www.python-httpx.org/) — cliente HTTP asíncrono
- [`openpyxl`](https://openpyxl.readthedocs.io/) — lectura de XLSX

## Estructura

```
src/datosgobdo_mcp/
  server.py        # FastMCP server + tool definitions
  ckan.py          # Cliente CKAN + formatters + Solr escaping
  preview.py       # Descarga + parsing CSV/XLSX/JSON
```

## Licencia

MIT — Alberto Castillo Aroca

## Archivos legacy (JS)

La versión inicial en JavaScript queda en `index.js`, `tools.js`, `ckan.js`, `package.json`. Pueden borrarse — todo está reescrito en Python.

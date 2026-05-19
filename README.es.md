<!-- mcp-name: io.github.alcastaro/datos.gob.do-MCP-server -->

**[English](README.md) · [Español](README.es.md)**

---

# datosgobdo-mcp

**Servidor [Model Context Protocol](https://modelcontextprotocol.io) que expone los datos abiertos de la República Dominicana ([datos.gob.do](https://datos.gob.do)) como herramientas consumibles por cualquier asistente de IA.**

Convierte el portal oficial de datos abiertos del Estado dominicano en una integración nativa para Claude Desktop, Claude Code, Cursor, ChatGPT Desktop o cualquier cliente compatible con MCP. Permite que el modelo busque, lea, analice y previsualice los 1,053+ datasets publicados por las 266 instituciones gubernamentales dominicanas, todo desde una conversación.

---

## ¿Qué problema resuelve?

[datos.gob.do](https://datos.gob.do) publica miles de archivos CSV, XLSX y JSON con información pública: nóminas, presupuestos, estadísticas de seguridad, indicadores de salud, datos electorales, etc. Hoy esa información es accesible solo a quien sabe navegar el portal CKAN y descargar archivos manualmente.

Este MCP cierra esa brecha. Cualquier persona puede preguntarle a su asistente:

- *"¿Cuánto gasta el Poder Judicial en remuneraciones?"*
- *"Compará el presupuesto aprobado vs. ejecutado de FONDOMARENA en los últimos tres años."*
- *"Listame las 10 instituciones que más datos publican."*
- *"¿Qué columnas tiene el dataset de robos del Ministerio de Interior?"*

…y el modelo, sin que el usuario tenga que escribir código, navegar a URLs ni descargar archivos, ejecuta las consultas reales contra el portal, baja los datos, los parsea y los analiza.

## ¿Para quién es?

- **Periodistas de datos** que quieren explorar fuentes oficiales sin escribir scrapers.
- **Investigadores y académicos** que necesitan acceso programático a datos del Estado dominicano.
- **Activistas de transparencia y sociedad civil** que monitorean ejecución presupuestaria, contrataciones, gestión pública.
- **Desarrolladores y científicos de datos** que prototipan dashboards o análisis sobre datos públicos.
- **Funcionarios públicos** que quieren consultar lo que su propia institución (u otras) ya publica.
- **Cualquiera con curiosidad cívica** sobre cómo opera el gobierno.

## ¿Qué es MCP?

[Model Context Protocol](https://modelcontextprotocol.io) es un estándar abierto (creado por Anthropic, adoptado por OpenAI y otros) que permite a los modelos de lenguaje conectarse de forma segura a fuentes de datos y herramientas externas. Un "servidor MCP" expone una colección de funciones tipadas; el modelo decide cuándo invocarlas, con qué argumentos, y cómo combinar los resultados.

Este proyecto es un servidor MCP especializado en `datos.gob.do`.

## ¿Qué es datos.gob.do?

Es el portal oficial de datos abiertos del Gobierno dominicano, administrado por la Oficina Gubernamental de Tecnologías de la Información y Comunicación (OGTIC). Corre sobre **CKAN 2.11.3**, el mismo software de gestión de datos abiertos que usan portales como data.gov (EEUU), data.gov.uk y muchos gobiernos latinoamericanos.

A mayo de 2026 contiene aproximadamente:

- **1,053 datasets** publicados
- **266 organizaciones** publicadoras (ministerios, alcaldías, organismos autónomos, etc.)
- **11 categorías temáticas** (Economía, Salud, Educación, Gestión Pública…)
- **852 etiquetas**

Cada dataset agrupa uno o varios "recursos" (archivos descargables) en formatos como CSV, XLSX, ODS, PDF o JSON.

Este MCP está inspirado en [`datagouv-mcp`](https://github.com/datagouv/datagouv-mcp) (Francia), pero datos.gob.do corre una plataforma distinta (CKAN, no udata), así que la implementación es propia.

---

## Herramientas (tools) expuestas

12 funciones tipadas, organizadas en cuatro grupos:

### Descubrimiento

| Tool | Qué hace |
|---|---|
| `search_datasets` | Busca datasets por palabra clave, organización, tag o grupo. Filtros combinables, paginación. |
| `get_dataset` | Devuelve metadatos completos de un dataset: título, descripción, licencia, autor, y la lista completa de sus recursos con URLs de descarga directa. |
| `list_recent_datasets` | Datasets ordenados por fecha de última modificación. Útil para monitorear actualizaciones del portal. |
| `get_site_stats` | Conteos globales del portal (totales de datasets, organizaciones, grupos, tags). |

### Recursos (archivos)

| Tool | Qué hace |
|---|---|
| `get_resource` | Metadatos de un recurso individual (URL, formato, tamaño, fecha). |
| `search_resources` | Busca recursos por nombre. |
| `download_resource_preview` | **Baja un archivo y devuelve las primeras N filas con sus columnas.** Funciona con CSV, TSV, XLSX, XLS y JSON. Cliente-side parsing porque el portal no tiene DataStore. Tope de 5 MB. |

### Catálogo

| Tool | Qué hace |
|---|---|
| `list_organizations` | Todas las instituciones que publican, con conteo de datasets de cada una. |
| `get_organization` | Detalle de una institución (descripción, número de datasets, URL). |
| `list_groups` | Categorías temáticas con conteo de datasets. |
| `list_tags` | Etiquetas disponibles, opcionalmente filtradas por prefijo. |

### Autocompletado

| Tool | Qué hace |
|---|---|
| `autocomplete` | Resuelve nombres parciales para datasets, organizaciones, grupos o tags. Útil cuando el usuario solo da nombre parcial — el modelo lo usa internamente para encontrar slugs exactos. |

---

## Instalación y configuración

### Opción A — Vía `uvx` desde GitHub (recomendado, cero clone manual)

Requisito previo: [`uv`](https://docs.astral.sh/uv/) instalado. En macOS:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Luego configurar el cliente MCP. El comando es siempre el mismo:

```
uvx --from git+https://github.com/alcastaro/datos.gob.do-MCP-server.git datosgobdo-mcp
```

`uvx` clona el repo a `~/.cache/uv/`, crea un venv aislado, instala las dependencias y arranca el servidor. La primera vez tarda unos segundos; subsiguientes son instantáneas.

### Opción B — Clone local (para desarrollo)

```bash
git clone https://github.com/alcastaro/datos.gob.do-MCP-server.git
cd datos.gob.do-MCP-server
uv sync
uv run datosgobdo-mcp   # arranca el servidor en stdio (Ctrl+C para salir)
```

> **Nota macOS:** evitá clonar dentro de `~/Library/CloudStorage/GoogleDrive-*` o similares. macOS bloquea la ejecución de binarios desde paths sincronizados con cloud (TCC restriction). Usá `~/code/` o equivalente.

### Configuración en Claude Desktop

Editar `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) o `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "datosgobdo": {
      "command": "/Users/TU_USUARIO/.local/bin/uvx",
      "args": [
        "--from",
        "git+https://github.com/alcastaro/datos.gob.do-MCP-server.git",
        "datosgobdo-mcp"
      ]
    }
  }
}
```

Reiniciar Claude Desktop completamente (Cmd+Q, no solo cerrar la ventana). Settings → Developer → Local MCP servers debería mostrar `datosgobdo` en estado **running**.

### Configuración en Claude Code

```bash
claude mcp add datosgobdo -- uvx --from git+https://github.com/alcastaro/datos.gob.do-MCP-server.git datosgobdo-mcp
```

### Configuración en Cursor / otros clientes

Mismo principio: registrar `uvx` como comando con los args `--from git+... datosgobdo-mcp`. Consultar la documentación específica de cada cliente para la ubicación del archivo de configuración.

---

## Ejemplos de uso

Una vez configurado, podés pedirle al modelo:

### Exploración básica

> *Usá el MCP datosgobdo y dime cuántos datasets hay en el portal datos.gob.do.*

→ Invoca `get_site_stats`. Respuesta: 1,053 datasets, 266 organizaciones.

### Búsqueda con análisis

> *Buscá los 5 datasets más relevantes sobre presupuesto en datos.gob.do y resumime de qué institución es cada uno.*

→ Invoca `search_datasets(query="presupuesto", limit=5)` y el modelo redacta el resumen.

### Resolución de nombres + detalle

> *Encontrá el slug del Ministerio de Hacienda y dime cuántos datasets tiene publicados.*

→ `autocomplete(kind="organization", query="hacienda")` → `get_organization(id="ministerio-de-hacienda")`.

### Análisis de datos reales

> *Mostrame las primeras 20 filas del CSV de presupuesto del Poder Judicial y dime cuáles son las tres partidas más grandes.*

→ `search_datasets(query="poder judicial")` → `get_dataset("presupuesto-poder-judicial")` → `download_resource_preview(url=..., format="csv", rows=20)` → el modelo identifica las partidas mayores.

### Monitoreo

> *Listame los 10 datasets que se actualizaron más recientemente en el portal.*

→ `list_recent_datasets(limit=10)`.

---

## Arquitectura

```
src/datosgobdo_mcp/
  server.py        FastMCP server + definiciones de tools (Pydantic typed)
  ckan.py          Cliente CKAN: requests, Solr escaping, formatters
  preview.py       Descarga capada de archivos + parsers CSV/XLSX/JSON
```

### Decisiones de diseño

- **FastMCP en lugar del SDK low-level**: tools como funciones decoradas con `@mcp.tool()` + tipos Pydantic. Menos boilerplate, validación automática de argumentos.
- **`httpx.AsyncClient` reutilizado**: una sola conexión persistente, sin overhead de TCP handshake en cada request.
- **Solr escape**: los filtros `fq` de CKAN usan sintaxis Solr/Lucene. Valores user-supplied pasan por `_escape_solr()` que escapa los 13 caracteres reservados (`+ - & | ! ( ) { } [ ] ^ " ~ * ? : \ /`). Sin esto, un tag con comilla rompería la query.
- **Truncado defensivo**: descripciones largas (algunas instituciones publican 5+ KB de texto por org) se truncan a 300 chars en respuestas de listado. Sin esto, una sola consulta podría quemar miles de tokens del contexto del modelo.
- **`list_recent_datasets` reorientado**: la API CKAN expone `recently_changed_packages_activity_list` pero devuelve "actividades" con metadatos crudos sin hidratar — el modelo recibiría `{object_id: "uuid", activity_type: "changed package"}` sin saber qué dataset es. Usamos `package_search?sort=metadata_modified+desc` para devolver datasets ya formateados en una sola llamada.
- **DataStore no disponible**: el portal datos.gob.do no tiene la extensión DataStore instalada, así que no hay endpoint `datastore_search` ni queries SQL sobre el contenido de los recursos. La solución es `download_resource_preview`: bajamos el archivo (tope 5 MB) y lo parseamos del lado cliente con `csv` (stdlib) u `openpyxl`. Suficiente para que el modelo entienda la estructura.
- **Fallback de encoding**: muchos archivos publicados están en CP1252 o Latin-1 (no UTF-8). El parser intenta UTF-8 → UTF-8-sig → Latin-1 → CP1252 → UTF-8 con `errors=replace`.
- **Logging a stderr**: per [MCP debugging guide](https://modelcontextprotocol.io/docs/tools/debugging), los servidores stdio nunca deben escribir a stdout (rompe el protocolo). Todo log va a stderr y es capturado por el cliente en `~/Library/Logs/Claude/mcp-server-datosgobdo.log` (macOS).

### Stack técnico

- [`mcp`](https://pypi.org/project/mcp/) — Python SDK oficial de Anthropic (FastMCP)
- [`httpx`](https://www.python-httpx.org/) — cliente HTTP asíncrono
- [`openpyxl`](https://openpyxl.readthedocs.io/) — lectura de XLSX en streaming read-only
- `csv`, `json` — stdlib para los otros formatos

---

## Limitaciones conocidas

- **Sin queries SQL** sobre el contenido de recursos: el portal no tiene DataStore. Workaround: `download_resource_preview` + análisis hecho por el modelo.
- **Preview limitado a 5 MB**: archivos más grandes se truncan. Suficiente para entender estructura, no para análisis estadístico completo.
- **Sin soporte de ODS y PDF en preview**: solo CSV, TSV, XLSX, XLS y JSON. Archivos ODS y PDF se exponen con su URL de descarga directa.
- **Solo lectura**: el MCP no escribe en el portal (no hay autenticación, no expone endpoints `package_create`, `resource_create`, etc.). Por diseño.
- **Encodings raros**: ya hay fallback (UTF-8 → CP1252), pero archivos con encoding exótico pueden mostrar caracteres rotos.

---

## Desarrollo

### Setup local

```bash
git clone https://github.com/alcastaro/datos.gob.do-MCP-server.git
cd datos.gob.do-MCP-server
uv sync
```

### Probar con el MCP Inspector

[MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector) es la herramienta oficial para testear servidores MCP de forma aislada:

```bash
npx @modelcontextprotocol/inspector uv run datosgobdo-mcp
```

Abre `http://localhost:6274` con form-builder para invocar tools manualmente y ver request/response JSON crudo.

### Logs

En Claude Desktop (macOS): `tail -f ~/Library/Logs/Claude/mcp-server-datosgobdo.log`

El servidor loguea a stderr:
- Startup (endpoint, número de tools registradas)
- Errores fatales con traceback completo
- Shutdown

### Iteración

Cuando edites código:

1. Commit + push a `main` en GitHub.
2. Limpiar cache de `uvx` para forzar refresh: `uv cache clean datosgobdo-mcp`.
3. Reiniciar el cliente MCP.

O para iteración rápida, configurar el cliente para apuntar a tu clone local en vez del repo de GitHub: `command: /ruta/al/clone/.venv/bin/datosgobdo-mcp`.

### Tests manuales contra API real

```bash
uv run python -c "
import asyncio
from datosgobdo_mcp import ckan
print(asyncio.run(ckan.get_site_stats()))
asyncio.run(ckan.close_client())
"
```

---

## Contribuir

Pull requests bienvenidos. Áreas de mejora obvias:

- Tests automatizados con `pytest-httpx` (mockear CKAN).
- Tool `summarize_csv` con estadísticas agregadas (count, min, max, distinct values por columna).
- Soporte de preview para ODS y Parquet.
- Cache local de respuestas frecuentes (organizaciones, grupos, tags cambian poco).
- Tool `find_dataset_about` que combine `autocomplete` + `search_datasets` con ranking semántico.

---

## Créditos

Desarrollado por **Alberto Castillo Aroca** ([@alcastaro](https://github.com/alcastaro)) con contribuciones de **Juana Casique** ([@juanacasique](https://github.com/juanacasique)).

Datos publicados por las instituciones del Estado dominicano vía [datos.gob.do](https://datos.gob.do), portal administrado por OGTIC.

Inspirado en [`datagouv-mcp`](https://github.com/datagouv/datagouv-mcp) (Etalab, Gobierno de Francia).

## Licencia

MIT. Ver [LICENSE](LICENSE) si existe, o asumir términos estándar MIT.

Los datos accedidos a través de este MCP están sujetos a la licencia con la que cada institución dominicana los publica en datos.gob.do (típicamente **Open Data Commons Open Database License — ODbL**).

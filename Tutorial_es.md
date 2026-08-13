<!-- mcp-name: io.github.alcastaro/datos.gob.do-MCP-server -->

**[English](Tutorial.md) · [Español](Tutorial_es.md)**

---

# Tutorial — Cómo funciona `datosgobdo-mcp` y cómo construir uno igual

Documento educativo. La Parte 1 enseña a **usar** el servidor. La Parte 2 explica
**cómo está construido**, módulo por módulo. La Parte 3 es una receta paso a paso para
**construir el tuyo** sobre un portal de datos abiertos (o cualquier API HTTP).

> El repositorio canónico es [`alcastaro/datos.gob.do-MCP-server`](https://github.com/alcastaro/datos.gob.do-MCP-server).
> Todo lo de abajo referencia código real de ese repo — ábrelo junto a este tutorial.

---

## 0. El modelo mental en un párrafo

Un **servidor MCP** es un programa pequeño que expone *funciones tipadas* (llamadas
**herramientas** / tools) a un asistente de IA. El asistente decide cuándo llamarlas, con
qué argumentos y cómo combinar los resultados. Las herramientas de este servidor envuelven
el portal de datos abiertos de República Dominicana ([datos.gob.do](https://datos.gob.do),
que corre **CKAN**). El servidor **no tiene datos propios** — cada llamada baja datos en
vivo del portal del gobierno, los analiza localmente con **DuckDB**, y devuelve una porción
al modelo.

---

## Parte 1 — Usar el servidor

### 1.1 Instalar

```bash
# Cualquier cliente MCP (Claude Desktop, Claude Code, Cursor, …) lo corre con uvx:
uvx dominican-open-data-mcp
```

Config de Claude Desktop (`claude_desktop_config.json`):

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

Las opciones van en un objeto `"env"` junto a `"args"` — `{"env": {"DATOSGOBDO_NETGUARD": "strict"}}` — y **no** en tu shell. Un servidor stdio hereda del cliente solo un subconjunto limitado del entorno, así que un `export` en tu terminal nunca le llega. Por la misma razón: usa rutas absolutas en todo, porque el directorio de trabajo de un servidor lanzado por el cliente es indefinido (`/` en macOS). Los dos hechos salen de la [guía de depuración de MCP](https://modelcontextprotocol.io/docs/tools/debugging), y los dos cuestan horas de depuración cuando construyes tu propio servidor — ver el Paso 7.

Reinicia el cliente. Aparece automáticamente todo lo que el servidor ofrece: **24 tools, 3 resources con 1 plantilla de URI, y 6 prompts**. Esta parte del tutorial recorre primero las tools porque son las que hacen el trabajo; la §1.7 cubre las otras dos, que es por donde debería empezar casi todo el mundo.

### 1.2 Las cinco categorías de herramientas

Ojo con la colisión de nombres, porque despista: MCP tiene una primitiva llamada *resources* (§1.7), y este catálogo también llama *recursos* a sus archivos descargables. La categoría de abajo es el segundo sentido — los archivos de CKAN.

| Categoría | Herramientas | Para qué sirven |
|---|---|---|
| **Descubrimiento** | `search_datasets`, `get_dataset`, `list_recent_datasets`, `get_site_stats` | Encontrar datasets |
| **Archivos de recursos** | `get_resource`, `search_resources`, `download_resource_preview`, `check_resources` | Inspeccionar archivos individuales, y preguntar si se pueden descargar |
| **Analytics** | `get_resource_schema`, `summarize_resource`, `filter_resource`, `aggregate_resource`, `query_resource`, `quantiles_resource`, `find_duplicates_resource`, `detect_outliers_resource`, `save_query_to_csv`, `get_cache_stats`, `clear_cache` | Consultar los datos reales |
| **Catálogo** | `list_organizations`, `get_organization`, `list_groups`, `list_tags` | Navegar el catálogo de instituciones/temas |
| **Autocompletado** | `autocomplete` | Resolver nombres parciales → slugs exactos |

### 1.3 El flujo típico (y por qué va en ese orden)

Una buena conversación analítica baja por un embudo, gastando el menor contexto primero:

```
search_datasets        →  encuentra el dataset         (barato: solo metadatos)
get_dataset            →  obtén las URLs de recursos    (barato: solo metadatos)
get_resource_schema    →  ve columnas + tipos           (descarga + cachea una vez)
summarize_resource     →  stats por columna             (sin filas crudas en contexto)
aggregate_resource     →  la respuesta real             (GROUP BY, sin escribir SQL)
filter_resource        →  saca registros específicos    (cuando necesitas filas)
save_query_to_csv      →  exporta a Excel               (el punto final)
```

Al modelo se le enseña (vía descripciones de las tools) a hacer reconocimiento antes de
sacar filas, así nunca inunda su propia ventana de contexto con una nómina de 800,000 filas.

### 1.4 Ejemplo: "¿Cuántos empleados por estatus, abril 2026?"

El modelo lo traduce a una sola llamada a `aggregate_resource` — **sin SQL**:

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

La primera llamada baja el archivo una vez (tope de 100 MB) y lo cachea como Parquet.
Cada llamada posterior contra la misma URL es sub-segundo.

### 1.5 El escape hatch: `query_resource`

Cuando las herramientas tipadas no alcanzan, el modelo puede escribir SQL read-only contra
una tabla llamada `data`:

```sql
SELECT Estatus, COUNT(*) c FROM data WHERE Año=2026 GROUP BY Estatus ORDER BY c DESC
```

Está **aislado en sandbox** (ver §2.5) — físicamente no puede leer tus archivos locales ni
acceder a la red, aunque sea SQL libre.

### 1.6 Verlo sin un asistente de por medio: el MCP Inspector

El [MCP Inspector](https://modelcontextprotocol.io/docs/2026-07-28/tools/inspector) es la
herramienta de desarrollo del propio protocolo. Habla MCP directamente, así que muestra
lo que el servidor expone de verdad — sin un modelo en medio decidiendo qué mencionar.
Necesita Node 22.19+; no hay que instalar nada.

```bash
npx -y @modelcontextprotocol/inspector uvx dominican-open-data-mcp
```

Imprime una URL con un token de un solo uso. Al abrirla hay cuatro paneles, uno por cada
primitiva del protocolo:

- **Tools** — las 24, con sus esquemas de entrada y salida y sus anotaciones. Llama
  `aggregate_resource` desde ahí y ves el `structuredContent` crudo: la cifra, el bloque
  `numeric_coercion` nombrando lo que quedó fuera, el `source_sha256` y el SQL en
  `computation`. Es exactamente lo que recibe un modelo, sin editar.
- **Resources** — el catálogo como contexto de sólo lectura.
- **Prompts** — las seis plantillas, que de paso sirven de guía sobre qué preguntar; ver §1.7.
- **Monitoring** — el tráfico JSON-RPC en vivo. Útil cuando algo se ve raro y hay que
  saber si la causa fue el servidor o el cliente.

Para scripts y CI hay un modo CLI que ejecuta una petición y sale:

```bash
npx -y @modelcontextprotocol/inspector --cli uvx dominican-open-data-mcp \
  --method tools/list --format json | jq -r '.result.tools[].name'
```

Los códigos de salida significan algo: `0` éxito, `3` requiere autenticación, `4`
inalcanzable, `5` la herramienta devolvió error. Ese último funciona porque este servidor
marca las llamadas fallidas con `isError` — el porqué de ese arreglo está en la entrada
0.13.0 del changelog.

**Probar una compilación local en vez del paquete publicado.** El Inspector interpreta
todo lo que va después del comando del servidor como flags suyos, así que
`uvx --from ./dist/….whl …` falla con `Connection closed`: se traga el `--from`. Envuelve
el lanzamiento en un script, que además es exactamente lo que hace la configuración de un
cliente real:

```bash
mkdir -p ~/bin
cat > ~/bin/datosgobdo-server.sh <<'EOF'
#!/bin/sh
exec uvx --from "$HOME/ruta/al/dominican_open_data_mcp-X.Y.Z-py3-none-any.whl" \
  dominican-open-data-mcp
EOF
chmod +x ~/bin/datosgobdo-server.sh

npx -y @modelcontextprotocol/inspector ~/bin/datosgobdo-server.sh
```

El repositorio incluye `scripts/inspector.sh`, que hace las dos cosas: el paquete
publicado por defecto, o un wheel local si le pasas uno.

### 1.7 Prompts, resources y plantillas — los otros dos tercios de MCP

Casi todo tutorial de MCP cubre las tools y ahí para. Eso deja fuera las dos primitivas
que deciden *quién tiene el control*, que es la parte que vale entender:

| Primitiva | Quién decide cuándo se usa | Especificación |
|---|---|---|
| **Tools** | el **modelo**, en medio de la conversación | [server/tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) |
| **Resources** | la **aplicación**, adjuntando un URI | [server/resources](https://modelcontextprotocol.io/specification/2026-07-28/server/resources) |
| **Prompts** | el **usuario**, deliberadamente | [server/prompts](https://modelcontextprotocol.io/specification/2026-07-28/server/prompts) |

#### Prompts: el punto de entrada que controla el usuario

Un prompt es una plantilla que invocas a propósito — normalmente un comando de barra. No
es una tool que el modelo pueda elegir, y eso es justamente el punto: permite que quien
escribe el servidor distribuya un método, no solo capacidades.

Este servidor tiene seis. Empieza aquí:

```
/empezar_aqui
```

Los otros cinco reciben un argumento cada uno — `/serie_temporal` (un tema),
`/auditar_nomina` (una institución), `/verificar_fuente` (una URL),
`/explorar_institucion` (una institución), `/cruzar_fuentes` (un tema). En un cliente que
presenta los prompts como menú en vez de comandos de barra, eliges desde ese menú; el
soporte es opcional por cliente, así que revisa el
[directorio de clientes](https://modelcontextprotocol.io/clients).

**La lección está en por qué existen estos seis.** Cada uno codifica un error que este
catálogo invita a cometer. Un dataset titulado `2020-2025` puede contener solo 2022, así
que `serie_temporal` declara el período real antes de graficar nada. Una columna de nómina
deja de ser numérica porque 37 celdas dicen `#REF!`, así que `auditar_nomina` reporta las
filas excluidas junto a cualquier total. La mitad del catálogo no se puede descargar, así
que `empezar_aqui` lo dice antes de que te encariñes con una pregunta. Veinticuatro tools
no pueden enseñar eso; un prompt sí.

En código es un decorador que devuelve un string:

```python
@mcp.prompt(
    name="serie_temporal",
    title="Serie temporal — declarando el periodo real",
    description="Serie por año declarando el periodo real y sin tratar el año como medida.",
)
def serie_temporal(tema: str) -> str:
    return f"Arma una serie anual sobre {tema}. …"
```

El nombre del parámetro se vuelve el argumento del prompt, y su presencia lo hace
obligatorio. Aquí no ocurre I/O — un prompt devuelve texto, y el modelo hace el trabajo.

#### Resources: contexto que controla la aplicación

Un resource es dato que la *aplicación anfitriona* puede adjuntar, direccionado por URI.
No se llama nada; no hay efectos secundarios. Recurre a uno cuando un hecho es pequeño,
estable, y un desperdicio de una llamada a herramienta redescubrirlo:

```
datosgobdo://catalog/overview       application/json   totales del portal
datosgobdo://catalog/institutions   application/json   quién publica, con conteos
datosgobdo://guide/verification     text/markdown      cómo hacer comprobable una cifra
```

El tercero es la decisión de diseño interesante. Podría haber sido un prompt, y no debe
serlo: no es una petición de actuar, es texto de referencia que conviene tener en contexto
mientras trabajas. **Prompt = «haz esto». Resource = «ten presente esto».**

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

#### Plantillas de recurso: una definición, todos los datasets

Una [plantilla de recurso](https://modelcontextprotocol.io/specification/2026-07-28/server/resources#resource-templates)
es un URI con un parámetro, para no tener que enumerar 1.061 resources estáticos — uno por
cada dataset del catálogo — y que cada uno sea direccionable:

```
datosgobdo://dataset/{dataset_id}       →  datosgobdo://dataset/nomina-poder-judicial
```

En FastMCP el placeholder del URI y el parámetro de la función solo tienen que coincidir:

```python
@mcp.resource("datosgobdo://dataset/{dataset_id}", ...)
async def dataset_resource(dataset_id: str) -> dict[str, Any]: ...
```

Las plantillas las lista `resources/templates/list`, **no** `resources/list` — una fuente
común de «mi plantilla no aparece».

#### Ver las tres de verdad

El Inspector es la vista honesta, porque muestra el protocolo y no la interpretación que
hace un cliente de él:

```bash
npx -y @modelcontextprotocol/inspector --cli uvx dominican-open-data-mcp \
  --method prompts/list --format json
npx -y @modelcontextprotocol/inspector --cli uvx dominican-open-data-mcp \
  --method resources/list --format json
npx -y @modelcontextprotocol/inspector --cli uvx dominican-open-data-mcp \
  --method resources/templates/list --format json
```

En la interfaz, el panel **Prompts** renderiza el texto expandido de un prompt antes de que
algo llegue a un modelo — la forma más rápida de ver qué va a pedir realmente un comando
de barra.

#### Lo que este servidor no implementa

MCP define también primitivas del lado del cliente — [sampling](https://modelcontextprotocol.io/specification/2026-07-28/client/sampling)
(el servidor le pide al cliente una respuesta del modelo), [elicitation](https://modelcontextprotocol.io/specification/2026-07-28/client/elicitation)
(el servidor le pide un dato al usuario en medio de una llamada) y [roots](https://modelcontextprotocol.io/specification/2026-07-28/client/roots)
(el cliente le dice al servidor qué directorios están en alcance). Ninguna se usa aquí, y
la declaración de capacidades lo dice en vez de reclamarlas:

```json
"capabilities": {
  "tools":     {"listChanged": false},
  "resources": {"subscribe": false, "listChanged": false},
  "prompts":   {"listChanged": false}
}
```

`listChanged: false` es una promesa, no una carencia: las listas quedan fijas al arrancar,
así que un cliente puede cachearlas.

---

## Parte 2 — Cómo está construido

### 2.1 La forma: servidor local stdio

Este es un **servidor MCP local stdio**: corre en la máquina del usuario, lanzado por el
cliente, comunicándose por stdin/stdout. La regla más importante:

> **Nunca `print()` a stdout.** stdout es el canal del protocolo MCP. Todos los logs van a
> stderr. (Ver `server.py` — `logging.basicConfig(stream=sys.stderr, ...)`.)

El cliente captura ese stderr y lo escribe en un archivo — `~/Library/Logs/Claude/mcp-server-datosgobdo.log` en Claude Desktop para macOS, `%APPDATA%\Claude\logs\mcp*.log` en Windows. Dos cosas que vale saber:

- **El protocolo tiene su propio canal de logging y no conviene usarlo.** `notifications/message` quedó [desaconsejado desde la especificación `2026-07-28`](https://modelcontextprotocol.io/docs/tools/debugging); stderr es lo que la especificación ahora recomienda. Este servidor nunca lo usó, así que no hay nada que migrar — el punto es no agregarlo.
- **Sobre Streamable HTTP nadie captura el stderr por ti.** La comodidad de stdio es una propiedad del transporte, no de MCP. Un servidor hospedado necesita su propia recolección de logs o [OpenTelemetry](https://opentelemetry.io/).

### 2.2 Mapa de módulos

```
src/datosgobdo_mcp/
├── server.py     Entrada FastMCP — 24 decoradores @mcp.tool (la superficie pública)
├── ckan.py       Cliente HTTP CKAN async + escape de Solr + formateadores JSON
├── download.py   Descarga con tope + detección de encoding
├── preview.py    Parsers CSV/TSV/XLSX/JSON para la tool de preview
├── analytics.py  Motor DuckDB: schema/summarize/filter/aggregate/query/…
├── cache.py      Cache Parquet en disco con evicción LRU + lookup URL→key
├── models.py     Modelos de respuesta Pydantic → outputSchema tipado
├── netguard.py   Guardia anti-SSRF: valida cada URL de descarga + cada redirect
└── gcp.py        Pipeline BigQuery opcional (solo se registra con el extra [gcp])
```

Cada archivo tiene **una responsabilidad**. `server.py` es una capa fina de decoradores que
delegan a la lógica real — así las tools quedan legibles y la lógica testeable.

### 2.3 El patrón del decorador de tool (FastMCP)

Una tool es una función decorada y anotada con tipos. Las anotaciones *son* el schema que
ve el modelo:

```python
@mcp.tool(annotations=_ro("Search datasets"))   # title + readOnlyHint + openWorldHint
async def search_datasets(
    query: Annotated[str | None, Field(description="Término de búsqueda libre…")] = None,
    limit: Annotated[int, Field(description="Resultados (1-50)", ge=1, le=50)] = 10,
) -> dict:
    """Busca datasets en datos.gob.do. Filtra por palabra clave, org, tag o grupo."""
    return await ckan.search_datasets(query=query, limit=limit)
```

Tres cosas que lee el modelo: el **docstring** (qué hace), el **`Field(description=…)`** de
cada parámetro con sus restricciones (`ge`/`le`/enum), y las **annotations** (`readOnlyHint`
deja que un host auto-apruebe; `destructiveHint` dispara un diálogo de confirmación).

### 2.4 Por qué DuckDB + Parquet (el motor de analytics)

datos.gob.do **no tiene DataStore** — no hay SQL del lado del servidor. Entonces el servidor
baja el archivo una vez y corre analytics localmente:

```
descarga → transcodifica a UTF-8 → DuckDB escribe Parquet (ZSTD) en ~/.cache → consulta
```

DuckDB lee CSV/XLSX/JSON nativamente y corre SQL completo. El cache Parquet significa que la
descarga+parseo costosa pasa una sola vez; las consultas repetidas pegan a un archivo
columnar en milisegundos. El cache se llavea por **URL + ETag/Last-Modified**, así se
auto-invalida cuando el portal actualiza un archivo (`cache.py: build_cache_key`).

### 2.5 Seguridad: las dos superficies de inyección

Un servidor que mete entrada generada por el modelo en SQL y HTTP tiene dos superficies de
ataque. Ambas se cierran a propósito:

**(a) Inyección Solr** (filtros de búsqueda CKAN). Todo valor del usuario que entra a un
filtro `fq` de CKAN pasa por `_escape_solr` / `_fq_term` en `ckan.py`. Nunca se interpola crudo.

**(b) Inyección SQL + exfiltración de archivos locales** (`query_resource`). El acceso a
archivos de DuckDB vive en *funciones de tabla* (`read_text`, `read_csv`, `glob`) — una
denylist de keywords no las atrapa. El fix real es un **sandbox** (`analytics.py: _open_sandboxed`):

```python
con.execute(f"CREATE TABLE data AS SELECT * FROM read_parquet('{p}')")  # materializa
con.execute("SET enable_external_access=false")   # mata lectura de archivos + red
con.execute("SET lock_configuration=true")        # no se puede re-activar a media query
# ahora corre el SELECT del usuario — `data` está en memoria, sin filesystem alcanzable
```

Materializa **primero** (leer el parquet ya es "acceso externo"), luego bloquea. Los
identificadores de columna en todos lados pasan por `_quote_ident` (regex allowlist +
denylist de `--`, `/*`, `;` + comillas dobles). La lección: **defensa en profundidad, y
nunca confíes en que una denylist está completa.**

Una trampa concreta de este código: la regex allowlist estaba anclada con `^…$`. En
Python, `$` también coincide *justo antes* de un salto de línea final — así que `"col\n"`
se colaba por una allowlist pensada para rechazar caracteres de control. Ancla con
`\A…\Z` (no `^…$`) cuando una regex es una frontera de seguridad, no solo un chequeo de
formato.

### 2.6 Encoding: el problema de los datos del mundo real

Los CSV gubernamentales suelen ser Windows-1252, no UTF-8. DuckDB requiere UTF-8. Entonces
`download.py: _detect_encoding` prueba UTF-8 → chardet → CP1252, y `analytics.py`
transcodifica archivos no-UTF-8 a un sidecar `.utf8` antes de parsear. Los datos abiertos
reales son sucios; presupuesta tiempo para encoding, detección de delimitador y casos borde
de headers.

### 2.7 Salida tipada (la capa `models.py`)

Devolver un `dict` pelado no le da schema al host. Devolver un **modelo Pydantic** hace que
FastMCP emita un `outputSchema` + `structuredContent` real, para que los hosts validen. El
truco para datos variables: `model_config = ConfigDict(extra="allow")` mantiene las claves
dinámicas (como percentiles `p25`/`p50`) fluyendo mientras tipa el sobre conocido.

### 2.8 Tests: herméticos por defecto

La suite hermética (180+ tests) corre **sin red** — `pytest-httpx` mockea las respuestas de CKAN y un CSV
diminuto en memoria ejercita todo el stack descarga→DuckDB→Parquet. Un puñado de tests en
vivo pegan a la API real solo con `RUN_LIVE_TESTS=1`. Los guardias de seguridad tienen
**tests adversariales**: `test_query_resource_blocks_file_access` prueba que
`read_text('/etc/passwd')` devuelve un error, no tu archivo de contraseñas.

Una trampa sutil que conviene interiorizar: una denylist de rutas que bloqueaba
`/private/var` pasaba en CI de Linux (donde los archivos temporales viven en `/tmp`) pero
**fallaba en macOS**, donde el directorio temporal por usuario *es* `/private/var/folders/…`.
Un CI verde no es verde en todas partes — prueba en las plataformas a las que realmente
distribuyes, y acota las denylists de seguridad lo suficiente como para que no se traguen
el espacio escribible legítimo del usuario.

---

## Parte 3 — Construye tu propio servidor MCP (receta)

Quieres envolver un portal de datos abiertos (o cualquier API HTTP) para un asistente de IA.
Acá está el camino que tomó este proyecto, generalizado.

### Paso 1 — Decide la forma

- **Local stdio** (este proyecto): lo más fácil de prototipar, corre en la máquina del usuario, se distribuye vía PyPI + `uvx`. Bueno para herramientas personales/cívicas.
- **Remote HTTP**: un solo despliegue sirve a todos, maneja OAuth, empuja actualizaciones. La opción correcta para un producto hosteado.

Empieza local; la capa de analytics se porta directo a remoto después.

### Paso 2 — Scaffold con FastMCP

```python
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("my-server")

@mcp.tool(annotations={"title": "Search", "readOnlyHint": True, "openWorldHint": True})
async def search(query: str) -> dict:
    """Busca en el catálogo. Devuelve hasta 10 resultados."""
    return await my_client.search(query)

def main():
    mcp.run()   # transporte stdio
```

`pyproject.toml` expone el entry point:

```toml
[project.scripts]
my-server = "my_package.server:main"
```

### Paso 3 — Diseña tools que el modelo realmente pueda usar

Lee las reglas de diseño de tools horneadas en este proyecto:

1. **Schemas ajustados.** `Field(ge=1, le=50)`, `Literal["head","tail","random"]` — cada restricción es una llamada mala menos.
2. **Las descripciones son el contrato.** Di qué hace, qué devuelve, y *qué no hace* (para que el modelo elija la tool hermana correcta).
3. **Annotations obligatorias.** `readOnlyHint`, `destructiveHint`, `title` — son pass/fail para el Anthropic Directory y manejan la UX de auto-aprobación.
4. **Separación read/write.** Una tool o es read-only o muta — nunca ambas.
5. **≤ ~30 tools.** Cada schema cuesta tokens de contexto en cada turno. Pasando ~30, cambia a un par `search_actions` + `execute_action`.

### Paso 3b — Distribuye un método, no solo capacidades

Las tools son lo que tu servidor *puede* hacer; los prompts son lo que tú crees que
alguien *debería* hacer primero. Si tu dominio tiene trampas — un título que miente sobre
su rango de fechas, un total que está mal si no revisas qué se excluyó — la descripción de
una tool no puede cargar eso de forma fiable, porque el modelo solo la lee cuando ya
eligió la tool.

Entonces: cuando las tools funcionen, escribe dos o tres prompts (§1.7). Uno sin
argumentos que oriente a alguien nuevo, y uno por cada flujo que te molestaría ver hecho
mal. Después agrega resources para los hechos permanentes en los que una conversación no
debería gastar una llamada, y una plantilla de recurso si tu dominio tiene una entidad
direccionable obvia (un dataset, un repo, un ticket). Es la palanca de calidad más barata
de todo el servidor: ~30 líneas que evitan el mal uso más común de las otras 3.000.

### Paso 4 — No vuelques datos crudos al contexto

El patrón clave para tools de datos: dale al modelo **tools de reconocimiento** (schema,
summarize) para que entienda los datos *antes* de sacar filas, y **tools de consulta tipada**
(filter, aggregate) para que obtenga respuestas, no megabytes. Trunca campos largos; reporta
cuándo lo haces (`"Mostrando 10 de 847…"`).

### Paso 5 — Cachea el paso costoso

Si cada llamada re-descarga + re-parsea, el servidor es lento y abusivo con la API upstream.
Cachea el artefacto transformado (acá: Parquet), llavéalo con un tag de versión (ETag), y
salta la red por completo en un hit caliente.

### Paso 6 — Cierra las superficies de inyección

- Escapa todo valor del usuario que va a un lenguaje de consulta (Solr, SQL, shell).
- Para SQL libre, **aísla el motor en sandbox**, no confíes en filtrado de keywords.
- Tapa las descargas (límites de bytes) para acotar memoria y bombas de descompresión.
- Escribe un `SECURITY.md` y **tests adversariales** que prueben que los guardias funcionan.

### Paso 7 — Hazlo distribuible

- Tests herméticos (mockea la red) + una matriz CI a través de versiones de Python.
- Gates de lint + format + type-check (ruff + mypy).
- `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`.
- Publica a **PyPI** (`uv build && uv publish`) y al **MCP Registry**
  (`mcp-publisher publish` con un `server.json`).

**No asumas nada del entorno en el que te lanzan.** Tu servidor funciona en tu terminal y luego se comporta distinto bajo un cliente, por dos razones que la [guía de depuración de MCP](https://modelcontextprotocol.io/docs/tools/debugging) dice sin rodeos: el cliente pasa solo un subconjunto limitado de variables de entorno, y el directorio de trabajo es indefinido (`/` en macOS). Las dos golpearon a este proyecto:

- Quien define una variable de **seguridad** en su shell se queda con el valor por defecto. Documenta el bloque `env`, no `export`.
- Una ruta relativa resuelve contra un directorio que nadie eligió. `DATOSGOBDO_ARCHIVE_DIR=mi-archivo` apagaba el fallback de archivo en silencio; un `dest` relativo en `save_query_to_csv` mandaba la escritura a la raíz del filesystem y fallaba con `[Errno 30] Read-only file system`.

La lección generaliza: **para toda entrada que sea una ruta, exige ruta absoluta y explica por qué en el error; para toda configuración que no encuentres, registra que la buscaste.** Una función apagada por mala configuración no debe verse igual que una función que nadie pidió.

### Paso 8 — Generaliza entre fuentes (opcional)

Una vez que un portal funciona, un **patrón adapter** deja que un servidor cubra muchos. Los
portales CKAN de toda América Latina comparten una API — un solo cliente cubre Argentina,
Chile, México, Uruguay, Ecuador y RD con solo cambiar la URL base. Ese es el camino de
`datosgobdo-mcp` a un `opendata-latam-mcp` regional.

---

## Dónde mirar en el código

| Para aprender… | Lee… |
|---|---|
| Definiciones de tools + annotations | `src/datosgobdo_mcp/server.py` |
| Cliente HTTP + escape de inyección | `src/datosgobdo_mcp/ckan.py` |
| Analytics DuckDB + el sandbox SQL | `src/datosgobdo_mcp/analytics.py` |
| Estrategia de cache | `src/datosgobdo_mcp/cache.py` |
| Salida tipada | `src/datosgobdo_mcp/models.py` |
| Guardia anti-SSRF (esquema/IP, redirects) | `src/datosgobdo_mcp/netguard.py` |
| Registro de tools con dependencia opcional | `src/datosgobdo_mcp/gcp.py` |
| Tests herméticos + adversariales | `tests/` |
| Modelo de amenazas | `SECURITY.md` |

Clónalo, corre `uv sync --extra dev && uv run pytest -v`, y empieza a cambiar cosas. Esa es
la forma más rápida de aprender.

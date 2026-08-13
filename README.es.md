<!-- mcp-name: io.github.alcastaro/datos.gob.do-MCP-server -->

**[English](README.md) · [Español](README.es.md)**

---

# datosgobdo-mcp

**Hazle una pregunta sobre datos públicos dominicanos a un asistente de IA y recibe una respuesta que se puede rastrear hasta el archivo del gobierno de donde salió.**

Este es un servidor [Model Context Protocol](https://modelcontextprotocol.io) para [datos.gob.do](https://datos.gob.do), el portal oficial de datos abiertos de la República Dominicana. Se conecta a Claude Desktop, Claude Code, Cursor, ChatGPT Desktop o cualquier cliente compatible con MCP, y permite que el modelo busque en el catálogo, descargue los archivos reales, los interprete y haga análisis de verdad — sin que tú escribas código, abras una URL ni bajes una hoja de cálculo.

> **Fuente oficial.** El repositorio canónico es
> [`alcastaro/datos.gob.do-MCP-server`](https://github.com/alcastaro/datos.gob.do-MCP-server).
> Las únicas distribuciones oficiales son el paquete de PyPI
> [`dominican-open-data-mcp`](https://pypi.org/project/dominican-open-data-mcp/)
> y la entrada del MCP Registry `io.github.alcastaro/datos.gob.do-MCP-server`.
> Las copias publicadas en otros lugares no las mantiene el autor y pueden estar
> desactualizadas o modificadas — verifica contra este repositorio antes de instalar.

Este README está escrito para leerse en orden. **La Parte 1 no requiere conocimientos técnicos.** La Parte 2 enseña qué es MCP en realidad, usando este servidor como ejemplo trabajado. Las Partes 3 a 6 son la referencia y el detalle de ingeniería. Si prefieres el mismo material como recorrido guiado, lee el [Tutorial](Tutorial_es.md) ([English](Tutorial.md)).

---

## Contenido

**Parte 1 — Empieza aquí (sin conocimientos técnicos)**
1. [Qué es esto, en palabras llanas](#s1)
2. [Arranque rápido](#s2)
3. [Los seis prompts guiados — empieza con `/empezar_aqui`](#s3)
4. [Qué puedes preguntar](#s4)
5. [Lee esto antes de citar una cifra](#s5)

**Parte 2 — Entender MCP (educativo)**

6. [¿Qué es MCP? Tools, resources y prompts](#s6)
7. [¿Qué es datos.gob.do?](#s7)

**Parte 3 — Qué expone este servidor**

8. [Tools (24, más 3 opcionales)](#s8)
9. [Resources (3) y una plantilla de recurso](#s9)
10. [Prompts (6)](#s10)
11. [A qué primitiva recurrir](#s11)

**Parte 4 — Por qué existe este servidor**

12. [Cómo se compara con otros MCP de CKAN](#s12)

**Parte 5 — Referencia técnica**

13. [Instalación y configuración de clientes](#s13)
14. [Lo que las respuestas dicen sobre sí mismas](#s14)
15. [Seguridad y variables de entorno](#s15)
16. [Arquitectura](#s16)
17. [Limitaciones medidas](#s17)

**Parte 6 — Desarrollo**

18. [Desarrollo, pruebas y el MCP Inspector](#s18)
19. [Contribuir, créditos, cómo citar, licencia](#s19)

---
---

# Parte 1 — Empieza aquí

<a id="s1"></a>

## 1. Qué es esto, en palabras llanas

El Estado dominicano publica miles de archivos: nóminas públicas, ejecución presupuestaria, actividad hospitalaria, flujos migratorios, contratos de compras, pérdidas eléctricas, incendios forestales. Todo es público. Casi nadie lo lee, porque leerlo implica saber cuál de 266 instituciones publicó qué, encontrar el archivo, bajar una hoja de cálculo con el encabezado en la fila 3 y saber qué hacer después.

Este servidor le entrega ese trabajo completo a tu asistente de IA. Preguntas con tus propias palabras. El asistente encuentra el dataset, descarga el archivo del servidor de la propia institución, deduce las columnas, hace la suma o el promedio, y te da la respuesta **junto con de dónde salió y qué tuvo que dejar fuera**.

Tres cosas conviene saberlas de entrada, porque condicionan todo lo demás:

- **Solo lee.** Nada de aquí puede modificar el portal ni publicar nada. No hay usuario ni contraseña.
- **Corre en tu computadora**, al lado de tu asistente. Tus preguntas no pasan por ningún servidor de este proyecto.
- **Te avisa cuando el dato está mal.** Cerca de la mitad del catálogo no se puede descargar por programa, y las herramientas lo dicen en vez de inventar una cifra. Ese es el eje del diseño, no una advertencia escondida al final.

<a id="s2"></a>

## 2. Arranque rápido

Necesitas [`uv`](https://docs.astral.sh/uv/), una herramienta pequeña que ejecuta programas de Python sin que instales nada más. En macOS o Linux, pega esto en una terminal:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

En Windows, sigue la [página de instalación de uv](https://docs.astral.sh/uv/getting-started/installation/).

Después, dile a tu asistente que el servidor existe.

**Claude Desktop.** Abre `Configuración → Desarrollador → Editar configuración`, o edita el archivo directamente:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Pega esto, reemplazando `TU_USUARIO`:

```json
{
  "mcpServers": {
    "datosgobdo": {
      "command": "/Users/TU_USUARIO/.local/bin/uvx",
      "args": ["dominican-open-data-mcp"]
    }
  }
}
```

Usa la **ruta completa** a `uvx` — Claude Desktop no lee el `PATH` de tu shell. Luego cierra Claude Desktop por completo (Cmd+Q en macOS, no solo cerrar la ventana) y vuelve a abrirlo. En `Configuración → Desarrollador` debe aparecer `datosgobdo` en estado **running**.

No hace falta nada más. Si después quieres cambiar alguna opción — la guardia de red, el directorio de caché — va en un bloque `"env"` **dentro de este mismo archivo**, no en tu shell: ver [§13](#s13).

**Claude Code.** Una línea:

```bash
claude mcp add datosgobdo -- uvx dominican-open-data-mcp
```

**Cualquier otro cliente.** La misma idea: registra `uvx` como comando con `dominican-open-data-mcp` como argumento. El [directorio de clientes MCP](https://modelcontextprotocol.io/clients) indica qué cliente soporta qué. Las opciones completas — versión de desarrollo, clone local, modo hosted — están en [§13](#s13).

<a id="s3"></a>

## 3. Los seis prompts guiados — empieza con `/empezar_aqui`

Veinticuatro herramientas no son una invitación. Alguien que nunca ha visto este catálogo no tiene forma de saber que nóminas, ejecución presupuestaria e inversión pública son las tres cosas que mejor cubre.

Por eso el servidor trae **seis prompts**: preguntas ya hechas, escritas para codificar los hábitos que costó aprender una auditoría completa del catálogo. En Claude Code y Claude Desktop aparecen como comandos de barra. Escribe:

```
/empezar_aqui
```

y el asistente te presentará el portal, te dirá qué cubre bien, propondrá tres preguntas concretas que podrías hacer a continuación, y te advertirá desde el principio qué no se puede descargar.

Los otros cinco reciben un argumento cada uno:

| Prompt | Le das | Qué hace |
|---|---|---|
| `/empezar_aqui` | — | Panorama del portal y tres preguntas para arrancar. |
| `/serie_temporal` | un tema | Arma una serie año por año, declarando el período real cubierto y sin tratar la columna de año como una medida. |
| `/auditar_nomina` | una institución | Suma, promedio y distribución salarial de una nómina pública, declarando cuántas filas se excluyeron y por qué. |
| `/verificar_fuente` | la URL de un recurso | Comprueba alcance, procedencia y forma de un archivo **antes** de que te apoyes en él. |
| `/explorar_institucion` | una institución | Inventario de todo lo que publica esa institución, con el estado real de descarga de cada archivo. |
| `/cruzar_fuentes` | un tema | Cruza dos recursos declarando unidades, períodos y los límites del cruce. |

Si tu cliente no muestra los prompts como comandos de barra, revisa su entrada en el [directorio de clientes](https://modelcontextprotocol.io/clients) — el soporte de prompts es [opcional para los clientes](https://modelcontextprotocol.io/specification/2026-07-28/server/prompts), y el [MCP Inspector](#s18) siempre puede listarlos y ejecutarlos.

<a id="s4"></a>

## 4. Qué puedes preguntar

Preguntas normales, en español o en inglés. Algunas que funcionan hoy:

> *¿Cuántos datasets hay en el portal datos.gob.do y qué instituciones publican más?*

> *Encuentra los cinco datasets más relevantes sobre presupuesto y dime qué institución publica cada uno.*

> *¿Cuánto gasta el Poder Judicial en sueldos?*

> *¿Cuántos empleados activos tiene el Ministerio de Agricultura en abril de 2026, desglosados por estatus?*

Vale la pena detenerse en la última, porque es el tipo de pregunta para la que existe toda la capa analítica. La nómina de Agricultura es un CSV de **826.000 filas y 94 MB** — muchísimo más de lo que cabe pegar en una conversación. El servidor lo descarga una vez, lo convierte a caché columnar y responde con una agregación agrupada: 6 tipos de estatus, unos 8.915 empleados. La primera llamada toma unos 14 segundos; cada pregunta posterior sobre el mismo archivo responde en menos de medio segundo.

> *Compara el presupuesto aprobado contra el ejecutado de FONDOMARENA en los últimos tres años.*

> *¿Qué columnas tiene el dataset de robo de vehículos del Ministerio de Interior?*

> *Lista los diez datasets actualizados más recientemente.*

**A quién le suele servir:** periodistas de datos que si no tendrían que escribir un scraper; investigadores que necesitan acceso programático; organizaciones de transparencia que siguen ejecución presupuestaria y compras; desarrolladores prototipando sobre datos públicos; funcionarios que quieren ver qué publica ya su propia institución; y cualquiera con curiosidad cívica sobre cómo opera el Estado.

<a id="s5"></a>

## 5. Lee esto antes de citar una cifra

Este catálogo tiene defectos reales, y se midieron — un censo del catálogo completo el **2026-08-08**, un recurso por dataset, 1.056 recursos sobre sesiones MCP reales. Cuatro hallazgos cambian cómo debes leer cualquier cifra que salga de aquí:

**Cerca de la mitad del catálogo no se puede descargar por programa.** **561 de 1.056** recursos se pueden leer (53,1 %), frente a 540 en el censo: el trabajo de formatos de 0.14.0 recuperó 21 de ellos, remedidos contra el portal vivo el 2026-08-13. La causa individual mayor del resto no es este servidor y ninguna versión suya puede arreglarla: **360 recursos de 98 instituciones** están detrás de una configuración de sitio que rechaza la descarga programática de los archivos que esas mismas instituciones publican como datos abiertos. Desde la misma dirección, 21 otros hosts gubernamentales detrás del mismo CDN responden con normalidad — así que es configuración por sitio, no nuestra red. Otros 15 enlaces están muertos y 6 archivos son ilegibles en cualquier codificación.

**Uno de cada tres datasets multiformato se contradice a sí mismo.** De 528 datasets cuyos formatos se pudieron comparar, **176 no coinciden** en número de filas o de columnas. Un ejemplo: `recaudaciones-sirite-2021-2025` de la Tesorería Nacional trae 971.818 filas como CSV y 197.338 como ODS. Un ciudadano que baja el ODS y un periodista que baja el CSV citarían cifras distintas del mismo dataset oficial. **Regla práctica: revisa más de un formato antes de publicar un total.**

**Los números suelen estar guardados como texto.** 93 de los 540 recursos legibles de ese censo tienen columnas numéricas como texto, normalmente porque un puñado de celdas dice `N/A` o `#REF!`. Las herramientas leen esa columna como números donde cada valor lo permite y **reportan lo que costó** — ver [§14](#s14). Lee `values_excluded` antes de citar el total.

**Ningún dataset declara cada cuánto se actualiza.** El campo `periodicidad` está vacío en los 1.056. Un dataset rotulado «2018-2026» puede haberse alimentado el mes pasado o haberse congelado hace dos años; la frescura hay que inferirla del último período con datos.

Nada de esto es razón para no usar el catálogo. Es razón para citarlo con precisión — que es para lo que existen `/verificar_fuente` y los campos que describen la propia respuesta.

---
---

# Parte 2 — Entender MCP

<a id="s6"></a>

## 6. ¿Qué es MCP? Tools, resources y prompts

[Model Context Protocol](https://modelcontextprotocol.io) es un estándar abierto — creado por Anthropic y hoy adoptado en toda la industria — para conectar modelos de lenguaje con datos y capacidades externas. En vez de que cada aplicación invente su propio formato de plugin, una app con modelo (el **cliente**, p. ej. Claude Desktop) habla con cualquier cantidad de **servidores** por un solo protocolo.

Un servidor puede ofrecer tres tipos de cosa. La distinción importa, porque determina *quién decide* cuándo se usa:

| Primitiva | La controla | Qué es | En este servidor |
|---|---|---|---|
| **[Tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)** | el **modelo** | Funciones que el modelo puede llamar, con argumentos tipados. El modelo elige cuándo y con qué. | 24 funciones: buscar, descargar, agregar, consultar… |
| **[Resources](https://modelcontextprotocol.io/specification/2026-07-28/server/resources)** | la **aplicación** | Datos que la app puede adjuntar como contexto, direccionados por URI. Sin efectos secundarios. | 3 documentos + 1 plantilla de URI |
| **[Prompts](https://modelcontextprotocol.io/specification/2026-07-28/server/prompts)** | el **usuario** | Plantillas que el usuario invoca deliberadamente, normalmente como comandos de barra. | 6 flujos guiados |

El protocolo define también primitivas del lado del cliente — [sampling](https://modelcontextprotocol.io/specification/2026-07-28/client/sampling), [elicitation](https://modelcontextprotocol.io/specification/2026-07-28/client/elicitation), [roots](https://modelcontextprotocol.io/specification/2026-07-28/client/roots) — que este servidor no usa.

Guías de concepto: [tools](https://modelcontextprotocol.io/docs/concepts/tools), [resources](https://modelcontextprotocol.io/docs/concepts/resources), [prompts](https://modelcontextprotocol.io/docs/concepts/prompts). Si quieres construir uno, empieza por [Build a server](https://modelcontextprotocol.io/docs/develop/build-server), y lee la [Parte 3 de nuestro Tutorial](Tutorial_es.md#parte-3--construye-tu-propio-servidor-mcp-receta) para lo que este proyecto aprendió haciéndolo.

Lo que este servidor declara al conectarse, verificado en sesión real el 2026-08-12:

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

`listChanged: false` y `subscribe: false` son declaraciones honestas, no omisiones: la lista de tools queda fija al arrancar, y ningún resource aquí cambia con la frecuencia que justificaría una suscripción.

> **Sobre los dos números de versión.** Los enlaces de especificación de arriba apuntan a `2026-07-28`, la especificación vigente, porque es la que conviene leer. El servidor negocia **`2025-11-25`** porque fija `mcp>=1.9.0,<2` — el SDK 2.0 renombró `FastMCP` a `MCPServer` y eliminó la ruta de importación anterior sin capa de compatibilidad. Lo que 2026-07-28 agrega y este servidor por tanto no implementa: [`server/discover`](https://modelcontextprotocol.io/specification/2026-07-28/server/discover), los campos `_meta` por petición, y niveles de log por petición. La migración está registrada como pendiente, no es un descuido.

<a id="s7"></a>

## 7. ¿Qué es datos.gob.do?

El portal oficial de datos abiertos del Estado dominicano, operado por OGTIC. Corre sobre **CKAN 2.11.3** — la misma plataforma detrás de data.gov (EE. UU.), data.gov.uk y buena parte de América Latina.

**Lo que el portal declara** (consultado en vivo el 2026-08-12):

| | |
|---|---|
| Datasets | **1.061** |
| Organizaciones registradas | **266** |
| Grupos temáticos | **11** |
| Etiquetas | **874** |
| Extensiones CKAN cargadas | `activity`, `datosgobdo_theme` |

**Lo que midió la auditoría del 2026-08-08**, que es otra cosa y la diferencia es instructiva:

| | |
|---|---|
| Recursos (archivos) en el catálogo | **3.826** |
| Organizaciones que realmente tienen un dataset | **261** de las 266 registradas |
| Recursos probados (uno por dataset) | **1.056** |
| Legibles por máquina | **561** (53,1 %) — 540 en el censo del 2026-08-08, más 21 recuperados por 0.14.0 |
| Filas descargadas y cacheadas | **13.371.601** en el censo, más **82.490** recuperadas — y **846.388** más en archivos hermanos fuera de él |
| Recursos alojados en `datos.gob.do` mismo | **66** — el resto vive en **273 dominios distintos** |

Esa última fila es el hecho estructural detrás de casi todo este proyecto. **El portal es un catálogo de enlaces, no un repositorio.** Cada institución guarda sus archivos en su propio servidor web, así que la disponibilidad, la higiene de formato y las reglas de acceso se deciden en 273 lugares que el portal no controla.

Nota también lo que la lista de extensiones **no** incluye: **el DataStore de CKAN no está instalado aquí.** Ese solo hecho es la razón de que este servidor tenga la forma que tiene — ver [§12](#s12).

Este proyecto se inspiró en [`datagouv-mcp`](https://github.com/datagouv/datagouv-mcp) (Etalab, Francia), pero datos.gob.do corre CKAN y no udata, así que la implementación es propia.

---
---

# Parte 3 — Qué expone este servidor

<a id="s8"></a>

## 8. Tools (24, más 3 opcionales)

Funciones tipadas, agrupadas en cinco familias. Las que producen datos (analytics, preview, caché) devuelven `outputSchema` / `structuredContent` tipados para que los hosts validen resultados; las de metadatos navegacionales devuelven JSON. Toda herramienta que toca el portal está anotada `readOnlyHint: true`; las que salen a la red, `openWorldHint: true`.

**Toda herramienta responde con un solo objeto.** Los listados nombran lo que traen y lo cuentan — `{organizations, count, limit_reached}`, `{tags, count, limit_reached}`, `{groups, count}`, `{suggestions, count, kind, query}`. `limit_reached` importa porque los topes son menores que el catálogo: 200 instituciones contra 266, y cualquier listado de etiquetas sin `query` es una muestra de 874.

### Descubrimiento

| Tool | Qué hace |
|---|---|
| `search_datasets` | Busca datasets por palabra clave, organización, etiqueta o grupo. Filtros combinables, paginación. |
| `get_dataset` | Metadatos completos de un dataset: título, descripción, licencia, autor y cada recurso con su URL directa de descarga. |
| `list_recent_datasets` | Datasets ordenados por modificación más reciente. Útil para monitorear actualizaciones del portal. |
| `get_site_stats` | Totales del portal (datasets, organizaciones, grupos, etiquetas). |

### Archivos de recursos

| Tool | Qué hace |
|---|---|
| `get_resource` | Metadatos de un recurso individual (URL, formato, tamaño, fecha). |
| `search_resources` | Busca recursos por nombre. |
| `download_resource_preview` | Descarga un archivo y devuelve N filas. CSV, TSV, XLSX, XLS, ODS, JSON. Tope de 5 MB. Modo de muestra: head / tail / random. |
| `check_resources` | Pregunta a hasta 25 URLs si sus archivos se pueden descargar de verdad, sin descargarlos. Devuelve una clase por URL — alcanzable, desafío de navegador, regla del sitio, enlace muerto, sin respuesta — porque una entrada de catálogo no es evidencia de que el archivo siga ahí. |

### Analytics

DuckDB sobre una caché Parquet persistente. La primera llamada por recurso descarga y cachea (hasta 100 MB); las siguientes responden en menos de un segundo. La caché vale aproximadamente **44×** sobre medianas medidas.

| Tool | Qué hace |
|---|---|
| `get_resource_schema` | Nombres de columna, tipos inferidos, valores de muestra. El paso de reconocimiento barato antes de cualquier agregación. |
| `summarize_resource` | Perfil automático: filas, nulos y distintos por columna, mín/máx/media en numéricas, top-N en categóricas. |
| `filter_resource` | WHERE / SELECT / ORDER BY / LIMIT tipados. Ops: `=`, `!=`, `<`, `<=`, `>`, `>=`, `in`, `not_in`, `contains`, `starts_with`, `ends_with`, `is_null`, `is_not_null`. |
| `aggregate_resource` | GROUP BY + agregaciones + HAVING + ORDER BY tipados. Fns: `count`, `count_distinct`, `sum`, `avg`, `mean`, `median`, `min`, `max`, `stddev`, `variance`. |
| `quantiles_resource` | Distribución por percentiles (p25/p50/p75/p90/p95/p99) de columnas numéricas. |
| `find_duplicates_resource` | Filas duplicadas según columnas dadas (o todas). Imprescindible para chequeos de calidad en nóminas y censos. |
| `detect_outliers_resource` | Filas fuera del cerco IQR de una columna numérica, ordenadas por distancia a la mediana. |
| `query_resource` | Escape hatch para usuarios avanzados: SQL de solo lectura contra la tabla `data`. Solo SELECT/WITH; DDL/DML/COPY/PRAGMA/ATTACH/LOAD rechazados, y en sandbox (ver [§15](#s15)). |
| `save_query_to_csv` | Escribe el resultado de un filtro o SQL a un CSV local. Destino absoluto, o el de por defecto `~/Downloads/datosgobdo-exports/`. Deshabilitado en modo hosted. |
| `get_cache_stats` | Estadísticas de la caché Parquet en disco, más la identidad del servidor y el modo de seguridad efectivo. `total_bytes` es uso de disco, no del índice: `orphan_entries` cuenta los Parquet que el índice no lista —escritos por una llamada que perdió el candado de la caché, o por un proceso que murió antes de registrarlos— y un valor distinto de cero ahí significa contención, no una caché sana. |
| `clear_cache` | Borra la caché Parquet local. La única herramienta no de solo lectura del servidor (`destructiveHint: true`). Deshabilitada en modo hosted. |

### Catálogo

| Tool | Qué hace |
|---|---|
| `list_organizations` | Instituciones publicadoras con su número de datasets. |
| `get_organization` | Detalle de una institución (descripción, número de datasets, URL). |
| `list_groups` | Categorías temáticas con conteos. |
| `list_tags` | Etiquetas, opcionalmente filtradas por prefijo. |

### Autocompletado

| Tool | Qué hace |
|---|---|
| `autocomplete` | Resuelve nombres parciales de datasets, organizaciones, grupos o etiquetas — para cuando el usuario solo da parte de un nombre. |

### Pipeline GCP (opcional)

Se instala con `pip install 'dominican-open-data-mcp[gcp]'`; tres herramientas extra se registran automáticamente cuando las librerías de Google Cloud están presentes, llevando el total a 27. Convierten este servidor en la mitad de *ingesta* de un pipeline BigQuery: descubre aquí, carga a BigQuery, y luego consulta con el MCP oficial de BigQuery para los JOIN entre datasets que una caché DuckDB local no puede hacer.

| Tool | Qué hace |
|---|---|
| `load_resource_to_bigquery` | Recurso → caché Parquet → subida a GCS → tabla externa de BigQuery (por defecto, zero-ETL) o load job. |
| `list_bigquery_exports` | Lista tablas de un dataset de BigQuery. |
| `get_bigquery_table_info` | Esquema, número de filas y URIs de origen de una tabla. |

Define `DATOSGOBDO_GCS_BUCKET` para no pasar el bucket en cada llamada. **Estado preview:** estas tres quedan fuera de la promesa de estabilidad y no se han ejercitado contra un proyecto real.

<a id="s9"></a>

## 9. Resources (3) y una plantilla de recurso

Los [resources](https://modelcontextprotocol.io/specification/2026-07-28/server/resources) se direccionan por URI y los lee la *aplicación*, no los llama el modelo. Existen aquí para los hechos que son pequeños, estables y un desperdicio de una llamada a herramienta. Los tres son de solo lectura y sin efectos secundarios.

| URI | Tipo | Qué contiene |
|---|---|---|
| `datosgobdo://catalog/overview` | `application/json` | Totales del portal: datasets, instituciones, grupos, etiquetas. |
| `datosgobdo://catalog/institutions` | `application/json` | Cada institución publicadora con su número de datasets — la respuesta a «¿qué institución?» antes de cualquier consulta. |
| `datosgobdo://guide/verification` | `text/markdown` | Los cuatro campos que hacen comprobable un número, y qué hacer cuando faltan. |

Ese último es un resource y no un prompt a propósito: no es una petición de actuar, es texto de referencia que conviene tener en contexto mientras trabajas.

Una [plantilla de recurso](https://modelcontextprotocol.io/specification/2026-07-28/server/resources#resource-templates) — un patrón de URI con un parámetro, de modo que una sola definición direcciona cualquier dataset del catálogo:

| Plantilla | Rellenas | Devuelve |
|---|---|---|
| `datosgobdo://dataset/{dataset_id}` | un id o slug de dataset | Los metadatos de ese dataset como contexto adjuntable. |

Ejemplo: `datosgobdo://dataset/nomina-poder-judicial`.

**Cómo usarlos.** En Claude Desktop los resources aparecen en el menú de adjuntar de una conversación con el servidor conectado. En otros clientes, revisa su entrada en el [directorio de clientes](https://modelcontextprotocol.io/clients) — el soporte de resources es opcional. En cualquier cliente, el panel **Resources** del Inspector los lista y muestra el payload crudo, incluida la expansión de la plantilla.

<a id="s10"></a>

## 10. Prompts (6)

Los [prompts](https://modelcontextprotocol.io/specification/2026-07-28/server/prompts) los controla el usuario: nada los invoca salvo tú. Cada uno de estos codifica un hábito aprendido a golpes durante la auditoría del catálogo — razón por la cual vale la pena usarlos incluso cuando ya conoces bien las herramientas.

| Prompt | Argumento | El hábito que codifica |
|---|---|---|
| `empezar_aqui` | — | Orientación antes de exploración, y la advertencia de descarga dicha de entrada en vez de descubierta después. |
| `serie_temporal` | `tema` (obligatorio) | Declarar el período **real** que cubre el dato, no el del título; nunca tratar la columna de año como una medida. |
| `auditar_nomina` | `institucion` (obligatorio) | Reportar filas excluidas y su procedencia junto a cualquier total salarial. |
| `verificar_fuente` | `url` (obligatorio) | Comprobar alcance, procedencia y forma **antes** de apoyarse en un archivo. |
| `explorar_institucion` | `institucion` (obligatorio) | Inventario con el estado real de descarga de cada archivo, no solo su entrada de catálogo. |
| `cruzar_fuentes` | `tema` (obligatorio) | Declarar unidades, períodos y límites del cruce antes de cruzar dos fuentes. |

**Cómo invocarlos.** En Claude Code y Claude Desktop, como comandos de barra: `/empezar_aqui`, o `/serie_temporal` y luego el tema cuando lo pida. Algunos clientes los presentan en un menú. En el Inspector, el panel **Prompts** lista cada uno con sus argumentos y renderiza el texto expandido antes de que algo llegue a un modelo — la forma más fiable de ver exactamente qué hace un prompt.

<a id="s11"></a>

## 11. A qué primitiva recurrir

| Quieres… | Usa | Por qué |
|---|---|---|
| Responder una pregunta concreta sobre datos | una **tool**, por conversación normal | El modelo las elige y las combina. |
| Arrancar de cero, o seguir un método riguroso | un **prompt** | Seis flujos con las advertencias ya incorporadas. |
| Darle al asistente contexto de fondo permanente | un **resource** | Se adjunta una vez; sin llamada a herramienta ni tokens gastados en decidir. |
| Fijar un dataset como contexto | la **plantilla de recurso** | `datosgobdo://dataset/{id}`. |
| Hacer algo que las tools tipadas no cubren | `query_resource` | SQL de solo lectura, en sandbox. El escape hatch, no el primer movimiento. |

---
---

# Parte 4 — Por qué existe este servidor

<a id="s12"></a>

## 12. Cómo se compara con otros MCP de CKAN

CKAN mueve cientos de portales gubernamentales, así que un MCP genérico de CKAN es una idea obvia y buena. El más desarrollado es **[`ondata/ckan-mcp-server`](https://github.com/ondata/ckan-mcp-server)** (MIT, TypeScript, adoptado por [AgID](https://github.com/AgID/ckan-mcp-server), la agencia digital de Italia): búsqueda de datasets con sintaxis Solr completa, organizaciones y grupos, descubrimiento de ~950 portales, y acceso tabular por la API DataStore de CKAN. Apunta a cualquier portal mediante un argumento `server_url`. Si tu portal tiene DataStore poblado, **úsalo** — es más amplio que este proyecto y tiene releases más activas.

La diferencia no es de calidad, es de dónde vive el dato. Verificado en vivo el 2026-08-12:

```
GET /api/3/action/status_show   → extensions: ["activity", "datosgobdo_theme"]
GET /api/3/action/datastore_search
  → 400  "Action name not known: datastore_search"
recursos con datastore_active: 0 / 254 muestreados
```

**datos.gob.do no tiene DataStore.** No hay `datastore_search`, no hay endpoint SQL, y ni un solo recurso está cargado en él. Un MCP genérico de CKAN apuntado aquí puede buscar metadatos perfectamente y no puede leer una sola fila de datos. Eso no es un defecto suyo — la extensión es opcional en CKAN y este portal nunca la habilitó.

Así que los dos proyectos se dividen por una línea real:

| | Portales **con** DataStore | Portales que son **catálogos de archivos** |
|---|---|---|
| Dónde está el dato | Cargado en CKAN, consultable por API | Archivos en 273 servidores web institucionales |
| Cómo se lee | `datastore_search_sql` | Descargar, olfatear la codificación, parsear, cachear, consultar |
| Mejor herramienta | [`ondata/ckan-mcp-server`](https://github.com/ondata/ckan-mcp-server) | esta |

Todo lo que hace este código más grande que un wrapper de la API de CKAN existe por esa columna derecha: detección de codificación puntuada por el español que recupera, parseo ODS por streaming (cargar el DOM completo multiplicaba la memoria ~580×), caché Parquet con la build del parser en la llave, coerción numérica que declara lo que excluyó, resolución página→archivo para las 37 URLs que responden HTML, guardia SSRF para descargas que alcanzan 273 hosts de terceros, y un fallback opcional a copia archivada que siempre dice cuándo se activó.

**Si estás construyendo para otro portal latinoamericano**, revisa `status_show` primero. Si DataStore no está — como en República Dominicana — la tubería de lectura de archivos de este repositorio es la parte que vas a necesitar, y el [Tutorial](Tutorial_es.md) la documenta para que se pueda reutilizar.

---
---

# Parte 5 — Referencia técnica

<a id="s13"></a>

## 13. Instalación y configuración de clientes

### Opción A — `uvx` desde PyPI (recomendado)

Paquete: [`dominican-open-data-mcp`](https://pypi.org/project/dominican-open-data-mcp/).

```bash
uvx dominican-open-data-mcp
```

También trae un binario con alias corto — los dos lanzan el mismo servidor:

```bash
uvx --from dominican-open-data-mcp datosgobdo-mcp
```

`uvx` descarga el paquete, crea un venv aislado y lo ejecuta. La primera vez toma segundos; las siguientes son instantáneas.

> **¿Vienes de ≤ 0.7.0?** Esas versiones fijaban `mcp>=1.9.0` sin tope superior, y el SDK de Python de MCP 2.0 (2026-07-28) eliminó la ruta de importación `mcp.server.fastmcp` — una instalación nueva falla con `ModuleNotFoundError`. Instala 0.7.1 o posterior, o fija el SDK tú mismo: `uvx --with "mcp<2" --from dominican-open-data-mcp datosgobdo-mcp`.

### Opción B — `uvx` desde GitHub (versión de desarrollo)

```bash
uvx --from git+https://github.com/alcastaro/datos.gob.do-MCP-server.git datosgobdo-mcp
```

### Opción C — clone local (para desarrollo)

```bash
git clone https://github.com/alcastaro/datos.gob.do-MCP-server.git
cd datos.gob.do-MCP-server
uv sync
uv run datosgobdo-mcp   # stdio; Ctrl+C para salir
```

> **Nota de macOS:** no clones dentro de `~/Library/CloudStorage/GoogleDrive-*` ni rutas similares. macOS bloquea la ejecución de binarios en rutas sincronizadas a la nube (restricción TCC). Usa `~/code/` o equivalente.

### Configuración de clientes

Claude Desktop y Claude Code están en [§2](#s2). Para seguir la versión de desarrollo en Claude Desktop, reemplaza los args por `["--from", "git+https://github.com/alcastaro/datos.gob.do-MCP-server.git", "datosgobdo-mcp"]`; en Claude Code, `claude mcp add datosgobdo -- uvx --from git+https://github.com/alcastaro/datos.gob.do-MCP-server.git datosgobdo-mcp`.

Para Cursor y otros el principio es idéntico — registra `uvx` como comando. La ubicación del archivo de configuración de cada cliente está en su propia documentación; el [directorio de clientes MCP](https://modelcontextprotocol.io/clients) es el índice.

### Pasarle opciones al servidor: el bloque `env`

Cada variable `DATOSGOBDO_*` de este README va en un objeto `"env"` dentro de la configuración del cliente:

```json
{
  "mcpServers": {
    "datosgobdo": {
      "command": "/Users/TU_USUARIO/.local/bin/uvx",
      "args": ["dominican-open-data-mcp"],
      "env": {
        "DATOSGOBDO_NETGUARD": "strict",
        "DATOSGOBDO_CACHE_DIR": "/Users/TU_USUARIO/.cache/datosgobdo-mcp"
      }
    }
  }
}
```

**`export DATOSGOBDO_NETGUARD=strict` en tu shell no llega al servidor.** Un servidor MCP stdio lanzado por un cliente hereda solo un subconjunto limitado del entorno, dependiente de plataforma — la [guía de depuración de MCP](https://modelcontextprotocol.io/docs/tools/debugging) lo dice explícitamente. Si pones la variable en el shell, el servidor arranca en el modo por defecto mientras crees que quedó restringido. Importa sobre todo para `DATOSGOBDO_NETGUARD`, que es un control de seguridad ([§15](#s15)).

Dos consecuencias del mismo hecho, útiles antes de reportar un bug:

- **Usa rutas absolutas en toda opción que sea una ruta.** El directorio de trabajo de un servidor lanzado por el cliente es indefinido — `/` en macOS. `DATOSGOBDO_ARCHIVE_DIR=mi-archivo` no resuelve a ninguna parte, y el servidor ahora registra `is not a directory … Archive fallback stays off` en vez de quedarse callado. Igual para `DATOSGOBDO_CACHE_DIR` y para el argumento `dest` de `save_query_to_csv`, que rechaza de plano una ruta relativa.
- **`uv run datosgobdo-mcp` en una terminal se comporta distinto** — ahí el directorio de trabajo es donde lo corriste, y tu entorno de shell sí aplica. Un bug que solo aparece bajo el cliente suele ser esto.

En Claude Code se pasan con `-e`: `claude mcp add datosgobdo -e DATOSGOBDO_NETGUARD=strict -- uvx dominican-open-data-mcp`.

### Modo hosted (experimental)

`DATOSGOBDO_TRANSPORT=streamable-http` sirve MCP sobre HTTP (sin estado, para escalado horizontal) en vez de stdio. En ese modo `save_query_to_csv` y `clear_cache` quedan deshabilitadas — tocan el filesystem del servidor y la caché compartida — y las estadísticas de caché omiten rutas del servidor.

**Los registros ahí corren por tu cuenta.** Bajo stdio el cliente captura el stderr del servidor y lo escribe en un archivo que puedes seguir con `tail`; sobre Streamable HTTP no. Recoge el stderr tú mismo, o conecta [OpenTelemetry](https://opentelemetry.io/), y usa herramientas HTTP normales (`curl`, el panel de red del navegador) para inspeccionar peticiones y flujos SSE.

| Variable | Por defecto | Significado |
|---|---|---|
| `DATOSGOBDO_TRANSPORT` | `stdio` | `streamable-http` para despliegues hospedados. |
| `DATOSGOBDO_HOST` / `DATOSGOBDO_PORT` | `127.0.0.1` / `8000` | Dirección de escucha HTTP. |
| `DATOSGOBDO_DUCKDB_MEMORY` | `2GB` | Techo de memoria de DuckDB por conexión. |
| `DATOSGOBDO_DUCKDB_THREADS` | `4` | Tope de hilos de DuckDB. |
| `DATOSGOBDO_QUERY_TIMEOUT` | `0` (apagado) | Segundos de reloj antes de interrumpir una corrida de DuckDB. Cubre tanto el SQL de `query_resource` como la conversión a Parquet de un archivo recién descargado. |

<a id="s14"></a>

## 14. Lo que las respuestas dicen sobre sí mismas

Tres campos aparecen en las respuestas cuando el servidor tuvo que hacer algo que quien llamó no pidió. Cada uno existe porque una herramienta usada para auditar no debe tapar en silencio un defecto del dato.

**`numeric_coercion`** — una columna guardada como texto se leyó como números.

El defecto más común de este catálogo: **93 de los 540 recursos legibles del censo del 2026-08-08** guardan columnas numéricas como texto, porque un puñado de celdas dice `N/A` o `#REF!` y eso basta para volver no numérica una columna entera de nómina. `aggregate_resource`, `quantiles_resource` y `detect_outliers_resource` leen esa columna como números donde cada valor lo permite, y reportan lo que costó:

```json
"numeric_coercion": [{
  "column": "SUELDO BRUTO (RD$)", "coerced": true,
  "values_used": 21469, "values_excluded": 37,
  "excluded_values": [{"value": "N/A", "count": 21}, {"value": "#REF!", "count": 16}]
}]
```

**Lee `values_excluded` antes de citar el total.** Una columna con menos del 90 % parseable se deja como texto y la respuesta dice por qué, en vez de contestar una pregunta sobre una medida a partir de un subconjunto arbitrario de filas. `count` y `count_distinct` nunca se coercionan.

**`linked_files`** — la URL sirvió una página, y la página enlazaba archivos de datos.

37 recursos del catálogo responden con una página web en vez de un archivo. Cuando un archivo enlazado coincide claramente con lo pedido, se descarga y `cache.resolved_from` registra `{page, followed}` — pediste una URL y recibiste datos de otra, y la respuesta lo dice en vez de esconderlo. Cuando varios candidatos son indistinguibles, vuelven como `linked_files` con nombres y puntajes, para que elijas y llames de nuevo. En este catálogo existen archivos llamados `clss.csv` y `xls.csv`; adivinar entre ellos sería inventar.

Un archivo que la página abre desde JavaScript también cuenta como enlazado. Algunos portales ponen la dirección en `onclick="window.location.assign('…')"` y en ningún otro sitio —el Tribunal Constitucional publica así sus tres formatos—, así que leer sólo las anclas hacía responder «no hay archivo de datos» sobre una página de la que cualquiera descarga con un clic.

**`cache.format_corrected`** — el formato que declara el catálogo estaba mal, y la respuesta dice en qué sentido.

El formato del catálogo es una afirmación sobre el archivo, y 83 de los 1,595 recursos hermanos la tienen mal en los dos sentidos: una hoja de cálculo registrada como CSV, y un CSV registrado como ODS. El contenedor se identifica por lo que hay dentro —el miembro `mimetype` para ODS, una parte de libro para XLSX— y nunca por la firma sola, porque `PK` es como empiezan ambos. Un ZIP con exactamente un archivo de datos se desempaqueta y `detected_from` nombra el miembro; uno con varios se deja en paz, porque decidir cuál es «el dato» sería inventar. `source_sha256` siempre corresponde a lo que sirvió el portal, así que una descarga posterior puede compararse aunque lo que se leyó viniera de dentro de un archivo comprimido.

Un `.xls` anterior a 2007 (BIFF/OLE2) no se puede leer y lo dice, con qué pedirle al publicador. Es el formato peor servido del catálogo: 12 de 22 legibles.

**Sobre el CSV que escribe `save_query_to_csv`.** Es UTF-8, con saltos CRLF y **sin BOM**. Ese CSV es correcto, y Excel en un Windows en español lo va a abrir como cp1252 y mostrar `AÃ±o` donde dice `Año`, porque sin BOM es lo que Excel asume. El archivo está bien; el problema es la herramienta con la que este público lo va a abrir. Dos salidas: ábrelo con `Datos → Desde texto/CSV`, que pregunta la codificación, o usa LibreOffice, que detecta UTF-8. Medido en Windows 11: `4E 6F 6D 62 72 65 2C 41 C3 B1 6F 0D` — `Nombre,Año\r`, UTF-8 válido, sin `EF BB BF`.

**`cache.provenance`** — la respuesta salió de una copia archivada, no del portal.

Los enlaces del gobierno se podren: el censo del 2026-08-08 encontró 15 URLs de recursos ya muertas y 98 instituciones cuyos sitios rechazan el acceso programático, así que una cifra que cites hoy puede ser incomprobable el año que viene. Apunta `DATOSGOBDO_ARCHIVE_DIR` a un directorio con un `manifest.json` y sus archivos Parquet, y cuando un portal no se pueda alcanzar el servidor responde desde la copia archivada. Está apagado por defecto, el portal se intenta siempre primero, y **la respuesta siempre lo dice** — `cache.provenance` lleva la fecha de captura, el `sha256`, la licencia y por qué no se usó el origen. Una herramienta que devolviera en silencio la copia de ayer como si fuera la de hoy dejaría de servir para auditar.

Un archivo solo guarda lo que se pudo descargar, así que no contiene los recursos que un portal rechaza. Esa es la suposición natural y es equivocada.

| Variable | Por defecto | Significado |
|---|---|---|
| `DATOSGOBDO_ARCHIVE_DIR` | sin definir (apagado) | Ruta **absoluta** a un directorio con `manifest.json` + copias Parquet a las que recurrir. |

Se define en el bloque `env` del cliente ([§13](#s13)), con ruta absoluta. Si el directorio no existe, el servidor registra una advertencia y deja el fallback apagado — no finge estar armado.

<a id="s15"></a>

## 15. Seguridad y variables de entorno

Política completa, modelo de amenazas y proceso de reporte: **[SECURITY.md](SECURITY.md)**. En resumen:

- **Solo lectura hacia el portal.** Sin autenticación, sin `package_create`, sin `resource_create`. La única herramienta que muta es `clear_cache`, sobre la caché local.
- **Dos superficies de inyección, ambas cerradas.** Los valores de usuario que entran a filtros `fq` de CKAN pasan por escapado Solr; cada identificador de columna que llega a DuckDB pasa una regex de lista blanca más una lista negra de subcadenas de comentario y terminador, y luego se entrecomilla doble.
- **`query_resource` está en sandbox.** Además de validar que la sentencia sea un único SELECT/WITH de solo lectura, el recurso se materializa en una tabla en memoria y entonces se fijan `enable_external_access=false` + `lock_configuration=true` antes de correr el SQL del usuario — de modo que las funciones de tabla de DuckDB (`read_text`, `read_csv`, `glob`, …) no pueden alcanzar el filesystem ni la red.
- **Guardia SSRF en cada descarga**, URL inicial **y** cada salto de redirección: solo http/https, y cada dirección a la que resuelva el host debe ser globalmente ruteable. Metadatos de nube (`169.254.169.254`), loopback, RFC-1918, link-local y ULA de IPv6 quedan bloqueados. La ruta guardada cubre también el HEAD de metadatos, no solo la descarga.
- **Topes de bytes** en las descargas remotas (5 MB preview, 100 MB analytics), en streaming — acotando memoria y exposición a bombas de descompresión.
- **`save_query_to_csv`** exige destino **absoluto** terminado en `.csv`/`.tsv`, rechaza `..` y rutas de sistema, y escribe con `O_NOFOLLOW`.

| Variable | Valores | Significado |
|---|---|---|
| `DATOSGOBDO_NETGUARD` | `public-only` (por defecto) / `strict` / `off` | `strict` restringe los hosts a `datos.gob.do` y subdominios; `off` desactiva la guardia. |
| `DATOSGOBDO_ALLOW_HOSTS` | separados por coma, comodines `*.` | Hosts de confianza del operador — el escape hatch para forks apuntando a otro portal CKAN. |

> **Estas van en el bloque `env` del cliente, no en tu shell** — [§13](#s13) tiene el JSON exacto. Un servidor stdio hereda solo un subconjunto limitado del entorno, así que `export DATOSGOBDO_NETGUARD=strict` deja el servidor corriendo con la guardia por defecto. No hay advertencia para esto, porque desde el lado del servidor no pasó nada. Para comprobarlo: `get_cache_stats` reporta el modo real en `server.netguard_mode`, y la línea de arranque en el log del cliente registra el modo efectivo.

El valor por defecto deliberadamente **no** es una lista blanca de hosts: como muestra [§7](#s7), los recursos legítimos viven en 273 sitios ministeriales, buckets y CDNs.

**Sobre las primitivas nuevas:** los prompts de aquí son plantillas estáticas con argumentos interpolados en texto — no hacen I/O. Los resources son lecturas de solo lectura de metadatos del portal. Ninguno agrega una ruta de escritura.

<a id="s16"></a>

## 16. Arquitectura

```
src/datosgobdo_mcp/
  server.py        Servidor FastMCP: 24 tools, 3 resources, 1 plantilla, 6 prompts
  ckan.py          Cliente CKAN: peticiones, escapado Solr, formateadores, procedencia
  analytics.py     Capa DuckDB: constructores de consulta tipados, coerción, validación SQL
  download.py      Descarga en streaming con tope, cabeceras de fetch, detección de codificación
  cache.py         Caché Parquet + índice, con llave por origen y build del parser
  preview.py       Parsers de vista por filas (CSV/TSV/XLSX/XLS/ODS/JSON)
  pagelink.py      Resuelve la URL de una página al archivo de datos que enlaza
  archive.py       Fallback a copia archivada con procedencia declarada
  reachability.py  check_resources: clasifica por qué una URL no se puede leer
  netguard.py      Guardia SSRF para URLs y cada salto de redirección
  models.py        Modelos de salida Pydantic (outputSchema tipado)
  gcp.py           Herramientas opcionales del pipeline BigQuery/GCS
```

### Decisiones de diseño

- **FastMCP en vez del SDK de bajo nivel.** Las tools son funciones decoradas con `@mcp.tool()` y tipadas con Pydantic: menos boilerplate, validación automática de argumentos.
- **DuckDB + Parquet en vez de pandas.** Caché columnar, motor SQL, streaming desde disco. Una nómina de 94 MB responde agregaciones agrupadas en menos de un segundo en caliente, y la memoria queda acotada.
- **La llave de caché incluye la build del parser** — versión del paquete más la de DuckDB, porque el sniffer de DuckDB decide los tipos de columna. Una actualización del parser no debe servir tipos inferidos por el anterior.
- **DataStore no está, así que los archivos se parsean del lado del cliente.** Ver [§12](#s12). Es la única decisión de la que se desprende el resto de la arquitectura.
- **La codificación se puntúa, no se adivina.** Las decodificaciones candidatas se ordenan por el español que recuperan, en vez de confiar en un número de confianza — el arreglo para mojibake real como `A¤o` por `Año`.
- **ODS se parsea haciendo streaming de `content.xml`.** Cargar el DOM completo convertía un archivo de 0,70 MB en 0,41 GB de RSS; ODS es cerca de un tercio de este catálogo, así que la ruta ingenua era insostenible.
- **El trabajo bloqueante corre en `asyncio.to_thread`** (transcodificación ODS, detección de codificación, COPY a Parquet) para que un parseo largo nunca detenga el event loop.
- **Truncado defensivo.** Las descripciones largas — algunas instituciones publican 5+ KB por organización — se cortan a 300 caracteres en las respuestas de listado, para que una llamada no queme miles de tokens de contexto.
- **`list_recent_datasets` está reorientada.** CKAN expone `recently_changed_packages_activity_list`, pero devuelve actividades sin hidratar (`{object_id: "uuid", activity_type: "changed package"}`) que el modelo no puede interpretar. Usamos `package_search?sort=metadata_modified+desc` y devolvemos datasets ya formateados en una sola llamada.
- **Todo el logging a stderr, y nada por el protocolo.** Según la [guía de depuración de MCP](https://modelcontextprotocol.io/docs/tools/debugging), un servidor stdio nunca debe escribir a stdout — corrompe el flujo del protocolo. El canal de logging del propio protocolo (`notifications/message`) nunca se usó aquí, y desde la especificación `2026-07-28` está desaconsejado: stderr es lo que la especificación ahora recomienda. Nada que migrar — pero no «mejores» esto agregando logging por protocolo.

### Stack

[`mcp`](https://pypi.org/project/mcp/) (SDK oficial de Python, FastMCP) · [`duckdb`](https://duckdb.org/) · [`httpx`](https://www.python-httpx.org/) · [`openpyxl`](https://openpyxl.readthedocs.io/) (XLSX en streaming de solo lectura) · [`pydantic`](https://docs.pydantic.dev/) · stdlib `csv`, `json`, `xml.etree` (ODS en streaming).

<a id="s17"></a>

## 17. Limitaciones medidas

Medidas contra el catálogo completo el 2026-08-08 — 1.056 recursos, uno por dataset, sobre sesiones MCP reales — no estimadas. Cada recurso que había fallado por una causa dentro del control de este servidor se remidió contra el portal vivo el **2026-08-13**, tras el trabajo de formatos de 0.14.0; los rechazos de sitio y los 4xx no se reintentaron, porque nada cambió de nuestro lado que pudiera afectarlos.

**No todo lo publicado es alcanzable.** **561 de 1.056** recursos se pueden leer — 540 en el censo, más **21 recuperados** por 0.14.0, que valen 82.490 filas. La recuperación es exacta y no estimada, así que el desglose se mueve con ella: de los 37 que servían una página web, **19** ahora resuelven al archivo que la página enlaza, y quedan 18; de los 8 archivos ilegibles, **2** ahora se parsean, y quedan 6.

| causa | recursos | ¿puede arreglarlo este servidor? |
|---|---:|---|
| Configuración de sitio que rechaza descargas programáticas | **360** en 98 instituciones | No. Desde una misma dirección, 21 otros hosts gubernamentales detrás del mismo CDN nos sirven con normalidad, así que es configuración por sitio y no nuestra red. |
| Fallo a nivel de transporte (causa no atribuible) | 85 | No establecido |
| Sirve una página web sin ningún archivo de datos | 18 | No — fichas del catálogo que apuntan a una página de aterrizaje |
| Enlace muerto | 15 | No |
| Archivo ilegible | 6 | Dos son `.xls` anteriores a 2007, que necesitan un lector nuevo |
| CDN cuyo origen no responde | 6 | No |
| Error del portal | 5 | No |

561 + 495 = 1.056. Los 360 rechazos no los toca nada de esto y ninguna versión de este servidor puede cambiarlos.

**Hay una segunda recuperación que no entra en esa cuenta, y es mayor.** 0.14.0 lee además **11 archivos hermanos** — un segundo o tercer formato de un dataset cuyo representante en el censo ya estaba contado — que valen **846.388 filas**, entre ellas 622.630 del ODS de SeNaSa y los archivos de nómina y vivienda que MAP, MIVHED y MESCyT publican como JSON. Quedan fuera de los 561 a propósito: el censo mide un recurso por dataset, y contar hermanos sería compararlos contra un denominador que nunca los incluyó. Lo que significa en la práctica es que un dataset cuyo CSV es ilegible puede ahora leerse en otro formato — la [§14](#s14) explica cómo `cache.format_corrected` lo dice cuando pasa.

Lo que queda **establecido**: esos sitios rechazan el acceso *programático* a sus propios datos abiertos desde la dirección medida. Lo que **no** queda establecido: que una persona con navegador en Santo Domingo sea rechazada. Esa prueba necesita un punto de salida residencial dominicano y no se ha corrido.

**Formatos.** CSV, XLSX y ODS leen alrededor del 93 % de lo que se descarga. JSON era el más débil con diferencia hasta 0.14.0 — `read_json_auto` rechazaba como malformado lo que estos portales publican de verdad, que suele ser un arreglo de registros envuelto en metadatos, o un objeto por línea — y los archivos recuperados el 2026-08-13 son en su mayoría de ese tipo. El `.xls` heredado (BIFF/OLE2) sigue siendo el peor servido y no se puede leer en absoluto: 12 de 22. **El PDF no se parsea**; solo se expone su URL de descarga.

**Tamaño.** `download_resource_preview` tope 5 MB; las herramientas de analytics, 100 MB. Un solo valor mayor de 16 MB excede el límite de DuckDB y el archivo no se puede parsear.

**Forma.** 41 recursos ponen un título o un logo encima de la fila de encabezado real, lo que estropea el esquema autodetectado — inspecciona con `download_resource_preview` y proyecta columnas explícitamente. 25 vuelven con nombres de columna genéricos (`column00`, sin nombre). 93 guardan números como texto, manejado y declarado según [§14](#s14).

**Los formatos pueden contradecirse entre sí.** 176 de 528 datasets multiformato comparables difieren en número de filas o columnas, y en 11 casos un formato está vacío mientras otro trae la tabla completa. Leer un solo formato no es evidencia de lo que contiene el dataset.

**La codificación está prácticamente resuelta:** un archivo de los 540 del censo aún muestra acentos dañados, y ese archivo está codificado en dos codepages a la vez, así que ninguna lectura única es correcta para él.

**La frescura no se puede leer de los metadatos.** `periodicidad` está vacío en los 1.056 datasets.

**Windows: probado el 2026-08-13, y hasta aquí llega.** Windows 11 (build 26200), Python 3.13, protección en tiempo real de Defender activa, cuenta sin privilegios de administrador. La suite corre en verde — 518 pasadas, 5 saltadas, y la que se salta es una prueba de `O_NOFOLLOW`, que solo existe en POSIX. La codificación aguanta de punta a punta: una nómina en cp1252 vuelve con `Año` y `UREÑA` intactos, 135 de 200 nombres de institución traen caracteres no-ASCII y ninguno llega roto, y las rutas con acentos y espacios funcionan. Una agregación sobre una nómina de 108.038 filas coincidió al céntimo con un recálculo independiente en `Decimal`. Defender no costó nada medible — la lectura en frío de 40 MB la domina el ~1 MB/s del publicador, y dos descargas crudas seguidas variaron más entre sí que Windows respecto de macOS.

Lo que **sigue sin probarse en Windows**, y por tanto no se afirma: un perfil de usuario acentuado (`C:\Users\José Pérez\`, común en República Dominicana — solo se ejercitaron subcarpetas con acentos), una carpeta Descargas redirigida a OneDrive, Claude Desktop como cliente (el transporte se condujo con otro cliente MCP), Windows instalado en una unidad distinta de `C:`, y una exclusión de Defender medida antes y después, que requiere privilegios de administrador. La rama del candado de caché específica de Windows también espera una corrida allí: su política de reintento está probada, su envoltura de cuatro líneas sobre `msvcrt` no.

**Sin probar, y por tanto sin afirmar:** el transporte hosted `streamable-http` bajo carga real, las tres herramientas GCP contra un proyecto real, y el uso concurrente más allá de cuatro procesos.

---
---

# Parte 6 — Desarrollo

<a id="s18"></a>

## 18. Desarrollo, pruebas y el MCP Inspector

### Setup local

```bash
git clone https://github.com/alcastaro/datos.gob.do-MCP-server.git
cd datos.gob.do-MCP-server
uv sync
uv run pytest          # herméticos por defecto: no requieren red
```

### El MCP Inspector

El [MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector) es la herramienta de desarrollo del propio protocolo. Habla MCP directamente, así que muestra lo que el servidor expone de verdad sin un modelo en medio — la mejor forma de ver tools, resources, plantillas y prompts como los ve el protocolo. Requiere Node 22.19+ y no instala nada permanente:

```bash
# El paquete publicado — sin clonar el repo
npx -y @modelcontextprotocol/inspector uvx dominican-open-data-mcp
```

Imprime una URL con un token de un solo uso. Ábrela para cuatro paneles:

- **Tools** — las 24 con sus esquemas. Llama una y lee el `structuredContent` crudo, incluidos `numeric_coercion`, `source_sha256` y `computation`.
- **Resources** — las tres URIs y la plantilla `datosgobdo://dataset/{dataset_id}`, con payloads crudos.
- **Prompts** — los seis, con sus argumentos, renderizados a su texto expandido antes de que algo llegue a un modelo.
- **Monitoring** — tráfico JSON-RPC en vivo en ambas direcciones.

Desde un clone, `scripts/inspector.sh` envuelve los dos casos:

```bash
./scripts/inspector.sh                                        # paquete publicado
./scripts/inspector.sh dist/dominican_open_data_mcp-*.whl     # una build local
./scripts/inspector.sh --cli --method tools/list --format json
```

La ruta de build local necesita ese wrapper: el Inspector lee todo lo que sigue al comando del servidor como sus propios flags, así que `uvx --from ./dist/….whl …` falla con `Connection closed` porque `--from` nunca llega a `uvx`.

El modo CLI sale con códigos significativos — `0` éxito, `3` requiere auth, `4` inalcanzable, `5` la herramienta devolvió error — así que entra directo en CI:

```bash
npx -y @modelcontextprotocol/inspector --cli uvx dominican-open-data-mcp \
  --method tools/list  --format json | jq -r '.result.tools[].name'
npx -y @modelcontextprotocol/inspector --cli uvx dominican-open-data-mcp \
  --method prompts/list --format json | jq -r '.result.prompts[].name'
npx -y @modelcontextprotocol/inspector --cli uvx dominican-open-data-mcp \
  --method resources/templates/list --format json
```

### Logs

Claude Desktop escribe un archivo de log por servidor, más el suyo:

```bash
tail -f ~/Library/Logs/Claude/mcp-server-datosgobdo.log   # macOS — este servidor
tail -n 20 -F ~/Library/Logs/Claude/mcp*.log              # macOS — todos los servidores + el cliente
```

```powershell
type "$env:AppData\Claude\logs\mcp*.log"                  # Windows
```

El servidor registra el arranque (endpoint, transporte, modo de guardia de red, archivo activado o no), aciertos y fallos de caché, sustituciones página→archivo, formas de parseo sospechosas, variables de entorno mal configuradas, errores fatales con traceback, y el apagado — todo a stderr, que el cliente captura. Bajo `DATOSGOBDO_TRANSPORT=streamable-http` no lo captura: ver [§13](#s13).

Los logs contienen URLs de recursos, llaves de caché y rutas de destino. No contienen credenciales — el servidor no tiene ninguna para el portal — y las herramientas GCP opcionales se autentican con tu propio ADC, que nunca se registra.

Cuando el sospechoso es el cliente y no el servidor, Claude Desktop puede abrir las DevTools de Chrome: escribe `{"allowDevTools": true}` en `~/Library/Application Support/Claude/developer_settings.json` y luego `Cmd-Option-I`. El panel Console muestra errores del lado cliente; el panel Network, cargas de mensajes y tiempos.

### Iteración

1. Commit y push a `main`.
2. Limpia la caché de `uvx` para forzar refresco: `uv cache clean dominican-open-data-mcp` (la llave es el nombre de la distribución, no el del binario).
3. Reinicia el cliente MCP.

Para ciclos más rápidos, apunta el cliente a tu clone: `command: /ruta/al/clone/.venv/bin/datosgobdo-mcp`.

### Prueba manual contra la API real

```bash
uv run python -c "
import asyncio
from datosgobdo_mcp import ckan
print(asyncio.run(ckan.get_site_stats()))
asyncio.run(ckan.close_client())
"
```

<a id="s19"></a>

## 19. Contribuir, créditos, cómo citar, licencia

### Contribuir

Los pull requests son bienvenidos. Áreas donde la ayuda rendiría bien:

- **Detección de encabezado.** 41 recursos ponen un banner encima de la fila de encabezado real. En XLSX eso puede costar el archivo entero: `precios_productos_primera_necesidad` (PROCONSUMIDOR) trae 890 filas en una hoja que declara `dimension A1:K890`, y se lee como 1 columna y 0 filas porque la celda A1 es un título. El CSV hermano recupera las 890 filas pero las nombra `column00`…`column10`. Detectar y saltar el banner recuperaría datos reales.
- **Reconciliación entre formatos.** Dado un dataset con varios formatos, elegir el fiable en vez del primero listado.
- **Parseo de JSON**, el formato más débil aquí.
- **Generalizar `ckan_endpoint`** para que la misma tubería de lectura de archivos sirva a otros portales sin DataStore de la región.
- **Pruebas en Windows**, hoy sin cubrir.

### Créditos

Desarrollado por **Alberto Castillo Aroca** ([@alcastaro](https://github.com/alcastaro)) con contribuciones de **Juana Casique** ([@juanacasique](https://github.com/juanacasique)).

Datos publicados por las instituciones del Estado dominicano vía [datos.gob.do](https://datos.gob.do), portal operado por OGTIC.

Inspirado en [`datagouv-mcp`](https://github.com/datagouv/datagouv-mcp) (Etalab, Gobierno de Francia). Para portales CKAN que sí tienen DataStore habilitado, [`ondata/ckan-mcp-server`](https://github.com/ondata/ckan-mcp-server) es la implementación de referencia y conviene usarla en su lugar — ver [§12](#s12).

### Cómo citar

Si usas este servidor — o una cifra obtenida a través de él — en un artículo, informe, dataset o charla, cítalo. El botón **«Cite this repository»** de GitHub lee [`CITATION.cff`](CITATION.cff) y ofrece APA y BibTeX directamente.

> Castillo Aroca, A. (2026). *dominican-open-data-mcp: an MCP server for
> datos.gob.do* [Computer software]. OLDS — Observatorio Latinoamericano de
> Desarrollo Sostenible. https://github.com/alcastaro/datos.gob.do-MCP-server

Es una petición, no una condición de licencia: los términos MIT están sin modificar, así que nada de aquí restringe tu uso. La cita importa por otra razón — las cifras de este catálogo cargan advertencias (qué excluyó una coerción numérica, qué archivos no se pudieron descargar), y una cita es cómo un lector llega de vuelta a ellas.

**Cita también a la institución.** Este servidor lee datos; no los produce. Cada cifra pertenece al organismo del Estado dominicano que la publicó, y `get_dataset` devuelve el nombre de esa institución precisamente para eso.

### Licencia

MIT. Ver [LICENSE](LICENSE).

Los datos accedidos por este MCP están sujetos a la licencia con la que cada institución dominicana los publica en datos.gob.do. Verificado en todo el catálogo: **1.020 datasets son ODbL**, 15 CC-BY, 6 PDDL, 3 con otros términos de dominio público, y **12 no declaran licencia alguna** — esos doce deberían quedar fuera de cualquier redistribución.

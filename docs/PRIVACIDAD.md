# Política de privacidad — `dominican-open-data-mcp`

**Vigente desde:** 2026-08-15 · **Aplica a:** `dominican-open-data-mcp`
(`datosgobdo-mcp`), todas las versiones · **Operador:** OLDS — Observatorio
Latinoamericano de Desarrollo Sostenible · **Contacto:** ai@olds2030.org

Versión en inglés: [`PRIVACY.md`](PRIVACY.md).

---

## Resumen

- **Sin cuentas, sin credenciales, sin telemetría.** El servidor no tiene
  registro de usuarios y no nos envía nada.
- **Tus términos de búsqueda sí salen de tu máquina.** Viajan a la API de
  `datos.gob.do`, porque ahí es donde corre la búsqueda. Es lo único que
  conviene leer dos veces.
- **Todo lo que lee este servidor es dato público de gobierno.** Ninguna API
  privada, ninguna fuente raspada.
- **Se cachean archivos, no preguntas.** Los archivos públicos descargados se
  cachean como Parquet; los prompts y los resultados nunca se guardan.

El servidor corre en dos modos y las respuestas difieren, así que cada sección
los distingue:

| Modo | Quién lo corre | Dónde queda el dato |
|---|---|---|
| **Local (stdio)** | tú, en tu máquina | tu disco |
| **Hosteado (streamable HTTP)** | OLDS, en infraestructura alquilada | disco efímero del contenedor |

---

## 1. Qué recogemos

Nada. No hay sistema de cuentas, ni clave de API nuestra, ni analítica, ni
reporte de fallos, ni baliza de uso. El servidor no llama a casa en ningún
modo.

## 2. Qué sale de tu máquina, y hacia quién

Existen tres flujos salientes. Los tres son el trabajo ordinario de leer un
portal de datos abiertos — ninguno es un canal lateral.

1. **Peticiones al catálogo → `https://datos.gob.do/api/3/action`.** Buscar,
   listar y leer metadatos envía tu consulta a la API CKAN del portal. **Si
   buscas `presupuesto salud 2026`, esa cadena llega a datos.gob.do.** El
   portal lo opera OGTIC (Gobierno Dominicano) bajo sus propios términos; no
   controlamos sus registros.
2. **Descargas de archivos → el host de la institución que publica.** Las
   herramientas analíticas buscan el archivo donde la institución lo publicó,
   que con frecuencia **no** es datos.gob.do. Ver el inventario en §3.
3. **Herramientas opcionales de Google Cloud → tu propio proyecto GCP.** Solo
   si instalaste el extra `[gcp]`. Ver §6.

Toda petición saliente identifica la herramienta en su `User-Agent`:

```
datosgobdo-mcp/<versión> (MCP Server)
```

Las instituciones publicadoras pueden por tanto ver en sus registros de acceso
que la petición vino de este servidor. Es deliberado: así un operador de portal
distingue un cliente documentado de un raspado anónimo.

## 3. A qué sistemas se conecta este servidor

La pregunta "¿qué bases de datos está tocando esto?" merece una respuesta real,
no una categoría. Este inventario se midió sobre el censo del catálogo de
**agosto de 2026** — un recurso por dataset, **1,056 recursos de 258
instituciones publicadoras**. Es una foto: las instituciones mueven archivos, y
la lista se puede regenerar del catálogo en cualquier momento.

**Un host de API, y muchos hosts de archivos.**

| Capa | Host | Nota |
|---|---|---|
| API del catálogo | `datos.gob.do` | toda búsqueda, listado y lectura de metadatos |
| Archivos de recursos | **273 hosts distintos** | donde cada institución publicó el archivo |

De los 1,056 recursos del censo, **1,033 (97.8 %) viven en dominios
dominicanos** — `gob.do` (932), `mil.do` (48), `edu.do` (25), `com.do` (15),
`gov.do` (10), `tse.do` (3). Entre los hosts de archivo más frecuentes están
`deepblue.simv.gob.do`, `www.fondomarena.gob.do`, `descargas.one.gob.do`,
`sb.gob.do`, `condei.gob.do`, `ambiente.gob.do` y `cnss.gob.do`; 66 recursos
están alojados en el propio `datos.gob.do`.

**Los 23 recursos que no están en dominio dominicano importan más para la
privacidad, porque un proveedor de nube extranjero ve la petición.** La lista
completa:

| Recursos | Host | Proveedor |
|---|---|---|
| 9 | `drive.google.com` | Google |
| 4 | `mopcstrapistorage.blob.core.windows.net` | Microsoft Azure |
| 4 | `institucionesestatales04-my.sharepoint.com` | Microsoft |
| 2 | `opencncc.web.app` | Google Firebase |
| 2 | `view.officeapps.live.com` | Microsoft |
| 1 | `uteco-my.sharepoint.com` | Microsoft |
| 1 | `tribunalsitestorage.blob.core.windows.net` | Microsoft Azure |

Cuando analizas uno de esos recursos, tu petición llega a Google o a Microsoft,
bajo sus términos — no los nuestros. Ni elegimos esos hosts ni podemos
cambiarlos: son donde la institución dominicana decidió publicar.

## 4. Qué se guarda, dónde y por cuánto tiempo

### Modo local (stdio) — el predeterminado

| Qué | Dónde | Retención |
|---|---|---|
| Caché Parquet de archivos públicos descargados | `~/.cache/datosgobdo-mcp` (se cambia con `DATOSGOBDO_CACHE_DIR`) | hasta que la desaloje `DATOSGOBDO_CACHE_MAX_BYTES`, o hasta que corras `clear_cache` |
| Exportaciones CSV | **solo la ruta que le pases** a `save_query_to_csv` | tuya; no la tocamos |
| Registros operativos | el log stderr de tu cliente (p. ej. `mcp-server-*.log` de Claude Desktop) | política de tu cliente |

Nada del modo local se transmite a OLDS. Tus prompts, tus resultados y tus
archivos se quedan en tu máquina.

### Modo hosteado (streamable HTTP)

| Qué | Dónde | Retención |
|---|---|---|
| Caché Parquet de archivos públicos descargados | disco efímero del contenedor | se pierde al reciclar la instancia |
| Prompts, preguntas, resultados de herramientas | **no se guardan** | — |
| Registros operativos (stderr) | flujo de logs del operador; incluyen las URLs consultadas | retención operativa corta |

Dos revelaciones honestas sobre el modo hosteado:

- **Las herramientas de sistema de archivos están desactivadas, no solo
  desaconsejadas.** `clear_cache` y `save_query_to_csv` se niegan a ejecutarse,
  porque el sistema de archivos es del host y la caché es compartida.
  `get_cache_stats` omite las rutas del servidor.
- **El proveedor de infraestructura ve metadatos de conexión.** Quien hospede
  el endpoint (CDN, plataforma de contenedores) procesa direcciones IP y
  metadatos de petición bajo su propia política de privacidad. No controlamos
  esa capa y no pretendemos lo contrario.

No hay cuentas en modo hosteado, así que de nuestro lado no hay nada que ligue
una petición a una persona.

## 5. Datos personales dentro de los datasets públicos

Este servidor lee lo que las instituciones dominicanas decidieron publicar.
**Algunos datasets públicos contienen datos personales** — nóminas, registros
de personal, listados de beneficiarios. El servidor no altera, enriquece,
cruza ni retiene esos datos más allá de la caché descrita en §4, y no les
aplica ningún tratamiento especial.

Si consideras que un dataset publicado no debería contener los datos
personales que contiene, el responsable es la institución publicadora, no este
servidor. Contáctala a ella, o a OGTIC como operador del portal.

## 6. Herramientas opcionales de Google Cloud

El extra `[gcp]` añade tres herramientas en vista previa que exportan un
recurso a tu propio bucket de Google Cloud Storage y dataset de BigQuery. Si lo
instalas y configuras:

- La autenticación usa **tus propias** Application Default Credentials. Nunca
  las vemos.
- El dato aterriza en **tu propio** proyecto, bucket y dataset.
- Google lo procesa bajo los términos de Google Cloud.

Estas herramientas no se instalan por defecto y no existen en el despliegue
hosteado.

## 7. Controles de seguridad relevantes para la privacidad

- **Guarda SSRF en cada descarga saliente.** Modo por defecto `public-only`:
  solo http/https, y toda IP resuelta debe ser globalmente enrutable — los
  endpoints de metadatos de nube, loopback y rangos privados se rechazan. El
  modo `strict` confina las descargas a `datos.gob.do` más una lista permitida
  por el operador.
- **SQL de solo lectura.** Las consultas analíticas corren contra una copia
  Parquet con el acceso a sistema de archivos y red de DuckDB desactivado, así
  que una consulta no puede leer archivos locales ni alcanzar la red.
- **Endurecimiento hosteado.** Las herramientas de sistema de archivos local y
  las destructivas compartidas están desactivadas, y las rutas del servidor no
  se devuelven a clientes remotos.

## 8. Terceros, en un solo lugar

| Tercero | Cuándo ve una petición | Bajo política de |
|---|---|---|
| OGTIC / `datos.gob.do` | toda búsqueda de catálogo y lectura de metadatos | OGTIC |
| Instituciones publicadoras (273 hosts) | cuando lees o analizas su archivo | cada institución |
| Google, Microsoft | para los 23 recursos listados en §3 | Google / Microsoft |
| Proveedor de hosting (solo modo hosteado) | toda conexión al endpoint hosteado | ese proveedor |
| Google Cloud (solo extra `[gcp]`) | cuando exportas | Google Cloud |

No vendemos nada, no compartimos nada, y no tenemos nada que compartir.

## 9. Tus opciones

- Corre en **modo local** y nada llega a OLDS en ningún momento.
- Borra la caché cuando quieras: `clear_cache`, o elimina
  `~/.cache/datosgobdo-mcp`.
- Pon `DATOSGOBDO_NETGUARD=strict` para confinar las descargas a
  `datos.gob.do`.
- No instales el extra `[gcp]` si no quieres que exista la vía de exportación.

## 10. Menores

No está dirigido a menores. No recoge nada de nadie, así que tampoco recoge
nada de un menor.

## 11. Cambios

Los cambios materiales se registran en `CHANGELOG.md` y se fechan al inicio de
este archivo. El historial de versiones de este documento es el propio
historial de git del repositorio.

## 12. Contacto

**ai@olds2030.org** — preguntas de privacidad, inquietudes sobre datos,
reportes de seguridad. Los temas de seguridad también pueden seguir
[`SECURITY.md`](../SECURITY.md).

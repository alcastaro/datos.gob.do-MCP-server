/**
 * Definición de todas las herramientas MCP expuestas.
 * Cada tool tiene: name, description, inputSchema (JSON Schema)
 */

export const TOOLS = [
  // ─── Búsqueda y descubrimiento ────────────────────────────────────────────
  {
    name: "search_datasets",
    description:
      "Busca datasets en el portal de datos abiertos de la República Dominicana (datos.gob.do). " +
      "Permite filtrar por palabra clave, organización gubernamental, etiquetas o grupos temáticos. " +
      "Ideal para explorar qué datos publica el gobierno dominicano sobre salud, educación, presupuesto, etc.",
    inputSchema: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description: "Término de búsqueda en texto libre. Ej: 'presupuesto', 'salud pública', 'educación'",
        },
        organization: {
          type: "string",
          description:
            "Slug de la organización gubernamental. Ej: 'ministerio-de-salud-publica', 'digepres', 'bcrd'",
        },
        tags: {
          type: "string",
          description: "Etiqueta temática. Ej: 'finanzas', 'poblacion', 'estadisticas'",
        },
        groups: {
          type: "string",
          description: "Grupo o categoría del portal. Ej: 'economia', 'salud'",
        },
        limit: {
          type: "integer",
          description: "Número máximo de resultados (por defecto 10, máximo 50)",
          default: 10,
          maximum: 50,
        },
        offset: {
          type: "integer",
          description: "Offset para paginación",
          default: 0,
        },
      },
    },
  },

  {
    name: "get_dataset",
    description:
      "Obtiene los metadatos completos de un dataset específico de datos.gob.do, incluyendo todos sus recursos descargables " +
      "(archivos CSV, Excel, JSON, PDF, etc.), organización responsable, licencia y descripción detallada.",
    inputSchema: {
      type: "object",
      properties: {
        id: {
          type: "string",
          description: "ID o slug del dataset. Ej: 'presupuesto-general-del-estado-2024'",
        },
      },
      required: ["id"],
    },
  },

  {
    name: "list_recent_datasets",
    description:
      "Lista los datasets modificados más recientemente en datos.gob.do. " +
      "Útil para monitorear actualizaciones del portal gubernamental dominicano.",
    inputSchema: {
      type: "object",
      properties: {
        limit: {
          type: "integer",
          description: "Número de actividades recientes a devolver (máximo 30)",
          default: 10,
          maximum: 30,
        },
      },
    },
  },

  // ─── Recursos ─────────────────────────────────────────────────────────────
  {
    name: "get_resource",
    description:
      "Obtiene los metadatos de un recurso específico (archivo) dentro de un dataset de datos.gob.do. " +
      "Incluye la URL de descarga directa, formato, tamaño y fecha de actualización.",
    inputSchema: {
      type: "object",
      properties: {
        id: {
          type: "string",
          description: "ID UUID del recurso",
        },
      },
      required: ["id"],
    },
  },

  {
    name: "search_resources",
    description:
      "Busca recursos (archivos individuales) dentro del portal datos.gob.do por nombre. " +
      "Devuelve URLs de descarga directa y metadatos de cada archivo.",
    inputSchema: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description: "Nombre o parte del nombre del recurso a buscar",
        },
        limit: {
          type: "integer",
          description: "Número máximo de resultados",
          default: 10,
          maximum: 50,
        },
      },
      required: ["query"],
    },
  },

  // ─── Organizaciones ───────────────────────────────────────────────────────
  {
    name: "list_organizations",
    description:
      "Lista todas las instituciones gubernamentales dominicanas que publican datos en datos.gob.do, " +
      "con el conteo de datasets de cada una. Incluye ministerios, organismos autónomos, municipios y otras entidades públicas.",
    inputSchema: {
      type: "object",
      properties: {
        limit: {
          type: "integer",
          description: "Número máximo de organizaciones a devolver",
          default: 50,
        },
      },
    },
  },

  {
    name: "get_organization",
    description:
      "Obtiene información detallada sobre una institución gubernamental específica en datos.gob.do: " +
      "descripción, número de datasets publicados y enlace directo al perfil.",
    inputSchema: {
      type: "object",
      properties: {
        id: {
          type: "string",
          description: "ID o slug de la organización. Ej: 'ministerio-de-hacienda', 'bcrd', 'indotel'",
        },
      },
      required: ["id"],
    },
  },

  // ─── Grupos y tags ────────────────────────────────────────────────────────
  {
    name: "list_groups",
    description:
      "Lista todas las categorías temáticas disponibles en datos.gob.do (economía, salud, educación, etc.) " +
      "con el número de datasets en cada categoría.",
    inputSchema: {
      type: "object",
      properties: {},
    },
  },

  {
    name: "list_tags",
    description:
      "Lista etiquetas disponibles en datos.gob.do, opcionalmente filtradas por prefijo. " +
      "Útil para descubrir vocabulario controlado y refinar búsquedas.",
    inputSchema: {
      type: "object",
      properties: {
        query: {
          type: "string",
          description: "Prefijo o texto para filtrar etiquetas",
        },
        limit: {
          type: "integer",
          default: 20,
        },
      },
    },
  },

  // ─── Stats ────────────────────────────────────────────────────────────────
  {
    name: "get_site_stats",
    description:
      "Devuelve estadísticas generales del portal datos.gob.do: total de datasets publicados, " +
      "número de organizaciones, grupos temáticos y etiquetas.",
    inputSchema: {
      type: "object",
      properties: {},
    },
  },
];

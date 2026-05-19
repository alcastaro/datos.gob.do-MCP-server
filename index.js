#!/usr/bin/env node
/**
 * datosgobdo-mcp
 * Servidor MCP para datos.gob.do — República Dominicana
 *
 * Expone los datos abiertos del gobierno dominicano como herramientas
 * accesibles por cualquier LLM compatible con el protocolo MCP.
 *
 * Autor: Alberto Castillo Aroca
 * Basado en la arquitectura de datagouv-mcp (data.gouv.fr)
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";

import { TOOLS } from "./tools.js";
import {
  searchDatasets,
  getDataset,
  getResource,
  searchResources,
  listRecentDatasets,
  listOrganizations,
  getOrganization,
  listGroups,
  listTags,
  getSiteStats,
} from "./ckan.js";

// ─── Servidor MCP ─────────────────────────────────────────────────────────────

const server = new Server(
  {
    name: "datosgobdo-mcp",
    version: "0.1.0",
  },
  {
    capabilities: {
      tools: {},
    },
  }
);

// ─── Listar herramientas disponibles ──────────────────────────────────────────

server.setRequestHandler(ListToolsRequestSchema, async () => {
  return { tools: TOOLS };
});

// ─── Ejecutar herramientas ────────────────────────────────────────────────────

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;

  try {
    let result;

    switch (name) {
      case "search_datasets":
        result = await searchDatasets(args);
        break;

      case "get_dataset":
        result = await getDataset(args.id);
        break;

      case "get_resource":
        result = await getResource(args.id);
        break;

      case "list_recent_datasets":
        result = await listRecentDatasets(args);
        break;

      case "search_resources":
        result = await searchResources(args);
        break;

      case "list_organizations":
        result = await listOrganizations(args);
        break;

      case "get_organization":
        result = await getOrganization(args.id);
        break;

      case "list_groups":
        result = await listGroups(args);
        break;

      case "list_tags":
        result = await listTags(args);
        break;

      case "get_site_stats":
        result = await getSiteStats();
        break;

      default:
        throw new Error(`Herramienta desconocida: ${name}`);
    }

    return {
      content: [
        {
          type: "text",
          text: JSON.stringify(result, null, 2),
        },
      ],
    };
  } catch (error) {
    return {
      content: [
        {
          type: "text",
          text: `Error: ${error.message}`,
        },
      ],
      isError: true,
    };
  }
});

// ─── Iniciar servidor ─────────────────────────────────────────────────────────

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("datosgobdo-mcp iniciado — datos.gob.do conectado ✓");
}

main().catch((err) => {
  console.error("Error fatal:", err);
  process.exit(1);
});

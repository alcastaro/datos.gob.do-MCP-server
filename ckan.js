/**
 * Cliente para la API CKAN de datos.gob.do
 * Documentación CKAN: https://docs.ckan.org/en/2.9/api/
 *
 * Requiere Node.js >= 18 (fetch nativo)
 */

const BASE_URL = "https://datos.gob.do/api/3/action";
const DEFAULT_TIMEOUT_MS = 10_000;

async function ckanRequest(action, params = {}) {
  const url = new URL(`${BASE_URL}/${action}`);
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
  });

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), DEFAULT_TIMEOUT_MS);

  let res;
  try {
    res = await fetch(url.toString(), {
      headers: { "User-Agent": "datosgobdo-mcp/0.1 (MCP Server)" },
      signal: controller.signal,
    });
  } catch (err) {
    if (err.name === "AbortError") throw new Error(`Timeout en ${action} (>${DEFAULT_TIMEOUT_MS}ms)`);
    throw err;
  } finally {
    clearTimeout(timer);
  }

  if (!res.ok) {
    throw new Error(`Error API datos.gob.do [${action}]: ${res.status} ${res.statusText}`);
  }

  const json = await res.json();
  if (!json.success) {
    throw new Error(`CKAN error en ${action}: ${json.error?.message || JSON.stringify(json.error)}`);
  }

  return json.result;
}

// ─── Datasets ────────────────────────────────────────────────────────────────

export async function searchDatasets({ query, organization, tags, groups, limit = 10, offset = 0 } = {}) {
  const fq_parts = [];
  if (organization) fq_parts.push(`organization:${organization}`);
  if (tags) fq_parts.push(`tags:"${tags}"`);
  if (groups) fq_parts.push(`groups:${groups}`);

  const params = {
    q: query || "*:*",
    rows: Math.min(Number(limit) || 10, 50), // nunca más de 50
    start: Number(offset) || 0,
  };
  if (fq_parts.length) params.fq = fq_parts.join(" AND ");

  const result = await ckanRequest("package_search", params);
  return {
    total: result.count,
    returned: result.results.length,
    datasets: result.results.map(formatDataset),
  };
}

export async function getDataset(id) {
  const result = await ckanRequest("package_show", { id });
  return formatDatasetFull(result);
}

export async function listRecentDatasets({ limit = 10 } = {}) {
  const result = await ckanRequest("recently_changed_packages_activity_list", {
    limit: Math.min(Number(limit) || 10, 30),
  });
  // Devuelve actividades, no datasets directamente
  return (result || []).map((a) => ({
    dataset_id: a.object_id,
    timestamp: a.timestamp,
    activity_type: a.activity_type,
    user: a.user_id,
  }));
}

// ─── Recursos ─────────────────────────────────────────────────────────────────

export async function getResource(id) {
  const result = await ckanRequest("resource_show", { id });
  return formatResource(result);
}

export async function searchResources({ query, limit = 10 } = {}) {
  const result = await ckanRequest("resource_search", {
    query: `name:${query}`,
    limit: Math.min(Number(limit) || 10, 50),
  });
  return {
    total: result.count,
    resources: (result.results || []).map(formatResource),
  };
}

// ─── Organizaciones ───────────────────────────────────────────────────────────

export async function listOrganizations({ limit = 50 } = {}) {
  // organization_list con all_fields=true no acepta limit — devuelve todo
  // Usamos package_search para obtener conteos reales por organización
  const result = await ckanRequest("organization_list", {
    all_fields: true,
    include_dataset_count: true,
    include_extras: false,
  });

  const orgs = (Array.isArray(result) ? result : []).map((org) => ({
    id: org.id,
    name: org.name,
    title: org.title || org.display_name || org.name,
    description: org.description ? org.description.slice(0, 200) : null,
    dataset_count: org.package_count ?? null,
    url: `https://datos.gob.do/organization/${org.name}`,
  }));

  // Aplicamos limit manualmente
  return orgs.slice(0, Number(limit) || 50);
}

export async function getOrganization(id) {
  const result = await ckanRequest("organization_show", {
    id,
    include_datasets: false,
    include_dataset_count: true,
    include_extras: true,
  });
  return {
    id: result.id,
    name: result.name,
    title: result.title || result.display_name,
    description: result.description,
    dataset_count: result.package_count ?? null,
    url: `https://datos.gob.do/organization/${result.name}`,
    extras: (result.extras || []).map((e) => ({ key: e.key, value: e.value })),
  };
}

// ─── Grupos / Temáticas ────────────────────────────────────────────────────────

export async function listGroups() {
  const result = await ckanRequest("group_list", {
    all_fields: true,
    include_dataset_count: true,
    include_extras: false,
  });
  return (Array.isArray(result) ? result : []).map((g) => ({
    id: g.id,
    name: g.name,
    title: g.title || g.display_name || g.name,
    description: g.description ? g.description.slice(0, 200) : null,
    dataset_count: g.package_count ?? null,
  }));
}

// ─── Tags ─────────────────────────────────────────────────────────────────────

export async function listTags({ query, limit = 20 } = {}) {
  const params = {};
  if (query) params.query = query;
  // No usar all_fields — CKAN devuelve strings simples por defecto, más seguro
  const result = await ckanRequest("tag_list", params);
  const tags = Array.isArray(result) ? result : [];
  // result puede ser array de strings o de objetos según versión CKAN
  const normalized = tags.map((t) => (typeof t === "string" ? t : t.name || t.display_name));
  return normalized.slice(0, Number(limit) || 20);
}

// ─── Stats del portal ─────────────────────────────────────────────────────────

export async function getSiteStats() {
  // Llamadas paralelas con catch individual para no fallar todo si una falla
  const [packages, orgs, groups] = await Promise.all([
    ckanRequest("package_search", { rows: 0, q: "*:*" })
      .then((r) => r.count)
      .catch(() => null),
    ckanRequest("organization_list", {})
      .then((r) => (Array.isArray(r) ? r.length : null))
      .catch(() => null),
    ckanRequest("group_list", {})
      .then((r) => (Array.isArray(r) ? r.length : null))
      .catch(() => null),
  ]);

  // Tags por separado — puede ser lento en portales grandes
  const tags = await ckanRequest("tag_list", {})
    .then((r) => (Array.isArray(r) ? r.length : null))
    .catch(() => null);

  return {
    total_datasets: packages,
    total_organizations: orgs,
    total_groups: groups,
    total_tags: tags,
    portal: "datos.gob.do",
    pais: "República Dominicana",
    plataforma: "CKAN",
  };
}

// ─── Helpers de formato ───────────────────────────────────────────────────────

function formatDataset(d) {
  return {
    id: d.id,
    name: d.name,
    title: d.title,
    organization: d.organization?.title || d.organization?.name || null,
    notes: d.notes ? d.notes.slice(0, 300) : null,
    tags: (d.tags || []).map((t) => t.name),
    groups: (d.groups || []).map((g) => g.title || g.name),
    resource_count: (d.resources || []).length,
    formats: [...new Set((d.resources || []).map((r) => r.format).filter(Boolean))],
    last_modified: d.metadata_modified || null,
    url: `https://datos.gob.do/dataset/${d.name}`,
  };
}

function formatDatasetFull(d) {
  return {
    ...formatDataset(d),
    resources: (d.resources || []).map(formatResource),
    license: d.license_title || d.license_id || null,
    author: d.author || null,
    maintainer: d.maintainer || null,
    extras: (d.extras || []).map((e) => ({ key: e.key, value: e.value })),
  };
}

function formatResource(r) {
  return {
    id: r.id,
    name: r.name || null,
    description: r.description || null,
    format: r.format || null,
    url: r.url || null,
    created: r.created || null,
    // CKAN usa last_modified o revision_timestamp según versión
    last_modified: r.last_modified || r.revision_timestamp || null,
    size: r.size || null,
    mimetype: r.mimetype || r.mimetype_inner || null,
  };
}

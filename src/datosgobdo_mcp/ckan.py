"""Cliente CKAN para datos.gob.do.

Documentación CKAN: https://docs.ckan.org/en/2.11/api/
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from . import USER_AGENT

logger = logging.getLogger(__name__)

BASE_URL = "https://datos.gob.do/api/3/action"
DEFAULT_TIMEOUT = 15.0
MAX_ROWS = 50
MAX_RECENT = 30
MAX_AUTOCOMPLETE = 30
DESC_TRUNC = 300
NOTES_TRUNC = 300

_client: httpx.AsyncClient | None = None


async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"User-Agent": USER_AGENT},
            timeout=DEFAULT_TIMEOUT,
            follow_redirects=True,
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


# What a catalog reply is, and is not. Every field here was typed by the
# publishing institution into CKAN; none of it was read out of the file. An
# assistant that described five datasets in confident detail — file counts,
# what the columns measure — had taken all of it from `description`, and every
# one of those files turned out to be unreachable. The answer read exactly the
# same as one built from data.
CATALOG_SOURCE = "catalog_metadata"
CATALOG_SOURCE_NOTE = (
    "These fields come from the catalog record the institution filled in, not "
    "from reading the file. Nothing here says the file is reachable or that its "
    "contents match its description; use get_resource_schema or "
    "check_resources before relying on either."
)


def with_provenance(payload: dict[str, Any]) -> dict[str, Any]:
    """Stamp a catalog reply with where its facts came from."""
    if "error" in payload:
        return payload
    return {**payload, "source": CATALOG_SOURCE, "source_note": CATALOG_SOURCE_NOTE}


def _clean(params: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in params.items() if v is not None}


async def ckan_request(action: str, params: dict[str, Any] | None = None) -> Any:
    client = await _get_client()
    try:
        r = await client.get(f"/{action}", params=_clean(params or {}))
    except httpx.TimeoutException as e:
        raise RuntimeError(f"Timeout en {action} (>{DEFAULT_TIMEOUT}s)") from e
    except httpx.HTTPError as e:
        raise RuntimeError(f"Error de red en {action}: {e}") from e
    if r.status_code >= 400:
        raise RuntimeError(f"Error API datos.gob.do [{action}]: {r.status_code} {r.reason_phrase}")
    data = r.json()
    if not data.get("success"):
        err = data.get("error", {})
        msg = err.get("message") if isinstance(err, dict) else str(err)
        raise RuntimeError(f"CKAN error en {action}: {msg}")
    return data["result"]


# ─── Solr escaping ────────────────────────────────────────────────────────────

_SOLR_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')


def _escape_solr(value: str) -> str:
    """Escape Solr/Lucene reserved chars so user-supplied values don't break fq."""
    return _SOLR_SPECIAL.sub(r"\\\1", value)


def _fq_term(field: str, value: str) -> str:
    """Build a single fq clause: field:"escaped value" if it has spaces, else field:escaped."""
    escaped = _escape_solr(value)
    if " " in value or '"' in value:
        return f'{field}:"{escaped}"'
    return f"{field}:{escaped}"


# ─── Formatters ───────────────────────────────────────────────────────────────


def _truncate(s: str | None, n: int) -> str | None:
    if s is None:
        return None
    s = s.strip()
    return s if len(s) <= n else s[:n].rstrip() + "…"


def format_resource(r: dict) -> dict:
    return {
        "id": r.get("id"),
        "name": r.get("name"),
        "description": _truncate(r.get("description"), DESC_TRUNC),
        "format": r.get("format"),
        "url": r.get("url"),
        "size": r.get("size"),
        "mimetype": r.get("mimetype") or r.get("mimetype_inner"),
        "created": r.get("created"),
        "last_modified": r.get("last_modified") or r.get("metadata_modified"),
    }


def format_dataset(d: dict) -> dict:
    org = d.get("organization") or {}
    resources = d.get("resources") or []
    return {
        "id": d.get("id"),
        "name": d.get("name"),
        "title": d.get("title"),
        "organization": org.get("title") or org.get("name"),
        "organization_slug": org.get("name"),
        "notes": _truncate(d.get("notes"), NOTES_TRUNC),
        "tags": [t.get("name") for t in (d.get("tags") or []) if t.get("name")],
        "groups": [g.get("title") or g.get("name") for g in (d.get("groups") or [])],
        "resource_count": len(resources),
        "formats": sorted({r.get("format") for r in resources if r.get("format")}),
        "last_modified": d.get("metadata_modified"),
        "license": d.get("license_title") or d.get("license_id"),
        "url": f"https://datos.gob.do/dataset/{d.get('name')}" if d.get("name") else None,
    }


def format_dataset_full(d: dict) -> dict:
    base = format_dataset(d)
    base["resources"] = [format_resource(r) for r in (d.get("resources") or [])]
    base["author"] = d.get("author")
    base["maintainer"] = d.get("maintainer")
    extras = d.get("extras") or []
    if extras:
        base["extras"] = [{"key": e.get("key"), "value": e.get("value")} for e in extras]
    return base


def format_organization(o: dict, *, short: bool = False) -> dict:
    out = {
        "id": o.get("id"),
        "name": o.get("name"),
        "title": o.get("title") or o.get("display_name") or o.get("name"),
        "dataset_count": o.get("package_count"),
        "url": f"https://datos.gob.do/organization/{o.get('name')}" if o.get("name") else None,
    }
    if not short:
        out["description"] = _truncate(o.get("description"), DESC_TRUNC)
    return out


def format_group(g: dict) -> dict:
    return {
        "id": g.get("id"),
        "name": g.get("name"),
        "title": g.get("title") or g.get("display_name") or g.get("name"),
        "description": _truncate(g.get("description"), DESC_TRUNC),
        "dataset_count": g.get("package_count"),
        "url": f"https://datos.gob.do/group/{g.get('name')}" if g.get("name") else None,
    }


# ─── Public CKAN operations ───────────────────────────────────────────────────


async def search_datasets(
    query: str | None = None,
    organization: str | None = None,
    tag: str | None = None,
    group: str | None = None,
    limit: int = 10,
    offset: int = 0,
) -> dict:
    try:
        fq_parts: list[str] = []
        if organization:
            fq_parts.append(_fq_term("organization", organization))
        if tag:
            fq_parts.append(_fq_term("tags", tag))
        if group:
            fq_parts.append(_fq_term("groups", group))

        params: dict[str, Any] = {
            "q": query or "*:*",
            "rows": min(max(int(limit), 1), MAX_ROWS),
            "start": max(int(offset), 0),
        }
        if fq_parts:
            params["fq"] = " AND ".join(fq_parts)

        result = await ckan_request("package_search", params)
        return with_provenance(
            {
                "total": result.get("count", 0),
                "returned": len(result.get("results", [])),
                "offset": params["start"],
                "datasets": [format_dataset(d) for d in result.get("results", [])],
            }
        )
    except RuntimeError as e:
        return {
            "error": str(e),
            "hint": "Try narrowing the query or verify the organization slug with autocomplete(kind='organization').",
        }


async def get_dataset(id: str) -> dict:
    try:
        result = await ckan_request("package_show", {"id": id})
        return with_provenance(format_dataset_full(result))
    except RuntimeError as e:
        return {
            "error": str(e),
            "hint": f"Dataset '{id}' not found. Use search_datasets or autocomplete(kind='dataset') to find valid IDs.",
        }


async def list_recent_datasets(limit: int = 10) -> dict:
    """Datasets sorted by metadata_modified desc — no N+1 hydration needed."""
    try:
        params = {
            "q": "*:*",
            "rows": min(max(int(limit), 1), MAX_RECENT),
            "sort": "metadata_modified desc",
        }
        result = await ckan_request("package_search", params)
        return with_provenance(
            {
                "total": result.get("count", 0),
                "returned": len(result.get("results", [])),
                "datasets": [format_dataset(d) for d in result.get("results", [])],
            }
        )
    except RuntimeError as e:
        return {"error": str(e), "hint": "The datos.gob.do portal may be temporarily unavailable."}


async def get_resource(id: str) -> dict:
    try:
        result = await ckan_request("resource_show", {"id": id})
        return with_provenance(format_resource(result))
    except RuntimeError as e:
        return {"error": str(e), "hint": "Get resource IDs from get_dataset(dataset_id)."}


async def search_resources(query: str, limit: int = 10) -> dict:
    # resource_search parses "field:term" by splitting on the FIRST colon, and the
    # term is matched as a plain string (not Solr — backslash escapes would corrupt
    # it). A user ':' or '"' would therefore alter the query structure: strip them.
    sanitized = query.replace(":", " ").replace('"', " ").strip()
    try:
        result = await ckan_request(
            "resource_search",
            {
                "query": f"name:{sanitized}",
                "limit": min(max(int(limit), 1), MAX_ROWS),
            },
        )
        raw = result.get("results") or []
        parents = await _parent_datasets(raw)
        resources = []
        for r in raw:
            formatted = format_resource(r)
            parent = parents.get(r.get("package_id"))
            if parent:
                formatted.update(parent)
            resources.append(formatted)
        return with_provenance({"total": result.get("count", 0), "resources": resources})
    except RuntimeError as e:
        return {"error": str(e), "hint": "Try a broader search term."}


async def _parent_datasets(resources: list[dict]) -> dict[str, dict]:
    """Map each resource's package_id to its dataset and publishing institution.

    CKAN's `resource_search` answers with the file and nothing around it: a
    name, a format and a URL, with no dataset and no institution. For this
    catalog that is close to useless — the answer to "who publishes this?" is
    the first thing anyone asks of a government file, and files here are named
    things like `clss.csv`. Worse, a caller with no institution has no way to
    judge whether the file is the one they meant.

    Resolved in a single extra request rather than one per resource: the
    unique package ids go into one `package_search` filter query. A failure
    here degrades to no parent information rather than failing the search,
    since a resource list without institutions still beats an error.
    """
    ids = {str(r["package_id"]) for r in resources if r.get("package_id")}
    if not ids:
        return {}
    try:
        found = await ckan_request(
            "package_search",
            {"fq": "id:(" + " OR ".join(sorted(ids)) + ")", "rows": len(ids)},
        )
    except RuntimeError:
        return {}
    out: dict[str, dict] = {}
    for pkg in found.get("results") or []:
        org = pkg.get("organization") or {}
        out[pkg.get("id")] = {
            "dataset_title": pkg.get("title"),
            "dataset_slug": pkg.get("name"),
            "organization": org.get("title"),
            "organization_slug": org.get("name"),
        }
    return out


ORGANIZATION_PAGE = 25  # CKAN's own ceiling for organization_list?all_fields=true


async def list_organizations(limit: int = 50) -> list[dict]:
    """List publishing institutions, honouring the limit that was asked for.

    `organization_list?all_fields=true` caps every response at 25 no matter
    what `limit` says, and the cap is silent: asking for 500 of this portal's
    266 institutions returned 25 and looked complete. That is worse than an
    error, and it had a second victim — the hint on a failed
    `get_organization` says "use list_organizations", which for any
    institution outside the first 25 alphabetically was advice that could not
    work. Pages are fetched until the requested number is reached or the
    portal runs out.
    """
    limit = max(int(limit), 1)
    try:
        collected: list[dict] = []
        offset = 0
        while len(collected) < limit:
            page = await ckan_request(
                "organization_list",
                {
                    "all_fields": True,
                    "include_dataset_count": True,
                    "include_extras": False,
                    "offset": offset,
                    "limit": ORGANIZATION_PAGE,
                },
            )
            if not isinstance(page, list) or not page:
                break
            collected.extend(page)
            if len(page) < ORGANIZATION_PAGE:
                break
            offset += ORGANIZATION_PAGE
        return [format_organization(o, short=True) for o in collected[:limit]]
    except RuntimeError as e:
        return [
            {"error": str(e), "hint": "The datos.gob.do portal may be temporarily unavailable."}
        ]


async def get_organization(id: str) -> dict:
    try:
        result = await ckan_request(
            "organization_show",
            {
                "id": id,
                "include_datasets": False,
                "include_dataset_count": True,
                "include_extras": True,
            },
        )
        out = format_organization(result)
        extras = result.get("extras") or []
        if extras:
            out["extras"] = [{"key": e.get("key"), "value": e.get("value")} for e in extras]
        return out
    except RuntimeError as e:
        # Telling the caller to go looking is a worse answer than looking. The
        # slugs here are the full legal name — INDOTEL is registered as
        # `instituto-dominicano-de-las-telecomunicaciones-indotel` — so the
        # acronym everyone actually types never resolves, and the old hint
        # sent them to a listing that silently showed only the first 25 of
        # 266. The lookup that would have worked is one request, so it is made
        # here instead of being suggested.
        return {"error": str(e), "hint": await _slug_suggestion("organization", id)}


async def _slug_suggestion(kind: str, wanted: str) -> str:
    """A hint that names the match, or explains how to find it when there is none."""
    try:
        matches = await autocomplete(kind=kind, query=wanted, limit=3)
    except RuntimeError:
        matches = []
    named = [m for m in matches if isinstance(m, dict) and m.get("name")]
    if named:
        best = named[0]
        hint = (
            f"'{wanted}' is not a valid slug. Did you mean '{best['name']}' ({best.get('title')})?"
        )
        if len(named) > 1:
            others = ", ".join(f"'{m['name']}'" for m in named[1:])
            hint += f" Other matches: {others}."
        return hint
    return (
        f"No {kind} matches '{wanted}'. Slugs here are the full registered name, not the "
        f"acronym. Try autocomplete(kind='{kind}', query='...') with part of the name."
    )


async def list_groups() -> list[dict]:
    try:
        result = await ckan_request(
            "group_list",
            {"all_fields": True, "include_dataset_count": True, "include_extras": False},
        )
        if not isinstance(result, list):
            return []
        return [format_group(g) for g in result]
    except RuntimeError as e:
        return [
            {"error": str(e), "hint": "The datos.gob.do portal may be temporarily unavailable."}
        ]


async def list_tags(query: str | None = None, limit: int = 20) -> list[str]:
    try:
        params: dict[str, Any] = {}
        if query:
            params["query"] = query
        result = await ckan_request("tag_list", params)
        if not isinstance(result, list):
            return []
        tags = [
            t if isinstance(t, str) else (t.get("name") or t.get("display_name")) for t in result
        ]
        tags = [t for t in tags if t]
        return tags[: max(int(limit), 1)]
    except RuntimeError as e:
        # list[str] contract can't carry an error dict — degrade to empty but
        # leave a trace so a failing portal isn't mistaken for "no tags".
        logger.warning("list_tags failed, returning []: %s", e)
        return []


async def autocomplete(kind: str, query: str, limit: int = 10) -> list[Any]:
    action_map = {
        "dataset": "package_autocomplete",
        "organization": "organization_autocomplete",
        "group": "group_autocomplete",
        "tag": "tag_autocomplete",
    }
    if kind not in action_map:
        return [{"error": f"Invalid kind '{kind}'. Must be one of: {list(action_map)}"}]
    try:
        params = {"q": query, "limit": min(max(int(limit), 1), MAX_AUTOCOMPLETE)}
        result = await ckan_request(action_map[kind], params)
        return result if isinstance(result, list) else []
    except RuntimeError as e:
        logger.warning("autocomplete(%s) failed, returning []: %s", kind, e)
        return []


async def get_site_stats() -> dict:
    """Stats agregados. Hace 4 llamadas paralelas; resilientes a fallos individuales."""
    import asyncio

    async def _safe_count(action: str, params: dict, key: str = "count") -> int | None:
        try:
            r = await ckan_request(action, params)
            if isinstance(r, dict):
                return r.get(key)
            if isinstance(r, list):
                return len(r)
            return None
        except Exception:
            return None

    datasets, orgs, groups, tags = await asyncio.gather(
        _safe_count("package_search", {"rows": 0, "q": "*:*"}),
        _safe_count("organization_list", {}),
        _safe_count("group_list", {}),
        _safe_count("tag_list", {}),
    )

    return {
        "total_datasets": datasets,
        "total_organizations": orgs,
        "total_groups": groups,
        "total_tags": tags,
        "portal": "datos.gob.do",
        "pais": "República Dominicana",
        "plataforma": "CKAN 2.11",
    }

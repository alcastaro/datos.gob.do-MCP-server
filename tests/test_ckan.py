"""Unit tests for the CKAN client (no network)."""

from __future__ import annotations

import httpx
import pytest

from datosgobdo_mcp import ckan

# ─── Solr escape ──────────────────────────────────────────────────────────────


def test_escape_solr_passes_safe_chars():
    assert ckan._escape_solr("safe-slug-123") == "safe-slug-123".replace("-", r"\-")


@pytest.mark.parametrize(
    "raw,expected_contains",
    [
        ('with "quote"', r"\"quote\""),
        ("with : colon", r"\:"),
        ("(parens)", r"\(parens\)"),
        ("a+b", r"a\+b"),
        ("a&b", r"a\&b"),
        ("path/slash", r"path\/slash"),
        ("back\\slash", r"back\\slash"),
        ("star*", r"star\*"),
    ],
)
def test_escape_solr_escapes_reserved(raw, expected_contains):
    out = ckan._escape_solr(raw)
    assert expected_contains in out


def test_fq_term_quotes_when_value_has_space():
    out = ckan._fq_term("organization", "ministerio de salud")
    assert out.startswith('organization:"')
    assert out.endswith('"')


def test_fq_term_no_quotes_when_value_simple():
    out = ckan._fq_term("organization", "ministerio-de-salud")
    assert '"' not in out
    assert out.startswith("organization:")


def test_fq_term_escapes_special_in_value():
    out = ckan._fq_term("tags", 'has "quote" inside')
    # Must contain the escaped quote, never raw `"` outside outer quotes.
    assert r"\"" in out
    # Outer must wrap with non-escaped quotes since value has a space.
    assert out.startswith('tags:"')
    assert out.endswith('"')


# ─── Formatters ───────────────────────────────────────────────────────────────


def test_truncate_short_string_returned_as_is():
    assert ckan._truncate("hola", 100) == "hola"


def test_truncate_long_string_gets_ellipsis():
    long = "x" * 500
    out = ckan._truncate(long, 50)
    assert out is not None
    assert len(out) <= 51  # 50 chars + ellipsis
    assert out.endswith("…")


def test_truncate_none_returns_none():
    assert ckan._truncate(None, 10) is None


def test_format_dataset_minimum_fields():
    raw = {
        "id": "abc",
        "name": "presupuesto-2024",
        "title": "Presupuesto 2024",
        "organization": {"name": "minhacienda", "title": "Min. de Hacienda"},
        "notes": "Descripción larga " * 100,
        "tags": [{"name": "finanzas"}, {"name": "presupuesto"}],
        "groups": [{"title": "Economía"}],
        "resources": [{"format": "CSV"}, {"format": "XLSX"}, {"format": "CSV"}],
        "metadata_modified": "2026-01-01T00:00:00",
    }
    d = ckan.format_dataset(raw)
    assert d["id"] == "abc"
    assert d["name"] == "presupuesto-2024"
    assert d["organization"] == "Min. de Hacienda"
    assert d["organization_slug"] == "minhacienda"
    assert d["resource_count"] == 3
    assert set(d["formats"]) == {"CSV", "XLSX"}  # deduped
    assert d["url"] == "https://datos.gob.do/dataset/presupuesto-2024"
    assert len(d["notes"]) <= ckan.NOTES_TRUNC + 1  # +1 for ellipsis


def test_format_resource_handles_missing_fields():
    r = ckan.format_resource({"id": "uuid"})
    assert r["id"] == "uuid"
    assert r["name"] is None
    assert r["format"] is None


def test_format_organization_short_strips_description():
    o = ckan.format_organization(
        {
            "id": "org1",
            "name": "minhac",
            "title": "Min. Hacienda",
            "description": "x" * 5000,
            "package_count": 12,
        },
        short=True,
    )
    assert "description" not in o
    assert o["dataset_count"] == 12


def test_format_organization_full_truncates_description():
    o = ckan.format_organization(
        {
            "id": "org1",
            "name": "minhac",
            "description": "x" * 5000,
            "package_count": 12,
        },
        short=False,
    )
    assert "description" in o
    assert len(o["description"]) <= ckan.DESC_TRUNC + 1


# ─── Error handling (return {error, hint} instead of raising) ──────────────────


@pytest.fixture(autouse=True)
async def reset_ckan_client():
    """Reset the ckan module-level client so httpx_mock intercepts cleanly.

    An async fixture, run by pytest-asyncio on the test's own loop. The previous
    version called `asyncio.get_event_loop().run_until_complete(...)` from a sync
    fixture, which Python 3.12 deprecates when no loop is running and 3.14 turns
    into a RuntimeError — a suite that passes today and stops collecting on the
    next interpreter.
    """
    await ckan.close_client()
    yield
    await ckan.close_client()


@pytest.mark.asyncio
async def test_search_datasets_returns_error_dict_on_network_failure(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("portal down"))
    result = await ckan.search_datasets(query="test")
    assert "error" in result
    assert "hint" in result


@pytest.mark.asyncio
async def test_get_dataset_returns_error_dict_on_network_failure(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("portal down"))
    result = await ckan.get_dataset("nonexistent-id")
    assert "error" in result
    assert "hint" in result
    assert "search_datasets" in result["hint"] or "autocomplete" in result["hint"]


@pytest.mark.asyncio
async def test_get_organization_returns_error_dict_on_network_failure(httpx_mock):
    """Both the lookup and the suggestion that follows it fail: the reply still
    carries an error and a hint, and the hint never invents a slug."""
    httpx_mock.add_exception(httpx.ConnectError("portal down"))
    httpx_mock.add_exception(httpx.ConnectError("portal down"))
    result = await ckan.get_organization("nonexistent-org")
    assert "error" in result
    assert "autocomplete" in result["hint"]


@pytest.mark.asyncio
async def test_get_organization_hint_names_the_slug_it_found(httpx_mock):
    """Telling the caller to go looking is a worse answer than looking. Slugs
    here are the full registered name, so the acronym everyone types — INDOTEL
    — never resolves on its own."""
    httpx_mock.add_response(status_code=404)
    httpx_mock.add_response(
        json={
            "success": True,
            "result": [
                {
                    "id": "x",
                    "name": "instituto-dominicano-de-las-telecomunicaciones-indotel",
                    "title": "Instituto Dominicano de las Telecomunicaciones (INDOTEL)",
                }
            ],
        }
    )
    result = await ckan.get_organization("indotel")
    assert "error" in result
    assert "instituto-dominicano-de-las-telecomunicaciones-indotel" in result["hint"]
    assert "Did you mean" in result["hint"]


@pytest.mark.asyncio
async def test_list_organizations_pages_past_ckans_silent_cap(httpx_mock):
    """organization_list?all_fields=true caps every response at 25 no matter
    what limit says, and the cap is silent: asking for 500 of this portal's 266
    institutions returned 25 and looked complete."""
    page1 = [{"id": str(i), "name": f"org-{i}", "title": f"Org {i}"} for i in range(25)]
    page2 = [{"id": "25", "name": "org-25", "title": "Org 25"}]
    httpx_mock.add_response(json={"success": True, "result": page1})
    httpx_mock.add_response(json={"success": True, "result": page2})
    orgs = await ckan.list_organizations(limit=100)
    assert len(orgs) == 26
    assert orgs[-1]["name"] == "org-25"


@pytest.mark.asyncio
async def test_list_organizations_stops_at_the_requested_limit(httpx_mock):
    """A caller asking for 10 must not pay for a second page."""
    page1 = [{"id": str(i), "name": f"org-{i}", "title": f"Org {i}"} for i in range(25)]
    httpx_mock.add_response(json={"success": True, "result": page1})
    orgs = await ckan.list_organizations(limit=10)
    assert len(orgs) == 10


@pytest.mark.asyncio
async def test_list_organizations_returns_error_list_on_network_failure(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("portal down"))
    result = await ckan.list_organizations()
    assert isinstance(result, list)
    assert len(result) == 1
    assert "error" in result[0]


@pytest.mark.asyncio
async def test_get_resource_returns_error_dict_on_network_failure(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("portal down"))
    result = await ckan.get_resource("nonexistent-uuid")
    assert "error" in result
    assert "hint" in result


# ─── Shared sample payloads ────────────────────────────────────────────────────

SAMPLE_DATASET = {
    "id": "abc-123",
    "name": "censo-2022",
    "title": "Censo Nacional 2022",
    "organization": {"name": "one", "title": "Oficina Nacional de Estadística"},
    "notes": "Resultados del censo nacional.",
    "tags": [{"name": "censo"}, {"name": "poblacion"}],
    "groups": [{"title": "Demografía"}],
    "resources": [
        {"id": "r1", "name": "censo.csv", "format": "CSV", "url": "https://x/censo.csv"},
        {"id": "r2", "name": "censo.xlsx", "format": "XLSX", "url": "https://x/censo.xlsx"},
    ],
    "metadata_modified": "2026-01-01T00:00:00",
    "license_title": "CC-BY",
    "author": "ONE",
    "maintainer": "ONE",
    "extras": [{"key": "frecuencia", "value": "decenal"}],
}

SAMPLE_RESOURCE = {
    "id": "r1",
    "name": "censo.csv",
    "description": "Archivo CSV del censo",
    "format": "CSV",
    "url": "https://x/censo.csv",
    "size": 1024,
    "mimetype": "text/csv",
    "created": "2026-01-01T00:00:00",
    "last_modified": "2026-02-01T00:00:00",
}


def _ok(result):
    return {"success": True, "result": result}


# ─── Client lifecycle ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_client_is_reused_across_calls():
    c1 = await ckan._get_client()
    c2 = await ckan._get_client()
    assert c1 is c2
    await ckan.close_client()
    assert ckan._client is None
    assert c1.is_closed


@pytest.mark.asyncio
async def test_close_client_noop_when_no_client():
    await ckan.close_client()  # _client is None — must not raise
    assert ckan._client is None


@pytest.mark.asyncio
async def test_get_client_recreates_after_close():
    c1 = await ckan._get_client()
    await ckan.close_client()
    c2 = await ckan._get_client()
    assert c2 is not c1
    assert not c2.is_closed
    await ckan.close_client()


# ─── ckan_request error branches ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ckan_request_timeout_becomes_runtime_error(httpx_mock):
    httpx_mock.add_exception(httpx.ReadTimeout("too slow"))
    result = await ckan.search_datasets(query="x")
    assert "Timeout" in result["error"]


@pytest.mark.asyncio
async def test_ckan_request_http_error_status(httpx_mock):
    httpx_mock.add_response(status_code=503)
    result = await ckan.search_datasets(query="x")
    assert "503" in result["error"]


@pytest.mark.asyncio
async def test_ckan_request_success_false_dict_error(httpx_mock):
    httpx_mock.add_response(
        json={"success": False, "error": {"message": "Not found", "__type": "Not Found Error"}}
    )
    result = await ckan.get_dataset("nope")
    assert "Not found" in result["error"]


@pytest.mark.asyncio
async def test_ckan_request_success_false_non_dict_error(httpx_mock):
    httpx_mock.add_response(json={"success": False, "error": "boom"})
    result = await ckan.get_dataset("nope")
    assert "boom" in result["error"]


# ─── search_datasets ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_datasets_happy_path(httpx_mock):
    httpx_mock.add_response(json=_ok({"count": 42, "results": [SAMPLE_DATASET]}))
    result = await ckan.search_datasets(query="censo", limit=5, offset=10)
    assert result["total"] == 42
    assert result["returned"] == 1
    assert result["offset"] == 10
    ds = result["datasets"][0]
    assert ds["name"] == "censo-2022"
    assert ds["organization"] == "Oficina Nacional de Estadística"
    assert ds["resource_count"] == 2
    assert "resources" not in ds  # list view uses the short formatter
    req = httpx_mock.get_requests()[0]
    assert req.url.params["q"] == "censo"
    assert req.url.params["rows"] == "5"
    assert req.url.params["start"] == "10"
    assert "fq" not in req.url.params


@pytest.mark.asyncio
async def test_search_datasets_builds_escaped_fq_filters(httpx_mock):
    httpx_mock.add_response(json=_ok({"count": 0, "results": []}))
    result = await ckan.search_datasets(
        organization="min-salud", tag="finanzas publicas", group="economia"
    )
    assert result["total"] == 0
    req = httpx_mock.get_requests()[0]
    fq = req.url.params["fq"]
    assert r"organization:min\-salud" in fq  # Solr-escaped hyphen
    assert 'tags:"finanzas publicas"' in fq  # quoted because of space
    assert "groups:economia" in fq
    assert fq.count(" AND ") == 2
    assert req.url.params["q"] == "*:*"  # no query → match-all


@pytest.mark.asyncio
async def test_search_datasets_clamps_limit_and_offset(httpx_mock):
    httpx_mock.add_response(json=_ok({"count": 0, "results": []}))
    await ckan.search_datasets(query="x", limit=9999, offset=-7)
    req = httpx_mock.get_requests()[0]
    assert req.url.params["rows"] == str(ckan.MAX_ROWS)
    assert req.url.params["start"] == "0"


# ─── get_dataset ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_dataset_happy_path_full_format(httpx_mock):
    httpx_mock.add_response(json=_ok(SAMPLE_DATASET))
    result = await ckan.get_dataset("censo-2022")
    assert result["id"] == "abc-123"
    assert result["author"] == "ONE"
    assert result["maintainer"] == "ONE"
    assert len(result["resources"]) == 2
    assert result["resources"][0]["format"] == "CSV"
    assert result["extras"] == [{"key": "frecuencia", "value": "decenal"}]
    req = httpx_mock.get_requests()[0]
    assert req.url.params["id"] == "censo-2022"


@pytest.mark.asyncio
async def test_get_dataset_omits_extras_key_when_absent(httpx_mock):
    bare = {k: v for k, v in SAMPLE_DATASET.items() if k != "extras"}
    httpx_mock.add_response(json=_ok(bare))
    result = await ckan.get_dataset("censo-2022")
    assert "extras" not in result
    assert "error" not in result


# ─── list_recent_datasets ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_recent_datasets_happy_path(httpx_mock):
    httpx_mock.add_response(json=_ok({"count": 1054, "results": [SAMPLE_DATASET]}))
    result = await ckan.list_recent_datasets(limit=5)
    assert result["total"] == 1054
    assert result["returned"] == 1
    assert result["datasets"][0]["name"] == "censo-2022"
    req = httpx_mock.get_requests()[0]
    assert req.url.params["sort"] == "metadata_modified desc"
    assert req.url.params["rows"] == "5"


@pytest.mark.asyncio
async def test_list_recent_datasets_clamps_limit(httpx_mock):
    httpx_mock.add_response(json=_ok({"count": 0, "results": []}))
    await ckan.list_recent_datasets(limit=500)
    req = httpx_mock.get_requests()[0]
    assert req.url.params["rows"] == str(ckan.MAX_RECENT)


@pytest.mark.asyncio
async def test_list_recent_datasets_error_dict(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("portal down"))
    result = await ckan.list_recent_datasets()
    assert "error" in result
    assert "hint" in result


# ─── get_resource ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_resource_happy_path(httpx_mock):
    httpx_mock.add_response(json=_ok(SAMPLE_RESOURCE))
    result = await ckan.get_resource("r1")
    assert result["id"] == "r1"
    assert result["format"] == "CSV"
    assert result["mimetype"] == "text/csv"
    assert result["last_modified"] == "2026-02-01T00:00:00"


# ─── search_resources ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_resources_happy_path(httpx_mock):
    httpx_mock.add_response(json=_ok({"count": 1, "results": [SAMPLE_RESOURCE]}))
    result = await ckan.search_resources("censo", limit=7)
    assert result["total"] == 1
    assert result["resources"][0]["name"] == "censo.csv"
    req = httpx_mock.get_requests()[0]
    assert req.url.params["query"] == "name:censo"
    assert req.url.params["limit"] == "7"


@pytest.mark.asyncio
async def test_search_resources_sanitizes_query_separators(httpx_mock):
    """resource_search splits 'field:term' on the first colon — user ':'/'"'
    must not reach the query or they alter its structure."""
    httpx_mock.add_response(json=_ok({"count": 0, "results": []}))
    await ckan.search_resources('url:"http://evil"')
    req = httpx_mock.get_requests()[0]
    q = req.url.params["query"]
    assert q.startswith("name:")
    assert ":" not in q.removeprefix("name:")
    assert '"' not in q


@pytest.mark.asyncio
async def test_search_resources_handles_null_results(httpx_mock):
    httpx_mock.add_response(json=_ok({"count": 0, "results": None}))
    result = await ckan.search_resources("nada")
    assert result["resources"] == []


@pytest.mark.asyncio
async def test_search_resources_error_dict(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("portal down"))
    result = await ckan.search_resources("censo")
    assert "error" in result
    assert "hint" in result


# ─── list_organizations ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_organizations_happy_path_respects_limit(httpx_mock):
    orgs = [
        {"id": f"o{i}", "name": f"org-{i}", "title": f"Org {i}", "package_count": i}
        for i in range(3)
    ]
    httpx_mock.add_response(json=_ok(orgs))
    result = await ckan.list_organizations(limit=2)
    assert len(result) == 2
    assert result[0]["name"] == "org-0"
    assert result[0]["dataset_count"] == 0
    assert "description" not in result[0]  # short formatter
    assert result[0]["url"] == "https://datos.gob.do/organization/org-0"


@pytest.mark.asyncio
async def test_list_organizations_non_list_result_returns_empty(httpx_mock):
    httpx_mock.add_response(json=_ok({"unexpected": "shape"}))
    result = await ckan.list_organizations()
    assert result == []


# ─── get_organization ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_organization_happy_path_with_extras(httpx_mock):
    httpx_mock.add_response(
        json=_ok(
            {
                "id": "o1",
                "name": "one",
                "title": "Oficina Nacional de Estadística",
                "description": "x" * 5000,
                "package_count": 99,
                "extras": [{"key": "sigla", "value": "ONE"}],
            }
        )
    )
    result = await ckan.get_organization("one")
    assert result["dataset_count"] == 99
    assert len(result["description"]) <= ckan.DESC_TRUNC + 1
    assert result["extras"] == [{"key": "sigla", "value": "ONE"}]


@pytest.mark.asyncio
async def test_get_organization_omits_extras_when_absent(httpx_mock):
    httpx_mock.add_response(json=_ok({"id": "o1", "name": "one", "package_count": 1}))
    result = await ckan.get_organization("one")
    assert "extras" not in result
    assert "error" not in result


# ─── list_groups ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_groups_happy_path(httpx_mock):
    httpx_mock.add_response(
        json=_ok(
            [
                {
                    "id": "g1",
                    "name": "economia",
                    "title": "Economía",
                    "description": "Datos económicos",
                    "package_count": 7,
                }
            ]
        )
    )
    result = await ckan.list_groups()
    assert len(result) == 1
    g = result[0]
    assert g["title"] == "Economía"
    assert g["dataset_count"] == 7
    assert g["url"] == "https://datos.gob.do/group/economia"


@pytest.mark.asyncio
async def test_list_groups_non_list_result_returns_empty(httpx_mock):
    httpx_mock.add_response(json=_ok({"unexpected": "shape"}))
    assert await ckan.list_groups() == []


@pytest.mark.asyncio
async def test_list_groups_error_list(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("portal down"))
    result = await ckan.list_groups()
    assert len(result) == 1
    assert "error" in result[0]


# ─── list_tags ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_tags_happy_path_with_query(httpx_mock):
    httpx_mock.add_response(json=_ok(["salud", "saludable", "salubridad"]))
    result = await ckan.list_tags(query="salu", limit=2)
    assert result == ["salud", "saludable"]  # limit applied
    req = httpx_mock.get_requests()[0]
    assert req.url.params["query"] == "salu"


@pytest.mark.asyncio
async def test_list_tags_handles_dict_entries_and_empties(httpx_mock):
    httpx_mock.add_response(
        json=_ok([{"name": "censo"}, {"display_name": "poblacion"}, {"other": 1}, ""])
    )
    result = await ckan.list_tags()
    assert result == ["censo", "poblacion"]  # falsy entries dropped


@pytest.mark.asyncio
async def test_list_tags_non_list_result_returns_empty(httpx_mock):
    httpx_mock.add_response(json=_ok({"unexpected": "shape"}))
    assert await ckan.list_tags() == []


@pytest.mark.asyncio
async def test_list_tags_error_returns_empty_list(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("portal down"))
    assert await ckan.list_tags() == []


# ─── autocomplete ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_autocomplete_invalid_kind_returns_error_no_http():
    result = await ckan.autocomplete("planet", "earth")
    assert len(result) == 1
    assert "Invalid kind" in result[0]["error"]


@pytest.mark.asyncio
async def test_autocomplete_dataset_happy_path(httpx_mock):
    matches = [{"name": "censo-2022", "title": "Censo 2022", "match_field": "name"}]
    httpx_mock.add_response(json=_ok(matches))
    result = await ckan.autocomplete("dataset", "cen", limit=5)
    assert result == matches
    req = httpx_mock.get_requests()[0]
    assert str(req.url.path).endswith("/package_autocomplete")
    assert req.url.params["q"] == "cen"
    assert req.url.params["limit"] == "5"


@pytest.mark.asyncio
async def test_autocomplete_clamps_limit(httpx_mock):
    httpx_mock.add_response(json=_ok([]))
    await ckan.autocomplete("tag", "x", limit=9999)
    req = httpx_mock.get_requests()[0]
    assert req.url.params["limit"] == str(ckan.MAX_AUTOCOMPLETE)


@pytest.mark.asyncio
async def test_autocomplete_non_list_result_returns_empty(httpx_mock):
    httpx_mock.add_response(json=_ok({"unexpected": "shape"}))
    assert await ckan.autocomplete("organization", "min") == []


@pytest.mark.asyncio
async def test_autocomplete_error_returns_empty_list(httpx_mock):
    httpx_mock.add_exception(httpx.ConnectError("portal down"))
    assert await ckan.autocomplete("group", "eco") == []


# ─── get_site_stats ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_site_stats_happy_path(httpx_mock):
    import re

    httpx_mock.add_response(
        url=re.compile(r".*/package_search.*"), json=_ok({"count": 1054, "results": []})
    )
    httpx_mock.add_response(url=re.compile(r".*/organization_list.*"), json=_ok(["a", "b", "c"]))
    httpx_mock.add_response(url=re.compile(r".*/group_list.*"), json=_ok(["g1", "g2"]))
    httpx_mock.add_response(url=re.compile(r".*/tag_list.*"), json=_ok(["t1", "t2", "t3", "t4"]))
    result = await ckan.get_site_stats()
    assert result["total_datasets"] == 1054
    assert result["total_organizations"] == 3
    assert result["total_groups"] == 2
    assert result["total_tags"] == 4
    assert result["portal"] == "datos.gob.do"
    assert result["pais"] == "República Dominicana"


@pytest.mark.asyncio
async def test_get_site_stats_resilient_to_partial_failures(httpx_mock):
    import re

    httpx_mock.add_exception(httpx.ConnectError("down"), url=re.compile(r".*/package_search.*"))
    httpx_mock.add_response(url=re.compile(r".*/organization_list.*"), json=_ok(["a"]))
    httpx_mock.add_response(url=re.compile(r".*/group_list.*"), json=_ok("weird-shape"))
    httpx_mock.add_response(url=re.compile(r".*/tag_list.*"), json=_ok(["t1"]))
    result = await ckan.get_site_stats()
    assert result["total_datasets"] is None  # request failed → None
    assert result["total_organizations"] == 1
    assert result["total_groups"] is None  # neither dict nor list → None
    assert result["total_tags"] == 1


@pytest.mark.asyncio
async def test_search_resources_names_the_publishing_institution(httpx_mock):
    """resource_search answers with the file and nothing around it — no dataset,
    no institution. For government files that is close to useless: files here
    are named things like `clss.csv`, and "who published this?" is the first
    question anyone asks. Resolved with one extra request, not one per row."""
    httpx_mock.add_response(
        json={
            "success": True,
            "result": {
                "count": 1,
                "results": [{"id": "r1", "name": "clss.csv", "format": "CSV", "package_id": "p1"}],
            },
        }
    )
    httpx_mock.add_response(
        json={
            "success": True,
            "result": {
                "count": 1,
                "results": [
                    {
                        "id": "p1",
                        "title": "Nómina 2026",
                        "name": "nomina-2026",
                        "organization": {"title": "Ministerio de Trabajo", "name": "mt"},
                    }
                ],
            },
        }
    )
    out = await ckan.search_resources(query="nomina", limit=5)
    row = out["resources"][0]
    assert row["organization"] == "Ministerio de Trabajo"
    assert row["organization_slug"] == "mt"
    assert row["dataset_slug"] == "nomina-2026"


@pytest.mark.asyncio
async def test_search_resources_survives_a_failed_parent_lookup(httpx_mock):
    """A resource list without institutions still beats an error."""
    httpx_mock.add_response(
        json={
            "success": True,
            "result": {
                "count": 1,
                "results": [{"id": "r1", "name": "clss.csv", "format": "CSV", "package_id": "p1"}],
            },
        }
    )
    httpx_mock.add_response(status_code=500)
    out = await ckan.search_resources(query="nomina", limit=5)
    assert out["resources"][0]["name"] == "clss.csv"
    assert "organization" not in out["resources"][0]

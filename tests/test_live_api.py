"""Optional live tests against the real datos.gob.do API.

Skipped by default. Enable with: RUN_LIVE_TESTS=1 pytest tests/test_live_api.py
"""

from __future__ import annotations

import pytest

from datosgobdo_mcp import ckan


pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
async def _fresh_ckan_client():
    """httpx clients are bound to the event loop that created them.
    pytest-asyncio creates a fresh loop per test, so we must close the
    singleton client between tests and let it be re-initialised lazily.
    """
    yield
    await ckan.close_client()


async def test_live_get_site_stats():
    s = await ckan.get_site_stats()
    assert s["portal"] == "datos.gob.do"
    assert (s["total_datasets"] or 0) > 100


async def test_live_search_datasets():
    r = await ckan.search_datasets(query="presupuesto", limit=2)
    assert r["total"] > 0
    assert len(r["datasets"]) <= 2
    for d in r["datasets"]:
        assert d["id"]
        assert d["name"]


async def test_live_autocomplete_organization():
    r = await ckan.autocomplete("organization", "salud", limit=3)
    assert isinstance(r, list)
    assert len(r) >= 1


async def test_live_list_groups():
    r = await ckan.list_groups()
    assert isinstance(r, list)
    assert len(r) >= 5

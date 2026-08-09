"""A 403 is not one thing, and the difference decides what the caller should do."""

from __future__ import annotations

import httpx
import pytest

from datosgobdo_mcp import analytics, reachability


def test_the_challenge_header_outranks_the_status():
    kind = reachability.classify(403, {"cf-mitigated": "challenge", "server": "cloudflare"})
    assert kind == reachability.CHALLENGE


def test_header_case_and_padding_do_not_change_the_verdict():
    assert reachability.classify(403, {"CF-Mitigated": " Challenge "}) == reachability.CHALLENGE


def test_a_bare_403_is_a_site_rule():
    assert reachability.classify(403, {"server": "cloudflare"}) == reachability.WAF_RULE


def test_the_widget_in_the_body_is_enough_when_the_header_is_missing():
    body = '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>'
    assert reachability.classify(403, {}, body) == reachability.CHALLENGE


def test_nothing_coming_back_is_a_statement_about_the_network():
    assert reachability.classify(None) == reachability.NETWORK


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (200, reachability.OK),
        (404, reachability.NOT_FOUND),
        (410, reachability.NOT_FOUND),
        (503, reachability.SERVER_ERROR),
        (429, reachability.WAF_RULE),
    ],
)
def test_the_rest_of_the_vocabulary(status, expected):
    assert reachability.classify(status, {}) == expected


def test_html_two_hundred_is_a_page_not_a_file():
    assert reachability.classify(200, {"content-type": "text/html; charset=UTF-8"}) == (
        reachability.HTML_PAGE
    )


def test_a_challenge_says_a_browser_would_succeed():
    """The distinction the caller needs: gone, or just not reachable from here."""
    out = reachability.explain(reachability.CHALLENGE)
    assert "browser" in out["hint"]
    assert "still published" in out["hint"]


def test_every_explanation_forbids_swapping_the_source():
    """The failure this exists to prevent.

    Asked for one file and unable to fetch it, an assistant answered from a
    different institution's file — figures a million apart — because the error
    it got offered no path at all.
    """
    for kind in (
        reachability.CHALLENGE,
        reachability.WAF_RULE,
        reachability.NOT_FOUND,
        reachability.SERVER_ERROR,
        reachability.NETWORK,
    ):
        assert "without" in reachability.explain(kind)["next_step"]


def test_an_archived_copy_is_offered_with_its_date():
    out = reachability.explain(
        reachability.CHALLENGE,
        archived={"captured_at": "2026-08-08", "sha256": "abc123def456789012345"},
    )
    assert "2026-08-08" in out["next_step"]
    assert "abc123def4567890" in out["next_step"]
    assert "capture date" in out["next_step"]


def test_no_archive_no_promise():
    """A copy that is not on disk must never be suggested."""
    out = reachability.explain(reachability.CHALLENGE, archived=None)
    assert "archived copy of this exact URL" not in out["next_step"]


async def test_a_blocked_download_reaches_the_caller_as_a_challenge(httpx_mock, tmp_cache_dir):
    """End to end: what the assistant actually sees.

    Before this, the reply was the raw text of an httpx error plus a link to
    MDN, with hint and next_step both null.
    """
    url = "https://example.test/nomina.xlsx"
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "x"})
    httpx_mock.add_response(
        url=url,
        method="GET",
        status_code=403,
        headers={"cf-mitigated": "challenge"},
        content=b"<html>reto</html>",
    )
    out = await analytics.get_resource_schema(url, "xlsx")
    assert out["reachability"] == reachability.CHALLENGE
    assert "browser" in out["hint"]
    assert "without saying so" in out["next_step"]


async def test_a_plain_refusal_is_not_dressed_up_as_a_challenge(httpx_mock, tmp_cache_dir):
    url = "https://example.test/otra.csv"
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "y"})
    httpx_mock.add_response(url=url, method="GET", status_code=403, content=b"denied")
    out = await analytics.get_resource_schema(url, "csv")
    assert out["reachability"] == reachability.WAF_RULE
    assert "nothing to solve" in out["hint"]


async def test_a_page_with_links_still_hands_over_the_links(httpx_mock, tmp_cache_dir):
    """The older path must not be swallowed by the new one."""
    page_url = "https://example.test/descargas/serie"
    page = (
        '<html><a href="/a/uno.csv">uno</a><a href="/b/dos.csv">dos</a>'
        '<a href="/c/tres.csv">tres</a></html>'
    )
    httpx_mock.add_response(url=page_url, method="HEAD", headers={"etag": "p"})
    httpx_mock.add_response(url=page_url, method="GET", content=page.encode())
    out = await analytics.get_resource_schema(page_url, "csv")
    assert out.get("linked_files")
    assert "reachability" not in out


async def test_a_dead_link_is_named_as_the_publishers_problem(httpx_mock, tmp_cache_dir):
    url = "https://example.test/se-fue.csv"
    httpx_mock.add_response(url=url, method="HEAD", headers={"etag": "z"})
    httpx_mock.add_response(url=url, method="GET", status_code=404, content=b"nope")
    out = await analytics.get_resource_schema(url, "csv")
    assert out["reachability"] == reachability.NOT_FOUND
    assert "publisher" in out["hint"]


async def test_no_answer_at_all_is_reported_as_inconclusive(httpx_mock, tmp_cache_dir):
    url = "https://example.test/silencio.csv"
    httpx_mock.add_exception(httpx.ConnectError("no route"), url=url, method="HEAD")
    httpx_mock.add_exception(httpx.ConnectError("no route"), url=url, method="GET")
    out = await analytics.get_resource_schema(url, "csv")
    assert out["reachability"] == reachability.NETWORK
    assert "inconclusive" in out["next_step"]

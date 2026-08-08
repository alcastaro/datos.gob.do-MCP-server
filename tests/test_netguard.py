"""SSRF guard tests — hermetic (DNS monkeypatched, no network)."""

from __future__ import annotations

import socket

import pytest

from datosgobdo_mcp import netguard


@pytest.fixture
def public_dns(monkeypatch):
    async def fake_resolve(host):
        return ["93.184.216.34"]  # public

    monkeypatch.setattr(netguard, "_resolve_host", fake_resolve)


@pytest.fixture
def private_dns(monkeypatch):
    async def fake_resolve(host):
        return ["93.184.216.34", "10.0.0.5"]  # one public, one private

    monkeypatch.setattr(netguard, "_resolve_host", fake_resolve)


# ─── mode: off ────────────────────────────────────────────────────────────────


async def test_off_mode_allows_anything(monkeypatch):
    monkeypatch.setenv("DATOSGOBDO_NETGUARD", "off")
    await netguard.validate_outbound_url("file:///etc/passwd")
    await netguard.validate_outbound_url("http://169.254.169.254/latest/meta-data/")


async def test_invalid_mode_rejected(monkeypatch):
    monkeypatch.setenv("DATOSGOBDO_NETGUARD", "yolo")
    with pytest.raises(netguard.NetGuardError, match="Invalid"):
        await netguard.validate_outbound_url("https://datos.gob.do/x.csv")


# ─── scheme / shape ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://datos.gob.do/x.csv",
        "gopher://datos.gob.do/",
        "https://",
    ],
)
async def test_bad_scheme_or_no_host_rejected(monkeypatch, url):
    monkeypatch.setenv("DATOSGOBDO_NETGUARD", "public-only")
    with pytest.raises(netguard.NetGuardError):
        await netguard.validate_outbound_url(url)


# ─── public-only: IP range checks ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://127.0.0.1:8080/internal",
        "https://10.0.0.5/db",
        "https://192.168.1.1/router",
        "http://[::1]/",
        "http://[fd00::1]/",  # IPv6 ULA
    ],
)
async def test_non_public_ip_literals_rejected(monkeypatch, url):
    monkeypatch.setenv("DATOSGOBDO_NETGUARD", "public-only")
    with pytest.raises(netguard.NetGuardError, match="non-public"):
        await netguard.validate_outbound_url(url)


async def test_public_host_allowed(monkeypatch, public_dns):
    monkeypatch.setenv("DATOSGOBDO_NETGUARD", "public-only")
    await netguard.validate_outbound_url("https://ministerio.example.do/nomina.csv")


async def test_rebind_style_mixed_resolution_rejected(monkeypatch, private_dns):
    """If ANY resolved address is non-public, the whole host is rejected."""
    monkeypatch.setenv("DATOSGOBDO_NETGUARD", "public-only")
    with pytest.raises(netguard.NetGuardError, match="non-public"):
        await netguard.validate_outbound_url("https://evil.example.com/x.csv")


async def test_dns_failure_rejected(monkeypatch):
    async def boom(host):
        raise socket.gaierror("NXDOMAIN")

    monkeypatch.setattr(netguard, "_resolve_host", boom)
    monkeypatch.setenv("DATOSGOBDO_NETGUARD", "public-only")
    with pytest.raises(netguard.NetGuardError, match="DNS resolution failed"):
        await netguard.validate_outbound_url("https://nope.invalid/x.csv")


# ─── strict mode ──────────────────────────────────────────────────────────────


async def test_strict_allows_portal_host(monkeypatch, public_dns):
    monkeypatch.setenv("DATOSGOBDO_NETGUARD", "strict")
    await netguard.validate_outbound_url("https://datos.gob.do/dataset/x.csv")
    await netguard.validate_outbound_url("https://files.datos.gob.do/x.csv")


async def test_strict_rejects_foreign_host(monkeypatch, public_dns):
    monkeypatch.setenv("DATOSGOBDO_NETGUARD", "strict")
    with pytest.raises(netguard.NetGuardError, match="strict allowlist"):
        await netguard.validate_outbound_url("https://example.com/x.csv")


async def test_strict_wildcard_does_not_match_bare_suffix_trick(monkeypatch, public_dns):
    monkeypatch.setenv("DATOSGOBDO_NETGUARD", "strict")
    with pytest.raises(netguard.NetGuardError):
        await netguard.validate_outbound_url("https://evildatos.gob.do.attacker.com/x.csv")


# ─── operator allowlist ───────────────────────────────────────────────────────


async def test_allow_hosts_bypasses_resolution(monkeypatch):
    """Operator-trusted hosts skip DNS entirely (fork/test escape hatch)."""
    monkeypatch.setenv("DATOSGOBDO_NETGUARD", "public-only")
    monkeypatch.setenv("DATOSGOBDO_ALLOW_HOSTS", "example.test, *.portal.gov.xx")

    async def boom(host):  # resolution must never be reached
        raise AssertionError("resolved a trusted host")

    monkeypatch.setattr(netguard, "_resolve_host", boom)
    await netguard.validate_outbound_url("https://example.test/nomina.csv")
    await netguard.validate_outbound_url("https://files.portal.gov.xx/d.csv")


async def test_allow_hosts_wildcard_not_fooled_by_lookalike(monkeypatch, public_dns):
    monkeypatch.setenv("DATOSGOBDO_NETGUARD", "strict")
    monkeypatch.setenv("DATOSGOBDO_ALLOW_HOSTS", "*.portal.gov.xx")
    with pytest.raises(netguard.NetGuardError):
        await netguard.validate_outbound_url("https://notportal.gov.xx.evil.com/x.csv")


# ─── wired into download.py ───────────────────────────────────────────────────


async def test_download_capped_blocks_metadata_ip(monkeypatch):
    """End-to-end: the guard actually fires inside download_capped."""
    from datosgobdo_mcp import download

    monkeypatch.setenv("DATOSGOBDO_NETGUARD", "public-only")
    monkeypatch.delenv("DATOSGOBDO_ALLOW_HOSTS", raising=False)
    with pytest.raises(netguard.NetGuardError, match="non-public"):
        await download.download_capped("http://169.254.169.254/latest/meta-data/")


async def test_download_capped_blocks_redirect_to_private(httpx_mock, monkeypatch):
    """A trusted host redirecting to a private address must be stopped at the hop."""
    monkeypatch.setenv("DATOSGOBDO_NETGUARD", "public-only")
    monkeypatch.setenv("DATOSGOBDO_ALLOW_HOSTS", "example.test")
    from datosgobdo_mcp import download

    httpx_mock.add_response(
        url="https://example.test/redir.csv",
        status_code=302,
        headers={"location": "http://127.0.0.1:9000/internal.csv"},
    )
    with pytest.raises(netguard.NetGuardError, match="non-public"):
        await download.download_capped("https://example.test/redir.csv")


async def test_head_metadata_is_guarded_like_the_download(monkeypatch):
    """The version probe must not be a hole the download closes.

    `ensure_cached` HEADs the URL before downloading it, to build the cache
    key. That request went out without the guard, so a caller naming an
    internal address got a real HEAD delivered to it — and its ETag back in the
    key — while the download that followed was correctly refused. Blind, but a
    working network probe from inside the perimeter.
    """
    monkeypatch.setenv("DATOSGOBDO_NETGUARD", "public-only")
    monkeypatch.delenv("DATOSGOBDO_ALLOW_HOSTS", raising=False)
    from datosgobdo_mcp import analytics

    # It raises rather than returning empty: the guard error is not an
    # httpx.HTTPError, so it travels past the transport-error handling this
    # function does for unreachable hosts. That is the behaviour we want —
    # "this URL is not allowed" is a different fact from "this host did not
    # answer", and the caller is told which one it got.
    with pytest.raises(netguard.NetGuardError, match="non-public"):
        await analytics._head_metadata("http://169.254.169.254/latest/meta-data/")


async def test_head_metadata_respects_strict_mode(monkeypatch, public_dns):
    """A host outside the allowlist is refused on this path too.

    Publicly routable, so the public-only check would wave it through; only the
    strict allowlist stops it, and that check was never reached from here.
    """
    monkeypatch.setenv("DATOSGOBDO_NETGUARD", "strict")
    monkeypatch.delenv("DATOSGOBDO_ALLOW_HOSTS", raising=False)
    from datosgobdo_mcp import analytics

    with pytest.raises(netguard.NetGuardError, match="strict allowlist"):
        await analytics._head_metadata("https://ajeno.example/x.csv")


def test_resource_requests_declare_their_fetch_context():
    """Three hosts' worth of the catalog is gated on these headers.

    Not a disguise: the User-Agent stays ours, and the values describe what this
    client actually does. Asserted so a future edit cannot quietly drop them and
    turn 16 datasets back into 403s that look like the publisher's fault.
    """
    from datosgobdo_mcp import download

    assert download.RESOURCE_HEADERS["Sec-Fetch-Mode"] == "cors"
    assert download.RESOURCE_HEADERS["Sec-Fetch-Dest"] == "empty"
    assert download.RESOURCE_HEADERS["Sec-Fetch-Site"] == "cross-site"
    assert download.RESOURCE_HEADERS["User-Agent"].startswith("datosgobdo-mcp/")

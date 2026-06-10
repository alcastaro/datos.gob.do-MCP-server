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

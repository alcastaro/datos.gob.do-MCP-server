"""SSRF guard for outbound resource downloads.

CKAN resources legitimately live on hosts other than datos.gob.do (ministry
sites, S3 buckets, CDNs), so the default policy is NOT a host allowlist — it is
"public internet only": http/https schemes, and every IP the hostname resolves
to must be globally routable. That blocks the actual SSRF targets — cloud
metadata (169.254.169.254), loopback, RFC-1918 ranges, IPv6 ULA — without
breaking legitimately external datasets.

Modes (env ``DATOSGOBDO_NETGUARD``):

- ``public-only`` (default): scheme + resolve-and-check-all-IPs as above.
- ``strict``: additionally the hostname must match ``datos.gob.do`` /
  ``*.datos.gob.do`` or an entry in ``DATOSGOBDO_ALLOW_HOSTS``. For hosted
  deployments that only ever serve the DR portal.
- ``off``: no checks (local trusted use, test suites).

``DATOSGOBDO_ALLOW_HOSTS`` (comma-separated, ``*.`` wildcard prefix allowed)
names operator-trusted hosts: they bypass resolution checks entirely, which is
also the hook for forks pointing at other portals and for hermetic tests.

Residual risk (documented, deferred): validation resolves DNS, then httpx
resolves again to connect — a fast-flux DNS rebind between the two lookups is
not blocked. Full mitigation (pin resolved IP at the transport) is a hosted
(P3) item. Redirect hops ARE each validated when the guard is installed as an
httpx request event hook.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from urllib.parse import urlsplit

import httpx

DEFAULT_STRICT_HOSTS = ("datos.gob.do", "*.datos.gob.do")

_MODE_ENV = "DATOSGOBDO_NETGUARD"
_HOSTS_ENV = "DATOSGOBDO_ALLOW_HOSTS"
_VALID_MODES = ("public-only", "strict", "off")


class NetGuardError(RuntimeError):
    """Raised when an outbound URL fails SSRF validation."""


def _mode() -> str:
    mode = os.environ.get(_MODE_ENV, "public-only").strip().lower()
    if mode not in _VALID_MODES:
        raise NetGuardError(
            f"Invalid {_MODE_ENV}={mode!r}; expected one of {', '.join(_VALID_MODES)}"
        )
    return mode


def _operator_hosts() -> tuple[str, ...]:
    raw = os.environ.get(_HOSTS_ENV, "")
    return tuple(h.strip().lower() for h in raw.split(",") if h.strip())


def _host_matches(host: str, pattern: str) -> bool:
    host = host.lower().rstrip(".")
    pattern = pattern.lower().rstrip(".")
    if pattern.startswith("*."):
        suffix = pattern[1:]  # ".example.do"
        return host.endswith(suffix) and host != suffix.lstrip(".")
    return host == pattern


async def _resolve_host(host: str) -> list[str]:
    """All A/AAAA addresses for host. Separate fn so tests can monkeypatch."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _check_ip(ip_str: str, host: str) -> None:
    ip = ipaddress.ip_address(ip_str.split("%")[0])  # strip IPv6 zone id
    # is_global is False for private, loopback, link-local (incl. 169.254.169.254
    # cloud metadata), CGNAT, ULA, and reserved ranges.
    if not ip.is_global:
        raise NetGuardError(
            f"Blocked URL: host '{host}' resolves to non-public address {ip} "
            "(private/loopback/link-local ranges are not allowed)"
        )


async def validate_outbound_url(url: str) -> None:
    """Raise NetGuardError unless `url` is safe to fetch under the current mode."""
    mode = _mode()
    if mode == "off":
        return

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise NetGuardError(f"Blocked URL: scheme '{parts.scheme}' not allowed (http/https only)")
    host = parts.hostname
    if not host:
        raise NetGuardError(f"Blocked URL: no hostname in {url!r}")

    # Operator-trusted hosts bypass everything (fork/test escape hatch).
    if any(_host_matches(host, pat) for pat in _operator_hosts()):
        return

    if mode == "strict" and not any(_host_matches(host, pat) for pat in DEFAULT_STRICT_HOSTS):
        raise NetGuardError(
            f"Blocked URL: host '{host}' not in the strict allowlist "
            f"({', '.join(DEFAULT_STRICT_HOSTS)}; extend via {_HOSTS_ENV})"
        )

    # Resolve and require EVERY address to be globally routable. Resolving even
    # IP literals through getaddrinfo also normalizes tricks like decimal IPs
    # ("2852039166" → 169.254.169.254).
    try:
        addresses = await _resolve_host(host)
    except socket.gaierror as e:
        raise NetGuardError(f"Blocked URL: DNS resolution failed for '{host}': {e}") from e
    if not addresses:
        raise NetGuardError(f"Blocked URL: '{host}' resolved to no addresses")
    for addr in addresses:
        _check_ip(addr, host)


async def guard_request_hook(request: httpx.Request) -> None:
    """httpx event hook: validates the initial request AND every redirect hop."""
    await validate_outbound_url(str(request.url))

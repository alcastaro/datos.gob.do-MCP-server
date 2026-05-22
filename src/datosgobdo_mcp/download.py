"""Capped streaming download for remote resources.

Extracted from preview.py so multiple tools (preview, schema, analytics) can
reuse it with different caps.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import httpx

USER_AGENT = "datosgobdo-mcp/0.2 (MCP Server)"
DEFAULT_TIMEOUT = 60.0  # bigger files = longer timeout vs preview

# Caps per call-site. Preview keeps the conservative 5 MB. Analytics tools
# (get_resource_schema, summarize_resource, aggregate_resource, etc.) opt into
# the bigger cap explicitly.
PREVIEW_MAX_BYTES = 5 * 1024 * 1024
ANALYTICS_MAX_BYTES = 100 * 1024 * 1024


def _detect_encoding(data: bytes) -> str:
    """Detect text encoding with chardet fallback."""
    if not data:
        return "utf-8"
    # Fast path: try UTF-8 first (most common).
    try:
        data.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass
    # Chardet for ambiguous cases.
    try:
        import chardet

        guess = chardet.detect(data[: min(len(data), 100_000)])
        enc = guess.get("encoding")
        if enc and guess.get("confidence", 0) > 0.7:
            # Normalize common Latin-1 family detections.
            if enc.lower() in ("iso-8859-1", "windows-1252"):
                return "cp1252"
            return enc.lower()
    except ImportError:
        pass
    # Hard fallback.
    return "cp1252"


async def download_capped(
    url: str,
    max_bytes: int = PREVIEW_MAX_BYTES,
) -> tuple[bytes, bool]:
    """Download URL with a hard byte cap.

    Returns:
        (data, truncated) — data is at most max_bytes long; truncated is True
        if the remote resource exceeded the cap.
    """
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=DEFAULT_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        async with client.stream("GET", url) as r:
            r.raise_for_status()
            buf = bytearray()
            truncated = False
            async for chunk in r.aiter_bytes():
                buf.extend(chunk)
                if len(buf) >= max_bytes:
                    truncated = True
                    break
            return bytes(buf[:max_bytes]), truncated


async def download_to_file(
    url: str,
    dest: Path,
    max_bytes: int = ANALYTICS_MAX_BYTES,
) -> tuple[int, bool]:
    """Stream URL to disk with byte cap.

    Returns:
        (bytes_written, truncated)
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    bytes_written = 0
    truncated = False
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=DEFAULT_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        async with client.stream("GET", url) as r:
            r.raise_for_status()
            with dest.open("wb") as f:
                async for chunk in r.aiter_bytes():
                    remaining = max_bytes - bytes_written
                    if remaining <= 0:
                        truncated = True
                        break
                    if len(chunk) > remaining:
                        f.write(chunk[:remaining])
                        bytes_written += remaining
                        truncated = True
                        break
                    f.write(chunk)
                    bytes_written += len(chunk)
    return bytes_written, truncated


def normalize_format(fmt: str | None) -> str:
    """Normalize CKAN format string to lowercase, no leading dot."""
    return (fmt or "").lower().strip().lstrip(".")


FormatKind = Literal["csv", "tsv", "xlsx", "xls", "xlsm", "json", "ods"]


def classify_format(fmt: str | None) -> FormatKind | None:
    """Map portal format string to a supported kind, or None if unsupported."""
    f = normalize_format(fmt)
    if f in ("csv", "tsv", "xlsx", "xls", "xlsm", "json", "ods"):
        return f  # type: ignore[return-value]
    return None

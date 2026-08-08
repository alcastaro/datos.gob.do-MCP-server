"""Capped streaming download for remote resources.

Extracted from preview.py so multiple tools (preview, schema, analytics) can
reuse it with different caps.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import httpx

from . import USER_AGENT
from .netguard import guard_request_hook

DEFAULT_TIMEOUT = 60.0  # bigger files = longer timeout vs preview

# Caps per call-site. Preview keeps the conservative 5 MB. Analytics tools
# (get_resource_schema, summarize_resource, aggregate_resource, etc.) opt into
# the bigger cap explicitly.
PREVIEW_MAX_BYTES = 5 * 1024 * 1024
ANALYTICS_MAX_BYTES = 100 * 1024 * 1024


# Characters that are almost never intended in Spanish-language government
# data, but appear the moment a single-byte codepage is decoded as the wrong
# one. 0xA4/0xA5 are ñ/Ñ in CP850 and CP437 (the DOS codepages Excel still
# emits on Windows in Latin America) and ¤/¥ in CP1252.
_MOJIBAKE_CHARS = "¤¥£¦§¨©ª«¬®¯°±²³´µ¶·¸¹º»¼½¾¿×÷ÿþýüûúùø÷"
_MOJIBAKE_PAIRS = ("Ã", "Â", "â€", "Ã‚", "ï¿½", "�")

# Ordered by how often each turns up in this catalog; ties broken by the first.
_CODEPAGE_LADDER = ("cp1252", "cp850", "cp437", "iso-8859-1")


def _mojibake_score(text: str) -> int:
    """How wrong a decoding looks. Lower is better; 0 means nothing suspicious.

    Counts only characters that would be extraordinary in a Spanish column
    header or value. Accented letters, ñ and the currency symbols a real file
    does use are deliberately not penalised.
    """
    score = sum(text.count(c) for c in _MOJIBAKE_CHARS)
    score += sum(text.count(p) * 2 for p in _MOJIBAKE_PAIRS)
    return score


def _best_codepage(data: bytes) -> str:
    """Pick the single-byte codepage that yields the least garbled text.

    chardet gets these files right but reports ~5% confidence on them, so the
    old threshold discarded a correct answer and fell back to CP1252 — which is
    how `Año` reached users as `A¤o`. Scoring the candidate decodings settles it
    from the bytes themselves rather than from a confidence number.
    """
    sample = data[: min(len(data), 100_000)]
    best, best_score = _CODEPAGE_LADDER[0], None
    for enc in _CODEPAGE_LADDER:
        try:
            score = _mojibake_score(sample.decode(enc, errors="replace"))
        except LookupError:  # pragma: no cover - stdlib always has these
            continue
        if best_score is None or score < best_score:
            best, best_score = enc, score
    return best


def _detect_encoding(data: bytes) -> str:
    """Detect text encoding, preferring the decoding that looks least garbled."""
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
        enc = (guess.get("encoding") or "").lower()
        # Normalize common Latin-1 family aliases to one spelling so the
        # ladder membership test below means what it says.
        if enc in ("iso-8859-1", "windows-1252", "latin-1", "latin_1"):
            enc = "cp1252"
        if enc and guess.get("confidence", 0) > 0.7:
            return enc
        # A low-confidence *multi-byte* guess is still worth trying: the ladder
        # below only knows single-byte codepages and would mangle UTF-16 or a
        # CJK encoding outright. A low-confidence single-byte guess is exactly
        # the case the ladder exists to settle, so it is ignored here.
        if enc.startswith(("utf-16", "utf-32", "gb", "big5", "shift", "euc", "iso-2022")):
            try:
                data[:1000].decode(enc)
                return enc
            except (UnicodeDecodeError, LookupError):
                pass
    except ImportError:
        pass
    return _best_codepage(data)


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
        # SSRF guard validates the initial URL and every redirect hop.
        event_hooks={"request": [guard_request_hook]},
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
        event_hooks={"request": [guard_request_hook]},
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


def err_text(e: BaseException) -> str:
    """Describe an exception, never returning an empty string.

    Several httpx timeout classes carry no message at all — a real
    `ConnectTimeout('')` from the catalog turned into the error message
    "Could not load resource:" with nothing after the colon, which tells the
    user and the assistant precisely nothing. Falling back to the class name at
    least names the failure.
    """
    text = str(e).strip()
    return text or type(e).__name__


_HTML_MARKERS = (b"<!doctype html", b"<html", b"<head", b"<?xml-stylesheet", b"<body")


def looks_like_html(head: bytes) -> bool:
    """True if these opening bytes are an HTML page rather than tabular data.

    Government portals routinely answer a dead or gated download link with a
    styled "not found" / "session expired" page and **HTTP 200**. Parsed as
    CSV that page becomes a one-column table named `<!DOCTYPE html>`, which
    the assistant would then report to the user as if it were real data. A
    wrong answer is worse than a failed one, so this is checked before parsing.
    """
    probe = head[:2048].lstrip().lower()
    if probe.startswith(b"\xef\xbb\xbf"):  # UTF-8 BOM ahead of the markup
        probe = probe[3:].lstrip()
    return any(probe.startswith(m) for m in _HTML_MARKERS)


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

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


# What a correct decoding of this catalog recovers. 0xA4/0xA5 are ñ/Ñ in CP850
# and CP437 — the DOS codepages Excel still emits on Windows in Latin America —
# and ¤/¥ in CP1252, which is how `AÑO` reached users as `A¥O`.
_SPANISH_LETTERS = "áéíóúüñÁÉÍÓÚÜÑ¿¡"

# Sequences that only appear when UTF-8 was decoded as a single-byte codepage,
# or when a decoder gave up. Unlike the ambiguous symbols, these are never
# produced by a correct reading.
_MOJIBAKE_PAIRS = ("Ã¡", "Ã©", "Ã­", "Ã³", "Ãº", "Ã±", "Â¿", "â€", "ï¿½", "�")

# Ordered by how often each turns up in this catalog; ties broken by the first.
_CODEPAGE_LADDER: tuple[str, ...] = ("cp1252", "cp850", "cp437", "iso-8859-1")


def _mojibake_score(text: str) -> int:
    """How wrong a decoding looks. Lower is better.

    Scoring the *suspicious* characters turned out to be the wrong question.
    Every candidate decoding of the same bytes renders the same positions as
    some odd symbol, so a byte that is odd under all of them is noise — and
    counting it let one character repeated 132 times in a long body outvote the
    header, choosing the reading that turned `AÑO` into `A¥O`.

    What actually separates a right decoding from a wrong one in this catalog is
    how much Spanish it recovers: the correct codepage yields `Año`, `Región`,
    `ÁREA`; the wrong one yields `A¥o`, `A±o`, `┴REA`. So the score counts
    accented Spanish letters and rewards them.

    The one absolute penalty left is for scripts that cannot occur here at all —
    Latin-1 bytes decode "successfully" as GB18030 or Big5, and chardet will
    volunteer one of those at single-digit confidence, so "it decoded" is no
    evidence. A Dominican payroll with Han characters in its headers is not a
    payroll we have decoded.
    """
    spanish = sum(text.count(c) for c in _SPANISH_LETTERS)
    alien = sum(1 for c in text if _is_alien(ord(c)))
    mangled = sum(text.count(p) for p in _MOJIBAKE_PAIRS)
    return 5 * alien + 2 * mangled - spanish


# Scripts and symbol blocks that cannot occur in this catalog but appear the
# moment a decoding goes wrong in a specific direction. The DOS codepages map
# their high bytes to Greek letters, box-drawing pieces and maths operators, so
# `Año` misread as CP437 becomes `A±o` and `investigación` becomes
# `investigaci≤n` — those are the tell.
_ALIEN_RANGES = (
    (0x0370, 0x03FF),  # Greek
    (0x0400, 0x052F),  # Cyrillic
    (0x0590, 0x08FF),  # Hebrew, Arabic, Syriac
    (0x2200, 0x22FF),  # mathematical operators
    (0x2500, 0x259F),  # box drawing and block elements
    (0x2E00, 0x9FFF),  # CJK and friends
    (0xAC00, 0xD7AF),  # Hangul
)


def _is_alien(cp: int) -> bool:
    return any(lo <= cp <= hi for lo, hi in _ALIEN_RANGES)


def _best_codepage(data: bytes, extra: str | None = None) -> str:
    """Pick the decoding that yields the least garbled text.

    chardet gets the DOS-codepage files right but reports ~5% confidence on
    them, so a plain threshold discarded a correct answer and fell back to
    CP1252 — which is how `Año` reached users as `A¤o`. Rather than trust or
    distrust the guess wholesale, it competes as one more candidate and the
    bytes decide.
    """
    sample = data[: min(len(data), 100_000)]
    candidates = list(_CODEPAGE_LADDER)
    if extra and extra not in candidates:
        candidates.insert(0, extra)
    best, best_score = _CODEPAGE_LADDER[0], None
    for enc in candidates:
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
        # Below the threshold the guess only competes if it is an encoding this
        # catalog could plausibly contain. Left unrestricted, chardet answers
        # Latin-1 bytes with macroman, cp874, cp1250 or even cp424 at ~5%
        # confidence, and each of those decodes without error — so "it decoded"
        # is no evidence at all. Every file measured here is CP1252 or a DOS
        # codepage; the ladder covers both.
        if enc in _CODEPAGE_LADDER:
            return _best_codepage(data, enc)
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

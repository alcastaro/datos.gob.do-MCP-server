"""Capped streaming download for remote resources.

Extracted from preview.py so multiple tools (preview, schema, analytics) can
reuse it with different caps.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx

from . import USER_AGENT
from .netguard import guard_request_hook

DEFAULT_TIMEOUT = 60.0  # bigger files = longer timeout vs preview

# Fetch-metadata headers, sent on every resource request.
#
# A sweep of the whole catalog found 67 hosts answering HTTP 403 to this
# server. Two of them — deepblue.simv.gob.do and migracion.gob.do, 16 datasets
# between them — answer 200 the moment these three headers are present, and
# 403 without them, reproducibly. The User-Agent turned out to be irrelevant:
# an honest `datosgobdo-mcp/…` and a Chrome string get the same answer either
# way, so nothing here is a disguise. The WAF is checking that the client
# states its request context at all, which browsers have done since 2020 and
# most HTTP libraries still do not.
#
# The values are the true ones for what this client does: a cross-site
# programmatic fetch whose destination is not a document. Claiming
# `navigate`/`document` also passes, and would be a lie about the request.
#
# The other 65 hosts refuse every header combination tried, which is consistent
# with a block on the network path rather than on the request.
_FETCH_METADATA = {
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-Dest": "empty",
}

RESOURCE_HEADERS = {"User-Agent": USER_AGENT, **_FETCH_METADATA}

# Google Drive share links, rewritten to the address that serves the bytes.
#
# Five resources in this catalog are registered as `drive.google.com/file/d/<id>/view`,
# which is the viewer page — HTML, no data, and the reason they were counted as
# unreadable files. The catalog also holds four registered as
# `drive.google.com/uc?export=download&id=<id>`, and those read fine.
#
# That is what makes this a normalisation rather than a workaround: the target
# form is the one the publisher already uses when they get it right, and both
# addresses are the same document with the same permissions. Nothing is
# bypassed — a private file stays private, and the request still goes through
# the SSRF guard like any other.
_DRIVE_FILE_ID = re.compile(
    r"^https?://(?:drive|docs)\.google\.com/(?:file/d/|open\?id=|uc\?[^#]*\bid=)([A-Za-z0-9_-]{10,})"
)


# Google's download endpoint refuses `Sec-Fetch-Site: cross-site` outright: the
# same URL answers 303→200 without these three headers and 403 with them,
# reproducibly. That is the endpoint defending itself against being embedded by
# another site, which is a reasonable thing for it to do and not a thing we are
# doing.
#
# The answer is to omit the headers there, not to send different values. The
# true description of this request is a cross-site programmatic fetch, and
# claiming `navigate`/`document` to get past a check would be a lie about it —
# the one thing the header block above promises not to do. Saying nothing is
# not a lie; it is what every HTTP client did before 2020.
_NO_FETCH_METADATA_HOSTS = ("drive.google.com", "docs.google.com")


def headers_for(url: str) -> dict[str, str]:
    """The request headers for this URL."""
    host = urlsplit(url or "").netloc.lower()
    if any(host == h or host.endswith("." + h) for h in _NO_FETCH_METADATA_HOSTS):
        return {"User-Agent": USER_AGENT}
    return RESOURCE_HEADERS


def direct_download_url(url: str) -> str:
    """The address that serves the bytes, when the given one only shows them.

    Returns the URL unchanged whenever nothing is recognised, which is the
    common case. A rewrite that guessed would be worse than none: the caller
    would be reading a different document from the one it asked for, which is
    the failure this whole server is built to avoid.
    """
    m = _DRIVE_FILE_ID.match(url or "")
    if not m:
        return url
    file_id = m.group(1)
    if "uc?" in url and "export=download" in url:
        return url  # already the download form
    return f"https://drive.google.com/uc?export=download&id={file_id}"


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
    url = direct_download_url(url)
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=DEFAULT_TIMEOUT,
        headers=headers_for(url),
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
    url = direct_download_url(url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    bytes_written = 0
    truncated = False
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=DEFAULT_TIMEOUT,
        headers=headers_for(url),
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


# ─── What the file actually is ────────────────────────────────────────────────

# Both formats are zip containers, so `PK` alone cannot tell them apart. ODS
# declares itself in a `mimetype` member; XLSX has a workbook part. Reading the
# declaration is the whole trick, and skipping it cost real resources: the
# catalog carries an ODS registered as CSV, and answering `PK` → XLSX sent it to
# `read_xlsx`, which replied "No [Content_Types].xml found in xlsx file" — a
# sentence about our internals, about a file whose own name ended in `.ods`.
_ODS_MIMETYPE = b"application/vnd.oasis.opendocument.spreadsheet"

# `d0cf11e0` is OLE2, the pre-2007 Excel container. `read_xlsx` cannot read BIFF
# and says so in terms of a missing zip member, which sounds like corruption.
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0"


def sniff_container(path: Path) -> tuple[str | None, str | None]:
    """What this file is, and what to say when it is nothing we read.

    Returns `(format, refusal)` — exactly one of the two is set. `format` is a
    supported kind inferred from the bytes; `refusal` is a sentence for the caller
    when the bytes are recognisable but unsupported.

    Only the container is identified, never the contents: a zip is opened to read
    its member names, not its data.
    """
    try:
        with path.open("rb") as fh:
            magic = fh.read(8)
    except OSError:  # pragma: no cover — the file was just written
        return None, None

    if magic.startswith(_OLE2_MAGIC):
        return None, (
            "The file is a pre-2007 Excel workbook (BIFF/OLE2), which this server "
            "cannot read — the reader handles the ZIP-based formats: XLSX, XLSM and "
            "ODS. Ask the publisher for the same table saved as XLSX or CSV."
        )

    if not magic.startswith(b"PK\x03\x04"):
        return None, None

    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if "mimetype" in names:
                try:
                    if z.read("mimetype").strip().startswith(_ODS_MIMETYPE):
                        return "ods", None
                except (KeyError, OSError):  # pragma: no cover — truncated member
                    pass
            if any(n.startswith("xl/") for n in names):
                return "xlsx", None
            # A zip holding exactly one data file is an archive, not a workbook —
            # three resources in this catalog are a single `.json` zipped up. Read
            # as a spreadsheet it produced a message about a missing workbook part;
            # what it needs is to be unpacked.
            data = [n for n in names if not n.endswith("/") and classify_format(_suffix(n))]
            if len(data) == 1:
                return "zip:" + data[0], None
    except (zipfile.BadZipFile, OSError):
        return None, (
            "The file starts like a ZIP container but cannot be opened as one. It "
            "is most likely truncated in the portal's own copy."
        )
    return None, None


def _suffix(name: str) -> str:
    return name.rsplit(".", 1)[-1] if "." in name else ""


def looks_like_text_table(head: bytes) -> str | None:
    """`csv` or `json` when these opening bytes are that, else None.

    The mirror image of the ZIP case, and just as common: a CSV registered as ODS.
    The old check demanded that an ODS start with `PK` and refused everything else,
    so a perfectly readable CSV was reported as "not a valid ODS" — accurate about
    the declaration and useless about the file.
    """
    probe = head[:4096].lstrip()
    if probe.startswith(b"\xef\xbb\xbf"):
        probe = probe[3:].lstrip()
    if not probe:
        return None
    if probe[:1] == b"<":
        # Markup, and the delimiter test below would have called it CSV: a
        # minified page's first line almost always contains a comma
        # (`content="width=device-width, initial-scale=1.0"`). A page reported as
        # a one-column CSV, with a correction note asserting the bytes are CSV,
        # is worse than the refusal this replaces — the model would receive markup
        # as data. `looks_like_html` catches the pages that *start* with a known
        # marker; this catches every other shape of markup by refusing to guess.
        # No CSV header begins with `<`.
        return None
    if probe[:1] in (b"{", b"["):
        return "json"
    try:
        text = probe.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = probe.decode("cp1252")
        except UnicodeDecodeError:  # pragma: no cover — binary
            return None
    first = text.splitlines()[0] if text.splitlines() else ""
    # A delimiter in the first line is the whole test. Deliberately weak: this
    # runs only for a file the catalog declared as a spreadsheet and whose bytes
    # are not a container, where the alternative to reading it is refusing a file
    # that may be perfectly good data. Weak is acceptable *because* markup is
    # excluded above; without that guard the weakness is a hazard rather than a
    # trade.
    if any(d in first for d in (",", ";", "\t", "|")):
        return "csv"
    return None

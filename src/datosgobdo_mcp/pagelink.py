"""Resolve a web page back to the data file it was supposed to be.

Portals answer a resource URL with a page rather than a file more often than
they should. In a census of the whole `datos.gob.do` catalog, 37 of 1,056
resources did it. Until now the answer was "the URL returned an HTML page,
not a data file" — true, and a dead end.

Opening all 37 shows they are not one thing:

    15  a page with no data file linked at all
     7  a login or restricted-access page
     6  a page linking the file directly
     6  a page linking it through a download handler (`?wpdmpro=`, `phocadownload`)
     3  a page whose HTML table *is* the data

Only the middle twelve can be resolved, and this module resolves them **without
any per-portal knowledge**. A curated map of "how each institution lays out its
site" would rot with the next redesign and would be worthless for the other six
countries in the regional catalog.

What replaces the map is the request itself. The URL the caller asked for
almost always names the resource — `?wpdmpro=cartera-de-afiliados-regimen-
subsidiado-senasa-noviembre-2025`, `…/titulaciones-tierras-2018-2025` — so the
links a page offers can be scored against it. Measured on the twelve real
cases: the URL-derived hint decides 6, while fetching the CKAN metadata to use
its resource name decides only 4. The cheaper signal is also the better one.

The other six stay ambiguous, and that is treated as a result rather than a
failure. Two reasons, both from the data: one portal offers six links that are
navigation rather than files, and another names its files `clss.csv` and
`xls.xlsx` — no scoring function will ever match `Inventario de Almacén` to
`clss.csv`. So when the score does not separate, the candidates are handed back.
The caller is an assistant that holds the user's actual question, and it will
choose better than a string-similarity ratio can.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from html.parser import HTMLParser
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

DATA_EXTENSIONS = ("csv", "tsv", "xlsx", "xls", "xlsm", "ods", "json")

# Handlers that serve a file without naming it in the path. These are the
# generic shapes — a query parameter or a path segment that means "download" —
# not a list of portals.
_DOWNLOAD_HINTS = (
    "wpdmpro=",
    "phocadownload",
    "?download=",
    "/download/",
    "&download=",
    "export=download",  # Drive's own direct-download form
)

# A Drive share link normalises to the download form — but only if it names a
# file. One page in this catalog embeds `drive.google.com/file/d//preview` with
# the id left empty, the real one sitting base64-encoded in the page's own
# query string. Accepting it turned "no candidate" into a confident answer
# pointing at nothing, which is worse than no answer and is the exact failure
# this server exists to avoid.
_DRIVE_SHARE = re.compile(r"drive\.google\.com/file/d/[A-Za-z0-9_-]{10,}")

# A candidate must beat the runner-up by this much to be followed. Below it the
# list goes back to the caller. Chosen so that the twelve measured cases split
# 6 resolved / 6 handed back rather than 12 guessed.
MIN_MARGIN = 0.15

_LOGIN_MARKERS = (
    "iniciar sesión",
    "iniciar sesion",
    "acceso restringido",
    'type="password"',
    "type='password'",
    "inicia sesión",
)


class _LinkCollector(HTMLParser):
    """Collect anchors with their visible text.

    A regex over `href=` was the first attempt and it also matched hrefs inside
    scripts and commented-out markup. The stdlib parser costs nothing and does
    not need a dependency, which matters: pulling in lxml to rescue twelve
    resources would be a poor trade.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("iframe", "embed"):
            # A page that embeds the file instead of linking it is still a page
            # that names the file. One resource in this catalog is a Drive
            # document in an iframe, and the src is a perfectly good address.
            src = dict(attrs).get("src")
            if src:
                self.links.append((src, ""))
            return
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self._href = href
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(self._text).strip()))
            self._href, self._text = None, []


def _normalize(text: str) -> str:
    """Fold to plain lowercase words: accents, %20 and punctuation all go."""
    folded = unquote(text or "")
    folded = unicodedata.normalize("NFKD", folded).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", folded.lower()).strip()


def _looks_like_data(href: str) -> bool:
    low = href.lower()
    path = urlsplit(low).path
    if any(path.endswith("." + ext) for ext in DATA_EXTENSIONS):
        return True
    if any(h in low for h in _DOWNLOAD_HINTS):
        return True
    return bool(_DRIVE_SHARE.search(low))


def hint_from_url(url: str) -> str:
    """The part of the requested URL that names the resource.

    The last two non-empty path segments plus the query. Taking only the last
    one gave an empty string for every URL ending in a slash — and these
    portals end in a slash constantly, so a whole class of pages scored zero
    against every candidate and could never resolve. Two segments also recovers
    the useful name when the last is a year:
    `…/relacion-de-militares-que-prestan-servicios/2026-2/`.
    """
    parts = urlsplit(url)
    segments = [seg for seg in parts.path.split("/") if seg][-2:]
    return " ".join(segments + ([parts.query] if parts.query else []))


def identity(url: str) -> str:
    """The part of a URL that says which file it is.

    For a plain path that is the filename. For a download handler it is the
    query — `?wpdmpro=cartera-de-afiliados-…` carries the whole identity while
    the path is just `/transparencia/`. Dropping the query lost every one of
    those, which is half the resolvable cases in this catalog.
    """
    parts = urlsplit(url)
    return f"{parts.path.rsplit('/', 1)[-1]} {parts.query}".strip()


def _extension(url: str) -> str:
    path = urlsplit(url).path.lower()
    return path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""


def score(candidate_url: str, link_text: str, hint: str, fmt: str | None) -> float:
    """How well a link matches what was asked for. Higher is better."""
    name = _normalize(identity(candidate_url) + " " + link_text)
    target = _normalize(hint)
    value = difflib.SequenceMatcher(None, name, target).ratio() if target else 0.0
    # The declared format is the decisive signal, and it took a real page to
    # see why. These portals publish the *same file* three times — `.csv`,
    # `.ods`, `.xlsx` — so the three candidates score within 0.001 of each
    # other and no amount of name matching will separate them. It does not have
    # to: the caller already said which format the resource is registered as.
    # The bonus therefore has to be worth more than the noise between siblings,
    # and it has to be tested against the URL's extension — comparing it to the
    # end of the name failed silently, because the link text sits after it.
    if fmt and _extension(candidate_url) == fmt.lower().lstrip("."):
        value += 0.2
    # Whole words carry more signal than character overlap: a file named
    # `Nomina-de-Empleados-DIGEV-2019-2024.csv` shares few characters in order
    # with `nomina-empleados-digev` but every meaningful word.
    words = [w for w in target.split() if len(w) > 3]
    if words:
        value += 0.35 * sum(1 for w in words if w in name) / len(words)
    return value


def candidates(html: str, base_url: str, fmt: str | None = None) -> list[dict[str, Any]]:
    """Every data file this page links, scored against the requested URL."""
    parser = _LinkCollector()
    try:
        parser.feed(html)
    except Exception:  # pragma: no cover — malformed markup, keep what we got
        pass
    hint = hint_from_url(base_url)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for href, text in parser.links:
        if not _looks_like_data(href):
            continue
        absolute = urljoin(base_url, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        out.append(
            {
                "url": absolute,
                "name": unquote(identity(absolute)) or text[:80],
                "score": round(score(absolute, text, hint, fmt), 3),
            }
        )
    out.sort(key=lambda c: c["score"], reverse=True)
    return out


# Formats that are not data but that a caller may still want to know about. A
# page offering three PDFs when a CSV was asked for is a different situation
# from a page offering nothing, and reporting both as "no data file" hides it.
_OTHER_EXTENSIONS = ("pdf", "doc", "docx", "zip", "rar", "txt", "xml")


def _other_format_candidates(html: str, base_url: str, fmt: str) -> list[dict[str, Any]]:
    """Files this page links that are not of the requested format."""
    parser = _LinkCollector()
    try:
        parser.feed(html)
    except Exception:  # pragma: no cover — malformed markup
        pass
    hint = hint_from_url(base_url)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for href, text in parser.links:
        ext = _extension(href)
        if ext not in _OTHER_EXTENSIONS and ext not in DATA_EXTENSIONS:
            continue
        absolute = urljoin(base_url, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        out.append(
            {
                "url": absolute,
                "name": unquote(identity(absolute)) or text[:80],
                "score": round(score(absolute, text, hint, None), 3),
                "note": f"not the declared format ({fmt}); this one is {ext or 'unknown'}",
            }
        )
    out.sort(key=lambda c: c["score"], reverse=True)
    return out


def resolve(html: str, base_url: str, fmt: str | None = None) -> tuple[str | None, list[dict]]:
    """Pick the linked file, or return the candidates for the caller to pick.

    Returns `(url, candidates)`. `url` is None when nothing was linked, or when
    the top two are too close to separate — the caller gets the list either way.
    """
    found = candidates(html, base_url, fmt)
    if not found and fmt:
        # The page links files, just none of them data. Saying "no data file"
        # would be false, and the caller is better placed than a scoring
        # function to decide whether a PDF answers the question.
        others = _other_format_candidates(html, base_url, fmt)
        if others:
            return None, others
    if not found:
        return None, []
    if len(found) == 1:
        return found[0]["url"], found
    if found[0]["score"] - found[1]["score"] >= MIN_MARGIN:
        return found[0]["url"], found
    return None, found


def describe(html: str) -> str:
    """Say which kind of page this is, so the reply is an instruction.

    Thirty-seven resources used to share one message. They are four situations
    and the reader's next move differs in each: a page with no file is a broken
    publication to report, a login means the data is not open, a table means the
    data is there in another shape.
    """
    low = html[:20000].lower()
    if any(marker in low for marker in _LOGIN_MARKERS):
        return (
            "The URL returned a login or restricted-access page. This resource is "
            "listed as open data but is not served openly."
        )
    if "<table" in low and ("<td" in low or "<th" in low):
        return (
            "The URL returned a page whose data is inside an HTML table rather than "
            "in a downloadable file. Reading tables out of pages is not supported."
        )
    if low.count("<a ") > 100 and ("admin-ajax.php" in low or "elementor" in low):
        # Measured on sixteen of these: hundreds of anchors, not one to a data
        # file, because the file list is fetched when a browser opens the page.
        # "No data file linked" is true and useless; this is what to do next.
        return (
            "The URL returned a section page of the institution's site whose file "
            "list is built when a browser opens it, so no file is linked in the "
            "HTML itself. It cannot be reached by any number of link hops — open "
            "it in a browser to get the file, or ask the publisher to register "
            "the file's own address in the catalog."
        )
    return (
        "The URL returned a web page with no data file linked on it. The portal "
        "most likely answered a dead or moved download link with HTTP 200."
    )

"""Why a government portal would not hand over a file, said in one vocabulary.

A 403 is not one thing. Measured across this catalog, at least two different
decisions arrive wearing the same status code, and they mean opposite things to
whoever is asking:

* A **site rule** refuses everyone the site has not allowed. Nothing the caller
  does changes it; the file is effectively unpublished for programmatic use.
* An **interactive challenge** refuses *programs*. It answers ``403`` with
  ``cf-mitigated: challenge`` and expects JavaScript to be run and a token
  returned. A person with a browser downloads the file without noticing.
  Verified on migracion.gob.do: the same URL, the same minute, a browser
  succeeded and every header combination a client can send did not.

Collapsing the two loses the only fact that matters to the person asking — is
this file gone, or is it just not reachable from here? The census called them
all "blocked", which reads as an accusation against 99 institutions when a good
share of it is a hosting default nobody chose deliberately.

The second reason this module exists is narrower and was learned the hard way.
Asked to analyse a file behind a challenge, an assistant could not fetch it,
found a similar-sounding file from a different institution, and answered with
that one — presenting figures that differed by a million people, naming the
substitute once in parentheses. The server had done nothing wrong: it returned
a 403 with the raw text of the HTTP error and a link to MDN. But an error that
offers no path invites the caller to invent one, so the messages here always
end by saying what not to do.
"""

from __future__ import annotations

from typing import Any

# The single vocabulary. The error messages, the reachability tool and the
# census all speak it; three hand-rolled classifiers would drift apart within a
# week and then disagree in public.
OK = "ok"
CHALLENGE = "challenge"
WAF_RULE = "waf_rule"
HTML_PAGE = "html_page"
NOT_FOUND = "not_found"
SERVER_ERROR = "server_error"
NETWORK = "network"

_CHALLENGE_MARKERS = ("challenges.cloudflare.com", "cf_chl", "cf-please-wait")


def classify(
    status: int | None,
    headers: dict[str, str] | None = None,
    body_head: str = "",
) -> str:
    """Name what happened, from the status and the response headers.

    `status` is None when nothing came back at all — DNS, TLS, timeout — which
    is a statement about the network between here and there, not about the
    resource.
    """
    if status is None:
        return NETWORK
    lowered = {k.lower(): v for k, v in (headers or {}).items()}

    # The header is unambiguous and outranks every guess about the body.
    if lowered.get("cf-mitigated", "").strip().lower() == "challenge":
        return CHALLENGE
    if status == 403:
        # Some routes serve the challenge widget without the header; the widget
        # script tag is the tell.
        if any(m in body_head for m in _CHALLENGE_MARKERS):
            return CHALLENGE
        return WAF_RULE
    if status in (404, 410):
        return NOT_FOUND
    if status >= 500:
        return SERVER_ERROR
    if status >= 400:
        return WAF_RULE
    if "html" in lowered.get("content-type", "").lower():
        return HTML_PAGE
    return OK


# Said once, because it is the sentence the whole module exists for.
_DO_NOT_SUBSTITUTE = (
    "Do not answer from a different file or a different institution without "
    "saying so plainly: a near-enough source presented as this one is worse "
    "than no answer."
)

_EXPLANATIONS: dict[str, tuple[str, str]] = {
    CHALLENGE: (
        "The site answered with an interactive browser challenge, not a refusal. "
        "The file is still published — a person opening this URL in a browser "
        "downloads it normally — but the challenge cannot be solved by any HTTP "
        "client, so no combination of headers will get past it.",
        "Ask the user to download it in a browser and supply the file, or use an "
        "archived copy if one is configured. " + _DO_NOT_SUBSTITUTE,
    ),
    WAF_RULE: (
        "The site refused the request outright. This is a rule the institution's "
        "own site applies to programmatic access; it is not a challenge and there "
        "is nothing to solve.",
        "Report the resource as published but not machine-readable, and name the "
        "institution. " + _DO_NOT_SUBSTITUTE,
    ),
    NOT_FOUND: (
        "The catalog lists this resource but the file is not at that address. The "
        "link is broken at the source, which is a finding about the publisher.",
        "Report the broken link with the dataset it belongs to. " + _DO_NOT_SUBSTITUTE,
    ),
    SERVER_ERROR: (
        "The site failed while serving the file. This is usually transient.",
        "Retry once after a short wait before concluding anything. " + _DO_NOT_SUBSTITUTE,
    ),
    NETWORK: (
        "Nothing answered at that address — the name did not resolve, the TLS "
        "handshake failed, or the host never replied. Nothing here distinguishes a "
        "site that is down from one unreachable only from this network.",
        "Say the result is inconclusive rather than reporting the resource as "
        "unavailable. " + _DO_NOT_SUBSTITUTE,
    ),
}


def explain(kind: str, archived: dict[str, Any] | None = None) -> dict[str, str]:
    """The `hint` and `next_step` that belong with a failure of this kind.

    `archived` is the manifest entry for this URL when the operator has
    configured an archive and it actually holds a copy — passed in rather than
    looked up here, so this module stays free of I/O and the caller never
    promises a copy that is not on disk.
    """
    hint, step = _EXPLANATIONS.get(kind, ("", _DO_NOT_SUBSTITUTE))
    out = {"reachability": kind}
    if hint:
        out["hint"] = hint
    if archived:
        captured = archived.get("captured_at") or archived.get("captured") or "an earlier date"
        digest = str(archived.get("sha256") or "")[:16]
        step = (
            f"An archived copy of this exact URL was captured on {captured}"
            + (f" (sha256 {digest}…)" if digest else "")
            + ". Answer from it only if you state the capture date alongside every "
            "figure. " + _DO_NOT_SUBSTITUTE
        )
    out["next_step"] = step
    return out

"""Serve a resource from an archived copy when the portal will not.

Opt-in, never silent. Set ``DATOSGOBDO_ARCHIVE_DIR`` to a directory holding a
``manifest.json`` and the Parquet files it names; every reply built from one
carries where it came from and when it was captured.

**What this does not do.** It does not rescue the resources the portal already
refuses. An archive can only hold what could be downloaded, so by definition it
does not contain the 360 catalog resources sitting behind a WAF. That is the
natural assumption and it is false, which is why it is written here.

What it does is make today's readable resource still readable tomorrow. In this
catalog that is not hypothetical: a census of all 1,056 found 15 links already
dead and 99 institutions whose sites had grown rules that refuse programmatic
access. A figure cited from a resource that later disappears can still be
recomputed against the same ``sha256``.

The guarantee that makes it usable in an audit is that provenance travels with
the data. A tool that answers with yesterday's copy as though it were today's
has stopped being an audit tool, so a fallback always reports the archive, the
capture date, the digest and why the origin was not used — and it only happens
when the operator has asked for it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

MANIFEST_NAME = "manifest.json"
ENV_DIR = "DATOSGOBDO_ARCHIVE_DIR"


def archive_dir() -> Path | None:
    """The configured archive, or None when the feature is off."""
    raw = os.environ.get(ENV_DIR)
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_dir() else None


def _load_manifest(root: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    except Exception:
        return {}
    entries = data.get("resources") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        return {}
    return entries


def lookup(url: str) -> tuple[Path, dict[str, Any]] | None:
    """Find the archived copy of a URL, or None.

    Keyed on the source URL because that is what the caller has. A resource
    whose entry names a file that is not on disk is skipped rather than
    reported: a manifest promising data it does not have is worse than an
    absent one.
    """
    root = archive_dir()
    if root is None:
        return None
    entry = _load_manifest(root).get(url)
    if not isinstance(entry, dict):
        return None
    relative = entry.get("parquet")
    if not isinstance(relative, str):
        return None
    parquet = root / relative
    if not parquet.is_file():
        return None
    return parquet, entry


def provenance(url: str, entry: dict[str, Any], reason: str) -> dict[str, Any]:
    """The block that must accompany any answer built from the archive."""
    return {
        "source": "archive",
        "original_url": url,
        "fetched_at": entry.get("fetched_at"),
        "sha256": entry.get("sha256"),
        "licence": entry.get("licence"),
        "parser_build": entry.get("parser_build"),
        "reason": reason,
        "note": (
            "This answer was built from an archived copy, not from the portal. "
            "The figures describe the file as it was captured, not as it is now."
        ),
    }

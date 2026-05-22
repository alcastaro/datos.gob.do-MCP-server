"""On-disk Parquet cache for downloaded resources.

Designed as a swappable backend. v0.3 ships LocalDiskCache; future versions
can add S3/object-storage backends without changing the analytics layer.

Key format: <resource_id_or_url_hash>__<last_modified_or_etag>.parquet
- ETag/last_modified ensures cache invalidates when source changes.
- LRU eviction keeps total bytes under MAX_CACHE_BYTES.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Protocol

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "datosgobdo-mcp"
DEFAULT_MAX_BYTES = 1 * 1024 * 1024 * 1024  # 1 GB
INDEX_FILENAME = "_index.json"


class CacheBackend(Protocol):
    """Interface so we can swap in S3/MinIO/etc. in future versions."""

    def get(self, key: str) -> Path | None: ...
    def put_path(self, key: str) -> Path: ...
    def touch(self, key: str) -> None: ...
    def evict_to_fit(self, max_bytes: int) -> None: ...
    def stats(self) -> dict: ...


def _hash_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _build_key(url: str, etag: str | None, last_modified: str | None) -> str:
    version_tag = etag or last_modified or "no-version"
    version_safe = hashlib.sha256(version_tag.encode("utf-8")).hexdigest()[:12]
    return f"{_hash_url(url)}__{version_safe}"


class LocalDiskCache:
    """Parquet-on-disk cache, single-user, single-host.

    Files live in cache_dir as `<key>.parquet`. An `_index.json` tracks last
    access time for LRU eviction.
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.index_path = self.cache_dir / INDEX_FILENAME
        self._index = self._load_index()

    def _load_index(self) -> dict[str, dict]:
        if not self.index_path.exists():
            return {}
        try:
            return json.loads(self.index_path.read_text())
        except Exception:
            return {}

    def _save_index(self) -> None:
        try:
            self.index_path.write_text(json.dumps(self._index, indent=2))
        except Exception:
            pass

    def _entry_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.parquet"

    def get(self, key: str) -> Path | None:
        p = self._entry_path(key)
        if not p.exists():
            return None
        self.touch(key)
        return p

    def put_path(self, key: str) -> Path:
        """Return the destination path the caller should write Parquet to."""
        p = self._entry_path(key)
        self._index[key] = {
            "created_at": time.time(),
            "accessed_at": time.time(),
            "bytes": 0,
        }
        return p

    def finalize(self, key: str) -> None:
        """Mark a put as complete; refresh size metadata."""
        p = self._entry_path(key)
        if p.exists():
            self._index.setdefault(key, {})["bytes"] = p.stat().st_size
            self._index[key]["accessed_at"] = time.time()
            self._save_index()
            self.evict_to_fit(self.max_bytes)

    def touch(self, key: str) -> None:
        self._index.setdefault(key, {})["accessed_at"] = time.time()
        self._save_index()

    def evict_to_fit(self, max_bytes: int) -> None:
        """LRU eviction until total cache size <= max_bytes."""
        entries = [
            (k, v.get("accessed_at", 0), v.get("bytes", 0))
            for k, v in self._index.items()
            if self._entry_path(k).exists()
        ]
        total = sum(b for _, _, b in entries)
        if total <= max_bytes:
            return
        # Oldest first.
        entries.sort(key=lambda x: x[1])
        for key, _accessed, size in entries:
            if total <= max_bytes:
                break
            try:
                self._entry_path(key).unlink(missing_ok=True)
                self._index.pop(key, None)
                total -= size
            except Exception:
                pass
        self._save_index()

    def stats(self) -> dict:
        entries = [
            (k, self._entry_path(k).stat().st_size)
            for k in self._index
            if self._entry_path(k).exists()
        ]
        return {
            "cache_dir": str(self.cache_dir),
            "entries": len(entries),
            "total_bytes": sum(s for _, s in entries),
            "max_bytes": self.max_bytes,
        }

    def clear(self) -> int:
        """Remove all entries. Returns count removed."""
        n = 0
        for p in self.cache_dir.glob("*.parquet"):
            try:
                p.unlink()
                n += 1
            except Exception:
                pass
        self._index = {}
        self._save_index()
        return n


# Module-level singleton. Override via env vars for testing/hosted deployments.
_singleton: LocalDiskCache | None = None


def get_cache() -> LocalDiskCache:
    global _singleton
    if _singleton is None:
        cache_dir = os.environ.get("DATOSGOBDO_CACHE_DIR")
        max_bytes_str = os.environ.get("DATOSGOBDO_CACHE_MAX_BYTES")
        max_bytes = int(max_bytes_str) if max_bytes_str else DEFAULT_MAX_BYTES
        _singleton = LocalDiskCache(
            cache_dir=Path(cache_dir) if cache_dir else None,
            max_bytes=max_bytes,
        )
    return _singleton


def build_cache_key(
    url: str,
    etag: str | None = None,
    last_modified: str | None = None,
) -> str:
    return _build_key(url, etag, last_modified)

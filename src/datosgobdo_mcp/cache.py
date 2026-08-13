"""On-disk Parquet cache for downloaded resources.

Designed as a swappable backend. v0.3 ships LocalDiskCache; future versions
can add S3/object-storage backends without changing the analytics layer.

Key format: <url_hash>__<last_modified_or_etag>__<parser_build>.parquet
- ETag/last_modified ensures cache invalidates when the *source* changes.
- parser_build ensures it also invalidates when *we* change, which the first
  two do not. See _parser_build().
- LRU eviction keeps total bytes under MAX_CACHE_BYTES.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

from . import __version__

try:  # POSIX cross-process lock
    import fcntl
except ImportError:
    fcntl = None  # type: ignore[assignment]

try:  # Windows cross-process lock; absent everywhere else
    import msvcrt
except ImportError:
    msvcrt = None  # type: ignore[assignment]

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


_parser_build_cached: str | None = None


def _parser_build() -> str:
    """Identify the code that produced a cached Parquet.

    A cache keyed only on URL + ETag answers "is the source still the same?"
    It cannot answer "would we parse it the same way today?", and those are
    different questions. When 0.7.5 fixed the codepage detection, ten Parquets
    written by 0.7.4 stayed valid by that key and kept serving `A隳` and
    `Informaci≤n` to every caller — a fix that shipped and did nothing, which is
    worse than no fix, because the tests said it worked. They had to be deleted
    by hand.

    Two inputs decide the bytes we write:

    - our own version, which this project's convention bumps on every change
      that touches the parsers; and
    - DuckDB's, because its CSV sniffer picks the column types. A `uv sync`
      that upgrades it changes our output without changing a line of our code.

    Hashing both is deliberately coarse. It discards a cache entry on releases
    that could not have altered the parse, and that is the trade we want: the
    cost of over-invalidating is one re-download inside a 1 GB LRU, and the
    cost of under-invalidating is silently serving corrupted data from a tool
    whose entire purpose is to be trusted about what the file says.
    """
    global _parser_build_cached
    if _parser_build_cached is None:
        try:
            import duckdb

            engine = duckdb.__version__
        except Exception:  # pragma: no cover — duckdb is a hard dependency
            engine = "unknown"
        material = f"{__version__}|duckdb{engine}"
        _parser_build_cached = hashlib.sha256(material.encode("utf-8")).hexdigest()[:8]
    return _parser_build_cached


def _build_key(url: str, etag: str | None, last_modified: str | None) -> str:
    version_tag = etag or last_modified or "no-version"
    version_safe = hashlib.sha256(version_tag.encode("utf-8")).hexdigest()[:12]
    return f"{_hash_url(url)}__{version_safe}__{_parser_build()}"


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

    @contextmanager
    def _lock(self) -> Generator[None]:
        """Cross-process exclusive lock for index/eviction mutations. Two server
        instances (or concurrent HTTP requests in hosted mode) share the cache
        dir; without this, eviction and finalize race.

        POSIX uses flock. Windows uses msvcrt.locking over one byte at offset
        0 — its LK_LOCK mode retries for about ten seconds and then raises
        rather than waiting forever, and a mutation under this lock is a JSON
        write measured in milliseconds, so a ten-second contention window
        failing loudly beats an indefinite silent wait. Only when neither
        module exists does this degrade to a per-process no-op.
        """
        if fcntl is None and msvcrt is None:  # pragma: no cover — unknown platform
            yield
            return
        lock_file = self.cache_dir / ".lock"
        with open(lock_file, "w") as f:
            if fcntl is not None:
                fcntl.flock(f, fcntl.LOCK_EX)
            else:  # Windows
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(f, fcntl.LOCK_UN)
                else:  # Windows
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)

    @contextmanager
    def _locked_index(self) -> Generator[dict[str, dict]]:
        """Hold the lock, and work on the index as it is on disk right now.

        The lock alone was not enough, and a four-process test proved it:
        45 of 60 entries vanished. Each instance loads the index once at
        construction and mutates that copy, so two processes that both write
        —serialised or not— each save their own snapshot and the last one
        silently drops everything the other added. Nothing raises; the index
        just forgets that a Parquet on disk belongs to a URL, and the next
        call re-downloads a file it already had.

        Serialising the write was never the hard part. Making read-modify-write
        atomic is, and that means re-reading inside the lock and discarding the
        stale in-memory copy.
        """
        with self._lock():
            self._index = self._load_index()
            yield self._index
            self._save_index()

    def _save_index(self) -> None:
        try:
            # Atomic: a crash mid-write must not leave a truncated index that
            # _load_index would silently discard (losing LRU + URL mappings).
            tmp = self.index_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._index, indent=2))
            os.replace(tmp, self.index_path)
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

    def finalize(
        self,
        key: str,
        url: str | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> None:
        """Mark a put as complete; refresh size metadata.

        `provenance` is whatever the caller must keep saying about this entry
        for as long as it is served — today, that the data came from a URL other
        than the one asked for, and that a large file parsed into a suspiciously
        small shape. It is stored because the first call is not the only call: a
        fact that only the download path reports is a fact the caller stops
        hearing the moment the cache warms up.
        """
        p = self._entry_path(key)
        if p.exists():
            with self._locked_index() as index:
                index.setdefault(key, {})["bytes"] = p.stat().st_size
                index[key]["accessed_at"] = time.time()
                index[key]["build"] = _parser_build()
                if url is not None:
                    index[key]["url"] = url
                if provenance:
                    index[key]["provenance"] = provenance
                self._evict_to_fit_locked(self.max_bytes)

    def provenance(self, key: str) -> dict[str, Any]:
        """What must travel with this entry on every hit, warm or cold.

        Empty for entries written before this was tracked. Absence is reported
        as absence, never invented: an old entry that followed a link has no
        record of it, and guessing one would be worse than saying nothing.
        """
        stored = self._index.get(key, {}).get("provenance")
        return dict(stored) if isinstance(stored, dict) else {}

    def get_by_url(self, url: str) -> tuple[Path, str] | None:
        """Return (path, key) for the most recently accessed entry matching url, or None.

        This is the warm path the analytics tools take, and it never computes a
        key — it matches on URL alone, so putting the parser build into the key
        does not reach it. An entry written by different code is therefore
        rejected here explicitly, by the build stamped at finalize(). Entries
        predating the stamp carry no build and are treated as stale, which is
        the right answer for them: they were written before we started tracking
        this, so we cannot claim they match.
        """
        build = _parser_build()
        best_key: str | None = None
        best_accessed: float = -1.0
        for key, meta in self._index.items():
            if meta.get("url") == url and meta.get("build") == build:
                p = self._entry_path(key)
                if p.exists():
                    accessed = meta.get("accessed_at", 0.0)
                    if accessed > best_accessed:
                        best_accessed = accessed
                        best_key = key
        if best_key is None:
            return None
        return self._entry_path(best_key), best_key

    def touch(self, key: str) -> None:
        # Also a read-modify-write, and it runs on every warm hit — the most
        # frequent index mutation there is.
        with self._locked_index() as index:
            index.setdefault(key, {})["accessed_at"] = time.time()

    def evict_to_fit(self, max_bytes: int) -> None:
        """LRU eviction until total cache size <= max_bytes."""
        with self._locked_index():
            self._evict_to_fit_locked(max_bytes)

    def _evict_to_fit_locked(self, max_bytes: int) -> None:
        entries = [
            (k, v.get("accessed_at", 0), v.get("bytes", 0))
            for k, v in self._index.items()
            if self._entry_path(k).exists()
        ]
        total = sum(b for _, _, b in entries)
        if total <= max_bytes:
            return
        # Oldest first; key as tie-break so eviction order is deterministic
        # when two entries share a timestamp.
        entries.sort(key=lambda x: (x[1], x[0]))
        for key, _accessed, size in entries:
            if total <= max_bytes:
                break
            try:
                self._entry_path(key).unlink(missing_ok=True)
                self._index.pop(key, None)
                total -= size
            except Exception:
                pass

    def stats(self) -> dict:
        entries = [
            (k, self._entry_path(k).stat().st_size)
            for k in self._index
            if self._entry_path(k).exists()
        ]
        current = _parser_build()
        return {
            "cache_dir": str(self.cache_dir),
            "entries": len(entries),
            "total_bytes": sum(s for _, s in entries),
            "max_bytes": self.max_bytes,
            "parser_build": current,
            # Written by an older parser: still on disk, never served, and the
            # next eviction pass reclaims them.
            "stale_entries": sum(
                1 for k, _ in entries if self._index.get(k, {}).get("build") != current
            ),
        }

    def clear(self) -> int:
        """Remove all entries. Returns count removed."""
        with self._locked_index() as index:
            n = 0
            for p in self.cache_dir.glob("*.parquet"):
                try:
                    p.unlink()
                    n += 1
                except Exception:
                    pass
            index.clear()
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

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
import logging
import os
import random
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

from . import __version__

logger = logging.getLogger(__name__)

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

# A mutation under this lock is a JSON write measured in milliseconds, so ten
# seconds of contention means something is wrong rather than merely busy.
LOCK_TIMEOUT_SECONDS = 10.0

# How long a Parquet with no index entry is left alone before it counts as
# abandoned. It has to exceed the window between writing a file and recording it,
# because inside that window a live write is indistinguishable from an orphan.
_ORPHAN_GRACE_SECONDS = 60.0
_LOCK_FIRST_DELAY = 0.01
_LOCK_MAX_DELAY = 0.25


class CacheLockError(RuntimeError):
    """The cache index could not be locked before the timeout."""


def _try_msvcrt_lock(f: Any) -> bool:  # pragma: no cover — Windows only
    """One non-blocking attempt at the Windows lock. False if someone holds it."""
    if msvcrt is None:
        return True  # no lock to take on this platform; callers never get here
    f.seek(0)
    try:
        # typeshed guards msvcrt by platform, so off Windows the module type has
        # no attributes at all and this cannot be expressed to mypy any other way.
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
        return True
    except OSError:
        return False


def _acquire_with_backoff(
    try_lock: Callable[[], bool],
    describe: str,
    *,
    timeout: float = LOCK_TIMEOUT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    jitter: Callable[[], float] = random.random,
) -> None:
    """Call `try_lock` until it succeeds, or raise CacheLockError at `timeout`.

    Windows' own blocking mode, `msvcrt.LK_LOCK`, retries once a second ten
    times and does not queue, so a waiter can watch the holder reacquire on
    every retry. Measured on real hardware: two writers, 200 entries each, one
    worst-case wait of 6.2 s against that ~10 s ceiling; at four writers, two of
    them exceeded it and died. Backoff with jitter fixes both halves — it retries
    in milliseconds rather than whole seconds, and the randomness breaks the
    lockstep that produced the starvation.

    The clock and the sleep are injectable because the msvcrt branch cannot run
    anywhere but Windows, and the retry policy is the part worth testing.
    """
    deadline = monotonic() + timeout
    delay = _LOCK_FIRST_DELAY
    attempts = 0
    while True:
        attempts += 1
        if try_lock():
            return
        if monotonic() >= deadline:
            raise CacheLockError(
                f"Could not lock {describe} within {timeout:g}s after {attempts} attempts. "
                "Another datosgobdo-mcp process is holding it — retry, or give this instance "
                "its own DATOSGOBDO_CACHE_DIR so it does not share one."
            )
        # Half the delay is fixed and half is random: the fixed part backs off,
        # the random part keeps two waiters from retrying in step forever.
        sleep(delay * (0.5 + jitter()))
        delay = min(delay * 2, _LOCK_MAX_DELAY)


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
            # Explicit encoding, not the platform default: on Windows that
            # default is the ANSI codepage (cp1252 on a Spanish install). The
            # index is pure ASCII today only because json.dumps defaults to
            # ensure_ascii=True, so this is one flag away from an index that
            # corrupts on one platform and nowhere else.
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    @contextmanager
    def _lock(self) -> Generator[None]:
        """Cross-process exclusive lock for index/eviction mutations. Two server
        instances (or concurrent HTTP requests in hosted mode) share the cache
        dir; without this, eviction and finalize race.

        POSIX uses flock, which blocks and queues. Windows has no equivalent:
        `msvcrt.locking` locks one byte at offset 0, and its blocking mode
        starves waiters, so this takes the non-blocking mode and does its own
        backoff (see `_acquire_with_backoff`). Failing after ten seconds is
        deliberate — a JSON write should never take that long, and waiting
        forever on a dead holder is worse — but it fails as CacheLockError, with
        something the caller can act on, rather than as a bare
        `OSError: [Errno 36] Resource deadlock avoided` from deep in the
        standard library. Only when neither module exists does this degrade to a
        per-process no-op.
        """
        if fcntl is None and msvcrt is None:  # pragma: no cover — unknown platform
            yield
            return
        lock_file = self.cache_dir / ".lock"
        with open(lock_file, "w") as f:
            if fcntl is not None:
                fcntl.flock(f, fcntl.LOCK_EX)
            else:  # pragma: no cover — Windows only
                _acquire_with_backoff(lambda: _try_msvcrt_lock(f), str(self.index_path))
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(f, fcntl.LOCK_UN)
                else:  # pragma: no cover — Windows only
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)

    @contextmanager
    def _index_if_lockable(self, what: str) -> Generator[dict[str, dict] | None]:
        """`_locked_index`, but yields None instead of raising on lock timeout.

        For the mutations that are bookkeeping rather than the answer. A caller
        who has already downloaded and parsed a file correctly should not be told
        the operation failed because another process held the index: the Parquet
        on disk is still valid and still found by key. What is lost is the URL
        mapping and the access time, which costs one re-download later — a worse
        outcome to hide than to report, and a much worse one to raise on.
        """
        try:
            with self._locked_index() as index:
                yield index
        except CacheLockError as e:
            logger.warning("%s skipped: %s", what, e)
            yield None

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
            tmp.write_text(json.dumps(self._index, indent=2), encoding="utf-8")
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
        if not p.exists():
            return
        # Best effort by design: the Parquet is written and correct by the time
        # this runs, so a lock this cannot get must not turn a good answer into a
        # failed tool call. What is lost is the URL mapping, so the next call for
        # the same URL downloads again — logged, not hidden.
        with self._index_if_lockable(f"cache bookkeeping for {key}") as index:
            if index is None:
                return
            try:
                size = p.stat().st_size
            except OSError:
                # Another process evicted it between the check above and here.
                # Recording a size for a file that is gone would put a name in the
                # index that no `get` can honour, and raising would fail a call
                # whose answer was already computed.
                logger.info("entry %s vanished before it could be recorded", key)
                return
            index.setdefault(key, {})["bytes"] = size
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
        # frequent index mutation there is, and therefore the one most likely to
        # meet contention. Losing an access timestamp costs LRU accuracy, not an
        # answer, so it degrades rather than raises.
        with self._index_if_lockable(f"access time for {key}") as index:
            if index is not None:
                index.setdefault(key, {})["accessed_at"] = time.time()

    def evict_to_fit(self, max_bytes: int) -> None:
        """LRU eviction until total cache size <= max_bytes.

        Raises CacheLockError if the index cannot be locked: unlike the
        bookkeeping paths, silently not enforcing a size ceiling is how a cache
        grows without bound.
        """
        with self._locked_index():
            self._evict_to_fit_locked(max_bytes)

    def _orphans(self) -> list[tuple[str, float, int]]:
        """Parquet files on disk with no index entry, as eviction candidates.

        They exist because `finalize` is best-effort: when it cannot take the
        lock it logs and returns, leaving a valid Parquet that the index never
        heard about. A crash between writing the file and recording it does the
        same, on any platform.

        Counting them matters because the alternative is a ceiling that is not a
        ceiling. Eviction walked `self._index`, so an unrecorded file was invisible
        to it and `stats()` under-reported the total — the cache could pass 1 GB
        with `get_cache_stats` reporting less and nothing ever reclaiming the
        difference. They sort as the oldest possible entries: an orphan has no
        access time, and nothing is going to serve it, since a hit needs the index.

        **The grace period is the whole safety argument.** A write in progress
        looks exactly like an orphan: `put_path` records the entry in memory only,
        so between the write and the `finalize` that persists it, another process
        reading `_index.json` sees a Parquet nobody claims. Without the grace
        period this method deletes files that are being written *right now* — the
        parallel-eviction test caught it doing precisely that, with three
        processes and a `FileNotFoundError` inside a peer's `finalize`. A minute
        is far longer than a download-and-convert holds the file unrecorded, and
        an abandoned orphan simply waits one more pass.
        """
        out: list[tuple[str, float, int]] = []
        cutoff = time.time() - _ORPHAN_GRACE_SECONDS
        try:
            for p in self.cache_dir.glob("*.parquet"):
                key = p.stem
                if key in self._index:
                    continue
                try:
                    stat = p.stat()
                except OSError:  # pragma: no cover — vanished mid-scan
                    continue
                if stat.st_mtime > cutoff:
                    continue  # someone may be writing it as we look
                out.append((key, 0.0, stat.st_size))
        except OSError:  # pragma: no cover — unreadable cache dir
            return []
        return out

    def _evict_to_fit_locked(self, max_bytes: int) -> None:
        entries = [
            (k, v.get("accessed_at", 0), v.get("bytes", 0))
            for k, v in self._index.items()
            if self._entry_path(k).exists()
        ]
        entries += self._orphans()
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
        orphans = self._orphans()
        return {
            "cache_dir": str(self.cache_dir),
            "entries": len(entries),
            # Disk usage, not index usage. A Parquet whose `finalize` could not
            # take the lock is still occupying the disk and still counts against
            # `max_bytes`; reporting only what the index knows about made this
            # number smaller than `du` and the ceiling unenforceable.
            "total_bytes": sum(s for _, s in entries) + sum(b for _, _, b in orphans),
            "orphan_entries": len(orphans),
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

"""Two processes, one cache directory.

The cross-process lock has existed since 0.7.x and gained a Windows arm in
0.11.1, and until now nothing had ever contended it: every test held the lock
alone, so it could have been a no-op and the suite would not have noticed. A
hosted deployment runs several workers over one cache; so does a person with
Claude Desktop and a terminal session open at once.

What can actually break is the index. It is a single JSON file rewritten on
every finalize, touch and eviction, so two unsynchronised writers lose entries
— which does not raise anything, it just silently forgets that a Parquet on
disk belongs to a URL, and the next call re-downloads a file it already had.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import time
from pathlib import Path

from datosgobdo_mcp import cache as cache_mod

WRITERS = 4
ENTRIES_EACH = 15


def _write_entries(cache_dir: str, worker: int) -> None:
    """One process's share of the writes, through the real cache object."""
    cache = cache_mod.LocalDiskCache(cache_dir=Path(cache_dir))
    for i in range(ENTRIES_EACH):
        key = f"w{worker}_e{i}"
        path = cache.put_path(key)
        path.write_bytes(b"parquet-ish payload")
        cache.finalize(key, url=f"https://example.test/w{worker}/{i}.csv")


def test_parallel_writers_do_not_lose_index_entries(tmp_path):
    """Every entry written by every process must survive in the index.

    Without the lock this is exactly the case that loses rows: each process
    holds its own in-memory copy of the index, and the last writer to finish
    overwrites whatever the others added.
    """
    cache_dir = tmp_path / "shared"
    cache_dir.mkdir()
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_write_entries, args=(str(cache_dir), w)) for w in range(WRITERS)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0, "a writer process failed"

    index = json.loads((cache_dir / cache_mod.INDEX_FILENAME).read_text())
    expected = {f"w{w}_e{i}" for w in range(WRITERS) for i in range(ENTRIES_EACH)}
    missing = expected - set(index)
    assert not missing, f"{len(missing)} of {len(expected)} entries lost from the index"

    # And the index is still valid JSON describing files that exist.
    for key in expected:
        assert (cache_dir / f"{key}.parquet").is_file()


def _evict_repeatedly(cache_dir: str, worker: int) -> None:
    cache = cache_mod.LocalDiskCache(cache_dir=Path(cache_dir), max_bytes=200)
    for i in range(10):
        key = f"ev{worker}_{i}"
        path = cache.put_path(key)
        path.write_bytes(b"x" * 64)
        cache.finalize(key, url=f"https://example.test/ev{worker}/{i}.csv")
        cache.evict_to_fit(200)


def test_parallel_eviction_leaves_a_consistent_index(tmp_path):
    """Eviction deletes files and rewrites the index at the same time, which is
    the most dangerous pair of operations in this module. Under contention the
    index must never name a file that is gone, and must never be corrupt."""
    cache_dir = tmp_path / "evicting"
    cache_dir.mkdir()
    ctx = mp.get_context("spawn")
    procs = [ctx.Process(target=_evict_repeatedly, args=(str(cache_dir), w)) for w in range(3)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0

    raw = (cache_dir / cache_mod.INDEX_FILENAME).read_text()
    index = json.loads(raw)  # must not be truncated or half-written
    orphans = [k for k in index if not (cache_dir / f"{k}.parquet").is_file()]
    assert not orphans, f"index names {len(orphans)} files that are not on disk"

    total = sum((cache_dir / f"{k}.parquet").stat().st_size for k in index)
    # The cap is honoured within one entry's slack: a writer may add its own
    # entry after another has just evicted down to the limit.
    assert total <= 200 + 64


def _read_while_writing(cache_dir: str) -> None:
    cache = cache_mod.LocalDiskCache(cache_dir=Path(cache_dir))
    for _ in range(40):
        cache.get_by_url("https://example.test/w0/0.csv")
        cache.stats()
        time.sleep(0.005)


def test_a_reader_never_sees_a_broken_index(tmp_path):
    """The index is replaced with os.replace, which is atomic on POSIX and on
    Windows, so a reader sees either the old file or the new one. This asserts
    the property rather than the implementation: a reader running throughout a
    writer's work must never raise."""
    cache_dir = tmp_path / "readwrite"
    cache_dir.mkdir()
    ctx = mp.get_context("spawn")
    reader = ctx.Process(target=_read_while_writing, args=(str(cache_dir),))
    writer = ctx.Process(target=_write_entries, args=(str(cache_dir), 0))
    reader.start()
    writer.start()
    for p in (reader, writer):
        p.join(timeout=60)
        assert p.exitcode == 0, "reader or writer crashed on a partially written index"

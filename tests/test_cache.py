"""Unit tests for cache.py — LocalDiskCache."""

from __future__ import annotations

import time

import pytest

from datosgobdo_mcp import cache as cache_mod


def test_build_cache_key_url_changes_key():
    k1 = cache_mod.build_cache_key("https://a/x.csv", etag="v1")
    k2 = cache_mod.build_cache_key("https://b/x.csv", etag="v1")
    assert k1 != k2


def test_build_cache_key_etag_changes_key():
    k1 = cache_mod.build_cache_key("https://a/x.csv", etag="v1")
    k2 = cache_mod.build_cache_key("https://a/x.csv", etag="v2")
    assert k1 != k2


def test_build_cache_key_no_version_tag_stable():
    k1 = cache_mod.build_cache_key("https://a/x.csv", etag=None, last_modified=None)
    k2 = cache_mod.build_cache_key("https://a/x.csv", etag=None, last_modified=None)
    assert k1 == k2


def test_same_source_but_a_different_parser_is_a_different_key(monkeypatch):
    """An unchanged file parsed by changed code is not the same artifact.

    Keying on URL + ETag alone let 0.7.5's encoding fix ship without evicting
    the ten Parquets 0.7.4 had written wrong; they kept being served as valid.
    """
    before = cache_mod.build_cache_key("https://a/x.csv", etag="unchanged")
    monkeypatch.setattr(cache_mod, "_parser_build_cached", None)
    monkeypatch.setattr(cache_mod, "__version__", "99.0.0")
    after = cache_mod.build_cache_key("https://a/x.csv", etag="unchanged")
    monkeypatch.setattr(cache_mod, "_parser_build_cached", None)
    assert before != after


def test_parser_build_tracks_the_duckdb_version(monkeypatch):
    """DuckDB's sniffer picks the column types, so its version is ours too.

    A `uv sync` that upgrades DuckDB changes the Parquet we write without
    changing a line of this package.
    """
    import duckdb

    monkeypatch.setattr(cache_mod, "_parser_build_cached", None)
    baseline = cache_mod._parser_build()
    monkeypatch.setattr(cache_mod, "_parser_build_cached", None)
    monkeypatch.setattr(duckdb, "__version__", "0.0.0-not-a-real-release")
    upgraded = cache_mod._parser_build()
    monkeypatch.setattr(cache_mod, "_parser_build_cached", None)
    assert baseline != upgraded


def test_get_by_url_refuses_an_entry_written_by_another_parser(tmp_path, monkeypatch):
    """The warm path never computes a key, so the key alone cannot protect it.

    `ensure_cached` matches on URL and returns before the HEAD request. Without
    this check the poisoned entry is served forever, whatever the key says.
    """
    c = cache_mod.LocalDiskCache(cache_dir=tmp_path)
    url = "https://example.test/data.csv"
    key = cache_mod.build_cache_key(url, etag="v1")
    c.put_path(key).write_bytes(b"parquet written by the old parser")
    c.finalize(key, url=url)
    assert c.get_by_url(url) is not None

    monkeypatch.setattr(cache_mod, "_parser_build_cached", None)
    monkeypatch.setattr(cache_mod, "__version__", "99.0.0")
    try:
        assert c.get_by_url(url) is None
    finally:
        monkeypatch.setattr(cache_mod, "_parser_build_cached", None)


def test_get_by_url_ignores_entries_predating_the_build_stamp(tmp_path):
    """Entries in a cache dir written before this version carry no build.

    We cannot claim they match, so they are stale — which also evicts any
    0.7.4 stragglers still sitting in a user's cache.
    """
    c = cache_mod.LocalDiskCache(cache_dir=tmp_path)
    url = "https://example.test/legacy.csv"
    key = "legacy_key"
    c.put_path(key).write_bytes(b"parquet")
    c.finalize(key, url=url)
    del c._index[key]["build"]  # as an older release left it
    assert c.get_by_url(url) is None


def test_stats_counts_entries_no_longer_servable(tmp_path):
    c = cache_mod.LocalDiskCache(cache_dir=tmp_path)
    c.put_path("k1").write_bytes(b"parquet")
    c.finalize("k1", url="https://example.test/a.csv")
    c._index["k1"]["build"] = "deadbeef"
    stats = c.stats()
    assert stats["stale_entries"] == 1
    assert stats["parser_build"] == cache_mod._parser_build()


def test_localdiskcache_put_and_get(tmp_path):
    c = cache_mod.LocalDiskCache(cache_dir=tmp_path)
    key = "abc123"
    path = c.put_path(key)
    path.write_bytes(b"hello")
    c.finalize(key)

    cached = c.get(key)
    assert cached is not None
    assert cached.read_bytes() == b"hello"


def test_localdiskcache_get_missing_returns_none(tmp_path):
    c = cache_mod.LocalDiskCache(cache_dir=tmp_path)
    assert c.get("nonexistent") is None


def test_localdiskcache_lru_eviction(tmp_path):
    # Tight 5 KB cap so writes trigger eviction.
    c = cache_mod.LocalDiskCache(cache_dir=tmp_path, max_bytes=5_000)

    for i in range(3):
        key = f"k{i}"
        p = c.put_path(key)
        p.write_bytes(b"x" * 2_000)  # 2 KB each
        c.finalize(key)
        # Stagger access times so LRU has a stable order.
        time.sleep(0.01)
        c.touch(key)

    # After 3 × 2 KB = 6 KB and a 5 KB cap, oldest entry should be gone.
    stats = c.stats()
    assert stats["total_bytes"] <= 5_000
    # k0 was the oldest; should be evicted.
    assert c.get("k0") is None
    # k2 is newest and should survive.
    assert c.get("k2") is not None


def test_localdiskcache_clear_removes_entries(tmp_path):
    c = cache_mod.LocalDiskCache(cache_dir=tmp_path)
    for k in ("a", "b", "c"):
        p = c.put_path(k)
        p.write_bytes(b"x")
        c.finalize(k)
    removed = c.clear()
    assert removed == 3
    assert c.stats()["entries"] == 0


def test_localdiskcache_touch_updates_access_time(tmp_path):
    c = cache_mod.LocalDiskCache(cache_dir=tmp_path)
    key = "k"
    p = c.put_path(key)
    p.write_bytes(b"x")
    c.finalize(key)

    before = c._index[key]["accessed_at"]
    time.sleep(0.05)
    c.touch(key)
    after = c._index[key]["accessed_at"]
    assert after > before


def test_get_cache_respects_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("DATOSGOBDO_CACHE_DIR", str(tmp_path / "custom"))
    monkeypatch.setenv("DATOSGOBDO_CACHE_MAX_BYTES", "12345")
    cache_mod._singleton = None
    c = cache_mod.get_cache()
    assert c.cache_dir == tmp_path / "custom"
    assert c.max_bytes == 12345
    cache_mod._singleton = None


def test_get_by_url_returns_none_before_any_entry(tmp_path):
    c = cache_mod.LocalDiskCache(cache_dir=tmp_path)
    assert c.get_by_url("https://example.test/data.csv") is None


def test_get_by_url_returns_path_after_finalize(tmp_path):
    c = cache_mod.LocalDiskCache(cache_dir=tmp_path)
    key = "testkey123"
    p = c.put_path(key)
    p.write_bytes(b"parquet data")
    c.finalize(key, url="https://example.test/data.csv")
    result = c.get_by_url("https://example.test/data.csv")
    assert result is not None
    path, returned_key = result
    assert path == p
    assert returned_key == key


def test_get_by_url_returns_none_for_unknown_url(tmp_path):
    c = cache_mod.LocalDiskCache(cache_dir=tmp_path)
    key = "testkey456"
    p = c.put_path(key)
    p.write_bytes(b"parquet data")
    c.finalize(key, url="https://example.test/data.csv")
    assert c.get_by_url("https://example.test/OTHER.csv") is None


def test_get_by_url_returns_none_if_file_deleted(tmp_path):
    c = cache_mod.LocalDiskCache(cache_dir=tmp_path)
    key = "testkey789"
    p = c.put_path(key)
    p.write_bytes(b"parquet data")
    c.finalize(key, url="https://example.test/data.csv")
    p.unlink()
    assert c.get_by_url("https://example.test/data.csv") is None


def test_get_by_url_returns_most_recently_accessed(tmp_path):
    """When two keys share the same URL, get_by_url returns the one accessed more recently."""
    c = cache_mod.LocalDiskCache(cache_dir=tmp_path)

    # Create two entries with the same URL but different keys.
    key_old = "old_key_aaa"
    key_new = "new_key_bbb"
    p_old = c.put_path(key_old)
    p_old.write_bytes(b"old parquet")
    c.finalize(key_old, url="https://example.test/shared.csv")

    time.sleep(0.01)  # ensure distinct timestamps

    p_new = c.put_path(key_new)
    p_new.write_bytes(b"new parquet")
    c.finalize(key_new, url="https://example.test/shared.csv")

    result = c.get_by_url("https://example.test/shared.csv")
    assert result is not None
    _, returned_key = result
    assert returned_key == key_new  # newer entry wins


# ─── Cross-process lock on Windows (msvcrt) ──────────────────────────────────
# Real Windows never runs in this suite, so the lock is exercised through a
# fake module — the same technique test_gcp.py uses for the google SDKs. What
# this verifies is the contract: when fcntl is absent and msvcrt is present,
# every index mutation locks byte 0 and unlocks it, even if the mutation
# raises. The real msvcrt semantics stay a documented risk until the suite
# runs on a Windows machine.


class _FakeMsvcrt:
    """Stands in for the module that only exists on Windows.

    `busy` counts how many acquisition attempts should fail the way a real
    contended lock does — `OSError` with errno 36 — before one succeeds.
    """

    LK_NBLCK = 0
    LK_UNLCK = 1

    def __init__(self, busy: int = 0):
        self.calls = []
        self._busy = busy

    def locking(self, fd, mode, nbytes):
        assert isinstance(fd, int)
        assert nbytes == 1
        if mode != self.LK_NBLCK:
            self.calls.append("unlock")
            return
        if self._busy > 0:
            self._busy -= 1
            self.calls.append("busy")
            raise OSError(36, "Resource deadlock avoided")
        self.calls.append("lock")


def test_windows_lock_wraps_mutations(tmp_path, monkeypatch):
    fake = _FakeMsvcrt()
    monkeypatch.setattr(cache_mod, "fcntl", None)
    monkeypatch.setattr(cache_mod, "msvcrt", fake)
    c = cache_mod.LocalDiskCache(cache_dir=tmp_path)
    key = "winlock_key"
    p = c.put_path(key)
    p.write_bytes(b"parquet data")
    c.finalize(key, url="https://example.test/win.csv")
    assert fake.calls == ["lock", "unlock"]
    c.evict_to_fit(0)
    assert fake.calls == ["lock", "unlock", "lock", "unlock"]


def test_windows_lock_releases_on_error(tmp_path, monkeypatch):
    fake = _FakeMsvcrt()
    monkeypatch.setattr(cache_mod, "fcntl", None)
    monkeypatch.setattr(cache_mod, "msvcrt", fake)
    c = cache_mod.LocalDiskCache(cache_dir=tmp_path)

    def boom(_max):
        raise RuntimeError("mutation failed")

    monkeypatch.setattr(c, "_evict_to_fit_locked", boom)
    try:
        c.evict_to_fit(0)
    except RuntimeError:
        pass
    assert fake.calls == ["lock", "unlock"]


def test_no_lock_modules_degrades_to_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_mod, "fcntl", None)
    monkeypatch.setattr(cache_mod, "msvcrt", None)
    c = cache_mod.LocalDiskCache(cache_dir=tmp_path)
    key = "nolock_key"
    p = c.put_path(key)
    p.write_bytes(b"parquet data")
    c.finalize(key, url="https://example.test/nolock.csv")  # must not raise
    assert c.get_by_url("https://example.test/nolock.csv") is not None


# ─── Lock contention: the retry policy, and what degrades when it runs out ────
#
# Windows testing on real hardware produced the numbers these tests encode. Two
# writers doing 200 entries each: no lost entries, but one wait of 6.2 s against
# msvcrt's own ~10 s ceiling. Four writers: two processes died with
# `OSError: [Errno 36] Resource deadlock avoided` raised straight out of
# finalize(). The index survived both — os.replace does its job — so what needed
# fixing was the acquisition, not the atomicity.
#
# The msvcrt branch cannot execute here, so the retry policy is written as a
# platform-agnostic function with the clock and the sleep injected. That part is
# tested properly; the four-line Windows shim around it is not, and needs a
# Windows run before it can be called verified.


def test_backoff_returns_as_soon_as_the_lock_is_free():
    slept = []
    attempts = {"n": 0}

    def try_lock():
        attempts["n"] += 1
        return attempts["n"] > 3  # busy three times, then free

    cache_mod._acquire_with_backoff(
        try_lock, "the index", sleep=slept.append, monotonic=lambda: 0.0, jitter=lambda: 0.5
    )
    assert attempts["n"] == 4
    assert len(slept) == 3


def test_backoff_grows_the_delay_but_caps_it():
    """Retries in milliseconds, not msvcrt's whole seconds, and never unbounded."""
    slept = []
    cache_mod._acquire_with_backoff(
        lambda: len(slept) >= 12,
        "the index",
        sleep=slept.append,
        monotonic=lambda: 0.0,
        jitter=lambda: 0.5,
    )
    assert slept == sorted(slept), slept
    assert slept[0] < 0.05
    assert max(slept) <= cache_mod._LOCK_MAX_DELAY
    # 12 retries under the cap means the whole sequence is well inside the
    # timeout — the old policy spent one full second per retry.
    assert sum(slept) < cache_mod.LOCK_TIMEOUT_SECONDS


def test_backoff_jitters_so_two_waiters_do_not_retry_in_lockstep():
    """The starvation was structural: msvcrt.LK_LOCK does not queue, so two
    processes retrying on the same rhythm can hand the lock back and forth while
    a third waits. Half of each delay is random for exactly this reason."""
    runs = []
    for jitter_value in (0.0, 1.0):
        slept = []
        cache_mod._acquire_with_backoff(
            lambda: len(slept) >= 4,
            "the index",
            sleep=slept.append,
            monotonic=lambda: 0.0,
            jitter=lambda: jitter_value,
        )
        runs.append(slept)
    assert runs[0] != runs[1]
    assert all(a < b for a, b in zip(runs[0], runs[1]))


def test_backoff_gives_up_with_something_the_caller_can_act_on():
    """The failure mode is deliberate — a JSON write should never take ten
    seconds — but `OSError: [Errno 36] Resource deadlock avoided` from inside the
    standard library tells the caller nothing."""
    clock = {"t": 0.0}

    def monotonic():
        clock["t"] += 1.0
        return clock["t"]

    with pytest.raises(cache_mod.CacheLockError) as excinfo:
        cache_mod._acquire_with_backoff(
            lambda: False,
            "/tmp/cache/_index.json",
            sleep=lambda _s: None,
            monotonic=monotonic,
            jitter=lambda: 0.5,
        )
    message = str(excinfo.value)
    assert "/tmp/cache/_index.json" in message
    assert "DATOSGOBDO_CACHE_DIR" in message
    assert "attempts" in message


def test_a_contended_lock_is_waited_for_not_failed(tmp_path, monkeypatch):
    fake = _FakeMsvcrt(busy=3)
    monkeypatch.setattr(cache_mod, "fcntl", None)
    monkeypatch.setattr(cache_mod, "msvcrt", fake)
    monkeypatch.setattr(cache_mod.time, "sleep", lambda _s: None)
    c = cache_mod.LocalDiskCache(cache_dir=tmp_path)
    key = "contended"
    c.put_path(key).write_bytes(b"parquet data")
    c.finalize(key, url="https://example.test/contended.csv")
    assert fake.calls == ["busy", "busy", "busy", "lock", "unlock"]
    assert c.get_by_url("https://example.test/contended.csv") is not None


def test_finalize_keeps_the_parquet_when_the_lock_never_comes(tmp_path, monkeypatch, caplog):
    """The file is downloaded, parsed and correct by the time finalize runs. A
    lock it cannot get must not turn that into a failed tool call — the cost is
    one re-download later, which is logged rather than hidden."""
    fake = _FakeMsvcrt(busy=10_000)
    monkeypatch.setattr(cache_mod, "fcntl", None)
    monkeypatch.setattr(cache_mod, "msvcrt", fake)
    monkeypatch.setattr(cache_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(cache_mod, "LOCK_TIMEOUT_SECONDS", 0.0)
    c = cache_mod.LocalDiskCache(cache_dir=tmp_path)
    key = "orphan_risk"
    parquet = c.put_path(key)
    parquet.write_bytes(b"parquet data")
    with caplog.at_level("WARNING", logger="datosgobdo_mcp.cache"):
        c.finalize(key, url="https://example.test/orphan.csv")  # must not raise
    assert parquet.exists(), "the parsed data must survive a lock timeout"
    assert c.get(key) is not None, "and stay reachable by key"
    assert "cache bookkeeping" in caplog.text
    assert "Could not lock" in caplog.text


def test_touch_degrades_too_since_it_runs_on_every_warm_hit(tmp_path, monkeypatch):
    fake = _FakeMsvcrt(busy=10_000)
    monkeypatch.setattr(cache_mod, "fcntl", None)
    monkeypatch.setattr(cache_mod, "msvcrt", fake)
    monkeypatch.setattr(cache_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(cache_mod, "LOCK_TIMEOUT_SECONDS", 0.0)
    c = cache_mod.LocalDiskCache(cache_dir=tmp_path)
    c.touch("whatever")  # must not raise


def test_eviction_still_raises_because_an_unbounded_cache_is_worse(tmp_path, monkeypatch):
    """Not everything should degrade: silently declining to enforce the size
    ceiling is how a cache eats a disk."""
    fake = _FakeMsvcrt(busy=10_000)
    monkeypatch.setattr(cache_mod, "fcntl", None)
    monkeypatch.setattr(cache_mod, "msvcrt", fake)
    monkeypatch.setattr(cache_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(cache_mod, "LOCK_TIMEOUT_SECONDS", 0.0)
    c = cache_mod.LocalDiskCache(cache_dir=tmp_path)
    with pytest.raises(cache_mod.CacheLockError):
        c.evict_to_fit(0)


def test_the_index_is_written_as_utf8_not_the_platform_codepage(tmp_path):
    """Explicit encoding, verified by round-tripping non-ASCII provenance. The
    index is pure ASCII today only because json.dumps defaults to
    ensure_ascii=True; on Windows the default text encoding is cp1252, so this
    was one flag away from corrupting on one platform only."""
    c = cache_mod.LocalDiskCache(cache_dir=tmp_path)
    key = "acentos"
    c.put_path(key).write_bytes(b"parquet data")
    c.finalize(
        key,
        url="https://example.test/dirección.csv",
        provenance={"note": "Dirección General de Migración — Año 2024 (ñ, Ó)"},
    )
    raw = (tmp_path / cache_mod.INDEX_FILENAME).read_bytes()
    assert raw.decode("utf-8"), "must be valid UTF-8 regardless of platform default"
    fresh = cache_mod.LocalDiskCache(cache_dir=tmp_path)
    assert fresh.provenance(key)["note"] == "Dirección General de Migración — Año 2024 (ñ, Ó)"


# ─── Parquet the index never heard about ──────────────────────────────────────


def _abandon(path, seconds: float = 3600.0) -> None:
    """Backdate a file past the orphan grace period.

    Writing it and expecting it to be reclaimed at once would test a behaviour
    that must not exist: inside the grace window a file may be a live write.
    """
    import os

    old = time.time() - seconds
    os.utime(path, (old, old))


def test_an_unrecorded_parquet_still_counts_against_the_ceiling(tmp_path):
    """`finalize` degrades when it cannot take the lock, which leaves a valid
    Parquet with no index entry. Eviction walked the index alone, so such a file
    was invisible to it: the cache could pass max_bytes and never come back
    down."""
    c = cache_mod.LocalDiskCache(cache_dir=tmp_path, max_bytes=1_000)
    for name in ("orphan-a.parquet", "orphan-b.parquet"):
        (tmp_path / name).write_bytes(b"x" * 800)
        _abandon(tmp_path / name)

    assert c.stats()["total_bytes"] == 1_600, "disk usage, not index usage"
    assert c.stats()["orphan_entries"] == 2

    c.evict_to_fit(1_000)

    remaining = sorted(p.name for p in tmp_path.glob("*.parquet"))
    assert len(remaining) == 1, "one of the two had to go to get under 1,000 bytes"
    assert c.stats()["total_bytes"] == 800


def test_an_orphan_is_evicted_before_a_recorded_entry(tmp_path):
    """An orphan has no access time and cannot be served — a hit needs the index
    — so it is the oldest thing in the cache by definition."""
    c = cache_mod.LocalDiskCache(cache_dir=tmp_path, max_bytes=10_000)
    c.put_path("recorded").write_bytes(b"x" * 500)
    c.finalize("recorded", url="https://example.test/a.csv")
    (tmp_path / "orphan.parquet").write_bytes(b"x" * 500)
    _abandon(tmp_path / "orphan.parquet")

    c.evict_to_fit(500)

    assert (tmp_path / "recorded.parquet").exists()
    assert not (tmp_path / "orphan.parquet").exists()


def test_a_write_in_progress_is_not_mistaken_for_an_orphan(tmp_path):
    """The regression this grace period exists to prevent, and the reason it is
    not optional. `put_path` records only in memory, so between the write and the
    `finalize` that persists it, every other process sees a Parquet nobody claims.
    Reclaiming it deletes a file that is being written right now — the
    parallel-eviction test caught exactly that, as a `FileNotFoundError` inside a
    peer's `finalize`."""
    writer = cache_mod.LocalDiskCache(cache_dir=tmp_path, max_bytes=100)
    writer.put_path("in-flight").write_bytes(b"x" * 4_000)  # well over the cap

    # A second process: same directory, and the index on disk names nothing.
    other = cache_mod.LocalDiskCache(cache_dir=tmp_path, max_bytes=100)
    assert other.stats()["orphan_entries"] == 0, "too young to be abandoned"
    other.evict_to_fit(100)

    assert (tmp_path / "in-flight.parquet").exists(), "a live write must survive"
    writer.finalize("in-flight", url="https://example.test/a.csv")


def test_a_crash_before_finalize_does_not_leak_disk_forever(tmp_path):
    """The same hole with no Windows in the picture. `put_path` records the entry
    in memory only — nothing reaches `_index.json` until a `finalize` saves it —
    so a process that dies after writing the Parquet leaves a file the next
    process has no record of at all."""
    c = cache_mod.LocalDiskCache(cache_dir=tmp_path, max_bytes=100)
    c.put_path("half-done").write_bytes(b"x" * 400)
    del c  # the process goes away before finalize
    _abandon(tmp_path / "half-done.parquet")

    fresh = cache_mod.LocalDiskCache(cache_dir=tmp_path, max_bytes=100)
    assert "half-done" not in fresh._index, "nothing was ever persisted"
    assert fresh.stats()["orphan_entries"] == 1
    assert fresh.stats()["total_bytes"] == 400, "the disk holds it either way"

    fresh.evict_to_fit(100)
    assert not (tmp_path / "half-done.parquet").exists()


def test_a_contended_clear_is_reported_not_raised(tmp_path, monkeypatch):
    """The lock used to fail as `OSError`, which `_ENVELOPE_ERRORS` catches, so a
    contended index came back as a readable error. Naming the failure
    `CacheLockError` took it out of that tuple — and `clear_cache` is synchronous,
    so `_tool_envelope` never covered it either. The clearer message would have
    reached the client as a traceback."""
    from datosgobdo_mcp import analytics

    fake = _FakeMsvcrt(busy=10_000)
    monkeypatch.setattr(cache_mod, "fcntl", None)
    monkeypatch.setattr(cache_mod, "msvcrt", fake)
    monkeypatch.setattr(cache_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(cache_mod, "LOCK_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(cache_mod, "_singleton", cache_mod.LocalDiskCache(cache_dir=tmp_path))

    result = analytics.clear_cache()

    assert "error" in result, "must be an envelope, not an exception"
    assert "DATOSGOBDO_CACHE_DIR" in result["error"]
    assert "removed_entries" not in result


def test_the_lock_error_is_in_the_envelope_tuple():
    """The async tools reach the lock through `ensure_cached`; the tuple is what
    keeps that path returning a sentence instead of a stack trace."""
    from datosgobdo_mcp import analytics

    assert cache_mod.CacheLockError in analytics._ENVELOPE_ERRORS

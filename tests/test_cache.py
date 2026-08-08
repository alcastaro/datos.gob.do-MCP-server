"""Unit tests for cache.py — LocalDiskCache."""

from __future__ import annotations

import time

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

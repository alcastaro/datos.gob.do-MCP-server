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

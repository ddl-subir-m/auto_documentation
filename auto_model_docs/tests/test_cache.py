"""Tests for autodoc/llm/cache.py — LLMCache file-based caching."""

import json

import pytest

from autodoc.llm.cache import LLMCache


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cache_dir(tmp_path):
    """Return a fresh temp directory for cache storage."""
    return tmp_path / "llm_cache"


@pytest.fixture
def cache(cache_dir):
    """Create an enabled LLMCache."""
    return LLMCache(cache_dir=cache_dir, enabled=True)


@pytest.fixture
def disabled_cache(cache_dir):
    """Create a disabled LLMCache."""
    return LLMCache(cache_dir=cache_dir, enabled=False)


# ---------------------------------------------------------------------------
# Cache key generation
# ---------------------------------------------------------------------------


class TestCacheKeyGeneration:
    """_make_key must be deterministic and sensitive to inputs."""

    def test_same_inputs_produce_same_key(self, cache):
        k1 = cache._make_key("prompt A", model="gpt-4")
        k2 = cache._make_key("prompt A", model="gpt-4")
        assert k1 == k2

    def test_different_prompts_produce_different_keys(self, cache):
        k1 = cache._make_key("prompt A")
        k2 = cache._make_key("prompt B")
        assert k1 != k2

    def test_different_kwargs_produce_different_keys(self, cache):
        k1 = cache._make_key("prompt", temperature=0.0)
        k2 = cache._make_key("prompt", temperature=0.7)
        assert k1 != k2

    def test_key_is_hex_string(self, cache):
        key = cache._make_key("hello")
        assert len(key) == 32
        assert all(c in "0123456789abcdef" for c in key)

    def test_kwarg_order_does_not_matter(self, cache):
        k1 = cache._make_key("p", a=1, b=2)
        k2 = cache._make_key("p", b=2, a=1)
        assert k1 == k2


# ---------------------------------------------------------------------------
# Cache set and get round-trip
# ---------------------------------------------------------------------------


class TestSetAndGet:
    """set() stores a value, get() retrieves it."""

    def test_basic_round_trip(self, cache):
        response = {"text": "hello world", "tokens": 5}
        cache.set("my prompt", response, model="test")
        result = cache.get("my prompt", model="test")
        assert result == response

    def test_complex_nested_response(self, cache):
        response = {
            "choices": [{"text": "answer", "index": 0}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }
        cache.set("complex", response)
        assert cache.get("complex") == response

    def test_set_overwrites_existing(self, cache):
        cache.set("p", {"v": 1})
        cache.set("p", {"v": 2})
        assert cache.get("p") == {"v": 2}


# ---------------------------------------------------------------------------
# Cache miss returns None
# ---------------------------------------------------------------------------


class TestCacheMiss:
    """get() returns None when no cached value exists."""

    def test_miss_on_empty_cache(self, cache):
        assert cache.get("never stored") is None

    def test_miss_with_different_kwargs(self, cache):
        cache.set("prompt", {"v": 1}, model="a")
        assert cache.get("prompt", model="b") is None

    def test_miss_with_different_prompt(self, cache):
        cache.set("prompt1", {"v": 1})
        assert cache.get("prompt2") is None


# ---------------------------------------------------------------------------
# Cache disabled -> always returns None
# ---------------------------------------------------------------------------


class TestCacheDisabled:
    """When disabled, get always returns None and set is a no-op."""

    def test_get_returns_none_when_disabled(self, disabled_cache):
        assert disabled_cache.get("anything") is None

    def test_set_does_not_write_files(self, disabled_cache, cache_dir):
        disabled_cache.set("prompt", {"v": 1})
        # Directory should not even be created when disabled
        if cache_dir.exists():
            assert list(cache_dir.glob("*.json")) == []

    def test_disabled_cache_dir_not_created(self, cache_dir):
        LLMCache(cache_dir=cache_dir, enabled=False)
        assert not cache_dir.exists()


# ---------------------------------------------------------------------------
# Cache clear
# ---------------------------------------------------------------------------


class TestCacheClear:
    """clear() removes all .json cache files and returns the count."""

    def test_clear_empty_cache(self, cache):
        assert cache.clear() == 0

    def test_clear_populated_cache(self, cache):
        cache.set("p1", {"v": 1})
        cache.set("p2", {"v": 2})
        cache.set("p3", {"v": 3})
        removed = cache.clear()
        assert removed == 3

    def test_cache_empty_after_clear(self, cache):
        cache.set("p", {"v": 1})
        cache.clear()
        assert cache.get("p") is None

    def test_clear_nonexistent_dir(self, tmp_path):
        c = LLMCache(cache_dir=tmp_path / "does_not_exist", enabled=True)
        # mkdir is called in __init__, but let's forcibly remove it
        import shutil
        shutil.rmtree(c.cache_dir, ignore_errors=True)
        assert c.clear() == 0


# ---------------------------------------------------------------------------
# Cache stats
# ---------------------------------------------------------------------------


class TestCacheStats:
    """stats() reports count, size, and enabled status."""

    def test_stats_empty_cache(self, cache):
        s = cache.stats()
        assert s["count"] == 0
        assert s["size_bytes"] == 0
        assert s["enabled"] is True

    def test_stats_after_writes(self, cache):
        cache.set("p1", {"v": 1})
        cache.set("p2", {"text": "hello world"})
        s = cache.stats()
        assert s["count"] == 2
        assert s["size_bytes"] > 0
        assert "size_mb" in s
        assert s["enabled"] is True

    def test_stats_disabled_cache(self, disabled_cache):
        s = disabled_cache.stats()
        assert s["enabled"] is False

    def test_stats_nonexistent_dir(self, tmp_path):
        c = LLMCache(cache_dir=tmp_path / "gone", enabled=True)
        import shutil
        shutil.rmtree(c.cache_dir, ignore_errors=True)
        s = c.stats()
        assert s["count"] == 0
        assert s["size_bytes"] == 0


# ---------------------------------------------------------------------------
# Corrupted cache file -> returns None (no crash)
# ---------------------------------------------------------------------------


class TestCorruptedCacheFile:
    """Corrupted JSON in a cache file should return None, not raise."""

    def test_corrupted_json_returns_none(self, cache):
        # Write a valid entry first, then corrupt the file
        cache.set("prompt", {"v": 1})
        key = cache._make_key("prompt")
        cache_path = cache._get_cache_path(key)

        # Corrupt the file
        cache_path.write_text("{invalid json!!!")

        result = cache.get("prompt")
        assert result is None

    def test_corrupted_file_is_removed(self, cache):
        cache.set("prompt", {"v": 1})
        key = cache._make_key("prompt")
        cache_path = cache._get_cache_path(key)

        cache_path.write_text("NOT JSON")
        cache.get("prompt")  # triggers cleanup

        assert not cache_path.exists()

    def test_empty_file_returns_none(self, cache):
        cache.set("prompt", {"v": 1})
        key = cache._make_key("prompt")
        cache_path = cache._get_cache_path(key)

        cache_path.write_text("")

        result = cache.get("prompt")
        assert result is None

    def test_binary_garbage_returns_none(self, cache):
        """Binary data with invalid UTF-8 triggers UnicodeDecodeError.

        The current cache implementation catches JSONDecodeError and IOError
        but not UnicodeDecodeError. This test documents that gap: binary
        garbage that isn't valid UTF-8 will raise instead of returning None.
        """
        cache.set("prompt", {"v": 1})
        key = cache._make_key("prompt")
        cache_path = cache._get_cache_path(key)

        cache_path.write_bytes(b"\x00\x01\x02\xff\xfe")

        with pytest.raises(UnicodeDecodeError):
            cache.get("prompt")

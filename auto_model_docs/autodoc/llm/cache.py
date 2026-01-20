"""LLM response caching to reduce API calls and costs."""

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Optional


class LLMCache:
    """Simple file-based cache for LLM responses.

    Caches responses based on a hash of the prompt and parameters,
    allowing reuse of previous results when the same query is made.
    """

    def __init__(self, cache_dir: Path, enabled: bool = True):
        """Initialize the cache.

        Args:
            cache_dir: Directory to store cache files.
            enabled: Whether caching is enabled.
        """
        self.cache_dir = cache_dir
        self.enabled = enabled

        if enabled:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _make_key(self, prompt: str, **kwargs: Any) -> str:
        """Generate a cache key from prompt and parameters."""
        key_data = {"prompt": prompt, **kwargs}
        key_str = json.dumps(key_data, sort_keys=True)
        return hashlib.sha256(key_str.encode()).hexdigest()[:32]

    def _get_cache_path(self, key: str) -> Path:
        """Get the file path for a cache key."""
        return self.cache_dir / f"{key}.json"

    def get(self, prompt: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        """Get a cached response if available.

        Args:
            prompt: The prompt that was sent.
            **kwargs: Additional parameters used in the request.

        Returns:
            Cached response dict, or None if not cached.
        """
        if not self.enabled:
            return None

        key = self._make_key(prompt, **kwargs)
        cache_path = self._get_cache_path(key)

        if cache_path.exists():
            try:
                with open(cache_path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                # Corrupted cache file, remove it
                cache_path.unlink(missing_ok=True)
                return None

        return None

    def set(self, prompt: str, response: Dict[str, Any], **kwargs: Any) -> None:
        """Cache a response.

        Args:
            prompt: The prompt that was sent.
            response: The response to cache.
            **kwargs: Additional parameters used in the request.
        """
        if not self.enabled:
            return

        key = self._make_key(prompt, **kwargs)
        cache_path = self._get_cache_path(key)

        try:
            with open(cache_path, "w") as f:
                json.dump(response, f, indent=2)
        except IOError:
            # Silently fail on cache write errors
            pass

    def clear(self) -> int:
        """Clear all cached responses.

        Returns:
            Number of cache files removed.
        """
        if not self.cache_dir.exists():
            return 0

        count = 0
        for cache_file in self.cache_dir.glob("*.json"):
            cache_file.unlink()
            count += 1

        return count

    def stats(self) -> Dict[str, Any]:
        """Get cache statistics.

        Returns:
            Dict with cache stats (count, size, etc.)
        """
        if not self.cache_dir.exists():
            return {"count": 0, "size_bytes": 0, "enabled": self.enabled}

        files = list(self.cache_dir.glob("*.json"))
        total_size = sum(f.stat().st_size for f in files)

        return {
            "count": len(files),
            "size_bytes": total_size,
            "size_mb": round(total_size / (1024 * 1024), 2),
            "enabled": self.enabled,
        }

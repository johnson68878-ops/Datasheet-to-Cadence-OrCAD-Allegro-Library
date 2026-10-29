from __future__ import annotations

import hashlib
import re
import unicodedata


def cadence_safe_component(value: str, *, fallback: str = "item", max_length: int = 54) -> str:
    """Create a deterministic ASCII-only path component for Cadence 17.4."""
    original = str(value).strip()
    normalized = unicodedata.normalize("NFKD", original)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", ascii_text).strip("_") or fallback
    changed = slug != original or len(slug) > max_length
    if changed:
        digest = hashlib.sha256(original.encode("utf-8")).hexdigest()[:8]
        stem_limit = max(8, max_length - len(digest) - 1)
        slug = f"{slug[:stem_limit].rstrip('_')}_{digest}"
    return slug[:max_length].rstrip("._ ") or fallback


def cadence_ascii_text(value: object) -> str:
    """Remove Unicode/control text from source consumed by old SKILL readers."""
    normalized = unicodedata.normalize("NFKD", str(value))
    result = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[\r\n\t]+", " ", result).strip()

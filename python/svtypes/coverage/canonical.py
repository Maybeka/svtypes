"""Canonical values and digests for functional-coverage declarations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def canonical_value(value: Any) -> Any:
    """Return a JSON-compatible, recursively canonical representation.

    Coverage declaration identities deliberately accept only static data.  A
    caller that reaches this boundary with an arbitrary runtime object has not
    compiled a reproducible declaration and must fail rather than stringify it.
    """
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, tuple):
        return [canonical_value(item) for item in value]
    if isinstance(value, list):
        return [canonical_value(item) for item in value]
    if isinstance(value, Mapping):
        items: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("coverage canonical mappings require string keys")
            items[key] = canonical_value(item)
        return {key: items[key] for key in sorted(items)}
    raise TypeError(
        f"coverage declaration contains non-canonical value {type(value).__name__}"
    )


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize a canonical coverage value with a fixed UTF-8 encoding."""
    return json.dumps(
        canonical_value(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def semantic_digest(value: Any) -> str:
    """Return the stable SHA-256 digest of a declaration semantic value."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()

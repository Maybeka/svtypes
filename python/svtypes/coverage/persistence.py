"""Versioned binary persistence for accumulated functional-coverage data.

The format is intentionally a container for canonical JSON chunks, rather
than a pickle of Python runtime objects.  It transports declaration identity,
instance layout and aggregate counters across runs without serializing a live
coverage runtime or object graph.
"""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Any

from ..errors import CoverageError

MAGIC = b"SVTCOVDB"
FORMAT_VERSION = 1
_HEADER = struct.Struct("<8sHH")
_CHUNK = struct.Struct("<4sI32s")


def encode_database(document: dict[str, Any]) -> bytes:
    """Encode one deterministic, self-validating coverage database image."""
    if set(document) != {"records"} or not isinstance(document["records"], list):
        raise CoverageError("coverage database document is invalid")
    meta = _json({"format": "svtypes.coverage.database", "version": FORMAT_VERSION})
    records = _json(document)
    return _HEADER.pack(MAGIC, FORMAT_VERSION, 0) + _chunk(b"META", meta) + _chunk(b"RECS", records)


def decode_database(data: bytes) -> dict[str, Any]:
    """Decode a database image and reject truncation, corruption and unknown ABI."""
    if len(data) < _HEADER.size:
        raise CoverageError("coverage database file is truncated")
    magic, version, flags = _HEADER.unpack_from(data)
    if magic != MAGIC:
        raise CoverageError("coverage database magic is invalid")
    if version != FORMAT_VERSION or flags != 0:
        raise CoverageError(f"unsupported coverage database format version {version}")
    offset = _HEADER.size
    chunks: dict[bytes, bytes] = {}
    while offset < len(data):
        if len(data) - offset < _CHUNK.size:
            raise CoverageError("coverage database chunk header is truncated")
        kind, length, digest = _CHUNK.unpack_from(data, offset)
        offset += _CHUNK.size
        if len(data) - offset < length:
            raise CoverageError("coverage database chunk payload is truncated")
        payload = data[offset:offset + length]
        offset += length
        if hashlib.sha256(payload).digest() != digest:
            raise CoverageError("coverage database chunk checksum mismatch")
        if kind in chunks:
            raise CoverageError("coverage database contains a duplicate chunk")
        chunks[kind] = payload
    if set(chunks) != {b"META", b"RECS"}:
        raise CoverageError("coverage database chunk layout is unsupported")
    try:
        meta = json.loads(chunks[b"META"])
        document = json.loads(chunks[b"RECS"])
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CoverageError("coverage database JSON chunk is invalid") from error
    if meta != {"format": "svtypes.coverage.database", "version": FORMAT_VERSION}:
        raise CoverageError("coverage database metadata is unsupported")
    if not isinstance(document, dict) or set(document) != {"records"} or not isinstance(document["records"], list):
        raise CoverageError("coverage database records chunk is invalid")
    return document


def write_database(path: str | Path, document: dict[str, Any]) -> None:
    Path(path).write_bytes(encode_database(document))


def read_database(path: str | Path) -> dict[str, Any]:
    return decode_database(Path(path).read_bytes())


def _json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return _CHUNK.pack(kind, len(payload), hashlib.sha256(payload).digest()) + payload

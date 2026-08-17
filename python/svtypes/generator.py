"""Deterministic public multi-file generation API."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable

from .errors import DeclarationError
from .schema import SCHEMA_FORMAT_VERSION, schema_descriptor


GENERATOR_VERSION = 1
MANIFEST_NAME = "svtypes-manifest.json"


@dataclass(frozen=True, slots=True)
class GenerationResult:
    output_dir: Path
    mode: str
    written: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()
    stale: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()

    @property
    def is_current(self) -> bool:
        return not self.written and not self.stale and not self.failed


def _safe_stem(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._")
    if not stem:
        raise DeclarationError(f"cannot derive generated file name from {name!r}")
    return stem


def _registered_types(registry_or_schema: Any) -> tuple[str, list[type[Any]]]:
    registered = getattr(registry_or_schema, "_types", None)
    if isinstance(registered, dict):
        name = getattr(registry_or_schema, "path", None) or getattr(registry_or_schema, "name", "schema")
        ordering = getattr(registry_or_schema, "_ordered_types", None)
        values = ordering() if ordering is not None else sorted(
            set(registered.values()), key=lambda value: (value.__module__, value.__qualname__)
        )
        return str(name).replace("::", "."), values
    if isinstance(registry_or_schema, type):
        return registry_or_schema.__name__, [registry_or_schema]
    if isinstance(registry_or_schema, Iterable) and not isinstance(registry_or_schema, (str, bytes, bytearray)):
        values = list(registry_or_schema)
        if not values:
            raise DeclarationError("generation input cannot be empty")
        return "schema", values
    return registry_or_schema.__class__.__name__, [registry_or_schema.__class__]


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _render_outputs(registry_or_schema: Any, targets: set[str], layout: str) -> tuple[str, dict[str, bytes], list[dict[str, Any]]]:
    if layout != "package":
        raise DeclarationError("SvTypes 1.0 generator currently supports only layout='package'")
    unknown = targets - {"sv", "cpp", "schema"}
    if unknown:
        raise DeclarationError(f"unknown generation targets: {sorted(unknown)}")
    package_name, types = _registered_types(registry_or_schema)
    stem = _safe_stem(package_name)
    schemas = [schema_descriptor(value).to_dict() for value in types]
    outputs: dict[str, bytes] = {}

    if "sv" in targets:
        renderer = getattr(registry_or_schema, "to_sv_pkg", None)
        if renderer is None:
            body = "\n\n".join(value.to_sv_obj() for value in types)
        else:
            body = renderer()
        outputs[f"{stem}.sv"] = (body.rstrip() + "\n").encode("utf-8")
    if "cpp" in targets:
        renderer = getattr(registry_or_schema, "to_cpp_pkg", None)
        if renderer is None:
            body = "\n\n".join(value.to_cpp_obj() for value in types)
        else:
            body = renderer()
        text = '#pragma once\n#include "svtypes.hpp"\n\n' + body.rstrip() + "\n"
        outputs[f"{stem}.hpp"] = text.encode("utf-8")
    if "schema" in targets:
        outputs[f"{stem}.schema.json"] = _json_bytes(
            {
                "package": package_name,
                "schema_format_version": SCHEMA_FORMAT_VERSION,
                "types": schemas,
            }
        )
    return package_name, outputs, schemas


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_manifest(output_dir: Path) -> dict[str, Any] | None:
    path = output_dir / MANIFEST_NAME
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeclarationError(f"cannot read existing SvTypes manifest {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("generator") != "svtypes":
        raise DeclarationError(f"existing manifest is not managed by SvTypes: {path}")
    return value


def _manifest(package_name: str, outputs: dict[str, bytes], schemas: list[dict[str, Any]]) -> dict[str, Any]:
    schema_fingerprints = sorted(item["schema_fingerprint"] for item in schemas)
    encoding_fingerprints = sorted(item["encoding_fingerprint"] for item in schemas)
    return {
        "artifacts": [
            {"path": path, "sha256": _sha256(data)}
            for path, data in sorted(outputs.items())
        ],
        "generator": "svtypes",
        "generator_version": GENERATOR_VERSION,
        "package": package_name,
        "schema_fingerprints": schema_fingerprints,
        "schema_format_version": SCHEMA_FORMAT_VERSION,
        "encoding_fingerprints": encoding_fingerprints,
    }


def generate(
    registry_or_schema: Any,
    output_dir: str | os.PathLike[str],
    *,
    targets: set[str] | frozenset[str] = frozenset({"sv", "cpp", "schema"}),
    layout: str = "package",
    mode: str = "write",
) -> GenerationResult:
    """Generate or check a deterministic package artifact set."""
    if mode not in {"write", "check"}:
        raise DeclarationError("generation mode must be 'write' or 'check'")
    target_set = set(targets)
    package_name, outputs, schemas = _render_outputs(registry_or_schema, target_set, layout)
    manifest_value = _manifest(package_name, outputs, schemas)
    manifest_bytes = _json_bytes(manifest_value)
    managed_outputs = {**outputs, MANIFEST_NAME: manifest_bytes}

    destination = Path(output_dir)
    old_manifest = _read_manifest(destination) if destination.exists() else None
    old_artifacts = {
        entry["path"]: entry["sha256"]
        for entry in (old_manifest or {}).get("artifacts", [])
        if isinstance(entry, dict) and "path" in entry and "sha256" in entry
    }
    stale_paths = sorted(set(old_artifacts) - set(outputs))
    changed: list[str] = []
    unchanged: list[str] = []
    for relative, data in sorted(managed_outputs.items()):
        path = destination / relative
        if path.is_file() and path.read_bytes() == data:
            unchanged.append(relative)
        else:
            changed.append(relative)

    protected_stale: list[str] = []
    removable_stale: list[str] = []
    for relative in stale_paths:
        path = destination / relative
        if not path.exists():
            continue
        if path.is_file() and _sha256(path.read_bytes()) == old_artifacts[relative]:
            removable_stale.append(relative)
        else:
            protected_stale.append(relative)

    if mode == "check":
        return GenerationResult(
            destination,
            mode,
            written=tuple(changed),
            unchanged=tuple(unchanged),
            stale=tuple(sorted(removable_stale + protected_stale)),
        )

    destination.mkdir(parents=True, exist_ok=True)
    staged: dict[str, Path] = {}
    previous: dict[str, bytes | None] = {
        relative: (destination / relative).read_bytes()
        if (destination / relative).is_file()
        else None
        for relative in changed
    }
    previous.update(
        {
            relative: (destination / relative).read_bytes()
            for relative in removable_stale
            if (destination / relative).is_file()
        }
    )
    published: list[str] = []
    failed: list[str] = []
    current_relative = "artifact publication"
    try:
        for relative in changed:
            current_relative = relative
            fd, temporary = tempfile.mkstemp(prefix=f".{Path(relative).name}.", dir=destination)
            staged[relative] = Path(temporary)
            with os.fdopen(fd, "wb") as stream:
                stream.write(managed_outputs[relative])
                stream.flush()
                os.fsync(stream.fileno())
        # Publish data artifacts first and the manifest last. A published
        # manifest therefore never describes files that were not staged.
        for relative in sorted(changed, key=lambda item: item == MANIFEST_NAME):
            current_relative = relative
            os.replace(staged[relative], destination / relative)
            del staged[relative]
            published.append(relative)
        for relative in removable_stale:
            current_relative = relative
            (destination / relative).unlink()
            published.append(relative)
    except OSError as exc:
        failed.append(current_relative)
        rollback_errors = []
        for relative in reversed(published):
            path = destination / relative
            old_data = previous.get(relative)
            try:
                if old_data is None:
                    path.unlink(missing_ok=True)
                    continue
                fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.rollback.", dir=destination)
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(old_data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, path)
                finally:
                    Path(temporary).unlink(missing_ok=True)
            except OSError as rollback_exc:
                rollback_errors.append(f"{relative}: {rollback_exc}")
        if rollback_errors:
            raise OSError(
                "SvTypes artifact publication and rollback both failed: "
                + "; ".join(rollback_errors)
            ) from exc
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)

    return GenerationResult(
        destination,
        mode,
        written=tuple(changed) if not failed else (),
        unchanged=tuple(unchanged),
        stale=tuple(protected_stale),
        failed=tuple(failed),
    )

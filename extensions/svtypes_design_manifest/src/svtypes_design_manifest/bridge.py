"""Deterministic export of the public SvTypes declaration surface.

This module is deliberately an external consumer.  It uses public descriptor
and coverage declaration APIs and serializes only static declaration data.
No runtime object, covergroup instance, sample result, or callable crosses the
bridge boundary.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
import hashlib
import inspect
import json
from pathlib import Path
import textwrap
from typing import Any

from svtypes import __version__ as _svtypes_version
from svtypes.coverage import CoverGroupDeclaration, CoverageIR, auto_coverage_ir, coverage_initializers
from svtypes.schema import schema_descriptor, unified_type_name


DESIGN_MANIFEST_VERSION = 1
DESIGN_MANIFEST_FORMAT = f"svtypes.design-manifest/v{DESIGN_MANIFEST_VERSION}"


class DesignManifestError(ValueError):
    """A design cannot be represented by the versioned bridge contract."""


def _canonical_value(value: Any) -> Any:
    """Return JSON-compatible data while rejecting runtime-only values."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, tuple | list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise DesignManifestError("manifest mappings require string keys")
            result[key] = _canonical_value(item)
        return {key: result[key] for key in sorted(result)}
    raise DesignManifestError(f"manifest contains non-JSON value {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Encode *value* using the bridge's deterministic UTF-8 JSON form."""
    return json.dumps(
        _canonical_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _semantic_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _source_anchor(function: Any, source_root: Path | None) -> dict[str, Any] | None:
    """Return non-semantic provenance without leaking absolute paths."""
    try:
        filename = inspect.getsourcefile(function)
        _, line = inspect.getsourcelines(function)
    except (OSError, TypeError):
        return None
    anchor: dict[str, Any] = {"line": line}
    if source_root is not None and filename is not None:
        try:
            anchor["file"] = str(Path(filename).resolve().relative_to(source_root))
        except ValueError:
            pass
    return anchor


def _display_order(function: Any | None) -> dict[str, Any]:
    """Extract non-semantic declaration order from one explicit covergroup."""
    if function is None:
        return {}
    try:
        source = textwrap.dedent(inspect.getsource(function))
        definition = next(
            node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)
        )
    except (OSError, TypeError, StopIteration, SyntaxError):
        return {}
    statements = definition.body
    sample = next(
        (node for node in statements if isinstance(node, ast.FunctionDef) and node.name == "sample"),
        None,
    )
    if sample is not None:
        statements = sample.body
    items: list[dict[str, str]] = []
    bins: dict[str, list[str]] = {}
    for statement in statements:
        if not isinstance(statement, ast.ClassDef) or len(statement.bases) != 1:
            continue
        base = statement.bases[0]
        base_name = base.id if isinstance(base, ast.Name) else None
        if base_name not in {"CovPoint", "CovPointArray", "Cross"}:
            continue
        kind = "cross" if base_name == "Cross" else "point"
        items.append({"kind": kind, "name": statement.name})
        bins[statement.name] = [
            target.id
            for member in statement.body
            if isinstance(member, ast.Assign)
            for target in member.targets
            if isinstance(target, ast.Name)
        ]
    return {"bins": bins, "items": items}


def _coverage_entries(cls: type[Any], source_root: Path | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Export declarations physically owned by *cls*, matching generated SV ownership."""
    declarations: list[tuple[CoverageIR, Any | None, str]] = []
    for name, value in cls.__dict__.items():
        if isinstance(value, CoverGroupDeclaration):
            declarations.append((value.freeze(), value.function, f"{cls.__module__}.{cls.__qualname__}.{name}"))
    automatic = auto_coverage_ir(cls)
    if automatic is not None:
        declarations.append((automatic, None, f"{cls.__module__}.{cls.__qualname__}.{automatic.declaration_name}"))

    initializer_calls = {
        group_name: []
        for initializer in coverage_initializers(cls)
        for group_name in (call.covergroup for call in initializer.calls)
    }
    for initializer in coverage_initializers(cls):
        for call in initializer.calls:
            initializer_calls[call.covergroup].append({
                "name": initializer.name,
                "parameters": [parameter.stable_dict() for parameter in initializer.parameters],
                "call": call.stable_dict(),
            })
    entries: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    for ir, function, qualified_name in declarations:
        entries.append({
            "covergroup_type_id": ir.covergroup_type_id,
            "declaration_semantic_digest": ir.declaration_semantic_digest,
            "definition": ir.definition_snapshot(),
            "owner_type": unified_type_name(cls),
            "coverage_initializers": initializer_calls.get(ir.declaration_name, []),
            "display_order": _display_order(function),
        })
        anchor = _source_anchor(function, source_root) if function is not None else None
        item: dict[str, Any] = {
            "covergroup_type_id": ir.covergroup_type_id,
            "declaration_qualname": qualified_name,
        }
        if anchor is not None:
            item.update(anchor)
        provenance.append(item)
    return entries, provenance


def _capabilities(type_entries: list[dict[str, Any]], covergroups: list[dict[str, Any]]) -> list[str]:
    """Describe semantic constructs present in this concrete manifest."""
    values: set[str] = set()
    if any("constraints" in item.get("schema", {}) for item in type_entries):
        values.add("constraint.ir")
    if covergroups:
        values.add("coverage.ir.v1")
    for group in covergroups:
        definition = group["definition"]
        for point in definition["points"]:
            if any(_contains_parameter_ref(bin_["selector"]) for bin_ in point["bins"]):
                values.add("coverage.ir.v1.parameter_ref")
            if any(bin_["kind"] == "transition" for bin_ in point["bins"]):
                values.add("coverage.bin.transition")
        for cross in definition["crosses"]:
            if any(_contains_parameter_ref(bin_["selector"]) for bin_ in cross["bins"]):
                values.add("coverage.ir.v1.parameter_ref")
            if cross["queue_functions"]:
                values.add("coverage.cross.function_bins")
            if any(bin_["kind"] == "transition" for bin_ in cross["bins"]):
                values.add("coverage.bin.transition")
    return sorted(values)


def _contains_parameter_ref(value: Any) -> bool:
    """Whether a serialized selector contains the public parameter reference kind."""
    if isinstance(value, Mapping):
        return value.get("kind") == "parameter_ref" or any(
            _contains_parameter_ref(item) for item in value.values()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_parameter_ref(item) for item in value)
    return False


def _normalize_types(types: Iterable[type[Any]]) -> list[type[Any]]:
    values = list(types)
    if not values:
        raise DesignManifestError("design manifest input cannot be empty")
    if any(not isinstance(value, type) for value in values):
        raise DesignManifestError("design manifest input must contain types")
    try:
        ordered = sorted(values, key=unified_type_name)
    except Exception as exc:  # convert only the bridge boundary's public error
        raise DesignManifestError(f"invalid design-manifest type input: {exc}") from exc
    seen: set[str] = set()
    for value in ordered:
        name = unified_type_name(value)
        if name in seen:
            raise DesignManifestError(f"design manifest has duplicate type {name!r}")
        seen.add(name)
    return ordered


def export_design_manifest(
    types: Iterable[type[Any]],
    *,
    design_name: str = "design",
    source_root: str | Path | None = None,
    producer_version: str = "0.1.0",
) -> dict[str, Any]:
    """Export a deterministic snapshot for external backends and tools.

    ``types`` must be the explicit design roots.  Only declarations physically
    owned by each supplied class are emitted; inherited declarations belong to
    their defining class, exactly as generated coverage declarations do.
    """
    if not isinstance(design_name, str) or not design_name:
        raise DesignManifestError("design_name must be a non-empty string")
    if not isinstance(producer_version, str) or not producer_version:
        raise DesignManifestError("producer_version must be a non-empty string")
    root = None if source_root is None else Path(source_root).resolve()
    normalized = _normalize_types(types)

    type_entries: list[dict[str, Any]] = []
    covergroups: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    for cls in normalized:
        descriptor = schema_descriptor(cls).to_dict()
        type_entries.append(descriptor)
        group_entries, group_provenance = _coverage_entries(cls, root)
        covergroups.extend(group_entries)
        provenance.extend(group_provenance)

    duplicate_ids = [
        item["covergroup_type_id"]
        for index, item in enumerate(covergroups[1:], 1)
        if item["covergroup_type_id"] == covergroups[index - 1]["covergroup_type_id"]
    ]
    if duplicate_ids:
        raise DesignManifestError(
            f"design manifest has ambiguous covergroup type {duplicate_ids[0]!r}"
        )
    capabilities = _capabilities(type_entries, covergroups)
    presentation = {
        "covergroup_display_order": {
            entry["covergroup_type_id"]: entry.pop("display_order")
            for entry in covergroups
        }
    }

    semantic_document = {
        "capabilities": capabilities,
        "coverage": {"covergroups": covergroups},
        "design": {"name": design_name},
        "format": DESIGN_MANIFEST_FORMAT,
        "producer": {"bridge_version": producer_version, "svtypes_version": _svtypes_version},
        "types": type_entries,
    }
    document = {
        "capabilities": capabilities,
        "coverage": semantic_document["coverage"],
        "design": {**semantic_document["design"], "manifest_digest": _semantic_digest(semantic_document)},
        "diagnostics": [],
        "format": DESIGN_MANIFEST_FORMAT,
        "presentation": presentation,
        "producer": semantic_document["producer"],
        "provenance": provenance,
        "types": type_entries,
    }
    validate_design_manifest(document)
    return document


def validate_design_manifest(document: Mapping[str, Any]) -> None:
    """Validate the E0 structural contract without a third-party dependency."""
    if not isinstance(document, Mapping):
        raise DesignManifestError("design manifest must be an object")
    if document.get("format") != DESIGN_MANIFEST_FORMAT:
        raise DesignManifestError(f"unsupported design manifest format {document.get('format')!r}")
    producer = document.get("producer")
    design = document.get("design")
    coverage = document.get("coverage")
    if not isinstance(producer, Mapping) or not isinstance(producer.get("bridge_version"), str) or not isinstance(producer.get("svtypes_version"), str):
        raise DesignManifestError("design manifest producer bridge_version and svtypes_version must be strings")
    if not isinstance(design, Mapping) or not isinstance(design.get("name"), str) or not isinstance(design.get("manifest_digest"), str):
        raise DesignManifestError("design manifest design name and digest must be strings")
    if not isinstance(document.get("types"), list):
        raise DesignManifestError("design manifest types must be an array")
    capabilities = document.get("capabilities")
    if not isinstance(capabilities, list) or any(not isinstance(item, str) or not item for item in capabilities):
        raise DesignManifestError("design manifest capabilities must be an array of non-empty strings")
    if capabilities != sorted(set(capabilities)):
        raise DesignManifestError("design manifest capabilities must be sorted and unique")
    if not isinstance(coverage, Mapping) or not isinstance(coverage.get("covergroups"), list):
        raise DesignManifestError("design manifest coverage.covergroups must be an array")
    if not isinstance(document.get("provenance"), list) or not isinstance(document.get("diagnostics"), list):
        raise DesignManifestError("design manifest provenance and diagnostics must be arrays")
    presentation = document.get("presentation")
    if presentation is not None and not isinstance(presentation, Mapping):
        raise DesignManifestError("design manifest presentation must be an object")
    covergroups = coverage["covergroups"]
    seen: set[str] = set()
    for group in covergroups:
        if not isinstance(group, Mapping):
            raise DesignManifestError("design manifest covergroups must be objects")
        for key in ("covergroup_type_id", "declaration_semantic_digest", "owner_type"):
            if not isinstance(group.get(key), str) or not group[key]:
                raise DesignManifestError(f"design manifest covergroup {key} must be a non-empty string")
        if not isinstance(group.get("definition"), Mapping):
            raise DesignManifestError("design manifest covergroup definition must be an object")
        if group["covergroup_type_id"] in seen:
            raise DesignManifestError(f"design manifest has duplicate covergroup type {group['covergroup_type_id']!r}")
        seen.add(group["covergroup_type_id"])
    semantic_document = {
        "capabilities": capabilities,
        "coverage": {"covergroups": covergroups},
        "design": {"name": design["name"]},
        "format": document["format"],
        "producer": {"bridge_version": producer["bridge_version"], "svtypes_version": producer["svtypes_version"]},
        "types": document["types"],
    }
    if design["manifest_digest"] != _semantic_digest(semantic_document):
        raise DesignManifestError("design manifest digest does not match semantic content")


def write_design_manifest(path: str | Path, document: Mapping[str, Any]) -> Path:
    """Validate and atomically write a manifest using canonical JSON."""
    validate_design_manifest(document)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(canonical_json_bytes(document) + b"\n")
    temporary.replace(destination)
    return destination


def load_design_manifest(path: str | Path) -> dict[str, Any]:
    """Load and validate an E0 manifest from disk."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DesignManifestError(f"cannot read design manifest: {exc}") from exc
    validate_design_manifest(document)
    return dict(document)

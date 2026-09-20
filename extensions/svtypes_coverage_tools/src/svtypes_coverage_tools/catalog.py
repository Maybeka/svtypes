"""Deterministic static coverage catalog derived from a Design Manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from svtypes_design_manifest import canonical_json_bytes, load_design_manifest


CATALOG_FORMAT = "svtypes.coverage-catalog/v1"


class CatalogError(ValueError):
    """A manifest cannot be represented or loaded as a coverage catalog."""


def _id(group_id: str, kind: str, name: str, bin_name: str | None = None) -> str:
    result = f"{group_id}::{kind}::{name}"
    return result if bin_name is None else f"{result}::bin::{bin_name}"


def _in_display_order(items: list[dict[str, Any]], names: list[str]) -> list[dict[str, Any]]:
    """Place source-declared names first without dropping generated entries."""
    ordered: list[dict[str, Any]] = []
    for name in names:
        ordered.extend(
            item
            for item in items
            if item["name"] == name or item["name"].startswith(f"{name}[")
        )
    ordered_ids = {item["semantic_id"] for item in ordered}
    return _sort_array_members(
        ordered + [item for item in items if item["semantic_id"] not in ordered_ids]
    )


def _array_member(name: str) -> tuple[str, int] | None:
    base, separator, suffix = name.rpartition("[")
    if not separator or not suffix.endswith("]") or not suffix[:-1].isdigit():
        return None
    return base, int(suffix[:-1])


def _sort_array_members(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Numerically order contiguous members of each generated array bin."""
    result: list[dict[str, Any]] = []
    index = 0
    while index < len(items):
        member = _array_member(items[index]["name"])
        if member is None:
            result.append(items[index])
            index += 1
            continue
        base, _ = member
        group: list[dict[str, Any]] = []
        while index < len(items):
            candidate = _array_member(items[index]["name"])
            if candidate is None or candidate[0] != base:
                break
            group.append(items[index])
            index += 1
        result.extend(sorted(group, key=lambda item: _array_member(item["name"])[1]))
    return result


def _input_value_type(annotation: Any) -> str:
    """Return the value type within a serialized CoverInput annotation."""
    if isinstance(annotation, str) and annotation.startswith("CoverInput[") and annotation.endswith("]"):
        return annotation[len("CoverInput["):-1]
    return str(annotation or "")


def _parameter_refs(value: Any) -> set[str]:
    if isinstance(value, Mapping):
        result = {value["name"]} if value.get("kind") == "parameter_ref" else set()
        for item in value.values():
            result.update(_parameter_refs(item))
        return result
    if isinstance(value, (list, tuple)):
        return set().union(*(_parameter_refs(item) for item in value))
    return set()


def _enum_fields(manifest: Mapping[str, Any], owner_type: str) -> dict[str, dict[int, str]]:
    """Return display labels for enum-valued fields of one manifest type."""
    entry = next(
        (item for item in manifest["types"] if item.get("unified_type_name") == owner_type),
        None,
    )
    if not isinstance(entry, Mapping):
        return {}
    result: dict[str, dict[int, str]] = {}
    for field in entry.get("schema", {}).get("fields", []):
        descriptor = field.get("type", {}) if isinstance(field, Mapping) else {}
        if descriptor.get("kind") != "enum":
            continue
        type_name = descriptor.get("type_name", "").rsplit(".", 1)[-1]
        result[field["name"]] = {
            int(value): f"{type_name}.{name}"
            for name, value in descriptor.get("items", [])
        }
    return result


def _point_enum_labels(
    value: Mapping[str, Any], enum_fields: Mapping[str, Mapping[int, str]]
) -> Mapping[int, str]:
    expression = value.get("expression")
    if not isinstance(expression, Mapping) or expression.get("kind") != "attribute":
        return {}
    return enum_fields.get(expression.get("name"), {})


def _cover_input_names(value: Any, formals: set[str]) -> list[str]:
    """Return constructor inputs referenced by one point or cross declaration."""
    names: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            if item.get("kind") == "name" and item.get("name") in formals:
                names.add(item["name"])
            for child in item.values():
                visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)

    visit(value)
    return sorted(names)


def _node(
    group_id: str,
    kind: str,
    value: Mapping[str, Any],
    bin_order: list[str] | None = None,
    enum_labels: Mapping[int, str] | None = None,
    cover_inputs: set[str] | None = None,
    cover_input_types: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    name = value["name"]
    bins = []
    for bin_ in value["bins"]:
        selector = bin_["selector"]
        display_selector = None
        if isinstance(selector, Mapping) and selector.get("kind") == "enum_literal":
            display_selector = f"{selector['type_name']}.{selector['member']}"
        if (
            enum_labels
            and display_selector is None
            and isinstance(selector, Mapping)
            and selector.get("kind") == "constant"
            and selector.get("value") in enum_labels
        ):
            display_selector = enum_labels[selector["value"]]
        bins.append({
            "kind": bin_["kind"],
            "name": bin_["name"],
            "selector": selector,
            "display_selector": display_selector,
            "semantic_id": _id(group_id, kind, name, bin_["name"]),
        })
    return {
        "bins": _in_display_order(bins, bin_order or []),
        "expression": value.get("expression"),
        "iff": value["iff"],
        "name": name,
        "options": value["options"],
        "enum_labels": {str(value): label for value, label in dict(enum_labels).items()} if enum_labels else {},
        "cover_input_shapes": [
            {"name": input_name, "type_name": (cover_input_types or {}).get(input_name, "")}
            for input_name in _cover_input_names(value, cover_inputs or set())
        ],
        "semantic_id": _id(group_id, kind, name),
    }


def _cross_member_view(
    group_id: str,
    cross_name: str,
    value: Mapping[str, Any],
    cover_inputs: set[str],
    cover_input_types: Mapping[str, str],
) -> dict[str, Any]:
    """Catalog presentation for a cross-private effective member point.

    It deliberately has no semantic ID: a member view is not a public
    coverpoint, database item, or navigable declaration identity.
    """
    node = _node(
        group_id,
        "cross-member-view",
        value["point"],
        cover_inputs=cover_inputs,
        cover_input_types=cover_input_types,
    )
    node.pop("semantic_id", None)
    for bin_ in node["bins"]:
        bin_.pop("semantic_id", None)
    return {"member": value["name"], "point": node}


def _referenced_types(schema: Any) -> list[tuple[str, str]]:
    """Return static named type references below one field schema."""
    if not isinstance(schema, Mapping):
        return []
    kind = schema.get("kind")
    references: list[tuple[str, str]] = []
    if kind in {"object", "struct", "object_ref", "type_ref"}:
        type_name = schema.get("type_name")
        if isinstance(type_name, str) and type_name:
            references.append((kind, type_name))
    for key in ("element", "key", "value"):
        references.extend(_referenced_types(schema.get(key)))
    return references


def _type_hierarchy(manifest: Mapping[str, Any], groups: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a finite, declaration-only type composition graph."""
    nodes: dict[str, dict[str, Any]] = {}
    groups_by_owner: dict[str, list[str]] = {}
    for group in groups:
        groups_by_owner.setdefault(group["owner_type"], []).append(group["covergroup_type_id"])

    for entry in manifest["types"]:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("unified_type_name"), str):
            raise CatalogError("design manifest type entries require unified_type_name")
        type_name = entry["unified_type_name"]
        schema = entry.get("schema")
        if not isinstance(schema, Mapping):
            raise CatalogError(f"design manifest type {type_name!r} requires a schema object")
        nodes[type_name] = {
            "covergroups": groups_by_owner.get(type_name, []),
            "exported": True,
            "references": [],
            "type_name": type_name,
        }

    for entry in manifest["types"]:
        type_name = entry["unified_type_name"]
        schema = entry["schema"]
        references: set[tuple[str, str, str]] = set()
        base_type = schema.get("base_type_name")
        if isinstance(base_type, str) and base_type:
            references.add(("base", "extends", base_type))
        fields = schema.get("fields", [])
        if not isinstance(fields, list):
            raise CatalogError(f"design manifest type {type_name!r} fields must be an array")
        for field in fields:
            if not isinstance(field, Mapping) or not isinstance(field.get("name"), str):
                raise CatalogError(f"design manifest type {type_name!r} has an invalid field")
            for kind, target in _referenced_types(field.get("type")):
                if target == type_name:
                    references.add((kind, field["name"], target))
                elif target.startswith("svtypes."):
                    continue
                else:
                    references.add((kind, field["name"], target))
        for kind, field_name, target in sorted(references):
            if target not in nodes:
                nodes[target] = {
                    "covergroups": [],
                    "exported": False,
                    "references": [],
                    "type_name": target,
                }
            nodes[type_name]["references"].append(
                {"field": field_name, "kind": kind, "target_type": target}
            )

    # Every exported (module-discovered) type is a sibling root. Field
    # references remain edges under the owning type; they do not demote the
    # target from the top-level coverage structure.
    roots = sorted(type_name for type_name, node in nodes.items() if node["exported"])
    return {
        "nodes": [nodes[type_name] for type_name in sorted(nodes)],
        "roots": roots,
    }


def build_catalog(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Build a static declaration tree without evaluating any user expression."""
    if manifest.get("format") != "svtypes.design-manifest/v1":
        raise CatalogError("catalog requires svtypes.design-manifest/v1")
    provenance_by_group = {
        item["covergroup_type_id"]: item
        for item in manifest.get("provenance", [])
        if isinstance(item, Mapping) and isinstance(item.get("covergroup_type_id"), str)
    }
    presentation = manifest.get("presentation", {})
    display_orders = (
        presentation.get("covergroup_display_order", {})
        if isinstance(presentation, Mapping)
        else {}
    )
    groups: list[dict[str, Any]] = []
    for item in manifest["coverage"]["covergroups"]:
        definition = item["definition"]
        group_id = item["covergroup_type_id"]
        cover_inputs = {
            parameter["name"] for parameter in definition.get("constructor_parameters", [])
        }
        cover_input_types = {
            parameter["name"]: _input_value_type(parameter["type"])
            for parameter in definition.get("constructor_parameters", [])
        }
        enum_fields = _enum_fields(manifest, item["owner_type"])
        owner_schema = next(
            (entry.get("schema", {}) for entry in manifest["types"]
             if entry.get("unified_type_name") == item["owner_type"]),
            {},
        )
        parameter_defaults = {
            parameter["name"]: parameter.get("value")
            for parameter in owner_schema.get("parameters", [])
        }
        parameter_names = _parameter_refs(definition)
        parameter_shapes = [
            {
                "name": name,
                "type_name": type(parameter_defaults[name]).__name__,
                "value": parameter_defaults[name],
            }
            for name in sorted(parameter_names)
            if name in parameter_defaults
        ]
        display_order = display_orders.get(group_id, item.get("display_order", {}))
        bin_order = display_order.get("bins", {}) if isinstance(display_order, Mapping) else {}
        points = [
            _node(
                group_id, "point", point, bin_order.get(point["name"].split("[", 1)[0]),
                _point_enum_labels(point, enum_fields),
                cover_inputs,
                cover_input_types,
            )
            for point in definition["points"]
        ]
        crosses = [
            {
                **_node(
                    group_id,
                    "cross",
                    cross,
                    bin_order.get(cross["name"]),
                    cover_inputs=cover_inputs,
                    cover_input_types=cover_input_types,
                ),
                "members": cross["members"],
                "member_views": [
                    _cross_member_view(
                        group_id,
                        cross["name"],
                        view,
                        cover_inputs,
                        cover_input_types,
                    )
                    for view in cross.get("member_views", [])
                ],
                "queue_functions": cross["queue_functions"],
            }
            for cross in definition["crosses"]
        ]
        item_order = display_order.get("items", []) if isinstance(display_order, Mapping) else []
        ordered_items: list[dict[str, Any]] = []
        for ordered in item_order:
            if not isinstance(ordered, Mapping):
                continue
            collection = points if ordered.get("kind") == "point" else crosses
            ordered_items.extend(
                value for value in collection
                if value["name"] == ordered.get("name") or value["name"].startswith(f"{ordered.get('name')}[")
            )
        ordered_ids = {value["semantic_id"] for value in ordered_items}
        ordered_items.extend(value for value in [*points, *crosses] if value["semantic_id"] not in ordered_ids)
        group = {
            "covergroup_type_id": group_id,
            "declaration_name": definition["declaration_name"],
            "declaration_semantic_digest": item["declaration_semantic_digest"],
            "owner_type": item["owner_type"],
            "options": definition["options"],
            "points": points,
            "crosses": crosses,
            "items": ordered_items,
            "type_options": definition["type_options"],
            "coverage_initializers": list(item.get("coverage_initializers", [])),
            "cover_input_shapes": [
                {"name": name, "type_name": cover_input_types[name]}
                for name in sorted(cover_input_types)
            ],
            "parameter_shapes": parameter_shapes,
        }
        if group_id in provenance_by_group:
            group["provenance"] = dict(provenance_by_group[group_id])
        groups.append(group)
    type_hierarchy = _type_hierarchy(manifest, groups)
    semantic = {
        "design_manifest_digest": manifest["design"]["manifest_digest"],
        "format": CATALOG_FORMAT,
        "groups": groups,
        "type_hierarchy": type_hierarchy,
    }
    return {
        **semantic,
        "catalog_digest": hashlib.sha256(canonical_json_bytes(semantic)).hexdigest(),
        "diagnostics": list(manifest.get("diagnostics", [])),
    }


def validate_catalog(catalog: Mapping[str, Any]) -> None:
    if catalog.get("format") != CATALOG_FORMAT:
        raise CatalogError("unsupported coverage catalog format")
    if not isinstance(catalog.get("groups"), list) or not isinstance(catalog.get("design_manifest_digest"), str):
        raise CatalogError("coverage catalog is missing groups or design digest")
    hierarchy = catalog.get("type_hierarchy")
    if not isinstance(hierarchy, Mapping) or not isinstance(hierarchy.get("roots"), list) or not isinstance(hierarchy.get("nodes"), list):
        raise CatalogError("coverage catalog is missing type hierarchy")
    semantic = {
        "design_manifest_digest": catalog["design_manifest_digest"],
        "format": catalog["format"],
        "groups": catalog["groups"],
        "type_hierarchy": hierarchy,
    }
    digest = hashlib.sha256(canonical_json_bytes(semantic)).hexdigest()
    if catalog.get("catalog_digest") != digest:
        raise CatalogError("coverage catalog digest does not match semantic content")


def write_catalog(path: str | Path, catalog: Mapping[str, Any]) -> Path:
    validate_catalog(catalog)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(canonical_json_bytes(catalog) + b"\n")
    temporary.replace(destination)
    return destination


def load_catalog(path: str | Path) -> dict[str, Any]:
    try:
        catalog = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogError(f"cannot read coverage catalog: {exc}") from exc
    validate_catalog(catalog)
    return catalog


def catalog_from_file(path: str | Path) -> dict[str, Any]:
    """Load a manifest file and produce the matching static catalog."""
    return build_catalog(load_design_manifest(path))

"""UCIS 1.0 XML functional-coverage projection and conservative importer.

UCIS is an interchange format, not SvTypes' persistence format. The exporter
therefore emits only coverage that has a lossless UCIS representation, and
reports every omitted SvTypes construct separately.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from ..errors import CoverageError

_LOSS_FORMAT = "svtypes.ucis.loss-report"
_SOURCE_ID = {"file": "1", "line": "1", "inlineCount": "1"}


def export_ucis(database: Any) -> tuple[str, dict[str, Any]]:
    """Return schema-shaped UCIS 1.0 XML plus an explicit loss report.

    Supported point bins are scalar integer constants and closed scalar integer
    ranges. Supported cross bins are static references to those exported point
    bins. The subset preserves every emitted count and never approximates a
    selector merely to make an XML document look complete.
    """
    now = datetime.now(timezone.utc).isoformat()
    root = ET.Element("UCIS", ucisVersion="1.0", writtenBy="SvTypes", writtenTime=now)
    ET.SubElement(root, "sourceFiles", fileName="<svtypes>", id="1")
    ET.SubElement(root, "historyNodes", historyNodeId="1", logicalName="svtypes",
                  kind="UCIS_HISTORYNODE_TEST", testStatus="true", date=now,
                  toolCategory="other", ucisVersion="1.0", vendorId="svtypes",
                  vendorTool="svtypes", vendorToolVersion="2.0")
    losses: list[dict[str, str]] = []
    for index, document in enumerate(database.snapshot_document()["records"], 1):
        _export_record(root, index, document, losses)
    # UCIS requires at least one instanceCoverages node even when the source
    # database has no losslessly representable functional coverage. Keep that
    # placeholder semantically empty rather than inventing a covergroup.
    if not root.findall("instanceCoverages"):
        instance = ET.SubElement(root, "instanceCoverages", name="svtypes", key="0")
        ET.SubElement(instance, "id", **_SOURCE_ID)
    return ET.tostring(root, encoding="unicode", xml_declaration=True), _loss_report(losses)


def export_ucis_file(path: str | Path, database: Any) -> dict[str, Any]:
    """Write UCIS XML to *path* and return its structured loss report."""
    xml, report = export_ucis(database)
    Path(path).write_text(xml, encoding="utf-8")
    return report


def import_ucis(xml: str, *, defaults: dict[str, Any] | None = None,
                config_path: str | Path | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read the functional subset without inventing SvTypes identity.

    ``defaults`` and the optional JSON ``config_path`` are global import
    settings. They fill SvTypes-only settings absent from UCIS; a caller must
    still explicitly map returned external records to a frozen declaration.
    """
    if defaults is not None and config_path is not None:
        raise CoverageError("UCIS import accepts either defaults or config_path, not both")
    if config_path is not None:
        defaults = _read_import_defaults(config_path)
    defaults = dict(defaults or {})
    if any(not isinstance(key, str) for key in defaults):
        raise CoverageError("UCIS import defaults must use string option names")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as error:
        raise CoverageError("UCIS XML is malformed") from error
    if root.tag != "UCIS" or root.get("ucisVersion") != "1.0":
        raise CoverageError("expected UCIS 1.0 XML")
    imported: list[dict[str, Any]] = []
    losses: list[dict[str, str]] = []
    for cg in root.findall(".//cgInstance"):
        record: dict[str, Any] = {"name": cg.get("name"), "points": {}, "crosses": {}, "options": {}}
        options = cg.find("options")
        if options is not None:
            record["options"] = dict(options.attrib)
        for key, value in defaults.items():
            if key not in record["options"]:
                record["options"][key] = value
                losses.append({"item": str(record["name"]), "reason": f"SvTypes option {key!r} defaulted during UCIS import"})
        for point in cg.findall("coverpoint"):
            record["points"][point.get("name", "")] = _import_bins(point, "coverpointBin")
        for cross in cg.findall("cross"):
            record["crosses"][cross.get("name", "")] = _import_bins(cross, "crossBin")
        imported.append(record)
    return imported, _loss_report(losses)


def import_ucis_file(path: str | Path, *, config_path: str | Path | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read a UTF-8 UCIS XML file with optional global JSON import settings."""
    return import_ucis(Path(path).read_text(encoding="utf-8"), config_path=config_path)


def _export_record(root: ET.Element, index: int, document: dict[str, Any], losses: list[dict[str, str]]) -> None:
    point_ir = {item["name"]: item for item in document["definition"].get("points", [])}
    exported_points = {
        name: _exportable_point_bins(point_ir.get(name, {}), definition)
        for name, definition in document.get("point_definitions", {}).items()
    }
    exported_points = {name: bins for name, bins in exported_points.items() if bins}
    if not exported_points:
        losses.append({"record": str(index), "item": document["covergroup_type_id"], "reason": "covergroup omitted: UCIS requires an exported coverpoint"})
        return

    instance = ET.SubElement(root, "instanceCoverages", name="svtypes", key=str(index))
    ET.SubElement(instance, "id", **_SOURCE_ID)
    metric = ET.SubElement(instance, "covergroupCoverage", weight=str(document.get("options", {}).get("weight", 1)))
    name = document.get("instance_name") or document.get("logical_instance_key") or document["covergroup_type_id"]
    cg = ET.SubElement(metric, "cgInstance", name=str(name), key=str(index), excluded="false")
    options, type_options = document.get("options", {}), document.get("type_options", {})
    ET.SubElement(cg, "options", weight=str(options.get("weight", 1)), goal=str(options.get("goal", 100)),
                  comment=str(document.get("comment", "")), at_least="1", detect_overlap="false", auto_bin_max="64",
                  cross_num_print_missing="0", per_instance=str(bool(options.get("per_instance", 0))).lower(),
                  merge_instances=str(bool(type_options.get("merge_instances", 0))).lower())
    cg_id = ET.SubElement(cg, "cgId", cgName=document["covergroup_type_id"], moduleName="SvTypes")
    ET.SubElement(cg_id, "cginstSourceId", **_SOURCE_ID)
    ET.SubElement(cg_id, "cgSourceId", **_SOURCE_ID)

    point_bin_index: dict[str, dict[str, int]] = {}
    for point, bins in exported_points.items():
        definition = document["point_definitions"][point]
        item = ET.SubElement(cg, "coverpoint", name=point, key=f"{index}:{point}", exprString=point)
        ET.SubElement(item, "options", weight=str(definition["weight"]), goal=str(definition["goal"]), comment="",
                      at_least=str(definition["at_least"]), detect_overlap="false", auto_bin_max="64")
        source_bins = {bin_["name"]: bin_ for bin_ in point_ir[point].get("bins", [])}
        counts = document["points"][point]
        point_bin_index[point] = {}
        for position, bin_name in enumerate(bins):
            point_bin_index[point][bin_name] = position
            source = source_bins[bin_name]
            _append_point_bin(item, index, point, source, _bin_count(counts, source))
        for source in point_ir[point].get("bins", []):
            if source["name"] not in bins:
                _loss(losses, index, point, source["name"], "point bin selector cannot be losslessly projected to UCIS")

    cross_ir = {item["name"]: item for item in document["definition"].get("crosses", [])}
    for cross, definition in document.get("cross_definitions", {}).items():
        ir = cross_ir.get(cross, {})
        members = ir.get("members", [])
        if not all(member in exported_points for member in members):
            _loss(losses, index, cross, "", "cross omitted: one or more member coverpoints are not exportable")
            continue
        exported_bins = _exportable_cross_bins(ir, members, point_bin_index)
        item = ET.SubElement(cg, "cross", name=cross, key=f"{index}:{cross}")
        ET.SubElement(item, "options", weight=str(definition["weight"]), goal=str(definition["goal"]), comment="",
                      at_least=str(definition["at_least"]), cross_num_print_missing="0")
        for member in members:
            ET.SubElement(item, "crossExpr").text = member
        source_bins = {bin_["name"]: bin_ for bin_ in ir.get("bins", [])}
        counts = document["points"][cross]
        for bin_name in exported_bins:
            source = source_bins[bin_name]
            bin_ = ET.SubElement(item, "crossBin", name=bin_name, key=f"{index}:{cross}:{bin_name}", type=_ucis_bin_kind(source["kind"]))
            for ref in source["selector"]["items"]:
                ET.SubElement(bin_, "index").text = str(point_bin_index[ref["point"]][ref["bin"]])
            ET.SubElement(bin_, "contents", coverageCount=str(_bin_count(counts, source)))
        for source in ir.get("bins", []):
            if source["name"] not in exported_bins:
                _loss(losses, index, cross, source["name"], "cross bin selector cannot be losslessly projected to UCIS")


def _exportable_point_bins(ir: dict[str, Any], definition: dict[str, Any]) -> list[str]:
    source = {bin_["name"]: bin_ for bin_ in ir.get("bins", [])}
    return [name for name in definition.get("normal_bins", []) if name in source and _integer_ranges(source[name].get("selector")) is not None]


def _exportable_cross_bins(ir: dict[str, Any], members: list[str], index: dict[str, dict[str, int]]) -> list[str]:
    result: list[str] = []
    for bin_ in ir.get("bins", []):
        selector = bin_.get("selector")
        refs = selector.get("items", []) if isinstance(selector, dict) and selector.get("kind") == "cross_bin_refs" else []
        if bin_.get("kind") in {"normal", "ignore"} and len(refs) == len(members) and all(ref.get("point") in index and ref.get("bin") in index[ref["point"]] for ref in refs):
            result.append(bin_["name"])
    return result


def _append_point_bin(parent: ET.Element, index: int, point: str, source: dict[str, Any], count: int) -> None:
    bin_ = ET.SubElement(parent, "coverpointBin", name=source["name"], key=f"{index}:{point}:{source['name']}", type=_ucis_bin_kind(source["kind"]))
    for lower, upper in _integer_ranges(source["selector"]) or ():
        rng = ET.SubElement(bin_, "range", **{"from": str(lower), "to": str(upper)})
        ET.SubElement(rng, "contents", coverageCount=str(count))


def _integer_ranges(selector: Any) -> list[tuple[int, int]] | None:
    if not isinstance(selector, dict):
        return None
    if selector.get("kind") == "constant" and isinstance(selector.get("value"), int) and not isinstance(selector["value"], bool):
        return [(selector["value"], selector["value"])]
    if selector.get("kind") == "range":
        lower, upper = selector.get("lower"), selector.get("upper")
        if all(isinstance(value, dict) and value.get("kind") == "constant" and isinstance(value.get("value"), int) and not isinstance(value["value"], bool) for value in (lower, upper)):
            return [(lower["value"], upper["value"])]
    return None


def _bin_count(counts: dict[str, Any], source: dict[str, Any]) -> int:
    return int((counts.get("illegal_hits", {}) if source["kind"] == "illegal" else counts.get("hits", {})).get(source["name"], 0))


def _ucis_bin_kind(kind: str) -> str:
    return {"normal": "default", "ignore": "ignore", "illegal": "illegal"}[kind]


def _import_bins(parent: ET.Element, tag: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for bin_ in parent.findall(tag):
        contents = bin_.find(".//contents")
        result[bin_.get("name", "")] = int(contents.get("coverageCount", "0")) if contents is not None else 0
    return result


def _read_import_defaults(path: str | Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CoverageError("UCIS import configuration must be a readable JSON object") from error
    if not isinstance(payload, dict):
        raise CoverageError("UCIS import configuration must be a JSON object")
    return payload


def _loss(losses: list[dict[str, str]], record: int, item: str, bin_name: str, reason: str) -> None:
    result = {"record": str(record), "item": item, "reason": reason}
    if bin_name:
        result["bin"] = bin_name
    losses.append(result)


def _loss_report(losses: list[dict[str, str]]) -> dict[str, Any]:
    return {"format": _LOSS_FORMAT, "losses": losses}

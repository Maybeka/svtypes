"""Reviewable coverage proposals and deliberately explicit source application."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from svtypes_design_manifest import canonical_json_bytes

from .catalog import CATALOG_FORMAT, validate_catalog


PROPOSAL_FORMAT = "svtypes.coverage-proposal/v1"
_OPERATION_KINDS = frozenset({"add_bin", "set_option", "add_ignore_bin", "add_illegal_bin", "add_comment"})


class ProposalError(ValueError):
    """A proposal or its explicit source application is unsafe or stale."""


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _relative_file(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ProposalError("proposal source file must be a non-empty relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.name in {"", "."}:
        raise ProposalError("proposal source file must be a clean relative path")
    return path.as_posix()


def _group(catalog: Mapping[str, Any], type_id: str) -> Mapping[str, Any]:
    if catalog.get("format") != CATALOG_FORMAT:
        raise ProposalError("proposal requires a supported coverage catalog")
    for group in catalog.get("groups", []):
        if group.get("covergroup_type_id") == type_id:
            return group
    raise ProposalError(f"covergroup {type_id!r} is absent from catalog")


def _validate_operation(operation: Mapping[str, Any], group: Mapping[str, Any]) -> dict[str, Any]:
    kind = operation.get("kind")
    if kind not in _OPERATION_KINDS:
        raise ProposalError(f"unsupported proposal operation {kind!r}")
    result = dict(operation)
    target = result.get("point") or result.get("cross")
    if kind != "add_comment" and not isinstance(target, str):
        raise ProposalError(f"proposal operation {kind!r} requires point or cross")
    available = {item["name"] for collection in ("points", "crosses") for item in group[collection]}
    if target is not None and target not in available:
        raise ProposalError(f"proposal target {target!r} is absent from covergroup")
    if kind in {"add_bin", "add_ignore_bin", "add_illegal_bin"}:
        if not isinstance(result.get("name"), str) or not result["name"]:
            raise ProposalError(f"proposal operation {kind!r} requires a non-empty bin name")
        if "selector" not in result:
            raise ProposalError(f"proposal operation {kind!r} requires a selector")
    if kind == "set_option" and (not isinstance(result.get("option"), str) or "value" not in result):
        raise ProposalError("set_option requires option and value")
    return json.loads(canonical_json_bytes(result))


def _validate_edit(edit: Mapping[str, Any], source_file: str) -> dict[str, Any]:
    result = dict(edit)
    if _relative_file(result.get("file")) != source_file:
        raise ProposalError("proposal edits must target the declared covergroup source file")
    start, end = result.get("start_line"), result.get("end_line")
    if not all(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in (start, end)) or start > end:
        raise ProposalError("proposal edit line range is invalid")
    if not isinstance(result.get("expected_text"), str) or not isinstance(result.get("replacement"), str):
        raise ProposalError("proposal edit requires expected_text and replacement strings")
    return json.loads(canonical_json_bytes(result))


def create_proposal(
    catalog: Mapping[str, Any],
    *,
    covergroup_type_id: str,
    operations: Sequence[Mapping[str, Any]],
    rationale: str,
    edits: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Create a review document; this function never changes source files."""
    validate_catalog(catalog)
    group = _group(catalog, covergroup_type_id)
    provenance = group.get("provenance")
    if not isinstance(provenance, Mapping) or "file" not in provenance or not isinstance(provenance.get("line"), int):
        raise ProposalError("proposal application requires relative source provenance for the covergroup")
    source_file = _relative_file(provenance["file"])
    if not operations:
        raise ProposalError("proposal requires at least one operation")
    if not isinstance(rationale, str) or not rationale:
        raise ProposalError("proposal rationale must be non-empty")
    semantic = {
        "catalog_digest": catalog["catalog_digest"],
        "edits": [_validate_edit(edit, source_file) for edit in edits],
        "format": PROPOSAL_FORMAT,
        "operations": [_validate_operation(operation, group) for operation in operations],
        "preconditions": {
            "covergroup_type_id": covergroup_type_id,
            "declaration_semantic_digest": group["declaration_semantic_digest"],
            "source_anchor": {"file": source_file, "line": provenance["line"]},
        },
        "rationale": rationale,
    }
    return {**semantic, "proposal_digest": _digest(semantic)}


def validate_proposal(proposal: Mapping[str, Any]) -> None:
    if proposal.get("format") != PROPOSAL_FORMAT:
        raise ProposalError("unsupported coverage proposal format")
    required = {"catalog_digest", "edits", "operations", "preconditions", "rationale", "proposal_digest"}
    if not required.issubset(proposal):
        raise ProposalError("coverage proposal is incomplete")
    preconditions = proposal["preconditions"]
    if not isinstance(preconditions, Mapping):
        raise ProposalError("proposal preconditions must be an object")
    source = preconditions.get("source_anchor")
    if not isinstance(source, Mapping):
        raise ProposalError("proposal requires source anchor")
    source_file = _relative_file(source.get("file"))
    if not isinstance(source.get("line"), int) or source["line"] <= 0:
        raise ProposalError("proposal source anchor line is invalid")
    if not isinstance(preconditions.get("covergroup_type_id"), str) or not isinstance(preconditions.get("declaration_semantic_digest"), str):
        raise ProposalError("proposal identity preconditions are invalid")
    if not isinstance(proposal["operations"], list) or not proposal["operations"] or not isinstance(proposal["edits"], list):
        raise ProposalError("proposal operations and edits are invalid")
    for edit in proposal["edits"]:
        _validate_edit(edit, source_file)
    semantic = {key: value for key, value in proposal.items() if key != "proposal_digest"}
    if proposal["proposal_digest"] != _digest(semantic):
        raise ProposalError("proposal digest does not match content")


def render_review(proposal: Mapping[str, Any]) -> str:
    """Produce a concise human review independent of source mutation."""
    validate_proposal(proposal)
    preconditions = proposal["preconditions"]
    lines = [
        f"Coverage proposal {proposal['proposal_digest']}",
        f"Target: {preconditions['covergroup_type_id']}",
        f"Required declaration digest: {preconditions['declaration_semantic_digest']}",
        f"Source: {preconditions['source_anchor']['file']}:{preconditions['source_anchor']['line']}",
        f"Rationale: {proposal['rationale']}",
        "Operations:",
    ]
    lines.extend(f"- {operation['kind']}: {json.dumps(operation, ensure_ascii=False, sort_keys=True)}" for operation in proposal["operations"])
    lines.append("Edits (applied only with explicit confirmation):")
    lines.extend(f"- {edit['file']}:{edit['start_line']}-{edit['end_line']}" for edit in proposal["edits"])
    return "\n".join(lines) + "\n"


def write_proposal(path: str | Path, proposal: Mapping[str, Any]) -> Path:
    validate_proposal(proposal)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(canonical_json_bytes(proposal) + b"\n")
    temporary.replace(destination)
    return destination


def load_proposal(path: str | Path) -> dict[str, Any]:
    try:
        proposal = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProposalError(f"cannot read coverage proposal: {exc}") from exc
    validate_proposal(proposal)
    return proposal


def apply_proposal(
    proposal: Mapping[str, Any],
    catalog: Mapping[str, Any],
    *,
    source_root: str | Path,
    confirm: bool = False,
) -> tuple[Path, ...]:
    """Apply already-reviewed literal edits after strict freshness checks.

    The caller must provide a freshly scanned catalog.  This adapter never
    generates Python syntax from semantic operations; it only applies the
    exact, reviewed text stored in the proposal.
    """
    validate_proposal(proposal)
    if not confirm:
        raise ProposalError("source application requires confirm=True")
    validate_catalog(catalog)
    preconditions = proposal["preconditions"]
    group = _group(catalog, preconditions["covergroup_type_id"])
    if group["declaration_semantic_digest"] != preconditions["declaration_semantic_digest"]:
        raise ProposalError("proposal declaration digest no longer matches freshly scanned catalog")
    source_file = _relative_file(preconditions["source_anchor"]["file"])
    root = Path(source_root).resolve()
    target = (root / source_file).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ProposalError("proposal source path escapes source_root") from exc
    if not target.exists():
        raise ProposalError(f"proposal source file does not exist: {source_file}")
    edits = [_validate_edit(edit, source_file) for edit in proposal["edits"]]
    lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    # Validate every range before writing anything; reverse application keeps
    # recorded line numbers stable when a proposal carries multiple edits.
    for edit in edits:
        actual = "".join(lines[edit["start_line"] - 1:edit["end_line"]])
        if actual != edit["expected_text"]:
            raise ProposalError(f"proposal source text is stale at {source_file}:{edit['start_line']}")
    for edit in sorted(edits, key=lambda item: item["start_line"], reverse=True):
        lines[edit["start_line"] - 1:edit["end_line"]] = edit["replacement"].splitlines(keepends=True)
    temporary = target.with_suffix(target.suffix + ".svtypes-proposal.tmp")
    temporary.write_text("".join(lines), encoding="utf-8")
    temporary.replace(target)
    return (target,)

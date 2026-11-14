"""Explicit backend discovery, boundary validation, and safe artifact writes."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import importlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile
from types import MappingProxyType
from typing import Any

from svtypes_design_manifest import canonical_json_bytes, load_design_manifest

from .api import Artifact, ArtifactSet, Backend, BackendMetadata, Diagnostic


ARTIFACT_MANIFEST = "svtypes-artifacts.json"


class BackendError(RuntimeError):
    """A backend cannot validate, render, or safely publish its artifacts."""


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _canonical_options(options: Mapping[str, Any] | None) -> dict[str, Any]:
    if options is None:
        return {}
    if not isinstance(options, Mapping):
        raise BackendError("backend options must be a mapping")
    try:
        return json.loads(canonical_json_bytes(dict(options)))
    except (TypeError, ValueError) as exc:
        raise BackendError(f"backend options are not JSON-shaped: {exc}") from exc


def load_backend(reference: str) -> Backend:
    """Load exactly the requested ``module:attribute`` backend object."""
    module_name, separator, attribute = reference.partition(":")
    if not separator or not module_name or not attribute:
        raise BackendError("backend reference must have the form module:attribute")
    try:
        value: Any = importlib.import_module(module_name)
        for part in attribute.split("."):
            value = getattr(value, part)
    except (ImportError, AttributeError) as exc:
        raise BackendError(f"cannot load backend {reference!r}: {exc}") from exc
    backend = value() if isinstance(value, type) else value
    metadata = getattr(backend, "metadata", None)
    if not isinstance(metadata, BackendMetadata) or not callable(getattr(backend, "validate", None)) or not callable(getattr(backend, "render", None)):
        raise BackendError(f"backend {reference!r} does not implement the SDK protocol")
    return backend


def _validate_artifacts(artifacts: ArtifactSet) -> tuple[Artifact, ...]:
    if not isinstance(artifacts, ArtifactSet):
        raise BackendError("backend render must return ArtifactSet")
    seen: set[str] = set()
    validated: list[Artifact] = []
    for artifact in artifacts.artifacts:
        if not isinstance(artifact, Artifact):
            raise BackendError("artifact set contains a non-Artifact value")
        path = PurePosixPath(artifact.path)
        if (path.is_absolute() or ".." in path.parts or path.name in {"", "."}
                or "\\" in artifact.path or ":" in artifact.path
                or path.as_posix().casefold() == ARTIFACT_MANIFEST.casefold()):
            raise BackendError(f"backend returned unsafe artifact path {artifact.path!r}")
        normalized = path.as_posix()
        if normalized.casefold() in seen:
            raise BackendError(f"backend returned duplicate artifact path {normalized!r}")
        seen.add(normalized.casefold())
        validated.append(Artifact(normalized, artifact.content, artifact.media_type, artifact.role))
    return tuple(validated)


def _diagnostic_errors(diagnostics: tuple[Diagnostic, ...]) -> tuple[Diagnostic, ...]:
    if any(not isinstance(item, Diagnostic) for item in diagnostics):
        raise BackendError("backend validation must return Diagnostic values")
    return tuple(item for item in diagnostics if item.severity == "error")


def _artifact_manifest(metadata: BackendMetadata, options: Mapping[str, Any], artifacts: tuple[Artifact, ...]) -> bytes:
    value = {
        "artifacts": [
            {
                "media_type": item.media_type,
                "path": item.path,
                "role": item.role,
                "sha256": hashlib.sha256(item.content).hexdigest(),
            }
            for item in sorted(artifacts, key=lambda item: item.path)
        ],
        "backend": {"id": metadata.backend_id, "version": metadata.version},
        "options": _thaw(options),
    }
    return canonical_json_bytes(value) + b"\n"


def _owned_paths(destination: Path) -> dict[str, str]:
    path = destination / ARTIFACT_MANIFEST
    if not path.exists():
        return {}
    if path.is_symlink():
        raise BackendError("artifact manifest must not be a symlink")
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))["artifacts"]
        _validate_artifacts(ArtifactSet(tuple(Artifact(item["path"], b"") for item in entries)))
        if any(not isinstance(item["sha256"], str) for item in entries):
            raise ValueError("missing artifact hash")
        return {item["path"]: item["sha256"] for item in entries}
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise BackendError("existing artifact manifest is invalid") from error


def _contained_path(destination: Path, name: str) -> Path:
    path = destination
    for part in PurePosixPath(name).parts:
        path = path / part
        if path.is_symlink():
            raise BackendError(f"artifact path traverses a symlink: {name!r}")
    return path


def _publish(destination: Path, artifacts: tuple[Artifact, ...], manifest: bytes,
             *, overwrite_modified: bool = False) -> None:
    if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
        raise BackendError("artifact destination must be a real directory")
    owned = _owned_paths(destination)
    names = {item.path for item in artifacts}
    for artifact in artifacts:
        path = _contained_path(destination, artifact.path)
        if path.exists():
            if not path.is_file():
                raise BackendError(f"artifact conflicts with a directory: {artifact.path!r}")
            current = path.read_bytes()
            if (current != artifact.content and hashlib.sha256(current).hexdigest() != owned.get(artifact.path)
                    and not overwrite_modified):
                raise BackendError(f"artifact has local edits or is unmanaged: {artifact.path!r}")
    removable = []
    for name, digest in owned.items():
        if name in names:
            continue
        path = _contained_path(destination, name)
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
            removable.append(name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent))
    backup = Path(tempfile.mkdtemp(prefix=f".{destination.name}.backup-", dir=destination.parent))
    backup.rmdir()
    try:
        if destination.exists():
            shutil.copytree(destination, staging, dirs_exist_ok=True, symlinks=True)
        for name in removable:
            (staging / name).unlink()
        for artifact in artifacts:
            output = staging / PurePosixPath(artifact.path)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(artifact.content)
        (staging / ARTIFACT_MANIFEST).write_bytes(manifest)
        if destination.exists():
            destination.replace(backup)
        staging.replace(destination)
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        if not destination.exists() and backup.exists():
            backup.replace(destination)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def build(
    backend: Backend,
    design: Mapping[str, Any],
    output_dir: str | Path,
    *,
    options: Mapping[str, Any] | None = None,
    overwrite_modified: bool = False,
) -> dict[str, Any]:
    """Validate, render, and atomically publish one backend artifact set."""
    metadata = backend.metadata
    if design.get("format") not in metadata.manifest_formats:
        raise BackendError(f"backend {metadata.backend_id!r} does not support manifest format {design.get('format')!r}")
    frozen_design = _freeze(_thaw(design))
    frozen_options = _freeze(_canonical_options(options))
    capabilities = set(frozen_design.get("capabilities", ()))
    missing = [item for item in metadata.required_capabilities if item not in capabilities]
    if missing:
        raise BackendError(f"backend {metadata.backend_id!r} requires unsupported capability {missing[0]!r}")
    diagnostics = tuple(backend.validate(frozen_design))
    errors = _diagnostic_errors(diagnostics)
    if errors:
        raise BackendError(f"backend validation failed: {errors[0].code}: {errors[0].message}")
    try:
        artifacts = _validate_artifacts(backend.render(frozen_design, frozen_options))
    except BackendError:
        raise
    except Exception as exc:
        raise BackendError(f"backend render failed: {exc}") from exc
    manifest = _artifact_manifest(metadata, frozen_options, artifacts)
    _publish(Path(output_dir), artifacts, manifest, overwrite_modified=overwrite_modified)
    return {
        "backend": {"id": metadata.backend_id, "version": metadata.version},
        "diagnostics": [item.to_dict() for item in diagnostics],
        "output_dir": str(Path(output_dir)),
        "artifacts": [item.path for item in sorted(artifacts, key=lambda item: item.path)],
    }


def build_from_files(
    backend_reference: str,
    manifest_path: str | Path,
    output_dir: str | Path,
    *,
    options: Mapping[str, Any] | None = None,
    overwrite_modified: bool = False,
) -> dict[str, Any]:
    """Load the explicit backend and input file, then call :func:`build`."""
    return build(load_backend(backend_reference), load_design_manifest(manifest_path), output_dir,
                 options=options, overwrite_modified=overwrite_modified)


def run_conformance(backend: Backend, design: Mapping[str, Any]) -> None:
    """Run lightweight protocol checks reusable by backend packages."""
    metadata = backend.metadata
    if not isinstance(metadata, BackendMetadata):
        raise BackendError("backend metadata is invalid")
    frozen = _freeze(_thaw(design))
    diagnostics = tuple(backend.validate(frozen))
    _diagnostic_errors(diagnostics)
    _validate_artifacts(backend.render(frozen, _freeze({})))

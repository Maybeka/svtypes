"""Public, JSON-only backend protocol values."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One stable backend diagnostic."""

    code: str
    severity: str
    message: str
    provenance: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.code or self.severity not in {"info", "warning", "error"} or not self.message:
            raise ValueError("diagnostic requires a code, valid severity, and message")

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "severity": self.severity, "message": self.message}
        if self.provenance is not None:
            result["provenance"] = dict(self.provenance)
        return result


@dataclass(frozen=True, slots=True)
class BackendMetadata:
    """Static backend identity and accepted manifest surface."""

    backend_id: str
    version: str
    manifest_formats: tuple[str, ...]
    required_capabilities: tuple[str, ...] = ()
    option_schema: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.backend_id or not self.version or not self.manifest_formats:
            raise ValueError("backend metadata requires id, version, and supported manifest formats")
        if any(not item for item in self.manifest_formats):
            raise ValueError("backend manifest formats must be non-empty strings")
        if tuple(sorted(set(self.required_capabilities))) != self.required_capabilities:
            raise ValueError("backend required capabilities must be sorted and unique")


@dataclass(frozen=True, slots=True)
class Artifact:
    """One output file returned by a backend."""

    path: str
    content: bytes
    media_type: str = "application/octet-stream"
    role: str = "generated"

    def __post_init__(self) -> None:
        if not self.path or not isinstance(self.content, bytes) or not self.media_type or not self.role:
            raise ValueError("artifact requires path, bytes content, media type, and role")


@dataclass(frozen=True, slots=True)
class ArtifactSet:
    """All artifacts produced by one successful backend render."""

    artifacts: tuple[Artifact, ...]

    def __post_init__(self) -> None:
        if not self.artifacts:
            raise ValueError("backend must return at least one artifact")


class Backend(Protocol):
    """Protocol implemented by independently packaged output backends."""

    metadata: BackendMetadata

    def validate(self, design: Mapping[str, Any]) -> tuple[Diagnostic, ...]: ...

    def render(self, design: Mapping[str, Any], options: Mapping[str, Any]) -> ArtifactSet: ...

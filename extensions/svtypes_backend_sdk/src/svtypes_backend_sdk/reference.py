"""A deterministic reference backend used to verify the SDK contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from svtypes_design_manifest import canonical_json_bytes

from .api import Artifact, ArtifactSet, BackendMetadata, Diagnostic


class ReferenceBackend:
    """Emit a stable coverage-oriented view of a Design Manifest."""

    metadata = BackendMetadata(
        backend_id="svtypes.reference-json",
        version="0.1.0",
        manifest_formats=("svtypes.design-manifest/v1",),
    )

    def validate(self, design: Mapping[str, Any]) -> tuple[Diagnostic, ...]:
        return ()

    def render(self, design: Mapping[str, Any], options: Mapping[str, Any]) -> ArtifactSet:
        del options
        content = {
            "coverage": design["coverage"],
            "design": design["design"],
            "format": "svtypes.reference-coverage/v1",
            "manifest_digest": design["design"]["manifest_digest"],
        }
        return ArtifactSet((Artifact("coverage-reference.json", canonical_json_bytes(content) + b"\n", "application/json", "reference"),))


backend = ReferenceBackend()

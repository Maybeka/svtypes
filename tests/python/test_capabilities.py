from __future__ import annotations

import pytest

from svtypes import (
    Bits,
    CompatibilityError,
    RuntimeCapabilities,
    SvObject,
    require_runtime_compatible,
    runtime_capabilities,
)


def test_runtime_capabilities_are_deterministic_and_ignore_unknown_provided_values():
    received = RuntimeCapabilities(provided=("future.extension", *runtime_capabilities().provided))

    assert received.provided == tuple(sorted(received.provided))
    require_runtime_compatible(("svtypes.codec-context.v1",), received)


def test_runtime_capabilities_reject_missing_requirement_and_version_mismatch():
    with pytest.raises(CompatibilityError, match="missing required capabilities"):
        require_runtime_compatible(("missing.feature",), runtime_capabilities())
    with pytest.raises(CompatibilityError, match="binary_format_version"):
        require_runtime_compatible((), RuntimeCapabilities(binary_format_version=99))


def test_generated_types_expose_runtime_capabilities():
    class Payload(SvObject):
        value = Bits(8)

    assert "svtypes_runtime_capabilities" in Payload.to_sv_obj()
    assert "svtypes_runtime_capabilities" in Payload.to_cpp_obj()

from __future__ import annotations

import pytest
from pathlib import Path

from svtypes import (
    Bit,
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


def test_runtime_capabilities_include_constraint_names():
    provided = runtime_capabilities().provided
    assert "svtypes.constraint-ir.v1" in provided
    assert "svtypes.constraint-sample.v1" in provided


def test_struct_data_contract_capability_is_available_in_all_runtimes():
    capability = "svtypes.struct-kinds.v1"
    require_runtime_compatible((capability,), runtime_capabilities())
    root = Path(__file__).resolve().parents[2]
    for runtime in ("sv/svtypes_pkg.sv", "cpp/svtypes.hpp"):
        assert f'"{capability}"' in (root / "svtypes_runtime" / runtime).read_text()


def test_generated_types_expose_runtime_capabilities():
    class Payload(SvObject):
        value = Bit[8]()

    assert "svtypes_runtime_capabilities" in Payload.to_sv_obj()
    assert "svtypes_runtime_capabilities" in Payload.to_cpp_obj()

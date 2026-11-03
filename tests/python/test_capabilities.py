from __future__ import annotations

import pytest
from pathlib import Path
import shutil
import subprocess

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


@pytest.mark.parametrize("capability", [
    "svtypes.optional-codec-diagnostics.v1", "svtypes.generated-field-visitors.v1",
    "svtypes.generated-field-operations.v1",
    "svtypes.generated-wire-helpers.v1",
])
def test_editability_runtime_requirements_reject_older_capability_sets(capability):
    require_runtime_compatible((capability,), runtime_capabilities())
    old = RuntimeCapabilities(provided=tuple(name for name in runtime_capabilities().provided if name != capability))
    with pytest.raises(CompatibilityError, match=capability):
        require_runtime_compatible((capability,), old)
    root = Path(__file__).resolve().parents[2]
    for runtime in ("sv/svtypes_pkg.sv", "cpp/svtypes.hpp"):
        assert f'"{capability}"' in (root / "svtypes_runtime" / runtime).read_text()


def test_generated_types_expose_runtime_capabilities():
    class Payload(SvObject):
        value = Bit[8]()

    assert "svtypes_runtime_capabilities" in Payload.to_sv_obj()
    assert "svtypes_runtime_capabilities" in Payload.to_cpp_obj()


def test_cpp_rejects_runtime_without_generated_wire_helpers(tmp_path):
    source = tmp_path / "capabilities.cpp"
    source.write_text(r'''
#include "svtypes.hpp"
#include <cassert>
int main() {
    const std::string required = "svtypes.generated-wire-helpers.v1";
    auto received = svtypes::runtime_capabilities();
    svtypes::require_runtime_compatible({required}, received);
    received.provided.erase(std::remove(received.provided.begin(), received.provided.end(), required),
                            received.provided.end());
    try { svtypes::require_runtime_compatible({required}, received); assert(false); }
    catch (const std::runtime_error& error) {
        assert(std::string(error.what()).find(required) != std::string::npos);
    }
}
''')
    compiler = shutil.which("c++")
    assert compiler
    root = Path(__file__).resolve().parents[2]
    executable = tmp_path / "capabilities"
    subprocess.run([compiler, "-std=c++20", "-I", str(root / "svtypes_runtime/cpp"),
                    str(source), "-o", str(executable)], check=True, capture_output=True, text=True)
    subprocess.run([str(executable)], check=True, capture_output=True, text=True)

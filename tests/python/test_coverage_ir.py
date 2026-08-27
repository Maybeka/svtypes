from __future__ import annotations

import pytest

from svtypes.coverage.canonical import canonical_json_bytes, semantic_digest
from svtypes.coverage.ir import (
    CoverageBinIR,
    CoverageCrossIR,
    CoverageIR,
    CoveragePointIR,
    CoverageProvenance,
    SampleParameterIR,
)


def _ir(*, bins: tuple[CoverageBinIR, ...] = ()) -> CoverageIR:
    return CoverageIR(
        sample_type="Packet",
        declaration_name="opcode_cg",
        constructor_parameters=(SampleParameterIR("max_opcode", "U8"),),
        sample_parameters=(SampleParameterIR("item", "Packet"),),
        points=(
            CoveragePointIR(
                "opcode",
                expression={"field": "item.opcode"},
                bins=bins,
                options=(("goal", 100),),
            ),
        ),
        crosses=(CoverageCrossIR("opcode_kind", ("opcode", "kind")),),
    )


def test_coverage_type_id_is_stable_while_definition_digest_changes():
    initial = _ir(bins=(CoverageBinIR("read", "normal", (0,)),))
    evolved = _ir(bins=(CoverageBinIR("write", "normal", (1,)),))

    assert initial.covergroup_type_id == "Packet::opcode_cg"
    assert evolved.covergroup_type_id == initial.covergroup_type_id
    assert evolved.declaration_semantic_digest != initial.declaration_semantic_digest


def test_semantic_snapshot_is_canonical_and_excludes_provenance_and_bindings():
    ir = _ir(bins=(CoverageBinIR("read", "normal", (0,)),))
    moved = CoverageProvenance("other.py", 92, 4, "Other.opcode_cg")

    assert moved.filename == "other.py"
    assert "provenance" not in ir.definition_snapshot()
    assert "actual" not in ir.definition_snapshot()
    assert canonical_json_bytes(ir.definition_snapshot()) == canonical_json_bytes(
        ir.definition_snapshot()
    )
    assert ir.declaration_semantic_digest == semantic_digest(ir.definition_snapshot())


def test_canonical_values_reject_runtime_objects_and_sort_mapping_keys():
    assert canonical_json_bytes({"z": 1, "a": [True, None]}) == b'{"a":[true,null],"z":1}'
    with pytest.raises(TypeError, match="non-canonical"):
        canonical_json_bytes({"actual": object()})

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
            CoveragePointIR("kind", expression={"field": "item.kind"}),
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


def test_named_declaration_items_are_canonicalized_but_cross_member_order_is_not():
    first = CoverageIR(
        sample_type="Packet",
        declaration_name="cg",
        points=(
            CoveragePointIR("z", {"field": "item.z"}, (CoverageBinIR("b", "normal", (1,)),)),
            CoveragePointIR("a", {"field": "item.a"}, (CoverageBinIR("a", "normal", (0,)),)),
        ),
        crosses=(CoverageCrossIR("cross", ("z", "a")),),
    )
    reordered = CoverageIR(
        sample_type="Packet",
        declaration_name="cg",
        points=(
            CoveragePointIR("a", {"field": "item.a"}, (CoverageBinIR("a", "normal", (0,)),)),
            CoveragePointIR("z", {"field": "item.z"}, (CoverageBinIR("b", "normal", (1,)),)),
        ),
        crosses=(CoverageCrossIR("cross", ("z", "a")),),
    )
    different_member_order = CoverageIR(
        sample_type="Packet",
        declaration_name="cg",
        points=reordered.points,
        crosses=(CoverageCrossIR("cross", ("a", "z")),),
    )

    assert [point.name for point in first.points] == ["a", "z"]
    assert first.declaration_semantic_digest == reordered.declaration_semantic_digest
    assert first.declaration_semantic_digest != different_member_order.declaration_semantic_digest


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (
            lambda: CoveragePointIR(
                "point",
                {"field": "item.a"},
                (CoverageBinIR("dup", "normal"), CoverageBinIR("dup", "ignore")),
            ),
            "duplicate bin",
        ),
        (
            lambda: CoveragePointIR("point", {"field": "item.a"}, options=(("goal", 1), ("goal", 2))),
            "duplicate option",
        ),
        (lambda: CoverageBinIR("bin", "unknown"), "unsupported kind"),
        (
            lambda: CoverageIR(
                "Packet",
                "cg",
                points=(CoveragePointIR("point", {"field": "item.a"}),),
                crosses=(CoverageCrossIR("cross", ("missing",)),),
            ),
            "unknown point",
        ),
    ],
)
def test_freeze_validation_rejects_ambiguous_or_unsupported_semantics(factory, message):
    with pytest.raises((TypeError, ValueError), match=message):
        factory()

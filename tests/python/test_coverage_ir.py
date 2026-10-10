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


def test_validation_only_path_matches_canonical_acceptance_and_errors():
    from collections import OrderedDict
    from svtypes.coverage.canonical import _validate_canonical_value, canonical_value
    from svtypes.logic import LogicValue

    values = [None, True, 7, -3, "name", LogicValue(4, 1, 2, 4),
              {"z": [1, (None, False)], "a": OrderedDict(x=2)},
              1.5, b"bytes", {1: 2}, {"nested": [object()]}, {1, 2}]
    for value in values:
        try:
            expected = canonical_value(value)
        except TypeError as error:
            with pytest.raises(TypeError) as actual:
                _validate_canonical_value(value)
            assert str(actual.value) == str(error)
        else:
            assert _validate_canonical_value(value) is None
            assert canonical_value(value) == expected


def test_declaration_validation_does_not_build_discarded_canonical_copies(monkeypatch):
    import svtypes.coverage.ir as module

    def unexpected_copy(value):
        raise AssertionError("declaration validation constructed a discarded canonical copy")

    monkeypatch.setattr(module, "canonical_value", unexpected_copy)
    point = CoveragePointIR("p", {"kind": "name", "name": "item"},
                            (CoverageBinIR("one", "normal", {"kind": "constant", "value": 1}),),
                            options=(("goal", 100),))
    assert CoverageIR("Packet", "cg", points=(point,)).points == (point,)


def test_materialized_layout_is_equal_and_isolated_with_validation_only(monkeypatch):
    import svtypes.coverage.ir as module
    import svtypes.coverage.declaration as declaration
    from svtypes.coverage.canonical import canonical_value
    from svtypes.coverage.declaration import _materialize_ir

    selector = {"kind": "cross_bin_refs", "items": [{"point": "opcode", "bin": "read"},
                                                    {"point": "kind", "bin": "write"}]}
    template = CoverageIR("Packet", "cg", points=(
        CoveragePointIR("opcode", {"kind": "name", "name": "limit"}),
        CoveragePointIR("kind", {"kind": "name", "name": "item"}),
    ), crosses=(CoverageCrossIR("combined", ("opcode", "kind"),
                                (CoverageBinIR("pair", "normal", selector),)),))
    optimized = _materialize_ir(template, {"limit": 7}, {})
    with monkeypatch.context() as context:
        context.setattr(module, "_validate_canonical_value", canonical_value)
        context.setattr(declaration, "_materialize_cross_selector", declaration._materialize_value)
        baseline = _materialize_ir(template, {"limit": 7}, {})
    assert optimized.definition_snapshot() == baseline.definition_snapshot()
    assert optimized.declaration_semantic_digest == baseline.declaration_semantic_digest
    optimized.crosses[0].bins[0].selector["items"][0]["bin"] = "changed"
    assert baseline.crosses[0].bins[0].selector["items"][0]["bin"] == "read"
    assert template.crosses[0].bins[0].selector["items"][0]["bin"] == "read"


def test_static_cross_selector_copy_and_general_fallback_are_equivalent():
    from svtypes.coverage.declaration import _materialize_cross_selector, _materialize_value

    selectors = [
        {"kind": "cross_bin_refs", "items": [{"point": "p", "bin": "b"}]},
        {"kind": "cross_bin_refs", "items": [], "extra": {"kind": "name", "name": "arg"}},
        {"kind": "cross_bin_refs", "items": [{"point": "p", "bin": {"kind": "name", "name": "arg"}}]},
        {"kind": "cross_queue_call", "function": "fn", "args": [{"kind": "name", "name": "arg"}]},
    ]
    for selector in selectors:
        actual = _materialize_cross_selector(selector, {"arg": 9}, {})
        assert actual == _materialize_value(selector, {"arg": 9}, {})
        assert actual is not selector
        if "items" in selector:
            assert actual["items"] is not selector["items"]
            for original, copied in zip(selector["items"], actual["items"]):
                assert copied is not original


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

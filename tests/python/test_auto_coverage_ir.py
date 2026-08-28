from __future__ import annotations

import pytest

from svtypes import Array, AssocArray, Bit, DynArray, Object, Queue, String, SvObject
from svtypes.coverage import AUTO_COVERGROUP_NAME, auto_coverage_ir
from svtypes.errors import CoverageDeclarationError


def test_effective_cov_fields_compile_into_one_default_coverage_group():
    class Packet(SvObject):
        opcode = Bit(8)
        fixed = Array(Bit(8), 2, cov=True)
        dynamic = DynArray(Bit(8), cov=True)
        queue = Queue(Bit(8), cov=True, cov_slots=3)
        lookup = AssocArray(Bit(8), Bit(8), cov=True)

    coverage = auto_coverage_ir(Packet)

    assert coverage is not None
    assert coverage.declaration_name == AUTO_COVERGROUP_NAME
    points = {point.name: point.expression for point in coverage.points}
    assert points["opcode"] == {"kind": "field", "path": "item.opcode"}
    assert points["fixed[0]"] == {"index": 0, "kind": "slot", "path": "item.fixed"}
    assert points["fixed[1]"] == {"index": 1, "kind": "slot", "path": "item.fixed"}
    assert points["dynamic"] == {"kind": "container_values", "path": "item.dynamic"}
    assert points["queue[2]"] == {"index": 2, "kind": "slot", "path": "item.queue"}
    assert points["lookup"] == {"kind": "assoc_values", "path": "item.lookup"}


def test_no_effective_cov_fields_produces_no_default_group():
    class Plain(SvObject):
        text = String()
        ignored = Bit(8, cov=False)

    assert auto_coverage_ir(Plain) is None


def test_object_handles_keep_the_existing_nullness_auto_coverage_semantics():
    class Packet(SvObject):
        child = Object("Child")

    coverage = auto_coverage_ir(Packet)

    assert coverage is not None
    assert coverage.points[0].name == "child"
    assert coverage.points[0].expression == {"kind": "is_null", "path": "item.child"}

    class Child(SvObject):
        value = Bit(8)

    class ByValueDisabled(SvObject):
        child = Child(cov=False)

    assert auto_coverage_ir(ByValueDisabled) is None


def test_cov_slots_is_restricted_to_dynamic_array_and_queue_fields():
    with pytest.raises(ValueError, match="cov_slots is only supported"):
        class Invalid(SvObject):
            fixed = Array(Bit(8), 2, cov=True, cov_slots=2)

    class Disabled(SvObject):
        values = DynArray(Bit(8), cov=False, cov_slots=2)

    with pytest.raises(CoverageDeclarationError, match="SVT-COV-SLOTS"):
        auto_coverage_ir(Disabled)

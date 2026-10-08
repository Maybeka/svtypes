from __future__ import annotations

import pytest

from svtypes import Array, AssocArray, Bit, DynArray, Enum, Logic, Object, Queue, String, SvObject
from svtypes.coverage import AUTO_COVERGROUP_NAME, auto_coverage_ir
from svtypes.errors import CoverageDeclarationError


def test_effective_cov_fields_compile_into_one_default_coverage_group():
    class Packet(SvObject):
        opcode = Bit[8]()
        fixed = Array[Bit[8], 2](cov=True)
        dynamic = DynArray[Bit[8]](cov=True)
        queue = Queue[Bit[8]](cov=True, cov_slots=3)
        lookup = AssocArray[Bit[8], Bit[8]](cov=True)

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
        ignored = Bit[8](cov=False)

    assert auto_coverage_ir(Plain) is None


def test_multidimensional_fixed_auto_coverage_retains_each_leaf_slot():
    class Packet(SvObject):
        matrix = Array[Bit[1], (2, 2)](cov=True)

    packet = Packet()
    packet.svtypes_auto_cov.sample()
    snapshot = packet.svtypes_auto_cov.instance.snapshot()
    assert set(snapshot) == {"matrix[0][0]", "matrix[0][1]", "matrix[1][0]", "matrix[1][1]"}
    assert all(point["hits"] == {"auto[0]": 1} for point in snapshot.values())
    packet.matrix.value = [[1, 1], [1, 1]]
    packet.svtypes_auto_cov.sample()
    snapshot = packet.svtypes_auto_cov.instance.snapshot()
    assert all(point["hits"] == {"auto[0]": 1, "auto[1]": 1} for point in snapshot.values())


def test_indexed_dynamic_auto_coverage_samples_selected_leaf_values():
    class Packet(SvObject):
        fixed = Array[Queue[Bit[1]], 2](cov=True)
        keyed = Array[AssocArray[String, Bit[1]], 2](cov=True)
        slotted = Queue[Queue[Bit[1]]](cov=True, cov_slots=2)
        fixed_slots = Queue[Array[Bit[1], 2]](cov=True, cov_slots=2)

    packet = Packet()
    packet.fixed.value = [[0, 1], []]
    packet.keyed.value = [{'one': 1}, {}]
    packet.slotted.value = [[0, 1]]
    packet.fixed_slots.value = [[0, 1]]
    packet.svtypes_auto_cov.sample()
    snapshot = packet.svtypes_auto_cov.instance.snapshot()
    assert snapshot['fixed[0]']['hits'] == {'auto[0]': 1, 'auto[1]': 1}
    assert snapshot['fixed[1]']['samples'] == 0
    assert snapshot['keyed[0]']['hits'] == {'auto[1]': 1}
    assert snapshot['keyed[1]']['samples'] == 0
    assert snapshot['slotted[0]']['hits'] == {'auto[0]': 1, 'auto[1]': 1}
    assert snapshot['slotted[1]']['samples'] == 0
    assert snapshot['fixed_slots[0][0]']['hits'] == {'auto[0]': 1}
    assert snapshot['fixed_slots[0][1]']['hits'] == {'auto[1]': 1}
    assert snapshot['fixed_slots[1][0]']['samples'] == 0


def test_default_auto_coverage_group_is_bound_and_samples_scalar_and_dynamic_values():
    class Packet(SvObject):
        opcode = Bit[2]()
        dynamic = DynArray[Bit[2]](cov=True)

    packet = Packet()
    packet.opcode.value = 1
    packet.dynamic.value = [0, 3]
    packet.svtypes_auto_cov.sample()
    snapshot = packet.svtypes_auto_cov.instance.snapshot()
    assert snapshot["opcode"]["hits"] == {"auto[1]": 1}
    assert snapshot["dynamic"]["hits"] == {"auto[0]": 1, "auto[3]": 1}


def test_default_auto_coverage_tracks_handle_nullness_and_excludes_xz_from_two_state_bins():
    class Packet(SvObject):
        state = Logic[1]()
        child = Object["Child"]()

    packet = Packet()
    packet.state.value = "x"
    packet.svtypes_auto_cov.sample()
    packet.state.value = 1
    packet.child = Packet()
    packet.svtypes_auto_cov.sample()
    snapshot = packet.svtypes_auto_cov.instance.snapshot()
    assert snapshot["state"]["hits"] == {"auto[1]": 1}
    assert "child" not in snapshot


def test_automatic_bins_create_one_named_bin_per_enum_value():
    class Opcode(Enum[Bit[8]]):
        READ = 1
        WRITE = 7

    class Packet(SvObject):
        opcode = Opcode()

    packet = Packet()
    packet.opcode.value = Opcode.WRITE
    packet.svtypes_auto_cov.sample()
    assert packet.svtypes_auto_cov.instance.snapshot()["opcode"]["hits"] == {"auto[WRITE]": 1}


def test_object_handles_do_not_create_default_nullness_coverage():
    class Packet(SvObject):
        child = Object["Child"]()

    coverage = auto_coverage_ir(Packet)

    assert coverage is None

    class Child(SvObject):
        value = Bit[8]()

    class ByValueDisabled(SvObject):
        child = Child(cov=False)

    assert auto_coverage_ir(ByValueDisabled) is None


def test_object_container_auto_coverage_tracks_element_nullness():
    class Child(SvObject):
        value = Bit[8]()

    class Packet(SvObject):
        fixed = Array[Object["Child"], 2](cov=True)
        dynamic = DynArray[Object["Child"]](cov=True)
        queue = Queue[Object["Child"]](cov=True)
        lookup = AssocArray[Bit[8], Object["Child"]](cov=True)

    coverage = auto_coverage_ir(Packet)
    assert coverage is not None
    points = {point.name: point.expression for point in coverage.points}
    assert points["fixed[0]"]["kind"] == "slot_is_null"
    assert points["dynamic"]["kind"] == "container_nullness"
    assert points["queue"]["kind"] == "container_nullness"
    assert points["lookup"]["kind"] == "assoc_nullness"

    packet = Packet()
    packet.dynamic.value = [None]
    packet.queue.value = [None]
    packet.lookup.value = {3: None}
    packet.svtypes_auto_cov.sample()
    snapshot = packet.svtypes_auto_cov.instance.snapshot()
    assert snapshot["dynamic"]["hits"] == {"auto[1]": 1}
    assert snapshot["queue"]["hits"] == {"auto[1]": 1}
    assert snapshot["lookup"]["hits"] == {"auto[1]": 1}


def test_cov_slots_is_restricted_to_dynamic_array_and_queue_fields():
    with pytest.raises(ValueError, match="cov_slots is only supported"):
        class Invalid(SvObject):
            fixed = Array[Bit[8], 2](cov=True, cov_slots=2)

    class Disabled(SvObject):
        values = DynArray[Bit[8]](cov=False, cov_slots=2)

    with pytest.raises(CoverageDeclarationError, match="SVT-COV-SLOTS"):
        auto_coverage_ir(Disabled)


def test_nested_auto_coverage_samples_leaf_values_not_keys_or_containers():
    class Packet(SvObject):
        matrix = Queue[Queue[Bit[2]]](cov=True)
        lookup = AssocArray[String, Queue[Bit[2]]](cov=True)
        single = AssocArray[String, Bit[2]](cov=True)

    packet = Packet()
    packet.matrix.value = [[0, 3], [], [1]]
    packet.lookup.value = {"left": [2, 3], "empty": []}
    packet.single.value = {"not-a-value": 1}
    packet.svtypes_auto_cov.sample()
    snapshot = packet.svtypes_auto_cov.instance.snapshot()
    assert snapshot["matrix"]["hits"] == {"auto[0]": 1, "auto[1]": 1, "auto[3]": 1}
    assert snapshot["lookup"]["hits"] == {"auto[2]": 1, "auto[3]": 1}
    assert snapshot["single"]["hits"] == {"auto[1]": 1}
    code = Packet.to_sv_obj()
    assert "sample(bit [1:0] value)" in code
    assert "item.matrix[__svtypes_cov_index_matrix][__svtypes_cov_index_matrix_1]" in code
    assert "item.lookup[__svtypes_cov_index_lookup][__svtypes_cov_index_lookup_1]" in code

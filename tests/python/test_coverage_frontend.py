from __future__ import annotations

import pytest

from svtypes import (
    Bit,
    CovPointArray,
    CovPoint,
    CoverInput,
    CoverRef,
    CoverGroupOption,
    SvObject,
    DynArray,
    bins,
    covergroup,
    default_bins,
    ignore_bins,
    illegal_bins,
    transition_bins,
)
from svtypes.errors import CoverageDeclarationError, CoverageError


def test_freeze_compiles_source_only_points_and_bins_without_executing_declaration():
    class Packet(SvObject):
        opcode = Bit(8)
        valid = Bit(1)

        @covergroup
        def cg(self, limit: CoverInput[int]):
            class option(CoverGroupOption):
                goal = 100

            class opcode_cp(CovPoint, source=self.opcode, iff=lambda: self.valid == 1):
                common = bins[0, 2, 8:15]
                reserved = illegal_bins[3]
                masked = ignore_bins[4]
                other = default_bins

    ir = Packet.cg.freeze()

    assert ir.declaration_name == "cg"
    assert ir.constructor_parameters[0].name == "limit"
    point = ir.points[0]
    assert point.name == "opcode_cp"
    assert [item.kind for item in point.bins] == ["normal", "ignore", "default", "illegal"]
    assert point.iff is not None


def test_freeze_rejects_statements_outside_the_source_only_declaration_subset():
    class Packet(SvObject):
        opcode = Bit(8)

        @covergroup
        def cg(self):
            forbidden = 1

    with pytest.raises(CoverageDeclarationError, match="SVT-COV-SYNTAX"):
        Packet.cg.freeze()


def test_python_runtime_classifies_iff_ignore_illegal_overlapping_normal_and_default_bins():
    class Packet(SvObject):
        opcode = Bit(8)
        valid = Bit(1)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode, iff=lambda: self.valid == 1):
                low = bins[0:3]
                even = bins[0, 2, 4]
                masked = ignore_bins[1]
                reserved = illegal_bins[3]
                other = default_bins

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    packet = Packet()
    packet.opcode.value = 2
    packet.valid.value = 1
    packet.cg.sample()
    packet.opcode.value = 1
    packet.cg.sample()
    packet.opcode.value = 3
    packet.cg.sample()
    packet.opcode.value = 9
    packet.cg.sample()
    packet.valid.value = 0
    packet.cg.sample()

    result = packet.cg.instance.snapshot()["opcode_cp"]
    assert result["samples"] == 4
    assert result["hits"] == {"even": 1, "low": 1, "other": 1}
    assert result["illegal_hits"] == {"reserved": 1}


def test_coverage_instance_methods_control_sampling_and_report_name():
    class Packet(SvObject):
        opcode = Bit(2)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode):
                zero = bins[0]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    packet = Packet()
    packet.cg.stop()
    packet.cg.sample()
    packet.cg.start()
    packet.cg.sample()
    packet.cg.set_inst_name("tb.packet.coverage")
    packet.cg.option.comment = "packet counters"

    assert packet.cg.instance.snapshot()["opcode_cp"]["samples"] == 1
    assert packet.cg.get_inst_coverage() == packet.cg.get_coverage() == 100.0
    assert packet.cg.snapshot_document()["instance_name"] == "tb.packet.coverage"
    assert packet.cg.snapshot_document()["comment"] == "packet counters"
    with pytest.raises(CoverageError, match="SVT-COV-OPTION-FROZEN"):
        packet.cg.option.goal = 90


def test_freeze_generates_deterministic_automatic_bins_from_a_bit_field_domain():
    class Packet(SvObject):
        opcode = Bit(4)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode):
                pass

    bins_ir = Packet.cg.freeze().points[0].bins

    assert {item.name for item in bins_ir} == {f"auto[{index}]" for index in range(16)}


def test_automatic_bins_honor_point_limit_and_assign_remainder_to_final_bin():
    class Packet(SvObject):
        opcode = Bit(4)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode):
                class option(CovPointOption):
                    auto_bin_max = 3

    bins_ir = Packet.cg.freeze().points[0].bins
    assert [(item.name, item.selector) for item in bins_ir] == [
        ("auto[0:4]", {"kind": "range", "lower": {"kind": "constant", "value": 0}, "upper": {"kind": "constant", "value": 4}}),
        ("auto[10:15]", {"kind": "range", "lower": {"kind": "constant", "value": 10}, "upper": {"kind": "constant", "value": 15}}),
        ("auto[5:9]", {"kind": "range", "lower": {"kind": "constant", "value": 5}, "upper": {"kind": "constant", "value": 9}}),
    ]


def test_nested_sample_formals_are_bound_at_each_sample_without_executing_function():
    class Packet(SvObject):
        @covergroup
        def cg(self):
            def sample(opcode: Bit) -> None:
                class opcode_cp(CovPoint, source=opcode):
                    zero = bins[0]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    packet = Packet()
    packet.cg.sample(Bit(1, value=0))
    packet.cg.sample(opcode=Bit(1, value=1))
    assert packet.cg.instance.snapshot()["opcode_cp"] == {
        "hits": {"zero": 1}, "illegal_hits": {}, "samples": 2,
    }


def test_array_points_freeze_to_fixed_slots_and_skip_missing_dynamic_elements():
    class Packet(SvObject):
        values = DynArray(Bit(8))

        @covergroup
        def cg(self):
            class value_cp(CovPointArray, source=self.values, length=3):
                low = bins[0:3]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    packet = Packet()
    packet.values.value = [1, 9]
    packet.cg.sample()
    points = packet.cg.declaration.ir.points
    assert [point.name for point in points] == ["value_cp[0]", "value_cp[1]", "value_cp[2]"]
    snapshot = packet.cg.instance.snapshot()
    assert snapshot["value_cp[0]"]["hits"] == {"low": 1}
    assert snapshot["value_cp[1]"]["hits"] == {}
    assert snapshot["value_cp[2]"]["samples"] == 0


def test_dynamic_container_value_domain_samples_each_current_element_into_one_point():
    class Packet(SvObject):
        values = DynArray(Bit(8))

        @covergroup
        def cg(self):
            class values_cp(CovPoint, source=self.values):
                low = bins[0:3]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    packet = Packet()
    packet.values.value = [1, 9, 2]
    packet.cg.sample()

    result = packet.cg.instance.snapshot()["values_cp"]
    assert result["samples"] == 3
    assert result["hits"] == {"low": 2}


def test_coverref_reads_its_bound_host_field_on_each_sample():
    class Packet(SvObject):
        mode = Bit(2)

        @covergroup
        def cg(self, mode: CoverRef[Bit]):
            class mode_cp(CovPoint, source=mode):
                read = bins[0]
                write = bins[1]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    packet = Packet()
    packet.mode.value = 0
    packet.cg.sample()
    packet.mode.value = 1
    packet.cg.sample()

    assert packet.cg.instance.snapshot()["mode_cp"]["hits"] == {"read": 1, "write": 1}


def test_coverage_uses_at_least_and_goal_options():
    class Packet(SvObject):
        opcode = Bit(2)

        @covergroup
        def cg(self):
            class option(CoverGroupOption):
                goal = 50

            class opcode_cp(CovPoint, source=self.opcode):
                class option:
                    at_least = 2
                zero = bins[0]
                one = bins[1]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    packet = Packet()
    packet.opcode.value = 0
    packet.cg.sample()
    assert packet.cg.get_coverage() == 0.0
    packet.cg.sample()
    assert packet.cg.get_coverage() == 100.0


def test_transition_bin_matches_only_after_its_finite_sequence_is_observed():
    class Packet(SvObject):
        opcode = Bit(2)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode):
                rise = transition_bins[0, 1]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    packet = Packet()
    packet.opcode.value = 0
    packet.cg.sample()
    assert packet.cg.instance.snapshot()["opcode_cp"]["hits"] == {}
    packet.opcode.value = 1
    packet.cg.sample()
    assert packet.cg.instance.snapshot()["opcode_cp"]["hits"] == {"rise": 1}


def test_coverage_snapshot_json_is_deterministic_and_carries_declaration_identity():
    class Packet(SvObject):
        opcode = Bit(2)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode):
                zero = bins[0]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    packet = Packet()
    packet.opcode.value = 0
    packet.cg.sample()
    document = packet.cg.snapshot_document()

    assert document["covergroup_type_id"].endswith("::cg")
    assert len(document["declaration_semantic_digest"]) == 64
    assert packet.cg.snapshot_json() == packet.cg.snapshot_json()

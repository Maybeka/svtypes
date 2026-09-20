from __future__ import annotations

import pytest

from svtypes import (
    Bit,
    CovPointArray,
    CovPoint,
    CovPointOption,
    CoverInput,
    CoverRef,
    CoverGroupOption,
    Cross,
    CrossOption,
    CrossQueueType,
    Enum,
    SvObject,
    DynArray,
    Logic,
    LogicValue,
    Parameter,
    bins,
    covergroup,
    coverage_init,
    preview_coverage_layout,
    default_bins,
    ignore_bins,
    illegal_bins,
    repeat,
    set_coverage_case_name,
    transition_bins,
)
from svtypes.errors import CoverageDeclarationError, CoverageError
from svtypes.coverage.context import _reset_coverage_case_name_for_testing


class _CoverageColor(Enum, width=8, signed=False):
    R = 0
    G = 1


class _CoverageHue(Enum, width=8, signed=False):
    R = 0


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


def test_static_cross_compiles_explicit_and_automatic_tuples_and_classifies_them():
    class Packet(SvObject):
        opcode = Bit(2)
        mode = Bit(1)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode):
                low = bins[0]
                high = bins[1]

            class mode_cp(CovPoint, source=self.mode):
                read = bins[0]
                write = bins[1]

            class opcode_mode(Cross, members=(opcode_cp, mode_cp)):
                class option(CrossOption):
                    cross_retain_auto_bins = 1

                low_read = bins[opcode_cp.low, mode_cp.read]
                high_write = ignore_bins[opcode_cp.high, mode_cp.write]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    ir = Packet.cg.freeze()
    cross = ir.crosses[0]
    assert cross.members == ("opcode_cp", "mode_cp")
    assert {bin_.name for bin_ in cross.bins} == {
        "low_read", "high_write", "auto[opcode_cp.high,mode_cp.read]",
        "auto[opcode_cp.low,mode_cp.write]",
    }

    packet = Packet()
    packet.opcode.value, packet.mode.value = 0, 0
    packet.cg.sample()
    packet.opcode.value, packet.mode.value = 1, 0
    packet.cg.sample()
    packet.opcode.value, packet.mode.value = 1, 1
    packet.cg.sample()
    assert packet.cg.instance.snapshot()["opcode_mode"] == {
        "hits": {
            "auto[opcode_cp.high,mode_cp.read]": 1,
            "low_read": 1,
        },
        "illegal_hits": {},
        "samples": 3,
    }
    assert packet.cg.get_coverage() == pytest.approx(88.88888888888889)


def test_cross_local_covpoint_replaces_only_that_cross_member_definition():
    class Packet(SvObject):
        opcode = Bit(3)
        mode = Bit(1)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode):
                public = bins[0:7]

            class mode_cp(CovPoint, source=self.mode):
                read = bins[0]
                write = bins[1]

            class opcode_mode(Cross, members=(opcode_cp, mode_cp)):
                class opcode_cp(CovPoint, source=self.opcode):
                    compact = bins[0:1]
                    reserved = illegal_bins[7]

                selected = bins[opcode_cp.compact, mode_cp.read]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    cross = Packet.cg.freeze().crosses[0]
    assert [(view.name, [bin_.name for bin_ in view.point.bins]) for view in cross.member_views] == [
        ("opcode_cp", ["compact", "reserved"]),
    ]
    packet = Packet()
    packet.opcode.value, packet.mode.value = 0, 0
    packet.cg.sample()
    packet.opcode.value, packet.mode.value = 7, 0
    packet.cg.sample()
    snapshot = packet.cg.instance.snapshot()
    # The public point still samples its public definition, while the cross
    # uses only its private replacement and therefore rejects opcode 7.
    assert snapshot["opcode_cp"]["hits"] == {"public": 2}
    assert snapshot["opcode_mode"]["hits"] == {"selected": 1}
    assert packet.cg.instance.runtime.cross_local_illegal_hits == {
        ("opcode_mode", "opcode_cp", "reserved"): 1,
    }
    assert packet.cg.has_illegal_hits()
    assert packet.cg.cross_local_illegal_snapshot() == {
        "opcode_mode": {"opcode_cp": {"reserved": {"hits": 1, "source_ids": []}}},
    }


def test_cross_local_covpoint_rejects_non_member_and_aggregation_options():
    class Packet(SvObject):
        opcode = Bit(1)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode):
                zero = bins[0]

            class cross(Cross, members=(opcode_cp,)):
                class unknown(CovPoint, source=self.opcode):
                    zero = bins[0]

    with pytest.raises(CoverageDeclarationError, match="not a declared member"):
        Packet.cg.freeze()

    class Aggregated(Packet):
        @covergroup
        def cg2(self):
            class opcode_cp(CovPoint, source=self.opcode):
                zero = bins[0]

            class cross(Cross, members=(opcode_cp,)):
                class opcode_cp(CovPoint, source=self.opcode):
                    class option(CovPointOption):
                        weight = 1
                    zero = bins[0]

    with pytest.raises(CoverageDeclarationError, match="aggregation option"):
        Aggregated.cg2.freeze()


def test_cross_default_policy_drops_automatic_tuples_when_explicit_bins_exist():
    class Packet(SvObject):
        opcode = Bit(1)
        mode = Bit(1)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode):
                zero = bins[0]
                one = bins[1]

            class mode_cp(CovPoint, source=self.mode):
                zero = bins[0]
                one = bins[1]

            class cross(Cross, members=(opcode_cp, mode_cp)):
                selected = bins[opcode_cp.zero, mode_cp.zero]

    assert [bin_.name for bin_ in Packet.cg.freeze().crosses[0].bins] == ["selected"]


def test_cross_may_reference_a_fixed_array_bin():
    class Packet(SvObject):
        opcode = Bit(3)
        mode = Bit(1)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode):
                band = bins[0:7].split(2)

            class mode_cp(CovPoint, source=self.mode):
                read = bins[0]
                write = bins[1]

            class opcode_mode(Cross, members=(opcode_cp, mode_cp)):
                first_band_read = bins[opcode_cp.band[0], mode_cp.read]

    cross = Packet.cg.freeze().crosses[0]
    assert cross.bins[0].name == "first_band_read"
    assert cross.bins[0].selector == {
        "kind": "cross_bin_refs",
        "items": [
            {"point": "opcode_cp", "bin": "band[0]"},
            {"point": "mode_cp", "bin": "read"},
        ],
    }


def test_cross_queue_function_materializes_per_instance_and_classifies_values():
    class Packet(SvObject):
        opcode = Bit(2)
        mode = Bit(2)

        @covergroup
        def cg(self, limit: CoverInput[int]):
            class opcode_cp(CovPoint, source=self.opcode):
                value = bins[0:3]

            class mode_cp(CovPoint, source=self.mode):
                value = bins[0:3]

            class matching(Cross, members=(opcode_cp, mode_cp)):
                def diagonal(count: int) -> CrossQueueType:
                    result = CrossQueueType()
                    for index in range(count):
                        result.push_back((index, index))
                    return result

                pairs = bins[diagonal(limit)]

        def __init__(self, limit: int):
            super().__init__()
            self.cg.instantiate(limit)

    packet = Packet(2)
    cross = packet.cg.instance.instance_ir.crosses[0]
    assert cross.bins[0].selector == {
        "kind": "cross_queue_values", "items": [[0, 0], [1, 1]],
    }
    assert packet.cg.instance.instance_layout_digest != Packet(3).cg.instance.instance_layout_digest
    for opcode, mode in ((0, 0), (0, 1), (1, 1)):
        packet.opcode.value, packet.mode.value = opcode, mode
        packet.cg.sample()
    assert packet.cg.instance.snapshot()["matching"] == {
        "hits": {"pairs": 2}, "illegal_hits": {}, "samples": 3,
    }


def test_cross_queue_function_rejects_zero_runtime_range_step():
    class Packet(SvObject):
        opcode = Bit(1)

        @covergroup
        def cg(self, step: CoverInput[int]):
            class point(CovPoint, source=self.opcode):
                zero = bins[0]

            class cross(Cross, members=(point,)):
                def values(increment: int) -> CrossQueueType:
                    result = CrossQueueType()
                    for value in range(0, 1, increment):
                        result.push_back((value,))
                    return result

                selected = bins[values(step)]

        def __init__(self):
            super().__init__()
            self.cg.instantiate(0)

    with pytest.raises(CoverageError, match="range step cannot be zero"):
        Packet()


def test_cross_queue_does_not_hit_when_a_member_value_has_no_point_bin():
    class Packet(SvObject):
        opcode = Bit(2)

        @covergroup
        def cg(self):
            class point(CovPoint, source=self.opcode):
                zero = bins[0]

            class cross(Cross, members=(point,)):
                def values() -> CrossQueueType:
                    result = CrossQueueType()
                    result.push_back((1,))
                    return result

                selected = bins[values()]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    packet = Packet()
    packet.opcode.value = 1
    packet.cg.sample()
    assert packet.cg.instance.snapshot()["cross"] == {
        "hits": {}, "illegal_hits": {}, "samples": 0,
    }


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


def test_get_coverage_is_cumulative_while_get_inst_coverage_is_per_instance():
    class Packet(SvObject):
        opcode = Bit(1)

        @covergroup
        def cg(self):
            class type_option(CoverGroupTypeOption):
                merge_instances = 1

            class opcode_cp(CovPoint, source=self.opcode):
                zero = bins[0]
                one = bins[1]

        def __init__(self, opcode: int):
            super().__init__()
            self.cg.instantiate()
            self.opcode.value = opcode

    left, right = Packet(0), Packet(1)
    left.cg.sample()
    right.cg.sample()
    assert left.cg.get_inst_coverage() == right.cg.get_inst_coverage() == 50.0
    assert left.cg.get_coverage() == right.cg.get_coverage() == 100.0


def test_freeze_generates_deterministic_automatic_bins_from_a_bit_field_domain():
    class Packet(SvObject):
        opcode = Bit(4)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode):
                pass

    bins_ir = Packet.cg.freeze().points[0].bins

    assert {item.name for item in bins_ir} == {f"auto[{index}]" for index in range(16)}


def test_bound_parameter_is_frozen_as_a_coverage_bin_constant():
    class Packet(SvObject):
        opcode = Bit(4)
        reserved_opcode = Parameter()(15)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode):
                reserved = bins[self.reserved_opcode]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    bin_ = Packet.cg.freeze().points[0].bins[0]

    assert bin_.selector == {
        "kind": "parameter_ref",
        "name": "reserved_opcode",
        "type": "int",
    }
    assert "bins reserved = {reserved_opcode};" in Packet.to_sv_obj()
    packet = Packet()
    packet.opcode.value = 15
    packet.cg.sample()
    assert packet.cg.instance.snapshot()["opcode_cp"]["hits"] == {"reserved": 1}


def test_parameter_range_split_materializes_fixed_array_bins_and_sv_count():
    class Packet(SvObject):
        opcode = Bit(4)
        first = Parameter()(4)
        last = Parameter()(7)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode):
                window = bins[self.first:self.last].split(3)

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    template = Packet.cg.freeze().points[0].bins[0]
    assert template.selector["kind"] == "array_split"
    assert "bins window[3] = {[first:last]};" in Packet.to_sv_obj()
    packet = Packet()
    assert [
        (bin_.name, bin_.selector)
        for bin_ in packet.cg.instance.instance_ir.points[0].bins
    ] == [
        ("window[0]", {"kind": "constant", "value": 4}),
        ("window[1]", {"kind": "constant", "value": 5}),
        ("window[2]", {
            "kind": "range",
            "lower": {"kind": "constant", "value": 6},
            "upper": {"kind": "constant", "value": 7},
        }),
    ]


def test_automatic_enum_bins_preserve_member_symbols():
    class Packet(SvObject):
        color = _CoverageColor()

        @covergroup
        def cg(self):
            class color_cp(CovPoint, source=self.color):
                pass

    bins_ir = Packet.cg.freeze().points[0].bins

    assert [(item.name, item.selector) for item in bins_ir] == [
        (
            "auto[G]",
            {
                "kind": "enum_literal",
                "member": "G",
                "type_name": "_CoverageColor",
                "value": 1,
            },
        ),
        (
            "auto[R]",
            {
                "kind": "enum_literal",
                "member": "R",
                "type_name": "_CoverageColor",
                "value": 0,
            },
        ),
    ]
    assert "bins auto_R_ = {R};" in Packet.to_sv_obj()


def test_enum_literal_provenance_requires_an_enum_covpoint_result_type():
    class Packet(SvObject):
        color = _CoverageColor()
        code = Bit(2)

        @covergroup
        def cg(self):
            class color_cp(CovPoint, source=self.color):
                red = bins[_CoverageColor.R]

            class code_cp(CovPoint, source=self.code):
                red_value = bins[_CoverageColor.R]

    points = {point.name: point for point in Packet.cg.freeze().points}

    assert dict(points["color_cp"].options)["comparison_domain"] == {
        "kind": "enum",
        "type_name": "_CoverageColor",
        "width": 8,
        "signed": False,
        "four_state": False,
    }
    assert points["color_cp"].bins[0].selector == {
        "kind": "enum_literal",
        "member": "R",
        "type_name": "_CoverageColor",
        "value": 0,
    }
    assert points["code_cp"].bins[0].selector == {"kind": "constant", "value": 0}


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


def test_transition_repeat_matches_every_finite_repetition_length():
    class Packet(SvObject):
        opcode = Bit(2)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode):
                burst = transition_bins[0, repeat(1, 2, 3), 2]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    packet = Packet()
    for value in (0, 1, 1, 2, 0, 1, 1, 1, 2):
        packet.opcode.value = value
        packet.cg.sample()
    assert packet.cg.instance.snapshot()["opcode_cp"]["hits"] == {"burst": 2}


def test_array_bins_split_expand_compress_and_keep_empty_bins_out_of_denominator():
    class Packet(SvObject):
        opcode = Bit(3)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode):
                expanded = bins[1, 3:4].split()
                fixed = bins[0:7].split(3)
                with_empty = bins[0:1].split(3)

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    point = Packet.cg.freeze().points[0]
    assert {bin_.name for bin_ in point.bins} >= {"expanded[1]", "expanded[3]", "expanded[4]", "fixed[0]", "fixed[1]", "fixed[2]", "with_empty[2]"}
    fixed = {bin_.name: bin_ for bin_ in point.bins}
    assert fixed["fixed[0]"].selector == {
        "kind": "range",
        "lower": {"kind": "constant", "value": 0},
        "upper": {"kind": "constant", "value": 1},
    }
    assert fixed["fixed[2]"].selector == {
        "kind": "range",
        "lower": {"kind": "constant", "value": 4},
        "upper": {"kind": "constant", "value": 7},
    }
    packet = Packet()
    packet.opcode.value = 0
    packet.cg.sample()
    assert packet.cg.instance.snapshot()["opcode_cp"]["hits"] == {"fixed[0]": 1, "with_empty[2]": 1}


def test_array_bins_split_max_bins_compresses_without_dropping_values():
    class Packet(SvObject):
        opcode = Bit(3)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode):
                compressed = bins[0:4].split(max_bins=2)

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    packet = Packet()
    packet.opcode.value = 4
    packet.cg.sample()
    point = packet.cg.instance.snapshot()["opcode_cp"]
    assert point["hits"] == {"compressed[1]": 1}


def test_repeat_is_rejected_outside_transition_bins():
    class Packet(SvObject):
        opcode = Bit(2)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode):
                invalid = bins[repeat(1, 2, 3)]

    with pytest.raises(CoverageDeclarationError, match="only in transition_bins"):
        Packet.cg.freeze()


def test_transition_bins_are_rejected_for_array_and_value_domain_points():
    class Packet(SvObject):
        values = DynArray(Bit(2))

        @covergroup
        def array_cg(self):
            class value_cp(CovPointArray, source=self.values, length=2):
                invalid = transition_bins[0, 1]

        @covergroup
        def domain_cg(self):
            class value_cp(CovPoint, source=self.values):
                invalid = transition_bins[0, 1]

    with pytest.raises(CoverageDeclarationError, match="point array.*cannot declare transition"):
        Packet.array_cg.freeze()
    with pytest.raises(CoverageDeclarationError, match="container value-domain.*cannot declare transition"):
        Packet.domain_cg.freeze()


def test_coverage_case_name_is_global_and_sample_does_not_take_case_metadata():
    class Packet(SvObject):
        opcode = Bit(2)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode):
                zero = bins[0]
                reserved = illegal_bins[3]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    packet = Packet()
    _reset_coverage_case_name_for_testing()
    set_coverage_case_name("smoke")
    packet.opcode.value = 0
    packet.cg.sample()
    packet.opcode.value = 3
    packet.cg.sample()
    result = packet.cg.instance.snapshot()["opcode_cp"]
    assert result["source_ids"] == {"zero": ["smoke"]}
    assert result["illegal_source_ids"] == {"reserved": ["smoke"]}
    with pytest.raises(CoverageError, match="already set"):
        set_coverage_case_name("other")
    with pytest.raises(CoverageError, match="process-wide"):
        packet.cg.sample(case_id="not-allowed")
    _reset_coverage_case_name_for_testing()


def test_cover_input_materializes_instance_bin_selectors_without_changing_declaration_digest():
    class Packet(SvObject):
        opcode = Bit(3)

        @covergroup
        def cg(self, limit: CoverInput[int]):
            class opcode_cp(CovPoint, source=self.opcode):
                limited = bins[0:limit]

        def __init__(self, limit: int):
            super().__init__()
            self.cg.instantiate(limit)

    narrow = Packet(1)
    wide = Packet(3)
    narrow.opcode.value = wide.opcode.value = 2
    narrow.cg.sample()
    wide.cg.sample()
    assert narrow.cg.instance.snapshot()["opcode_cp"]["hits"] == {}
    assert wide.cg.instance.snapshot()["opcode_cp"]["hits"] == {"limited": 1}
    assert narrow.cg.instance.declaration.ir.declaration_semantic_digest == wide.cg.instance.declaration.ir.declaration_semantic_digest
    assert narrow.cg.instance.instance_layout_digest != wide.cg.instance.instance_layout_digest


def test_cover_input_materializes_unbounded_array_bins_into_instance_shape():
    class Packet(SvObject):
        opcode = Bit(2)

        @covergroup
        def cg(self, first: CoverInput[int], last: CoverInput[int]):
            class opcode_cp(CovPoint, source=self.opcode):
                window = bins[first:last].split(max_bins=None)

        @coverage_init
        def configure_coverage(self, first: int, last: int):
            self.cg.instantiate(first, last)

        def __init__(self, first: int, last: int):
            super().__init__()
            self.configure_coverage(first, last)

    low, high = Packet(0, 1), Packet(2, 3)
    assert [bin_.name for bin_ in low.cg.instance.instance_ir.points[0].bins] == ["window[0]", "window[1]"]
    assert [bin_.name for bin_ in high.cg.instance.instance_ir.points[0].bins] == ["window[2]", "window[3]"]
    for value in range(4):
        low.opcode.value = high.opcode.value = value
        low.cg.sample()
        high.cg.sample()
    assert low.cg.instance.snapshot()["opcode_cp"]["hits"] == {"window[0]": 1, "window[1]": 1}
    assert high.cg.instance.snapshot()["opcode_cp"]["hits"] == {"window[2]": 1, "window[3]": 1}
    preview = preview_coverage_layout(Packet, "cg", first=2, last=3)
    assert [bin_.name for bin_ in preview.points[0].bins] == ["window[2]", "window[3]"]


def test_layout_preview_resolves_static_host_member_actuals():
    class Packet(SvObject):
        opcode = Bit(2)
        first = Bit(2, value=1)
        last = Bit(2, value=2)

        @covergroup
        def cg(self, lower: CoverInput[int], upper: CoverInput[int]):
            class opcode_cp(CovPoint, source=self.opcode):
                window = bins[lower:upper].split(max_bins=None)

        @coverage_init
        def configure_coverage(self):
            self.cg.instantiate(self.first, self.last)

    preview = preview_coverage_layout(Packet, "cg", lower=1, upper=2)
    assert [bin_.name for bin_ in preview.points[0].bins] == ["window[1]", "window[2]"]
    overridden = preview_coverage_layout(Packet, "cg", lower=0, upper=3)
    assert [bin_.name for bin_ in overridden.points[0].bins] == [
        "window[0]",
        "window[1]",
        "window[2]",
        "window[3]",
    ]


def test_coverage_options_require_their_declared_base_and_supported_fields():
    class Packet(SvObject):
        opcode = Bit(1)

        @covergroup
        def wrong_base(self):
            class option(CovPointOption):
                goal = 100

        @covergroup
        def unknown_option(self):
            class option(CoverGroupOption):
                strobe = 1

        @covergroup
        def bad_limit(self):
            class opcode_cp(CovPoint, source=self.opcode):
                class option(CovPointOption):
                    auto_bin_max = 0

    with pytest.raises(CoverageDeclarationError, match="must inherit CoverGroupOption"):
        Packet.wrong_base.freeze()
    with pytest.raises(CoverageDeclarationError, match="unsupported option"):
        Packet.unknown_option.freeze()
    with pytest.raises(CoverageDeclarationError, match="positive integer"):
        Packet.bad_limit.freeze()


def test_coverage_freeze_rejects_expression_names_outside_the_declared_sample_scope():
    class Packet(SvObject):
        opcode = Bit(1)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=missing):
                zero = bins[0]

    with pytest.raises(CoverageDeclarationError, match="unknown name 'missing'"):
        Packet.cg.freeze()


def test_optional_sample_log_obeys_record_and_byte_budgets_without_affecting_hits():
    class Packet(SvObject):
        opcode = Bit(1)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode):
                zero = bins[0]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    packet = Packet()
    packet.cg.enable_sample_log(max_records=1, max_bytes=256)
    packet.cg.sample()
    packet.cg.sample()
    assert packet.cg.instance.snapshot()["opcode_cp"]["hits"] == {"zero": 2}
    assert packet.cg.sample_log_snapshot() == [{"case_id": None, "values": {}}]


def test_case_source_limit_is_configurable_only_before_sampling():
    class Packet(SvObject):
        opcode = Bit(1)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode):
                zero = bins[0]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    packet = Packet()
    packet.cg.configure_case_sources(1)
    packet.cg.sample()
    packet.cg.sample()
    assert "source_ids" not in packet.cg.instance.snapshot()["opcode_cp"]
    with pytest.raises(CoverageError, match="before first sample"):
        packet.cg.configure_case_sources(2)


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


def test_array_and_value_domain_points_inherit_automatic_bins_from_element_type():
    class Packet(SvObject):
        values = DynArray(Bit(2))

        @covergroup
        def cg(self):
            class slots(CovPointArray, source=self.values, length=2):
                pass

            class domain(CovPoint, source=self.values):
                pass

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    packet = Packet()
    packet.values.value = [1, 3]
    packet.cg.sample()
    result = packet.cg.instance.snapshot()
    assert result["slots[0]"]["hits"] == {"auto[1]": 1}
    assert result["slots[1]"]["hits"] == {"auto[3]": 1}
    assert result["domain"]["hits"] == {"auto[1]": 1, "auto[3]": 1}


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
                class option(CovPointOption):
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
    assert packet.cg.get_inst_coverage() == 0.0
    packet.opcode.value = 1
    packet.cg.sample()
    assert packet.cg.instance.snapshot()["opcode_cp"]["hits"] == {"rise": 1}
    assert packet.cg.get_inst_coverage() == 100.0


def test_transition_history_does_not_advance_when_coverpoint_iff_is_false():
    class Packet(SvObject):
        code = Bit(2)
        enable = Bit(1)

        @covergroup
        def cg(self):
            class point(CovPoint, source=self.code, iff=lambda: self.enable == 1):
                rise = transition_bins[0, 1]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    packet = Packet()
    packet.enable.value, packet.code.value = 1, 0
    packet.cg.sample()
    packet.enable.value, packet.code.value = 0, 1
    packet.cg.sample()
    packet.enable.value, packet.code.value = 1, 1
    packet.cg.sample()
    assert packet.cg.instance.snapshot()["point"]["hits"] == {"rise": 1}


def test_four_state_sample_does_not_complete_a_two_state_transition():
    class Packet(SvObject):
        signal = Logic(2)

        @covergroup
        def cg(self):
            class point(CovPoint, source=self.signal):
                rise = transition_bins[0, 1]
                exact_x = bins["2'b1x"]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    packet = Packet()
    packet.signal.value = 0
    packet.cg.sample()
    packet.signal.value = LogicValue.from_string("1x")
    packet.cg.sample()
    packet.signal.value = 1
    packet.cg.sample()
    assert packet.cg.instance.snapshot()["point"]["hits"] == {"exact_x": 1}


def test_freeze_rejects_duplicate_default_and_unreachable_transition_sequences():
    class DuplicateDefault(SvObject):
        code = Bit(1)

        @covergroup
        def cg(self):
            class point(CovPoint, source=self.code):
                first = default_bins
                second = default_bins

    class LongTransition(SvObject):
        code = Bit(1)

        @covergroup
        def cg(self):
            class point(CovPoint, source=self.code):
                too_long = transition_bins[0, repeat(1, 16, 16)]

    with pytest.raises(CoverageDeclarationError, match="at most one default"):
        DuplicateDefault.cg.freeze()
    with pytest.raises(CoverageDeclarationError, match="length 2\\.\\.16"):
        LongTransition.cg.freeze()


def test_covergroup_sample_count_is_not_container_element_count():
    class Packet(SvObject):
        data = DynArray(Bit(2))

        @covergroup
        def cg(self):
            class values(CovPoint, source=self.data):
                all_values = bins[0:3]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    packet = Packet()
    packet.data.value = [0, 1, 2]
    packet.cg.sample()
    assert packet.cg.instance.sample_count == 1
    assert packet.cg.instance.snapshot_document()["sample_count"] == 1
    assert packet.cg.instance.snapshot()["values"]["samples"] == 3


def test_explicit_four_state_bin_matches_xz_but_ranges_do_not():
    class Packet(SvObject):
        signal = Logic(2)

        @covergroup
        def cg(self):
            class point(CovPoint, source=self.signal):
                exact_x = bins["1x"]
                numeric = bins[0:3]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    packet = Packet()
    packet.signal.value = LogicValue.from_string("1x")
    packet.cg.sample()
    assert packet.cg.instance.snapshot()["point"]["hits"] == {"exact_x": 1}
    assert "bins exact_x = {2'b1x};" in Packet.to_sv_obj()


def test_bin_literals_are_losslessly_normalized_to_the_source_domain():
    class Unsigned(SvObject):
        code = Bit(8)

        @covergroup
        def cg(self):
            class point(CovPoint, source=self.code):
                typed = bins[Bit(4, value=15)]
                sv = bins["8'hff"]

    ir = Unsigned.cg.freeze()
    assert {bin_.selector["value"] for bin_ in ir.points[0].bins} == {15, 255}

    class Overflow(SvObject):
        code = Bit(8)

        @covergroup
        def cg(self):
            class point(CovPoint, source=self.code):
                invalid = bins[256]

    with pytest.raises(CoverageDeclarationError, match="cannot be losslessly cast"):
        Overflow.cg.freeze()

    class TwoState(SvObject):
        code = Bit(2)

        @covergroup
        def cg(self):
            class point(CovPoint, source=self.code):
                invalid = bins["2'b1x"]

    with pytest.raises(CoverageDeclarationError, match="four-state literal"):
        TwoState.cg.freeze()

    class Signedness(SvObject):
        code = Bit(8)

        @covergroup
        def cg(self):
            class point(CovPoint, source=self.code):
                allowed = bins[Bit(8, signed=True, value=1)]

    assert Signedness.cg.freeze().points[0].bins[0].selector["value"] == 1

    class SignedHex(SvObject):
        code = Bit(8, signed=True)

        @covergroup
        def cg(self):
            class point(CovPoint, source=self.code):
                minus_one = bins["8'shff"]

    assert SignedHex.cg.freeze().points[0].bins[0].selector["value"] == -1

    class XRange(SvObject):
        code = Logic(2)

        @covergroup
        def cg(self):
            class point(CovPoint, source=self.code):
                invalid = bins["2'b0x": "2'b11"]

    with pytest.raises(CoverageDeclarationError, match="range endpoints cannot contain X/Z"):
        XRange.cg.freeze()

    class TypedLogic(SvObject):
        code = Logic(4)

        @covergroup
        def cg(self):
            class point(CovPoint, source=self.code):
                invalid = bins[Logic(4, value="1x")]

    with pytest.raises(CoverageDeclarationError, match="width mismatch"):
        TypedLogic.cg.freeze()


def test_derived_source_uses_a_static_comparison_domain_or_fails_at_freeze():
    class Packet(SvObject):
        code = Bit(8)
        other = Bit(8)

        @covergroup
        def cg(self):
            class point(CovPoint, source=self.code + self.other):
                value = bins[255]

    assert Packet.cg.freeze().points[0].bins[0].selector["value"] == 255

    class UntypedExpression(SvObject):
        code = Bit(8)

        @covergroup
        def cg(self):
            class point(CovPoint, source=self.code + 1):
                bucket = bins[0]

    with pytest.raises(CoverageDeclarationError, match="no statically inferable comparison domain"):
        UntypedExpression.cg.freeze()

    class UntypedSample(SvObject):
        @covergroup
        def cg(self):
            def sample(value: int) -> None:
                class point(CovPoint, source=value):
                    bucket = bins[0]

    with pytest.raises(CoverageDeclarationError, match="no statically inferable comparison domain"):
        UntypedSample.cg.freeze()


def test_transition_and_cross_queue_values_use_member_comparison_domains():
    class TransitionOverflow(SvObject):
        code = Bit(2)

        @covergroup
        def cg(self):
            class point(CovPoint, source=self.code):
                invalid = transition_bins[0, 4]

    with pytest.raises(CoverageDeclarationError, match="cannot be losslessly cast"):
        TransitionOverflow.cg.freeze()

    class QueueOverflow(SvObject):
        code = Bit(2)

        @covergroup
        def cg(self):
            class point(CovPoint, source=self.code):
                all_values = bins[0:3]

            class cross(Cross, members=(point,)):
                def values() -> CrossQueueType:
                    result = CrossQueueType()
                    result.push_back((4,))
                    return result

                invalid = bins[values()]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    with pytest.raises(CoverageDeclarationError, match="cannot be losslessly cast"):
        QueueOverflow()


def test_enum_and_same_domain_integral_bins_both_hit():
    class Packet(SvObject):
        color = _CoverageColor()

        @covergroup
        def cg(self):
            class point(CovPoint, source=self.color):
                red = bins[_CoverageColor.R]
                zero = bins[0]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    ir = Packet.cg.freeze()
    assert {bin_.selector["value"] for bin_ in ir.points[0].bins} == {0}
    packet = Packet()
    packet.color.value = 0
    packet.cg.sample()
    assert packet.cg.instance.snapshot()["point"]["hits"] == {"red": 1, "zero": 1}

    class Mismatch(SvObject):
        color = _CoverageColor()

        @covergroup
        def cg(self):
            class point(CovPoint, source=self.color):
                other = bins[_CoverageHue.R]

    with pytest.raises(CoverageDeclarationError, match="different enum type"):
        Mismatch.cg.freeze()


def test_illegal_hits_are_exposed_as_a_report_failure_signal():
    class Packet(SvObject):
        code = Bit(1)

        @covergroup
        def cg(self):
            class point(CovPoint, source=self.code):
                bad = illegal_bins[1]
                zero = bins[0]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    packet = Packet()
    packet.code.value = 1
    packet.cg.sample()
    assert packet.cg.has_illegal_hits()


def test_cross_honors_iff_member_illegal_default_and_overlapping_normal_bins():
    class Packet(SvObject):
        code = Bit(2)
        mode = Bit(1)
        enabled = Bit(1)

        @covergroup
        def cg(self):
            class code_cp(CovPoint, source=self.code):
                zero = bins[0]
                low = bins[0:1]
                bad = illegal_bins[2]

            class mode_cp(CovPoint, source=self.mode):
                zero = bins[0]
                other = default_bins

            class combined(Cross, members=(code_cp, mode_cp), iff=lambda: self.enabled == 1):
                exact = bins[code_cp.zero, mode_cp.zero]
                overlapping = bins[code_cp.low, mode_cp.zero]
                default_member = bins[code_cp.zero, mode_cp.other]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    packet = Packet()
    packet.enabled.value = 1
    packet.code.value, packet.mode.value = 0, 0
    packet.cg.sample()
    packet.code.value, packet.mode.value = 0, 1
    packet.cg.sample()
    packet.code.value, packet.mode.value = 2, 0
    packet.cg.sample()
    packet.enabled.value = 0
    packet.code.value, packet.mode.value = 0, 0
    packet.cg.sample()
    assert packet.cg.instance.snapshot()["combined"] == {
        "hits": {"default_member": 1, "exact": 1, "overlapping": 1},
        "illegal_hits": {}, "samples": 2,
    }


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

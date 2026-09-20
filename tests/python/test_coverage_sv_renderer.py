from __future__ import annotations

import pytest

from svtypes import AssocArray, Bit, CovPoint, CovPointArray, CovPointOption, CoverGroupOption, CoverGroupTypeOption, CoverInput, CoverRef, Cross, CrossOption, CrossQueueType, DynArray, Enum, Object, Queue, SvObject, bins, coverage_init, covergroup, ignore_bins, repeat, transition_bins
from svtypes.errors import CoverageDeclarationError
from svtypes.coverage.observation import compare_manifest_hits, parse_observation
from svtypes.coverage.sv import _queue_function_lines, observation_manifest


class _RendererPacketKind(Enum, width=8, signed=False):
    request = 0
    response = 1


def test_renderer_preserves_enum_member_symbols():
    class Packet(SvObject):
        kind = _RendererPacketKind()

        @covergroup
        def cg(self):
            class kind_cp(CovPoint, source=self.kind):
                request = bins[_RendererPacketKind.request]

    code = Packet.to_sv_obj()

    assert "bins request = {request};" in code


def test_renderer_uses_frozen_cross_ir_and_manifest_has_all_observable_bins():
    class Packet(SvObject):
        opcode = Bit(1)
        mode = Bit(1)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode):
                zero = bins[0]
                one = bins[1]

            class mode_cp(CovPoint, source=self.mode):
                read = bins[0]
                write = bins[1]

            class opcode_mode(Cross, members=(opcode_cp, mode_cp)):
                class option(CrossOption):
                    cross_retain_auto_bins = 1

                zero_read = bins[opcode_cp.zero, mode_cp.read]
                masked = ignore_bins[opcode_cp.one, mode_cp.write]

    code = Packet.to_sv_obj()
    assert "opcode_cp: coverpoint item.opcode {" in code
    assert "opcode_mode: cross opcode_cp, mode_cp {" in code
    assert "bins zero_read = binsof(opcode_cp.zero) && binsof(mode_cp.read);" in code
    assert "ignore_bins masked = binsof(opcode_cp.one) && binsof(mode_cp.write);" in code
    assert "bins auto_opcode_cp_one_mode_cp_read_ = binsof(opcode_cp.one) && binsof(mode_cp.read);" in code

    manifest = observation_manifest([Packet])
    group = next(item for item in manifest["covergroups"] if item["covergroup_type_id"].endswith("::cg"))
    cross = group["crosses"][0]
    assert cross["semantic_id"].endswith("::cross::opcode_mode")
    assert {item["kind"] for item in cross["bins"]} == {"normal", "ignore"}
    assert {item["sv_name"] for item in cross["bins"]} >= {"zero_read", "masked"}


def test_renderer_emits_cross_local_covpoint_as_private_zero_weight_support():
    class Packet(SvObject):
        opcode = Bit(2)
        mode = Bit(1)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode):
                all_values = bins[0:3]

            class mode_cp(CovPoint, source=self.mode):
                read = bins[0]

            class opcode_mode(Cross, members=(opcode_cp, mode_cp)):
                class opcode_cp(CovPoint, source=self.opcode):
                    compact = bins[0:1]

                selected = bins[opcode_cp.compact, mode_cp.read]

    code = Packet.to_sv_obj()
    assert "__svtypes_cross_opcode_mode_opcode_cp: coverpoint item.opcode" in code
    assert "__svtypes_cross_opcode_mode_opcode_cp: coverpoint item.opcode {\n" in code
    assert "option.weight = 0;" in code
    assert "type_option.weight = 0;" in code
    assert "opcode_mode: cross __svtypes_cross_opcode_mode_opcode_cp, mode_cp" in code
    manifest = observation_manifest([Packet])
    group = next(item for item in manifest["covergroups"] if item["covergroup_type_id"].endswith("::cg"))
    assert [item["python_name"] for item in group["points"]] == ["mode_cp", "opcode_cp"]
    assert [item["python_name"] for item in group["crosses"]] == ["opcode_mode"]


def test_renderer_suppresses_unselected_static_cross_tuples_when_auto_retain_is_disabled():
    class Packet(SvObject):
        opcode = Bit(1)
        mode = Bit(1)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode):
                zero = bins[0]
                one = bins[1]

            class mode_cp(CovPoint, source=self.mode):
                read = bins[0]
                write = bins[1]

            class opcode_mode(Cross, members=(opcode_cp, mode_cp)):
                selected = bins[opcode_cp.zero, mode_cp.read]

    code = Packet.to_sv_obj()
    assert "bins selected = binsof(opcode_cp.zero) && binsof(mode_cp.read);" in code
    assert "ignore_bins __svtypes_unselected_1" in code


def test_renderer_guards_dynamic_slot_coverpoints_by_size():
    class Packet(SvObject):
        values = DynArray(Bit(2))

        @covergroup
        def cg(self):
            class slots(CovPointArray, source=self.values, length=3):
                low = bins[0:1]

    code = Packet.to_sv_obj()
    assert "slots_0_: coverpoint item.values[0] iff ((item.values.size() > 0)) {" in code
    assert "slots_2_: coverpoint item.values[2] iff ((item.values.size() > 2)) {" in code
    assert "function real get_coverage();" in code
    assert "return cg.get_coverage();" in code


def test_renderer_emits_transition_sequences_with_implication():
    class Packet(SvObject):
        code = Bit(2)

        @covergroup
        def cg(self):
            class point(CovPoint, source=self.code):
                rise = transition_bins[0, 1]
                burst = transition_bins[0, repeat(1, 1, 2), 2]

    code = Packet.to_sv_obj()
    assert "bins rise = (0 => 1);" in code
    assert "bins burst = (0 => 1[*1:2] => 2);" in code


def test_renderer_binds_cover_input_constructor_and_cover_ref_sample_path():
    class Packet(SvObject):
        opcode = Bit(2)
        mode = Bit(1)

        @covergroup
        def cg(self, limit: CoverInput[int], mode: CoverRef[Bit]):
            class limited(CovPoint, source=self.opcode):
                window = bins[0:limit]

            class ref_cp(CovPoint, source=mode):
                zero = bins[0]
                one = bins[1]

    code = Packet.to_sv_obj()
    assert "covergroup cg (int limit) with function sample(Packet item);" in code
    assert "bins window = {[0:limit]};" in code
    assert "ref_cp: coverpoint item.mode {" in code
    assert "cg = new(limit);" in code


def test_renderer_emits_a_coverage_init_method_with_the_same_actual_mapping():
    class Packet(SvObject):
        opcode = Bit(2)

        @covergroup
        def cg(self, first: CoverInput[int], last: CoverInput[int]):
            class opcode_cp(CovPoint, source=self.opcode):
                window = bins[first:last]

        @coverage_init
        def configure_coverage(self, first: int, last: int):
            self.cg.instantiate(first, last)

    code = Packet.to_sv_obj()
    assert "Packet__cg__coverage cg;" in code
    assert "function void configure_coverage(int first, int last);" in code
    assert "cg = new(first, last);" in code


def test_renderer_preserves_input_driven_unbounded_array_bin_syntax():
    class Packet(SvObject):
        opcode = Bit(2)

        @covergroup
        def cg(self, first: CoverInput[int], last: CoverInput[int]):
            class opcode_cp(CovPoint, source=self.opcode):
                window = bins[first:last].split(max_bins=None)

        @coverage_init
        def configure_coverage(self, first: int, last: int):
            self.cg.instantiate(first, last)

    code = Packet.to_sv_obj()
    assert "bins window[] = {[first:last]};" in code


def test_renderer_emits_cross_iff_on_the_cross_declaration():
    class Packet(SvObject):
        code = Bit(1)
        mode = Bit(1)
        gate = Bit(1)

        @covergroup
        def cg(self):
            class code_cp(CovPoint, source=self.code, iff=lambda: self.gate == 1):
                zero = bins[0]
                one = bins[1]

            class mode_cp(CovPoint, source=self.mode):
                zero = bins[0]
                one = bins[1]

            class combined(Cross, members=(code_cp, mode_cp), iff=lambda: self.gate == 1):
                both_zero = bins[code_cp.zero, mode_cp.zero]

    code = Packet.to_sv_obj()
    assert "code_cp: coverpoint item.code iff ((item.gate == 1)) {" in code
    assert "combined: cross code_cp, mode_cp iff ((item.gate == 1)) {" in code


def test_renderer_explicitly_emits_supported_coverage_options():
    class Packet(SvObject):
        opcode = Bit(1)

        @covergroup
        def cg(self):
            class option(CoverGroupOption):
                per_instance = 1
                goal = 75

            class opcode_cp(CovPoint, source=self.opcode):
                class option(CovPointOption):
                    at_least = 2

                zero = bins[0]

    code = Packet.to_sv_obj()
    assert "option.per_instance = 1;" in code
    assert "option.goal = 75;" in code
    assert "option.at_least = 2;" in code


def test_renderer_gates_target_unsupported_instance_type_options():
    class InstCoverage(SvObject):
        opcode = Bit(1)

        @covergroup
        def cg(self):
            class option(CoverGroupOption):
                get_inst_coverage = 1

            class opcode_cp(CovPoint, source=self.opcode):
                zero = bins[0]

    with pytest.raises(CoverageDeclarationError, match="get_inst_coverage"):
        InstCoverage.to_sv_obj()

    class MergeInstances(SvObject):
        opcode = Bit(1)

        @covergroup
        def cg(self):
            class type_option(CoverGroupTypeOption):
                merge_instances = 1

            class opcode_cp(CovPoint, source=self.opcode):
                zero = bins[0]

    with pytest.raises(CoverageDeclarationError, match="merge_instances"):
        MergeInstances.to_sv_obj()


def test_renderer_rejects_cross_queue_when_configured_target_lacks_type_support():
    class Packet(SvObject):
        opcode = Bit(2)
        mode = Bit(2)

        @covergroup
        def cg(self, limit: CoverInput[int]):
            class opcode_cp(CovPoint, source=self.opcode):
                value = bins[0:3]

            class mode_cp(CovPoint, source=self.mode):
                value = bins[0:3]

            class match(Cross, members=(opcode_cp, mode_cp)):
                def diagonal(count: int) -> CrossQueueType:
                    result = CrossQueueType()
                    for index in range(count):
                        result.push_back((index, index))
                    return result

                pairs = bins[diagonal(limit)]

    with pytest.raises(CoverageDeclarationError, match="SVT-COV-SV-BACKEND"):
        Packet.to_sv_obj()
    function_lines = _queue_function_lines(Packet.cg.freeze().crosses[0], "  ")
    assert any("function CrossQueueType diagonal(int count);" in line for line in function_lines)
    assert any("? index < count : index > count" in line for line in function_lines)


def test_cross_queue_function_rejects_non_queue_return_and_self_access():
    class NonQueueReturn(SvObject):
        value = Bit(1)

        @covergroup
        def cg(self):
            class point(CovPoint, source=self.value):
                zero = bins[0]

            class cross(Cross, members=(point,)):
                def invalid() -> CrossQueueType:
                    count = 1
                    return count

                selected = bins[invalid()]

    with pytest.raises(CoverageDeclarationError, match="unsupported statement"):
        NonQueueReturn.cg.freeze()

    class SelfParameter(SvObject):
        value = Bit(1)

        @covergroup
        def cg(self):
            class point(CovPoint, source=self.value):
                zero = bins[0]

            class cross(Cross, members=(point,)):
                def invalid(self) -> CrossQueueType:
                    result = CrossQueueType()
                    return result

                selected = bins[invalid()]

    with pytest.raises(CoverageDeclarationError, match="cannot declare self"):
        SelfParameter.cg.freeze()


def test_observation_protocol_requires_every_manifest_named_item():
    parsed = parse_observation({
        "items": {"opcode": {"hits": {"zero": 2}, "illegal_hits": {"bad": 1}}},
        "summary": {"coverage": 100.0, "sample_count": 2},
    })
    manifest = {
        "covergroups": [{
            "points": [{"observation_label": "opcode"}],
            "crosses": [],
        }],
    }
    compare_manifest_hits(
        manifest,
        parsed,
        {"opcode": {"hits": {"zero": 2}, "illegal_hits": {"bad": 1}}, "summary": {"coverage": 100.0, "sample_count": 2}},
    )
    with pytest.raises(AssertionError, match="omits manifest items"):
        compare_manifest_hits(
            {"covergroups": [{"points": [{"observation_label": "opcode"}, {"observation_label": "mode"}], "crosses": []}]},
            parsed,
            {"opcode": {"hits": {"zero": 2}, "illegal_hits": {"bad": 1}}, "summary": {"coverage": 100.0, "sample_count": 2}},
        )


def test_observation_manifest_binds_instance_keys_only_through_explicit_target_labels():
    class Packet(SvObject):
        opcode = Bit(1)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode):
                zero = bins[0]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    left, right = Packet(), Packet()
    left.cg.bind_logical_instance("dut.left")
    right.cg.bind_logical_instance("dut.right")
    manifest = observation_manifest(
        [Packet],
        instances=(left.cg.instance, right.cg.instance),
        target_labels={"dut.left": "target.left.cg", "dut.right": "target.right.cg"},
    )
    assert manifest["instances"] == [
        {
            "covergroup_type_id": Packet.cg.freeze().covergroup_type_id,
            "instance_layout_digest": left.cg.instance.instance_layout_digest,
            "logical_instance_key": "dut.left",
            "instance_name": None,
            "target_label": "target.left.cg",
            "item_bins": {"Packet__cg__point__opcode_cp": ["zero"]},
        },
        {
            "covergroup_type_id": Packet.cg.freeze().covergroup_type_id,
            "instance_layout_digest": right.cg.instance.instance_layout_digest,
            "logical_instance_key": "dut.right",
            "instance_name": None,
            "target_label": "target.right.cg",
            "item_bins": {"Packet__cg__point__opcode_cp": ["zero"]},
        },
    ]
    with pytest.raises(ValueError, match="requires a non-empty target label"):
        observation_manifest([Packet], instances=(left.cg.instance,))


def test_observation_protocol_accepts_per_instance_documents():
    parsed = parse_observation({
        "instances": {
            "dut.left": {"items": {"opcode": {"hits": {"zero": 2}, "illegal_hits": {}}}, "summary": {"coverage": 50.0, "sample_count": 2}},
            "dut.right": {"items": {"opcode": {"hits": {"one": 3}, "illegal_hits": {}}}, "summary": {"coverage": 50.0, "sample_count": 3}},
        }
    })
    manifest = {
        "covergroups": [{"points": [{"observation_label": "opcode"}], "crosses": []}],
        "instances": [{"logical_instance_key": "dut.left"}, {"logical_instance_key": "dut.right"}],
    }
    compare_manifest_hits(
        manifest,
        parsed,
        {
            "instances": {
                "dut.left": {"items": {"opcode": {"hits": {"zero": 2}, "illegal_hits": {}}}, "summary": {"coverage": 50.0, "sample_count": 2}},
                "dut.right": {"items": {"opcode": {"hits": {"one": 3}, "illegal_hits": {}}}, "summary": {"coverage": 50.0, "sample_count": 3}},
            }
        },
    )


def test_observation_labels_disambiguate_equal_item_names_from_multiple_covergroups():
    class Packet(SvObject):
        opcode = Bit(1)

        @covergroup
        def control(self):
            class point(CovPoint, source=self.opcode):
                zero = bins[0]

        @covergroup
        def data(self):
            class point(CovPoint, source=self.opcode):
                zero = bins[0]

    manifest = observation_manifest([Packet])
    labels = [
        point["observation_label"]
        for group in manifest["covergroups"]
        if group["covergroup_type_id"].endswith("::control") or group["covergroup_type_id"].endswith("::data")
        for point in group["points"]
    ]
    assert len(labels) == len(set(labels)) == 2


def test_renderer_emits_nullness_value_points_for_object_dynamic_containers():
    class Child(SvObject):
        value = Bit(8)

    class Packet(SvObject):
        dynamic = DynArray(Object("Child"), cov=True)
        queue = Queue(Object("Child"), cov=True)
        lookup = AssocArray(Bit(8), Object("Child"), cov=True)

    code = Packet.to_sv_obj()
    assert "dynamic: coverpoint (value == null)" in code
    assert "queue: coverpoint (value == null)" in code
    assert "lookup: coverpoint (value == null)" in code


def test_renderer_strips_field_randomization_qualifier_from_value_sample_formal():
    class Child(SvObject):
        value = Bit(8)

    class Packet(SvObject):
        dynamic = DynArray(Object("Child", rand=True), cov=True)

    code = Packet.to_sv_obj()
    assert "sample(Child value)" in code
    assert "sample(rand Child value)" not in code

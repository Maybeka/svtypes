from __future__ import annotations

import pytest

from svtypes import AssocArray, Bit, CovPoint, CoverInput, Cross, CrossOption, CrossQueueType, DynArray, Object, Queue, SvObject, bins, covergroup, ignore_bins
from svtypes.errors import CoverageDeclarationError
from svtypes.coverage.observation import compare_manifest_hits, parse_observation
from svtypes.coverage.sv import _queue_function_lines, observation_manifest


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


def test_observation_protocol_compares_only_manifest_named_items():
    parsed = parse_observation({
        "items": {"opcode": {"hits": {"zero": 2}, "illegal_hits": {"bad": 1}}},
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
        {"opcode": {"hits": {"zero": 2}, "illegal_hits": {"bad": 1}}},
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
        },
        {
            "covergroup_type_id": Packet.cg.freeze().covergroup_type_id,
            "instance_layout_digest": right.cg.instance.instance_layout_digest,
            "logical_instance_key": "dut.right",
            "instance_name": None,
            "target_label": "target.right.cg",
        },
    ]
    with pytest.raises(ValueError, match="requires a non-empty target label"):
        observation_manifest([Packet], instances=(left.cg.instance,))


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

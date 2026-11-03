"""Public struct kinds, value semantics and boundary contracts."""

import copy
from pathlib import Path
import subprocess

import pytest
from svtypes import CovPoint, Cross, Enum, bins, covergroup

from svtypes import (Array, AssocArray, Bit, CodecSession, DynArray, Int, Object, ObjectRegistry,
                     Queue, String, SvObject, SvStruct, SvPackedStruct,
                     UnsupportedTypeError, checked_unpack, encoding_descriptor,
                     materialize_type_spec, schema_descriptor, type_spec_identity,
                     type_spec_from_identity, constraint, rand_layer, unique, ConstraintTypeError)
from svtypes import CovPoint, covergroup


class PackedHeader(SvPackedStruct):
    opcode = Bit[4]()
    length = Bit[12]()


class UnpackedHeader(SvStruct):
    opcode = Bit[4]()
    length = Bit[12]()


class Payload(SvStruct):
    header = PackedHeader()
    label = String()
    data = Queue[Bit[8]]()


def test_struct_copy_allows_member_named_value_including_nested_struct():
    class NamedValue(SvStruct):
        value = Bit[8]()

    class Nested(SvStruct):
        item = NamedValue()

    source = NamedValue()
    source.value.value = 7
    cloned = copy.copy(source)
    cloned.value.value = 9
    assert source.value.value == 7
    assert cloned.value.value == 9
    nested = Nested()
    nested.item.value.value = 11
    nested_copy = copy.copy(nested)
    nested_copy.item.value.value = 12
    assert nested.item.value.value == 11
    assert nested_copy.item.value.value == 12
    deep_copy = copy.deepcopy(nested)
    deep_copy.item.value.value = 13
    assert nested.item.value.value == 11
    assert deep_copy.item.value.value == 13


def test_struct_member_named_value_survives_all_container_value_copies():
    class NamedValue(SvStruct):
        value = Bit[8]()

    source = NamedValue()
    source.value.value = 7
    for container, values, key in (
        (Array[NamedValue, 1](), [source], 0),
        (DynArray[NamedValue](), [source], 0),
        (Queue[NamedValue](), [source], 0),
        (AssocArray[Int, NamedValue](), {1: source}, 1),
    ):
        container.value = values
        assert isinstance(container.value[key], NamedValue)
        container[key].value.value = 9
        assert source.value.value == 7
        assert container.value[key].value.value == 9
    queue = Queue[NamedValue]()
    queue.push_back(source)
    popped = queue.pop_front()
    assert isinstance(popped, NamedValue)
    assert popped.value.value == 7


_struct_registry = ObjectRegistry()


class StructChild(SvObject):
    number = Bit[8]()
    next_child = Object["StructChild"](registry=_struct_registry)


_struct_registry.register(StructChild)


class HandleRecord(SvStruct):
    left = Object["StructChild"](registry=_struct_registry, rand=True)
    right = Object["StructChild"](registry=_struct_registry)
    children = Queue[StructChild](cov=False)
    text = String()


def test_struct_handles_share_one_graph_transaction_and_rollback_truncated_input():
    from svtypes import UnpackContext

    value = HandleRecord()
    child = StructChild()
    child.number.value = 7
    child.next_child = child
    value.left = value.right = child
    value.children.value = [child, None]
    value.text.value = "tail"
    data = value.to_bytes()
    inline_size = len(child.to_bytes())
    assert data[inline_size] == 2
    session = CodecSession()
    decoded, used = checked_unpack(HandleRecord.codec_template(), data, encoding_descriptor(HandleRecord), context=UnpackContext(session))
    assert used == len(data)
    assert decoded.left is decoded.right
    assert decoded.left.next_child is decoded.left
    assert decoded.children[0] is decoded.left and decoded.children[1] is None
    for size in (inline_size, len(data) - 1):
        empty = CodecSession()
        with pytest.raises((ValueError, TypeError)):
            checked_unpack(HandleRecord.codec_template(), data[:size], encoding_descriptor(HandleRecord), context=UnpackContext(empty))
        assert empty.get(child.svtypes_object_number) is None


def test_struct_decode_enforces_input_and_nesting_limits():
    from svtypes import DecodeLimits, ResourceLimitError, UnpackContext

    source = Payload()
    encoded = source.to_bytes()
    with pytest.raises(ResourceLimitError, match="input size"):
        checked_unpack(Payload.codec_template(), encoded, encoding_descriptor(Payload), context=UnpackContext(limits=DecodeLimits(max_input_bytes=1)))
    with pytest.raises(ResourceLimitError, match="nesting depth"):
        checked_unpack(Payload.codec_template(), encoded, encoding_descriptor(Payload), context=UnpackContext(limits=DecodeLimits(max_nesting_depth=1)))


def test_generated_cpp_struct_shared_and_cyclic_handle_codecs(tmp_path):
    child = StructChild()
    child.number.value = 7
    child.next_child = child
    value = HandleRecord()
    value.left = value.right = child
    value.children.value = [child, None]
    value.text.value = "tail"
    expected = ",".join(str(byte) for byte in value.to_bytes())
    source = '#include "svtypes.hpp"\n' + StructChild.to_cpp_obj() + "\n" + HandleRecord.to_cpp_obj()
    source += '''
int main() {
    std::vector<uint8_t> expected = {''' + expected + '''};
    HandleRecord value{}; size_t offset = 0;
    unpack(value, expected, offset);
    if (offset != expected.size() || value.left != value.right ||
        value.left->next_child != value.left || value.children[0] != value.left ||
        value.children[1] != nullptr || value.text != "tail") return 1;
    std::vector<uint8_t> actual; pack(value, actual);
    return actual == expected ? 0 : 2;
}
'''
    path = tmp_path / "graph.cpp"
    path.write_text(source)
    binary = tmp_path / "graph_test"
    result = subprocess.run(["g++", "-std=c++20", str(path), "-I",
                             str(Path(__file__).resolve().parents[2] / "svtypes_runtime/cpp"),
                             "-o", str(binary)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    result = subprocess.run([str(binary)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


class PartialPayload(SvStruct):
    encoded = Bit[8]()
    transient_value = Bit[8](7, pack_bytes=False)


def test_unpacked_pack_bytes_policy_is_shared_by_all_renderers():
    value = PartialPayload()
    value.encoded.value = 42
    value.transient_value.value = 99
    assert value.to_bytes() == b"\x2a"
    decoded, count = PartialPayload().unpack(b"\x2a")
    assert count == 1 and decoded.encoded.value == 42 and decoded.transient_value.value == 7
    sv = PartialPayload.to_sv_obj().split("class PartialPayload_packer;", 1)[1]
    cpp = PartialPayload.to_cpp_obj().split("inline void pack", 1)[1]
    assert '::apply(value.transient_value, "transient_value", 1\'b0, 1\'b0,' in sv
    assert "pack(value.transient_value" not in cpp


def test_packed_uniform_zero_default_does_not_name_members():
    class UniformZero(SvPackedStruct):
        first = Bit[8]()
        second = Int()

    class ExplicitNonzero(SvPackedStruct):
        first = Bit[8](3)
        second = Int()

    assert UniformZero().sv_initializer() == "'{default: '0}"
    assert ExplicitNonzero().sv_initializer() == "ExplicitNonzero_packer::svtypes_default()"
    assert "if (action == 5) member = 8'h3;" in ExplicitNonzero.to_sv_obj()


def test_generated_cpp_unpacked_nested_dynamic_and_partial_codecs(tmp_path):
    original = Payload()
    original.header.opcode.value = 3
    original.header.length.value = 7
    original.label.value = "payload"
    original.data.value = [1, 2, 255]
    expected = ",".join(str(byte) for byte in original.to_bytes())
    source = '#include "svtypes.hpp"\n' + "\n".join(
        cls.to_cpp_obj() for cls in (PackedHeader, Payload, PartialPayload))
    source += '''
int main() {
    Payload input{}; input.header.opcode = 3; input.header.length = 7;
    input.label = "payload"; input.data = {1, 2, 255};
    std::vector<uint8_t> bytes; pack(input, bytes);
    if (bytes != std::vector<uint8_t>{''' + expected + '''}) return 1;
    Payload output{}; size_t offset = 0; unpack(output, bytes, offset);
    if (offset != bytes.size() || output.label != input.label || output.data != input.data) return 2;
    PartialPayload partial{}; partial.encoded = 42; partial.transient_value = 99;
    bytes.clear(); pack(partial, bytes);
    if (bytes != std::vector<uint8_t>{42}) return 3;
    partial.transient_value = 7; offset = 0; unpack(partial, bytes, offset);
    return partial.transient_value == 7 ? 0 : 4;
}
'''
    path = tmp_path / "struct.cpp"
    path.write_text(source)
    binary = tmp_path / "struct_test"
    result = subprocess.run(["g++", "-std=c++20", str(path), "-I",
                             str(Path(__file__).resolve().parents[2] / "svtypes_runtime/cpp"),
                             "-o", str(binary)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    result = subprocess.run([str(binary)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_struct_kind_controls_sv_typedef_and_public_identity():
    assert issubclass(SvPackedStruct, SvStruct)
    for cls, packed in ((PackedHeader, True), (UnpackedHeader, False), (Payload, False)):
        identity = type_spec_identity(cls)
        assert identity["kind"] == "struct"
        assert identity["packed"] is packed
        assert type_spec_from_identity(identity) is cls
        assert schema_descriptor(cls).schema["packed"] is packed
        assert schema_descriptor(cls).schema["base_type_name"] is None
        prefix = "typedef struct packed {" if packed else "typedef struct {"
        assert cls.to_sv_obj().startswith(prefix)
        codec = materialize_type_spec(cls, location="struct")
        assert isinstance(codec, cls)
        assert not hasattr(codec, "svtypes_object_number")


@pytest.mark.parametrize("packing", [None, 0, 1, "false", "true"])
def test_struct_identity_requires_boolean_packing_including_nested_container(packing):
    identity = type_spec_identity(Queue[PackedHeader])
    identity["args"][0]["packed"] = packing
    with pytest.raises(UnsupportedTypeError, match="explicit packed boolean"):
        type_spec_from_identity(identity)


def test_struct_packing_does_not_change_memberwise_bytes():
    values = []
    for cls in (PackedHeader, UnpackedHeader):
        value = cls()
        value.opcode.value = 10
        value.length.value = 0xABC
        values.append(value.pack(value))
    assert values == [b"\x0a\xbc\x0a"] * 2
    assert schema_descriptor(PackedHeader).encoding_fingerprint == schema_descriptor(UnpackedHeader).encoding_fingerprint


def test_unpacked_nested_string_and_queue_roundtrip():
    original = Payload()
    original.header.opcode.value = 3
    original.header.length.value = 7
    original.label.value = "payload"
    original.data.value = [1, 2, 255]
    data = original.pack(original)
    decoded, used = Payload.codec_template().unpack(data)
    assert used == len(data)
    assert decoded.header.opcode.value == 3
    assert decoded.header.length.value == 7
    assert decoded.label.value == "payload"
    assert decoded.data.value == [1, 2, 255]
    for end in range(len(data)):
        with pytest.raises(ValueError):
            Payload.codec_template().unpack(data[:end])


def test_packed_members_cannot_contain_unpacked_types():
    for member in (String(), Payload(), Array[Bit[4], 2](), DynArray[Bit[8]]()):
        with pytest.raises(UnsupportedTypeError, match="portable packed value"):
            type("InvalidPacked", (SvPackedStruct,), {"member": member})


def test_boundary_rejects_bare_struct_bases_and_changed_packing():
    for cls in (SvStruct, SvPackedStruct):
        with pytest.raises(UnsupportedTypeError, match="concrete"):
            materialize_type_spec(cls, location="struct")
    identity = type_spec_identity(PackedHeader)
    identity["packed"] = False
    with pytest.raises(UnsupportedTypeError, match="differs"):
        type_spec_from_identity(identity)


def test_unpacked_value_assignment_is_independent():
    source, target = Payload(), Payload()
    source.header.opcode.value = 1
    source.label.value = "source"
    source.data.value = [2, 3]
    target.value = source
    target.header.opcode.value = 2
    target.data[0].value = 4
    assert source.header.opcode.value == 1
    assert source.data.value == [2, 3]
    assert target.label.value == "source"


def test_struct_handle_fields_share_graph_context_without_struct_identity():
    registry = ObjectRegistry()

    class Child(SvObject):
        code = Bit[8]()

    registry.register(Child)

    class References(SvStruct):
        left = Object["Child"](registry=registry)
        right = Object["Child"](registry=registry)

    source = References()
    child = Child()
    child.code.value = 17
    source.left = child
    source.right = child
    data = source.pack(source)
    context = CodecSession(origin=2).unpack_context()
    decoded, used = checked_unpack(References.codec_template(), data,
                                   encoding_descriptor(References), context=context)
    assert used == len(data)
    assert decoded.left is decoded.right
    assert decoded.left.code.value == 17
    assert context.session.get(decoded.left.svtypes_object_number) is decoded.left
    target = References()
    target.value = source
    assert target.left is source.left
    assert target.right is source.right
    copied = copy.copy(source)
    assert copied.left is child
    assert copied.right is child


@pytest.mark.parametrize("factory", [lambda: Array[PackedHeader, 2](),
                                     lambda: DynArray[PackedHeader](),
                                     lambda: Queue[PackedHeader](),
                                     lambda: AssocArray[Int, PackedHeader]()])
def test_struct_container_assignment_copies_values_not_struct_references(factory):
    original = PackedHeader()
    original.opcode.value = 3
    container = factory()
    container.value = {1: original} if isinstance(container, AssocArray) else [original, original]
    first = container[1] if isinstance(container, AssocArray) else container[0]
    first.opcode.value = 7
    assert original.opcode.value == 3
    if not isinstance(container, AssocArray):
        assert container[1].opcode.value == 3


class SmallPacked(SvPackedStruct):
    high = Bit[2]()
    low = Bit[2]()


class WholeUnique(SvObject):
    left = SmallPacked(rand=True)
    right = SmallPacked(rand=True)

    @constraint
    def legal(self):
        self.left.high == 0
        self.right.high == 0
        self.left.low == 1
        self.right.low == 2
        unique(self.left, self.right)


class PackedQueueUnique(SvObject):
    data = Queue[SmallPacked](rand=True)

    @constraint
    def legal(self):
        self.data.size() == 3
        unique(self.data)


class PackedIntegral(SvObject):
    header = SmallPacked(rand=True)
    wide = Bit[8]()

    @constraint
    def legal(self):
        self.header == 13
        self.header + self.wide == 37


class MixedUnpacked(SvStruct):
    selected = Bit[2](rand=True)
    fixed = Bit[2](2, rand=False)
    label = String("state")
    data = DynArray[Bit[3]](rand=True)


class MixedHost(SvObject):
    packet = MixedUnpacked(rand=True)

    @constraint
    def legal(self):
        self.packet.selected == 3
        self.packet.fixed == 2
        self.packet.data.size() == 2
        unique(self.packet.data)


class StructLayers(SvObject):
    packet = MixedUnpacked(rand=True)

    @constraint
    def choose(self):
        self.packet.selected == 3

    @constraint
    def choose_data(self):
        self.packet.data.size() == 2
        unique(self.packet.data)

    @rand_layer(2)
    def early(self):
        self.packet.selected
        self.choose

    @rand_layer(1)
    def late(self):
        self.packet.data
        self.choose_data


def test_mixed_unpacked_randomization_respects_state_members():
    obj = MixedHost()
    assert obj.randomize()
    assert obj.packet.selected.value == 3
    assert obj.packet.fixed.value == 2
    assert obj.packet.label.value == "state"
    assert len(set(obj.packet.data.value)) == 2
    code = MixedUnpacked.to_sv_obj()
    assert "rand bit [1:0] selected;" in code
    assert "bit [1:0] fixed = 2'h2;" in code
    assert "rand bit [2:0] data []" in code


def test_struct_modes_control_unpacked_members_but_not_packed_slices():
    obj = MixedHost()
    obj.packet.selected.value = 0
    obj.packet.selected.rand_mode(0)
    assert not obj.randomize()
    obj.packet.selected.rand_mode(1)
    assert obj.randomize()
    assert not hasattr(obj.packet.fixed, "rand_mode")
    packed = WholeUnique()
    assert hasattr(packed.left, "rand_mode")
    assert not hasattr(packed.left.high, "rand_mode")
    assert not hasattr(packed.left.low, "rand_mode")


def test_unpacked_struct_mode_task_updates_members_and_allows_individual_reenable():
    from svtypes import ConstraintError

    obj = MixedHost()
    obj.packet.data.value = [1, 2]
    obj.packet.rand_mode(0)
    assert obj.packet.selected.rand_mode() == 0
    obj.packet.selected.rand_mode(1)
    assert obj.packet.selected.rand_mode() == 1
    obj.packet.rand_mode(1)
    assert obj.packet.selected.rand_mode() == 1 and obj.packet.data[0].rand_mode() == 1
    with pytest.raises(ConstraintError, match="singular"):
        obj.packet.rand_mode()


def test_struct_member_layers_and_nested_dynamic_modes_are_restored():
    obj = StructLayers()
    obj.packet.data.value = [1, 2]
    obj.packet.data[0].rand_mode(0)
    assert obj.layered_randomize()
    assert obj.packet.selected.value == 3
    assert obj.packet.fixed.value == 2
    assert obj.packet.data[0].value == 1
    assert obj.packet.data[0].rand_mode() == 0
    assert obj.packet.selected.rand_mode() == 1
    code = StructLayers.to_sv_obj()
    assert "packet.selected.rand_mode()" in code
    assert "packet.data[" in code
    assert "packet.rand_mode()" not in code


def test_packed_integral_width_extension_and_member_bit_order():
    obj = PackedIntegral()
    assert obj.randomize()
    assert obj.header.high.value == 3
    assert obj.header.low.value == 1
    assert obj.wide.value == 24


def test_packed_struct_unique_compares_whole_values_not_individual_members():
    obj = WholeUnique()
    assert obj.randomize()
    assert obj.left.high.value == obj.right.high.value == 0
    assert obj.left.low.value == 1
    assert obj.right.low.value == 2
    assert "unique {left, right}" in WholeUnique.to_sv_obj()


def test_dynamic_unique_has_one_comparison_item_per_packed_struct():
    obj = PackedQueueUnique()
    assert obj.randomize()
    assert len(obj.data) == 3
    values = {(item.high.value << 2) | item.low.value for item in obj.data._elements}
    assert len(values) == 3


def test_unique_still_rejects_unpacked_struct_values():
    with pytest.raises(ConstraintTypeError, match="unpacked structs"):
        class Invalid(SvObject):
            left = UnpackedHeader(rand=True)
            right = UnpackedHeader(rand=True)

            @constraint
            def legal(self):
                unique(self.left, self.right)


class StructCoverage(SvObject):
    packed_value = SmallPacked(cov=True)
    unpacked_value = MixedUnpacked(cov=True)

    def __init__(self):
        super().__init__()
        self.cg.instantiate()

    @covergroup
    def cg(self):
        class whole(CovPoint, source=self.packed_value):
            pass

        class member(CovPoint, source=self.unpacked_value.selected):
            pass


class StructContainerCoverage(SvObject):
    fixed = Array[MixedUnpacked, 2](cov=True)
    dynamic = Queue[MixedUnpacked](cov=True)
    slotted = Queue[MixedUnpacked](cov=True, cov_slots=2)
    keyed = AssocArray[String, MixedUnpacked](cov=True)


class PackedCoverageExpressions(SvObject):
    header = SmallPacked()
    increment = Bit[4](1)
    flag = Bit[1]()

    def __init__(self):
        super().__init__()
        self.cg.instantiate()

    @covergroup
    def cg(self):
        class whole(CovPoint, source=self.header):
            nine = bins[9]

        class computed(CovPoint, source=self.header + self.increment, iff=lambda: self.header != 0):
            ten = bins[10]

        class enabled(CovPoint, source=self.flag, iff=lambda: self.header):
            yes = bins[1]

        class combined(Cross, members=(whole, enabled)):
            class whole(CovPoint, source=self.header):
                nine = bins[9]

            match = bins[whole.nine, enabled.yes]


def test_packed_coverage_arithmetic_iff_and_cross_local_view_use_integral_value():
    obj = PackedCoverageExpressions()
    obj.flag.value = 1
    obj.cg.sample()
    assert obj.cg.instance.snapshot()["enabled"]["samples"] == 0
    obj.header.high.value = 2
    obj.header.low.value = 1
    obj.cg.sample()
    snapshot = obj.cg.instance.snapshot()
    for point, name in (("whole", "nine"), ("computed", "ten"), ("enabled", "yes"), ("combined", "match")):
        assert snapshot[point]["hits"] == {name: 1}


def test_coverage_rejects_whole_unpacked_struct_even_with_explicit_bins():
    from svtypes import CoverageDeclarationError

    class Invalid(SvObject):
        header = UnpackedHeader()

        @covergroup
        def cg(self):
            class invalid(CovPoint, source=self.header):
                zero = bins[0]

    with pytest.raises(CoverageDeclarationError, match="unpacked struct member"):
        Invalid.cg.freeze()


def test_unpacked_struct_container_coverage_projects_members_and_skips_missing_slots():
    obj = StructContainerCoverage()
    value = MixedUnpacked()
    value.selected.value = 3
    value.data.value = [1, 7]
    obj.fixed.value = [value, value]
    obj.dynamic.value = [value, value]
    obj.slotted.value = [value]
    obj.keyed.value = {"first": value, "second": value}
    obj.svtypes_auto_cov.sample()
    snapshot = obj.svtypes_auto_cov.instance.snapshot()
    assert snapshot["fixed[0].selected"]["hits"] == {"auto[3]": 1}
    assert snapshot["fixed[1].data"]["hits"] == {"auto[1]": 1, "auto[7]": 1}
    for name in ("dynamic", "keyed"):
        assert snapshot[f"{name}.selected"]["hits"] == {"auto[3]": 2}
        assert snapshot[f"{name}.data"]["hits"] == {"auto[1]": 2, "auto[7]": 2}
    assert snapshot["slotted[0].selected"]["hits"] == {"auto[3]": 1}
    assert snapshot["slotted[1].selected"]["samples"] == 0
    assert snapshot["slotted[1].data"]["samples"] == 0
    code = StructContainerCoverage.to_sv_obj()
    assert "foreach (item.dynamic[" in code
    assert "].data[" in code
    assert "if ($size(item.slotted) > 1) begin" in code


def test_struct_coverage_samples_packed_whole_and_unpacked_members():
    obj = StructCoverage()
    obj.packed_value.high.value = 2
    obj.packed_value.low.value = 1
    obj.unpacked_value.selected.value = 3
    obj.unpacked_value.data.value = [1, 7]
    obj.cg.sample()
    snapshot = obj.cg.instance.snapshot()
    assert snapshot["whole"]["hits"] == {"auto[9]": 1}
    assert snapshot["member"]["hits"] == {"auto[3]": 1}
    obj.svtypes_auto_cov.sample()
    snapshot = obj.svtypes_auto_cov.instance.snapshot()
    assert snapshot["packed_value"]["hits"] == {"auto[9]": 1}
    assert snapshot["unpacked_value.selected"]["hits"] == {"auto[3]": 1}
    assert snapshot["unpacked_value.data"]["hits"] == {"auto[1]": 1, "auto[7]": 1}
    assert "unpacked_value" not in snapshot
    code = StructCoverage.to_sv_obj()
    assert "coverpoint item.packed_value" in code
    assert "coverpoint item.unpacked_value.selected" in code
    assert "foreach (item.unpacked_value.data[" in code


def test_packed_coverage_preserves_four_state_member_masks():
    from svtypes import Logic

    class FourState(SvPackedStruct):
        high = Bit[2]()
        low = Logic[2]()

    class Host(SvObject):
        data = FourState(cov=True)

    obj = Host()
    obj.data.high.value = 2
    obj.data.low.value = "xz"
    assert obj.data.width == 4 and obj.data.state_domain == "4state"
    obj.svtypes_auto_cov.sample()
    assert obj.svtypes_auto_cov.instance.snapshot()["data"]["hits"] == {}
    obj.data.low.value = 1
    obj.svtypes_auto_cov.sample()
    assert obj.svtypes_auto_cov.instance.snapshot()["data"]["hits"] == {"auto[9]": 1}


def test_unpacked_struct_random_graph_reaches_allocated_handles_without_allocating_nulls():
    registry = ObjectRegistry()

    class Child(SvObject):
        tag = Bit[3](rand=True)

        @constraint
        def choose(self):
            self.tag == 5

    registry.register(Child)

    class Bundle(SvStruct):
        child = Object["Child"](registry=registry, rand=True)

    class Host(SvObject):
        data = Bundle(rand=True)

    obj = Host()
    descriptor = Bundle.child
    assert obj.data.__dict__.get(descriptor._cache_key) is None
    assert obj.randomize()
    assert obj.data.__dict__.get(descriptor._cache_key) is None
    cloned = copy.copy(obj.data)
    assert cloned.__dict__.get(descriptor._cache_key) is None
    assert obj.data.to_bytes() == b"\0"
    assert obj.data.__dict__.get(descriptor._cache_key) is None
    child = Child()
    obj.data.child = child
    assert obj.randomize()
    assert obj.data.child is child and child.tag.value == 5
    child.tag.value = 0
    child.tag.rand_mode(0)
    assert not obj.randomize()
    assert child.tag.value == 0

    class ContainerHost(SvObject):
        data = DynArray[Bundle](rand=True)

    class StateContainerHost(SvObject):
        data = DynArray[Bundle](rand=False)

    child.tag.rand_mode(1)
    bundle = Bundle()
    bundle.child = child
    for cls, expected in ((ContainerHost, 5), (StateContainerHost, 0)):
        child.tag.value = 0
        host = cls()
        host.data.value = [bundle]
        assert host.randomize()
        assert child.tag.value == expected


class PackedCycle(SvObject):
    cycle_value = SmallPacked(randc=True)


class ConstrainedPackedCycle(SvObject):
    cycle_value = SmallPacked(randc=True)

    @constraint
    def choose(self):
        self.cycle_value.high == 0
        self.cycle_value.low < 3


def test_packed_randc_cycles_whole_value_and_preserves_history_when_paused():
    obj = PackedCycle()
    values = []
    for _ in range(16):
        assert obj.randomize()
        values.append(obj.cycle_value._packed_value())
    assert set(values) == set(range(16))
    assert obj.randomize()
    first = obj.cycle_value._packed_value()
    obj.cycle_value.rand_mode(0)
    assert obj.randomize()
    assert obj.cycle_value._packed_value() == first
    obj.cycle_value.rand_mode(1)
    assert obj.randomize()
    assert obj.cycle_value._packed_value() != first


def test_constrained_packed_randc_restarts_only_after_legal_domain_exhaustion():
    obj = ConstrainedPackedCycle()
    for _ in range(2):
        values = []
        for _ in range(3):
            assert obj.randomize()
            assert obj.cycle_value.high.value == 0
            values.append(obj.cycle_value.low.value)
        assert set(values) == {0, 1, 2}


class LayerElement(SvStruct):
    selected = Bit[2](rand=True)
    header = SmallPacked(rand=True)
    label = String("state")


class StructFlag(Enum[Bit[2]]):
    ZERO = 0
    FULL = 3


class EnumPacked(SvPackedStruct):
    flag = StructFlag()


class EnumPackedHost(SvObject):
    header = EnumPacked(rand=True)

    @constraint
    def choose(self):
        self.header == 1


def test_packed_enum_randomization_uses_named_values_but_codec_preserves_integral_domain():
    from svtypes import DecodeError

    obj = EnumPackedHost()
    assert not obj.randomize()
    obj.choose.constraint_mode(0)
    for _ in range(64):
        assert obj.randomize()
        assert obj.header.flag.value in (0, 3)
    obj.header.flag.value = 1
    assert obj.header.flag.value == 1 and obj.header.flag.name() == ""
    assert obj.header.to_bytes() == b"\x01"
    restored, used = EnumPacked().unpack(b"\x01")
    assert used == 1 and restored.flag.value == 1
    with pytest.raises(ValueError):
        StructFlag().value = 1
    with pytest.raises(DecodeError, match="encoded value"):
        StructFlag().unpack(b"\x01")


class StructContainerLayers(SvObject):
    data = DynArray[LayerElement](rand=True, cov=False)

    @constraint
    def choose(self):
        self.data.size() == 2
        for i in range(self.data.size()):
            self.data[i].selected == 3

    @rand_layer(1)
    def layer(self):
        self.data
        self.choose


def test_dynamic_struct_layers_preserve_each_random_member_mode():
    obj = StructContainerLayers()
    first = LayerElement()
    first.selected.value = 3
    first.header.high.value = 2
    first.header.low.value = 1
    obj.data.value = [first]
    obj.data[0].selected.rand_mode(0)
    obj.data[0].header.rand_mode(0)
    assert obj.layered_randomize()
    assert len(obj.data) == 2
    assert obj.data[0].selected.value == 3 and obj.data[0].selected.rand_mode() == 0
    assert obj.data[0].header._packed_value() == 9 and obj.data[0].header.rand_mode() == 0
    assert obj.data[1].selected.rand_mode() == 1 and obj.data[1].header.rand_mode() == 1
    assert obj.data[0].label.value == obj.data[1].label.value == "state"
    code = StructContainerLayers.to_sv_obj()
    assert "].selected.rand_mode()" in code
    assert "].header.rand_mode()" in code
    assert "].header.high.rand_mode()" not in code
    assert "].label.rand_mode()" not in code


def test_external_unpacked_members_containers_and_random_publication_share_storage():
    from svtypes import FieldIdentity, MemoryExternalFieldStorage

    storage = MemoryExternalFieldStorage()
    initial = MixedUnpacked()
    initial.data.value = [1, 2]
    storage.seed("packet", MixedHost.packet, initial)
    host = MixedHost()
    host.bind_external_storage(storage, {FieldIdentity(MixedHost, "packet"): "packet"})
    host.packet.selected.value = 1
    host.packet.data.value.append(3)
    assert host.packet.selected.value == 1 and list(host.packet.data.value) == [1, 2, 3]
    assert host.randomize()
    assert host.packet.selected.value == 3
    assert host.packet.fixed.value == 2 and host.packet.label.value == "state"
    assert len(set(host.packet.data.value)) == 2
    replacement = MixedUnpacked()
    replacement.selected.value = 2
    replacement.data.value = [4]
    host.packet.value = replacement
    assert host.packet.selected.value == 2 and list(host.packet.data.value) == [4]
    assert replacement.selected.value == 2


def test_external_packed_enum_and_struct_container_member_paths_roundtrip():
    from svtypes import FieldIdentity, MemoryExternalFieldStorage

    class Host(SvObject):
        records = Queue[MixedUnpacked]()
        header = EnumPacked()

    storage = MemoryExternalFieldStorage()
    storage.seed("records", Host.records, [MixedUnpacked()])
    storage.seed("header", Host.header, EnumPacked())
    obj = Host()
    obj.bind_external_storage(storage, {FieldIdentity(Host, "records"): "records",
                                       FieldIdentity(Host, "header"): "header"})
    obj.records[0].selected.value = 3
    obj.records[0].data.value.append(5)
    obj.header.flag.value = 1
    assert obj.records[0].selected.value == 3
    assert list(obj.records[0].data.value) == [5]
    assert obj.header.flag.value == 1


def test_struct_request_response_codecs_preserve_value_fields_and_opaque_references():
    from svtypes import RecordSchema, RemoteRef

    class Data(SvStruct):
        payload = Payload()
        owner = RemoteRef["example.Owner"]()
        references = Queue[RemoteRef["example.Owner"]]()

    request = RecordSchema("example.method.request", [("value", Data())], class_name="StructRequest").build()
    response = RecordSchema("example.method.response", [("result", Data()), ("previous", Data()),
                            ("current", Data())], class_name="StructResponse").build()
    original = Data()
    original.payload.label.value = "input"
    original.payload.data.value = [1, 2]
    original.owner.value = 42
    original.references.value = [42, None, 42]
    incoming = request()
    incoming.value.value = original
    original.payload.data.value.append(9)
    decoded, used = checked_unpack(request.codec_template(), incoming.to_bytes(), encoding_descriptor(request))
    assert used == len(incoming.to_bytes())
    assert decoded.value.payload.data.value == [1, 2]
    outgoing = response()
    for name in ("result", "previous", "current"):
        getattr(outgoing, name).value = decoded.value
    outgoing.previous.payload.label.value = "output"
    outgoing.current.payload.data.value = [3]
    restored, used = checked_unpack(response.codec_template(), outgoing.to_bytes(), encoding_descriptor(response))
    assert used == len(outgoing.to_bytes())
    assert restored.result.payload.label.value == "input"
    assert restored.previous.payload.label.value == "output"
    assert restored.current.payload.data.value == [3]
    assert restored.result.owner.value.object_number == 42
    assert [item.object_number for item in restored.result.references.value] == [42, 0, 42]
    assert 'struct_field_operations#(Data_t, Data_packer)::apply(this.value, "value",' in request.to_sv_obj()

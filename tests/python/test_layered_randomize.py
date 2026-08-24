from __future__ import annotations

import copy

import pytest

from svtypes import (
    Array,
    Bit,
    ConstraintError,
    ConstraintSyntaxError,
    DeclarationError,
    LayeredRandomizationPriorityWarning,
    Logic,
    LogicValue,
    RandomContext,
    SvObject,
    constraint,
    rand_layer,
    schema_descriptor,
    set_layered_randomization_reference_policy,
)
from svtypes.constraint.layer import get_layered_randomization_reference_policy


def _layers(cls):
    return cls._SvObject__svtypes_layer_table


def _batches(cls):
    return cls._SvObject__svtypes_layer_batches


def test_rand_layer_alias_is_function_name_and_rejects_illegal_api():
    class Packet(SvObject):
        addr = Bit(32)
        payload = Bit(8)

        @rand_layer(100)
        def address(self):
            self.addr

        @rand_layer(-50)
        def payload_data(self):
            self.payload

    table = _layers(Packet)
    assert table["address"].priority == 100
    assert table["address"].variables == ("addr",)
    assert table["payload_data"].priority == -50
    assert table["builtin"].priority == 0

    with pytest.raises(ConstraintSyntaxError, match="non-zero int priority"):
        class BadBare(SvObject):
            addr = Bit(8)

            @rand_layer
            def address(self):
                self.addr

    with pytest.raises(ConstraintSyntaxError, match="non-zero int priority"):
        class BadBool(SvObject):
            addr = Bit(8)

            @rand_layer(True)
            def address(self):
                self.addr

    with pytest.raises(ConstraintSyntaxError, match="reserved priority 0"):
        class BadZero(SvObject):
            addr = Bit(8)

            @rand_layer(0)
            def address(self):
                self.addr

    with pytest.raises(ConstraintSyntaxError, match="keyword arguments"):
        class BadKw(SvObject):
            addr = Bit(8)

            @rand_layer(1, alias="x")
            def address(self):
                self.addr

    with pytest.raises(DeclarationError, match="builtin"):
        class BadBuiltin(SvObject):
            addr = Bit(8)

            @rand_layer(1)
            def builtin(self):
                self.addr

    source = (
        "from svtypes import SvObject, Bit, rand_layer\n"
        "class Missing(SvObject):\n"
        "    addr = Bit(8)\n"
        "    @rand_layer(1)\n"
        "    def address(self):\n"
        "        self.addr\n"
    )
    filename = "/nonexistent/svtypes_rand_layer_missing.py"
    code = compile(source, filename, "exec")
    with pytest.raises(ConstraintSyntaxError, match="source is unavailable"):
        exec(code, {})


def test_constraint_decorator_rejects_arguments():
    with pytest.raises(ConstraintSyntaxError, match="does not accept arguments"):
        class BadCall(SvObject):
            addr = Bit(8)

            @constraint()
            def legal(self):
                self.addr == 1

    with pytest.raises(ConstraintSyntaxError, match="does not accept arguments"):
        class BadArgs(SvObject):
            addr = Bit(8)

            @constraint(1)
            def legal(self):
                self.addr == 1


def test_rand_layer_body_rejects_assignment_and_instance_access():
    with pytest.raises(ConstraintSyntaxError, match="not allowed"):
        class BadAssign(SvObject):
            addr = Bit(8)

            @rand_layer(1)
            def address(self):
                self.addr = 1

    class Packet(SvObject):
        addr = Bit(8)

        @rand_layer(1)
        def address(self):
            self.addr

    pkt = Packet()
    with pytest.raises(ConstraintError, match="cannot be accessed"):
        _ = pkt.address
    with pytest.raises(ConstraintError, match="cannot be called"):
        Packet.address()


def test_same_priority_merges_and_order_is_numeric_including_builtin():
    class Packet(SvObject):
        addr = Bit(8)
        length = Bit(8)
        payload = Bit(8)
        extra = Bit(8)

        @rand_layer(100)
        def address(self):
            self.addr

        @rand_layer(100)
        def size(self):
            self.length

        @rand_layer(-50)
        def payload_data(self):
            self.payload

    batches = _batches(Packet)
    assert [batch.priority for batch in batches] == [100, 0, -50]
    merged = next(batch for batch in batches if batch.priority == 100)
    assert set(merged.aliases) == {"address", "size"}
    assert set(merged.variables) == {"addr", "length"}
    builtin = next(batch for batch in batches if batch.priority == 0)
    assert builtin.aliases == ("builtin",)
    assert builtin.variables == ("extra",)

    seen: list[tuple[int, int, int, int]] = []

    class Probe(Packet):
        def pre_randomize(self):
            seen.append(
                (
                    self.addr.rand_mode(),
                    self.length.rand_mode(),
                    self.extra.rand_mode(),
                    self.payload.rand_mode(),
                )
            )

    obj = Probe()
    with RandomContext(seed=3):
        assert obj.layered_randomize() is True
    assert seen == [
        (1, 1, 0, 0),
        (0, 0, 1, 0),
        (0, 0, 0, 1),
    ]


def test_unique_membership_inheritance_super_merge_and_constraint_replace():
    class Base(SvObject):
        addr = Bit(8)
        length = Bit(8)

        @constraint
        def address_legal(self):
            self.addr != 0

        @rand_layer(100)
        def address(self):
            self.addr
            self.address_legal

    class Child(Base):
        payload = Bit(8)

        @constraint
        def address_legal(self):
            self.addr == 1

        @rand_layer(100)
        def address(self):
            super().address()
            self.length

        @rand_layer(-1)
        def data(self):
            self.payload

    table = _layers(Child)
    assert table["address"].variables == ("addr", "length")
    assert table["address"].constraints == ("address_legal",)
    assert table["data"].variables == ("payload",)
    assert table["builtin"].variables == ()
    assert table["builtin"].constraints == ()

    class Replaced(Base):
        @rand_layer(100)
        def address(self):
            self.length

    assert _layers(Replaced)["address"].variables == ("length",)
    assert _layers(Replaced)["builtin"].variables == ("addr",)
    assert _layers(Replaced)["builtin"].constraints == ("address_legal",)

    with pytest.raises(DeclarationError, match="overlaps"):
        class DupVar(SvObject):
            addr = Bit(8)

            @rand_layer(2)
            def a(self):
                self.addr

            @rand_layer(1)
            def b(self):
                self.addr

    with pytest.raises(DeclarationError, match="non-rand"):
        class StateListed(SvObject):
            addr = Bit(8, rand=False)

            @rand_layer(1)
            def address(self):
                self.addr

    with pytest.raises(DeclarationError, match="same priority|conflicts with inherited"):
        class BadPriority(Base):
            @rand_layer(50)
            def address(self):
                super().address()

    with pytest.raises(DeclarationError, match="no inherited rand_layer"):
        class MissingSuper(SvObject):
            addr = Bit(8)

            @rand_layer(1)
            def address(self):
                super().address()
                self.addr


def test_lower_priority_reference_warning_and_error_policy():
    previous = get_layered_randomization_reference_policy()
    try:
        with pytest.warns(LayeredRandomizationPriorityWarning, match="payload"):
            class Warned(SvObject):
                addr = Bit(8)
                payload = Bit(8)

                @constraint
                def high_uses_low(self):
                    self.addr == self.payload

                @rand_layer(100)
                def high(self):
                    self.addr
                    self.high_uses_low

                @rand_layer(-50)
                def low(self):
                    self.payload

        set_layered_randomization_reference_policy("error")
        with pytest.raises(DeclarationError, match="payload"):
            class Errored(SvObject):
                addr = Bit(8)
                payload = Bit(8)

                @constraint
                def high_uses_low(self):
                    self.addr == self.payload

                @rand_layer(100)
                def high(self):
                    self.addr
                    self.high_uses_low

                @rand_layer(-50)
                def low(self):
                    self.payload
    finally:
        set_layered_randomization_reference_policy(previous)
        assert get_layered_randomization_reference_policy() == "warning"


def test_layered_randomize_ignores_entry_modes_and_restores_them():
    class Packet(SvObject):
        addr = Bit(8)
        payload = Bit(8)

        @constraint
        def address_legal(self):
            self.addr == 3

        @rand_layer(100)
        def address(self):
            self.addr
            self.address_legal

    pkt = Packet()
    pkt.addr.value = 1
    pkt.payload.value = 9
    pkt.addr.rand_mode(0)
    pkt.address_legal.constraint_mode(0)
    pkt.payload.rand_mode(0)
    with RandomContext(seed=4):
        assert pkt.layered_randomize() is True
    assert pkt.addr.value == 3
    assert pkt.addr.rand_mode() == 0
    assert pkt.address_legal.constraint_mode() == 0
    assert pkt.payload.rand_mode() == 0

    class Boom(Packet):
        def post_randomize(self):
            raise RuntimeError("hook boom")

    boom = Boom()
    boom.addr.rand_mode(0)
    boom.payload.rand_mode(0)
    with pytest.raises(RuntimeError, match="hook boom"):
        boom.layered_randomize()
    assert boom.addr.rand_mode() == 0
    assert boom.payload.rand_mode() == 0


def test_layered_failure_status_does_not_rollback_earlier_success():
    class Packet(SvObject):
        addr = Bit(8)
        payload = Bit(8)
        flag = Bit(1, rand=False)

        @constraint
        def address_legal(self):
            self.addr == 7

        @constraint
        def payload_legal(self):
            self.payload == 1
            self.flag == 1

        @rand_layer(100)
        def address(self):
            self.addr
            self.address_legal

        @rand_layer(-50)
        def payload_data(self):
            self.payload
            self.payload_legal

    pkt = Packet()
    pkt.flag.value = 0
    pkt.payload.value = 4
    with RandomContext(seed=8):
        assert pkt.layered_randomize() is False
    status = pkt.svtypes_layered_randomize_status
    assert status.ok is False
    assert status.reason == "unsat"
    assert status.failed_priority == -50
    assert status.failed_aliases == ("payload_data",)
    assert pkt.svtypes_randomize_status.reason == "unsat"
    assert pkt.addr.value == 7
    assert pkt.payload.value == 4

    class XzPkt(SvObject):
        addr = Bit(8)
        status = Logic(8, rand=False)

        @constraint
        def tied(self):
            self.addr == self.status

        @rand_layer(5)
        def later(self):
            self.addr
            self.tied

    xz = XzPkt()
    xz.status.value = LogicValue.from_string("xxxxxxxx")
    xz.addr.value = 2
    assert xz.layered_randomize() is False
    layered = xz.svtypes_layered_randomize_status
    assert layered.reason == "state_xz"
    assert layered.failed_priority == 5
    assert xz.svtypes_randomize_status.state_path == "status"
    assert xz.addr.value == 2


def test_pre_post_randomize_counts_and_inheritance():
    class Base(SvObject):
        addr = Bit(8)
        payload = Bit(8)

        @rand_layer(10)
        def high(self):
            self.addr

        def pre_randomize(self):
            self.pre_count = getattr(self, "pre_count", 0) + 1

        def post_randomize(self):
            self.post_count = getattr(self, "post_count", 0) + 1

    class Child(Base):
        def pre_randomize(self):
            super().pre_randomize()
            self.child_pre = getattr(self, "child_pre", 0) + 1

    child = Child()
    with RandomContext(seed=2):
        assert child.layered_randomize() is True
    assert child.pre_count == 2
    assert child.post_count == 2
    assert child.child_pre == 2

    class FailPost(Base):
        def post_randomize(self):
            super().post_randomize()
            if self.post_count == 1:
                raise RuntimeError("first post")

    fail = FailPost()
    with pytest.raises(RuntimeError, match="first post"):
        fail.layered_randomize()
    assert fail.post_count == 1
    assert fail.svtypes_layered_randomize_status is None

    ordinary = Base()
    ordinary.pre_count = 0
    ordinary.post_count = 0
    assert ordinary.randomize() is True
    assert ordinary.pre_count == 1
    assert ordinary.post_count == 1

    class Unsat(SvObject):
        addr = Bit(8)

        @constraint
        def impossible(self):
            self.addr == 1
            self.addr == 2

        def pre_randomize(self):
            self.pre_count = getattr(self, "pre_count", 0) + 1

        def post_randomize(self):
            self.post_count = getattr(self, "post_count", 0) + 1

    bad = Unsat()
    assert bad.randomize() is False
    assert bad.pre_count == 1
    assert getattr(bad, "post_count", 0) == 0


def test_final_entry_methods_cannot_be_redefined():
    with pytest.raises(DeclarationError, match="framework-owned"):
        class BadRand(SvObject):
            addr = Bit(8)

            def randomize(self):
                return False

    with pytest.raises(DeclarationError, match="framework-owned"):
        class BadWith(SvObject):
            addr = Bit(8)

            def randomize_with(self, fn):
                return False

    with pytest.raises(DeclarationError, match="framework-owned"):
        class BadLayer(SvObject):
            addr = Bit(8)

            def layered_randomize(self):
                return False

    with pytest.raises(DeclarationError, match="framework-owned"):
        class BadLayerContext(SvObject):
            addr = Bit(8)

            def svtypes_layered_randomize_priority(self):
                return 0

    pkt = type("Plain", (SvObject,), {"addr": Bit(8)})()
    with pytest.raises(TypeError, match="does not accept arguments"):
        pkt.layered_randomize(1)


def test_hooks_can_observe_current_layered_randomize_priority():
    seen: list[tuple[str, bool, int]] = []

    class Packet(SvObject):
        high = Bit(8)
        low = Bit(8)

        @rand_layer(5)
        def high_layer(self):
            self.high

        @rand_layer(-2)
        def low_layer(self):
            self.low

        def pre_randomize(self):
            seen.append(("pre", self.svtypes_layered_randomize_active(), self.svtypes_layered_randomize_priority()))

        def post_randomize(self):
            seen.append(("post", self.svtypes_layered_randomize_active(), self.svtypes_layered_randomize_priority()))

    packet = Packet()
    assert not packet.svtypes_layered_randomize_active()
    assert packet.layered_randomize()
    assert seen == [
        ("pre", True, 5),
        ("post", True, 5),
        ("pre", True, 0),
        ("post", True, 0),
        ("pre", True, -2),
        ("post", True, -2),
    ]
    assert not packet.svtypes_layered_randomize_active()


def test_generated_sv_emits_layered_randomize_and_schema_identity():
    class Packet(SvObject):
        addr = Bit(32)
        payload = Bit(8)

        @constraint
        def address_legal(self):
            self.addr % 64 == 0

        @rand_layer(100)
        def address(self):
            self.addr
            self.address_legal

    class Moved(SvObject):
        addr = Bit(32)
        payload = Bit(8)

        @constraint
        def address_legal(self):
            self.addr % 64 == 0

        @rand_layer(-3)
        def address(self):
            self.addr
            self.address_legal

    text = Packet.to_sv_obj()
    assert "virtual function int layered_randomize();" in text
    assert "addr.rand_mode(1);" in text
    assert "address_legal.constraint_mode(1);" in text
    assert "this.randomize()" in text
    assert "void layered_randomize" not in Packet.to_cpp_obj()

    src_a = schema_descriptor(Packet)
    src_b = schema_descriptor(Moved)
    assert src_a.schema["rand_layers"][0]["alias"] == "address"
    assert src_a.schema["rand_layers"][0]["priority"] == 100
    assert src_a.encoding_fingerprint == src_b.encoding_fingerprint
    assert src_a.schema_fingerprint != src_b.schema_fingerprint
    assert src_a.schema["constraints"][0]["ir_digest"] == src_b.schema["constraints"][0]["ir_digest"]
    assert "rand_layers" not in src_a.encoding_schema

    copied = copy.deepcopy(Packet())
    assert copied.svtypes_layered_randomize_status is None


def test_no_explicit_layer_runs_only_builtin_batch():
    class Plain(SvObject):
        addr = Bit(8)

        def pre_randomize(self):
            self.calls = getattr(self, "calls", 0) + 1

    obj = Plain()
    with RandomContext(seed=1):
        assert obj.layered_randomize() is True
    assert obj.calls == 1
    assert obj.svtypes_layered_randomize_status.ok is True
    assert obj.svtypes_layered_randomize_status.failed_priority is None
    assert obj.svtypes_layered_randomize_status.failed_aliases == ()
    layers = schema_descriptor(Plain).schema["rand_layers"]
    assert layers[0]["alias"] == "builtin"
    assert layers[0]["priority"] == 0
    assert layers[0]["variables"] == ("addr",)


def test_fixed_array_element_targets_and_illegal_indices():
    class Packet(SvObject):
        words = Array(Bit(8), 4)
        grid = Array(Bit(8), (2, 2))

        @constraint
        def first_nonzero(self):
            self.words[0] != 0

        @constraint
        def second_tied(self):
            self.words[1] == self.words[0]

        @rand_layer(100)
        def first_word(self):
            self.words[0]
            self.first_nonzero

        @rand_layer(-50)
        def second_word(self):
            self.words[1]
            self.second_tied

        @rand_layer(20)
        def corner(self):
            self.grid[0][1]

    table = _layers(Packet)
    assert table["first_word"].variables == ("words[0]",)
    assert table["second_word"].variables == ("words[1]",)
    assert table["corner"].variables == ("grid[0][1]",)
    assert table["builtin"].variables == ("words[2]", "words[3]", "grid[0][0]", "grid[1][0]", "grid[1][1]")

    seen: list[tuple[int, int]] = []

    class Probe(Packet):
        def pre_randomize(self):
            seen.append((self.words[0].rand_mode(), self.words[1].rand_mode()))

    obj = Probe()
    obj.words[0].value = 0
    obj.words[1].value = 9
    obj.words[0].rand_mode(0)
    obj.words[1].rand_mode(0)
    with RandomContext(seed=11):
        assert obj.layered_randomize() is True
    assert obj.words[0].value != 0
    assert obj.words[1].value == obj.words[0].value
    assert obj.words[0].rand_mode() == 0
    assert obj.words[1].rand_mode() == 0
    assert (1, 0) in seen
    assert (0, 1) in seen

    text = Packet.to_sv_obj()
    assert "virtual function int layered_randomize();" in text
    assert "words[0].rand_mode(1);" in text
    assert "words[1].rand_mode(1);" in text
    high = text.split("words[0].rand_mode(1);", 1)[1].split("if (!this.randomize())", 1)[0]
    assert "words[1].rand_mode(1);" not in high

    with pytest.raises(ConstraintSyntaxError, match="constant non-bool int"):
        class BadDynamic(SvObject):
            words = Array(Bit(8), 2)

            @rand_layer(1)
            def first(self):
                self.words[i]

    with pytest.raises(ConstraintSyntaxError, match="slice"):
        class BadSlice(SvObject):
            words = Array(Bit(8), 2)

            @rand_layer(1)
            def first(self):
                self.words[0:1]

    with pytest.raises(ConstraintSyntaxError, match="non-bool int"):
        class BadBoolIndex(SvObject):
            words = Array(Bit(8), 2)

            @rand_layer(1)
            def first(self):
                self.words[True]

    with pytest.raises(ConstraintSyntaxError, match="non-negative"):
        class BadNeg(SvObject):
            words = Array(Bit(8), 2)

            @rand_layer(1)
            def first(self):
                self.words[-1]

    with pytest.raises(DeclarationError, match="out of range"):
        class BadOob(SvObject):
            words = Array(Bit(8), 2)

            @rand_layer(1)
            def first(self):
                self.words[4]

    with pytest.raises(DeclarationError, match="packed vector"):
        class BadBitSelect(SvObject):
            addr = Bit(8)

            @rand_layer(1)
            def bit0(self):
                self.addr[0]

    with pytest.raises(DeclarationError, match="overlaps"):
        class WholeAndElem(SvObject):
            words = Array(Bit(8), 2)

            @rand_layer(2)
            def all_words(self):
                self.words

            @rand_layer(1)
            def first(self):
                self.words[0]


def test_array_element_cross_priority_is_state():
    class Packet(SvObject):
        words = Array(Bit(8), 2)

        @constraint
        def first_fixed(self):
            self.words[0] == 5

        @constraint
        def second_uses_first(self):
            self.words[1] == self.words[0]

        @rand_layer(100)
        def first(self):
            self.words[0]
            self.first_fixed

        @rand_layer(-1)
        def second(self):
            self.words[1]
            self.second_uses_first

    obj = Packet()
    with RandomContext(seed=6):
        assert obj.layered_randomize() is True
    assert obj.words[0].value == 5
    assert obj.words[1].value == 5

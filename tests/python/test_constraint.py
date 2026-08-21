from __future__ import annotations

import copy

import pytest

from svtypes import (
    Array,
    Bit,
    ConstraintError,
    ConstraintNameError,
    ConstraintSyntaxError,
    ConstraintTypeError,
    ConstraintUnsupportedError,
    DeclarationError,
    Enum,
    Int,
    Logic,
    LogicValue,
    ParamRef,
    Parameter,
    RandomContext,
    SCHEMA_FORMAT_VERSION,
    SvObject,
    SvStruct,
    constraint,
    schema_descriptor,
)
from svtypes.constraint.eval import eval_bool
from svtypes.constraint.leaves import iter_class_leaves, leaf_unsigned, resolve_attr


class Mode(Enum, width=8, signed=False):
    READ = 0
    WRITE = 1
    IDLE = 2


class Header(SvStruct):
    addr = Bit(16)
    extra = Bit(8)


class Packet(SvObject):
    addr = Bit(32)
    length = Bit(16)
    burst = Bit(1)
    data = Logic(32)
    limit = Bit(32, rand=False)
    unused = Bit(8)
    mode = Mode()
    header = Header(rand=True)
    words = Array(Bit(8), 4)

    @constraint
    def legal(self):
        0x1000 <= self.addr
        self.addr < self.limit
        self.addr % 64 == 0
        self.data & 0x1 == 0
        if self.burst:
            1 <= self.length
            self.length <= 256
        else:
            self.length == 1


class Child(Packet):
    @constraint
    def legal(self):
        self.addr == 0x1000

    @constraint
    def extra(self):
        self.unused == 3


def _irs(cls):
    return cls._SvObject__svtypes_constraint_irs


def _leaf_snapshot(obj):
    snap = {}
    for path, _desc, _declared in iter_class_leaves(type(obj)):
        target = resolve_attr(obj, path)
        value = target.value
        if isinstance(value, LogicValue):
            snap[path] = (value.width, value.value_mask, value.x_mask, value.z_mask)
        else:
            snap[path] = value
    return snap


def _env_and_widths(obj):
    env = {}
    widths = {}
    for path, desc, _declared in iter_class_leaves(type(obj)):
        target = resolve_attr(obj, path)
        env[path] = leaf_unsigned(target, target.value)
        widths[path] = (desc.width, bool(desc.signed))
    return env, widths


def _assert_enabled_predicates(obj):
    modes = obj._SvObject__svtypes_constraint_modes
    env, widths = _env_and_widths(obj)
    for name, ir in _irs(type(obj)).items():
        if modes.get(name, 1) != 1:
            continue
        for pred in ir.predicates:
            assert eval_bool(pred, env, widths), (name, pred.to_stable())


def test_constraint_frontend_rejects_runtime_and_python():
    with pytest.raises(ConstraintSyntaxError):
        class BadValue(SvObject):
            addr = Bit(8)

            @constraint
            def legal(self):
                self.addr.value == 1

    with pytest.raises(ConstraintSyntaxError):
        class BadCall(SvObject):
            addr = Bit(8)

            @constraint
            def legal(self):
                print(self.addr)


def test_packet_constraint_compiles_and_randomizes():
    pkt = Packet()
    pkt.limit.value = 0x2000
    ctx = RandomContext(seed=1)
    with ctx:
        assert pkt.randomize() is True
    assert pkt.svtypes_randomize_status.ok is True
    assert pkt.svtypes_randomize_status.reason == "sat"
    assert pkt.addr.value >= 0x1000
    assert pkt.addr.value < 0x2000
    assert pkt.addr.value % 64 == 0
    assert pkt.data.value.x_mask == 0 and pkt.data.value.z_mask == 0
    assert (pkt.data.value.value_mask & 1) == 0
    if pkt.burst.value:
        assert 1 <= pkt.length.value <= 256
    else:
        assert pkt.length.value == 1
    _assert_enabled_predicates(pkt)


def test_logicbits_state_xz_is_transactional():
    class StatePkt(SvObject):
        addr = Bit(8)
        unused = Bit(8)
        status = Logic(8, rand=False)
        payload = Logic(4)

        @constraint
        def legal(self):
            self.addr == self.status

    obj = StatePkt()
    obj.addr.value = 3
    obj.unused.value = 9
    obj.payload.value = LogicValue.from_string("10xz")
    obj.status.value = LogicValue.from_string("10xx0001")
    before = _leaf_snapshot(obj)
    assert obj.randomize() is False
    assert obj.svtypes_randomize_status.reason == "state_xz"
    assert obj.svtypes_randomize_status.state_path == "status"
    assert _leaf_snapshot(obj) == before


def test_rand_logic_with_xz_is_overwritten():
    class Payload(SvObject):
        data = Logic(4)

    obj = Payload()
    obj.data.value = LogicValue.from_string("xxzz")
    assert obj.randomize() is True
    assert obj.data.value.x_mask == 0
    assert obj.data.value.z_mask == 0


def test_sat_logicbits_writeback_has_no_xz():
    pkt = Packet()
    pkt.limit.value = 0x2000
    pkt.data.value = LogicValue.from_string("xxxxzzzz" * 4)
    assert pkt.randomize() is True
    for path, desc, _declared in iter_class_leaves(Packet):
        if not isinstance(desc, Logic):
            continue
        value = resolve_attr(pkt, path).value
        assert value.x_mask == 0
        assert value.z_mask == 0


def test_unsat_is_transactional_for_every_leaf():
    class Clash(SvObject):
        addr = Bit(8)
        data = Logic(8)
        mode = Mode()
        header = Header(rand=True)
        words = Array(Bit(8), 2)

        @constraint
        def a(self):
            self.addr == 1

        @constraint
        def b(self):
            self.addr == 2

    obj = Clash()
    obj.addr.value = 9
    obj.data.value = LogicValue.from_string("10xz0011")
    obj.mode.value = Mode.IDLE
    obj.header.addr.value = 7
    obj.header.extra.value = 5
    obj.words[0].value = 11
    obj.words[1].value = 12
    before = _leaf_snapshot(obj)
    ctx = RandomContext(seed=4)
    with ctx:
        assert obj.randomize() is False
        assert ctx.call_index == 1
        assert obj.randomize() is False
        assert ctx.call_index == 2
    assert obj.svtypes_randomize_status.reason == "unsat"
    assert _leaf_snapshot(obj) == before


def test_constraint_mode_disables_block_at_randomize_time():
    class Clash(SvObject):
        addr = Bit(8)

        @constraint
        def a(self):
            self.addr == 1

        @constraint
        def b(self):
            self.addr == 2

    obj = Clash()
    obj.addr.value = 9
    obj.a.constraint_mode(0)
    assert obj.randomize() is True
    assert obj.addr.value == 2
    obj.a.constraint_mode(1)
    obj.b.constraint_mode(0)
    assert obj.randomize() is True
    assert obj.addr.value == 1


def test_constraint_mode_and_rand_mode():
    pkt = Packet()
    pkt.limit.value = 0x2000
    assert pkt.legal.constraint_mode() == 1
    pkt.legal.constraint_mode(0)
    assert pkt.legal.constraint_mode() == 0
    digest_before = schema_descriptor(Packet).schema["constraints"][0]["ir_digest"]
    pkt.addr.rand_mode(0)
    digest_after = schema_descriptor(Packet).schema["constraints"][0]["ir_digest"]
    assert digest_before == digest_after
    with pytest.raises(TypeError):
        pkt.legal.constraint_mode(True)
    with pytest.raises(TypeError):
        pkt.addr.rand_mode(False)
    with pytest.raises((AttributeError, ConstraintError, TypeError)):
        pkt.limit.rand_mode(0)
    with pytest.raises(ConstraintError):
        Packet.legal.constraint_mode(0)
    with pytest.raises(ConstraintError):
        pkt.legal()
    with pytest.raises(TypeError):
        pkt.randomize(seed=1)


def test_rand_mode_keeps_current_value_and_nested_leaves():
    pkt = Packet()
    pkt.limit.value = 0x2000
    pkt.unused.value = 0x5A
    pkt.unused.rand_mode(0)
    pkt.header.addr.value = 12
    pkt.header.addr.rand_mode(0)
    pkt.words[0].value = 9
    pkt.words[0].rand_mode(0)
    assert pkt.randomize() is True
    assert pkt.unused.value == 0x5A
    assert pkt.header.addr.value == 12
    assert pkt.words[0].value == 9
    assert pkt.header.extra.value != 12 or pkt.words[1].value != 9
    pkt.header.rand_mode(0)
    extra = pkt.header.extra.value
    assert pkt.randomize() is True
    assert pkt.header.addr.value == 12
    assert pkt.header.extra.value == extra


def test_rand_mode_on_state_field_and_standalone_struct_is_rejected():
    pkt = Packet()
    with pytest.raises((AttributeError, ConstraintError, TypeError)):
        pkt.limit.rand_mode(1)
    header = Header()
    with pytest.raises((AttributeError, ConstraintError, TypeError)):
        header.addr.rand_mode(0)


def test_copy_has_independent_modes():
    pkt = Packet()
    pkt.legal.constraint_mode(0)
    pkt.addr.rand_mode(0)
    other = copy.copy(pkt)
    deep = copy.deepcopy(pkt)
    assert other.legal.constraint_mode() == 0
    assert other.addr.rand_mode() == 0
    assert deep.legal.constraint_mode() == 0
    other.legal.constraint_mode(1)
    other.addr.rand_mode(1)
    deep.addr.rand_mode(1)
    assert pkt.legal.constraint_mode() == 0
    assert pkt.addr.rand_mode() == 0
    assert other.legal.constraint_mode() == 1
    assert deep.addr.rand_mode() == 1


def test_inheritance_override_and_extra():
    child = Child()
    child.limit.value = 0x2000
    assert child.randomize() is True
    assert child.addr.value == 0x1000
    assert child.unused.value == 3
    names = [item["name"] for item in schema_descriptor(Child).schema["constraints"]]
    assert names == ["extra", "legal"]
    parent_digest = schema_descriptor(Packet).schema["constraints"][0]["ir_digest"]
    child_legal = next(
        item for item in schema_descriptor(Child).schema["constraints"] if item["name"] == "legal"
    )
    assert child_legal["ir_digest"] != parent_digest
    _assert_enabled_predicates(child)


def test_schema_constraints_and_encoding_isolation():
    class Plain(SvObject):
        addr = Bit(8)

    class Constrained(SvObject):
        addr = Bit(8)

        @constraint
        def zebra(self):
            self.addr != 0

        @constraint
        def alpha(self):
            self.addr == 1

    plain = schema_descriptor(Plain)
    constrained = schema_descriptor(Constrained)
    assert "constraints" not in plain.schema
    names = [item["name"] for item in constrained.schema["constraints"]]
    assert names == ["alpha", "zebra"]
    digest = constrained.schema["constraints"][0]["ir_digest"]
    assert len(digest) == 64
    assert digest == digest.lower()
    assert all(ch in "0123456789abcdef" for ch in digest)
    assert "constraints" not in constrained.encoding_schema
    assert SCHEMA_FORMAT_VERSION == 1

    class TwinA(SvObject):
        addr = Bit(8)

    class TwinB(SvObject):
        addr = Bit(8)

        @constraint
        def legal(self):
            self.addr == 1

    twin_a = schema_descriptor(TwinA)
    twin_b = schema_descriptor(TwinB)
    assert list(twin_a.encoding_schema["fields"]) == list(twin_b.encoding_schema["fields"])
    assert twin_a.encoding_fingerprint == twin_b.encoding_fingerprint
    assert twin_a.schema_fingerprint != twin_b.schema_fingerprint
    assert "constraints" not in schema_descriptor(Header).schema

    class SameA(SvObject):
        addr = Bit(8)

        @constraint
        def legal(self):
            self.addr == 1

    class SameB(SvObject):
        addr = Bit(8)

        @constraint
        def legal(self):
            self.addr == 1

    assert (
        schema_descriptor(SameA).schema["constraints"][0]["ir_digest"]
        == schema_descriptor(SameB).schema["constraints"][0]["ir_digest"]
    )


def test_ir_marks_logicbits_projection_and_unrolls_paths():
    legal = _irs(Packet)["legal"]
    by_path = {var.path: var for var in legal.vars}
    assert by_path["data"].projected_from == "logic"
    assert by_path["addr"].projected_from is None
    assert by_path["addr"].declared_rand is True
    assert by_path["limit"].declared_rand is False

    class Arr(SvObject):
        data = Array(Bit(8), 3)

        @constraint
        def legal(self):
            for i in range(3):
                self.data[i] != 0

    paths = {var.path for var in _irs(Arr)["legal"].vars}
    assert paths == {"data[0]", "data[1]", "data[2]"}

    class Nested(SvObject):
        header = Header(rand=True)

        @constraint
        def legal(self):
            self.header.addr % 4 == 0

    assert {var.path for var in _irs(Nested)["legal"].vars} == {"header.addr"}


def test_mixed_width_sign_and_logic_vs_bits():
    class Mix(SvObject):
        small = Bit(8, signed=False)
        wide = Int()
        bits = Bit(8)
        logic = Logic(8, signed=True)

        @constraint
        def legal(self):
            self.small == 3
            self.wide == 3
            self.bits == self.logic
            self.logic != 0

    obj = Mix()
    assert obj.randomize() is True
    assert obj.small.value == 3
    assert obj.wide.value == 3
    assert obj.bits.value == obj.logic.value.value_mask
    assert obj.logic.value.x_mask == 0
    assert obj.logic.value.z_mask == 0
    _assert_enabled_predicates(obj)


def test_enum_domain_is_enforced():
    class Box(SvObject):
        mode = Mode()

        @constraint
        def legal(self):
            self.mode in (Mode.READ, Mode.WRITE)

    obj = Box()
    seen = set()
    ctx = RandomContext(seed=8)
    with ctx:
        for _ in range(16):
            assert obj.randomize() is True
            seen.add(int(obj.mode.value))
            _assert_enabled_predicates(obj)
    assert seen <= {0, 1}
    assert 2 not in seen


def test_unconstrained_field_is_still_written():
    pkt = Packet()
    pkt.limit.value = 0x2000
    pkt.unused.value = 0
    values = set()
    ctx = RandomContext(seed=3)
    with ctx:
        pkt.randomize()
        values.add(pkt.unused.value)
        pkt.randomize()
        values.add(pkt.unused.value)
    assert len(values) == 2


def test_template_and_specialize():
    class Template(SvObject):
        WIDTH = Parameter(Int)
        addr = Bit(8)

        @constraint
        def legal(self):
            self.addr < WIDTH

    with pytest.raises(DeclarationError):
        Template()
    with pytest.raises(DeclarationError):
        schema_descriptor(Template)
    with pytest.raises(DeclarationError):
        Template.specialize()
    # The parameterized class itself is a template; its definition is
    # generated and specializations simply reference it by parameters.
    # Constraints live on the template with symbolic parameter references.
    sv = Template.to_sv_obj()
    assert "class Template #(parameter int WIDTH) extends svtypes_pkg::sv_object;" in sv
    assert "rand bit [7:0] addr;" in sv
    legal_block = sv.split("constraint legal {", 1)[1].split("}", 1)[0]
    assert "addr < WIDTH" in legal_block
    assert "10" not in legal_block
    cpp = Template.to_cpp_obj()
    assert "template <int32_t WIDTH>" in cpp
    assert "struct Template : public svtypes::SvObject {" in cpp
    Actual = Template.specialize(WIDTH=10)
    Again = Template.specialize(WIDTH=10)
    Wider = Template.specialize(WIDTH=11)
    # A specialization is a Python-side binding; it never emits a generated
    # SV/C++ class (the target-language type is Template#(.WIDTH(10))).
    with pytest.raises(DeclarationError, match="Python-side binding"):
        Actual.to_sv_obj()
    with pytest.raises(DeclarationError, match="Python-side binding"):
        Actual.to_cpp_obj()
    obj = Actual()
    assert obj.randomize() is True
    assert obj.addr.value < 10
    assert (
        schema_descriptor(Actual).schema["constraints"][0]["ir_digest"]
        == schema_descriptor(Again).schema["constraints"][0]["ir_digest"]
    )
    assert (
        schema_descriptor(Actual).schema["constraints"][0]["ir_digest"]
        != schema_descriptor(Wider).schema["constraints"][0]["ir_digest"]
    )
    canon = _irs(Actual)["legal"].stable_dict()
    assert any(node == ["int", 10] for pred in canon["predicates"] for node in _walk_stable(pred))


def _walk_stable(node):
    yield node
    if isinstance(node, list):
        for item in node[1:]:
            yield from _walk_stable(item)


def test_specialize_requires_every_unbound_parameter():
    class Template(SvObject):
        A = Parameter(Int)
        B = Parameter(Int)
        addr = Bit(8)

        @constraint
        def legal(self):
            self.addr < A

    with pytest.raises(DeclarationError, match="missing"):
        Template.specialize(A=1)
    Bound = Template.specialize(A=4, B=8)
    obj = Bound()
    assert obj.randomize() is True
    assert obj.addr.value < 4


def test_template_cannot_be_used_as_field_type():
    class Template(SvObject):
        WIDTH = Parameter(Int)
        addr = Bit(8)

    with pytest.raises(DeclarationError, match="parameterized template"):
        class Holder(SvObject):
            child = Template()


def test_type_parameter_in_constraint_is_rejected_at_class_creation():
    # Template constraints are compiled at class creation with symbolic
    # parameter references; a type parameter cannot enter the solver domain,
    # so the template class itself is rejected.
    class Payload(SvObject):
        x = Bit(4)

    with pytest.raises(ConstraintTypeError, match="cannot be used in constraints"):
        class Template(SvObject):
            T = Parameter(type)
            addr = Bit(8)

            @constraint
            def legal(self):
                self.addr < T


def test_unbound_int_parameter_template_randomize_is_rejected():
    class Template(SvObject):
        WIDTH = Parameter(Int)
        addr = Bit(8)

    with pytest.raises(DeclarationError, match="parameterized template"):
        Template().randomize()


def test_parameter_value_is_folded_into_ir_digest():
    class Template(SvObject):
        WIDTH = Parameter(Int)
        addr = Bit(8)

        @constraint
        def legal(self):
            self.addr == WIDTH

    Narrow = Template.specialize(WIDTH=4)
    Wide = Template.specialize(WIDTH=8)
    digest_a = schema_descriptor(Narrow).schema["constraints"][0]["ir_digest"]
    digest_b = schema_descriptor(Wide).schema["constraints"][0]["ir_digest"]
    assert digest_a != digest_b
    assert digest_a == schema_descriptor(Template.specialize(WIDTH=4)).schema["constraints"][0]["ir_digest"]


def test_source_unavailable_fails_at_class_creation():
    source = (
        "from svtypes import SvObject, Bit, constraint\n"
        "class Missing(SvObject):\n"
        "    addr = Bit(8)\n"
        "    @constraint\n"
        "    def legal(self):\n"
        "        self.addr == 1\n"
    )
    filename = "/nonexistent/svtypes_constraint_missing.py"
    code = compile(source, filename, "exec")
    ns: dict = {}
    with pytest.raises(ConstraintSyntaxError, match="source is unavailable"):
        exec(code, ns)


def test_randomize_with_inline_function():
    pkt = Packet()
    pkt.limit.value = 0x2000

    def aligned(pkt):
        pkt.addr == 0x1000

    assert pkt.randomize_with(aligned) is True
    assert pkt.addr.value == 0x1000
    _assert_enabled_predicates(pkt)


def test_name_conflict_with_method_and_field():
    with pytest.raises(DeclarationError):
        class ClashMethod(SvObject):
            addr = Bit(8)

            @constraint
            def pack(self):
                self.addr == 1

    with pytest.raises(DeclarationError, match="field or parameter"):
        class BaseField(SvObject):
            addr = Bit(8)

        class ClashField(BaseField):
            @constraint
            def addr(self):
                self.addr == 1

    with pytest.raises(DeclarationError, match="reserved"):
        class ClashReserved(SvObject):
            addr = Bit(8)

            @constraint
            def _svtypes_secret(self):
                self.addr == 1


def test_unknown_field_is_a_name_error():
    with pytest.raises(ConstraintNameError):
        class Missing(SvObject):
            addr = Bit(8)

            @constraint
            def legal(self):
                self.length == 1


def test_sv_codegen_emits_constraint_and_signed_logic():
    class Sample(SvObject):
        addr = Bit(8)
        data = Logic(8, signed=True)

        @constraint
        def legal(self):
            self.addr == 1
            self.data != 0

    text = Sample.to_sv_obj()
    assert "constraint legal {" in text
    assert "virtual function int layered_randomize();" in text
    assert "rand logic signed [7:0] data;" in text
    assert "svtypes_encoding_descriptor" in text
    assert "(addr == 8'd1)" in text
    cpp = Sample.to_cpp_obj()
    assert "constraint legal" not in cpp
    assert "void randomize" not in cpp


def test_python_sat_assignments_satisfy_ir_predicates():
    pkt = Packet()
    pkt.limit.value = 0x2000
    ctx = RandomContext(seed=17)
    with ctx:
        for _ in range(12):
            assert pkt.randomize() is True
            _assert_enabled_predicates(pkt)
            assert pkt.data.value.x_mask == 0
            assert pkt.data.value.z_mask == 0


def test_symbolic_for_loop_renders_on_template():
    """range() bounds that are unbound parameters stay a loop and render as a
    target-language for; constant bounds are unrolled for Python randomize."""
    class Loop(SvObject):
        WIDTH = Parameter(Int)
        words = Array(Bit(8), 4)

        @constraint
        def legal(self):
            for i in range(WIDTH):
                self.words[i] != 0

    sv = Loop.to_sv_obj()
    legal = sv.split("constraint legal {", 1)[1].split("}", 1)[0]
    # SV constraints have no `for`; a symbolic range loop renders as a
    # foreach over the indexed array filtered by the loop bounds.
    assert "foreach (words[i])" in legal
    assert "i < WIDTH" in legal
    assert "words[i] != " in legal

    # Constant bound: unrolled (loop variable folded into the index).
    Spec = Loop.specialize(WIDTH=3)
    canon = _irs(Spec)["legal"].stable_dict()
    preds = canon["predicates"]
    assert len(preds) == 3
    text = str(preds)
    assert all(f"words[{idx}]" in text for idx in range(3))
    obj = Spec()
    ctx = RandomContext(seed=7)
    with ctx:
        assert obj.randomize() is True
        assert all(word.value != 0 for word in obj.words)


def test_constraints_render_only_on_declaring_class():
    class CBase(SvObject):
        W = Parameter(Int)
        addr = Bit(8)

        @constraint
        def legal(self):
            self.addr < W

    class Kid(CBase.specialize(W=8)):
        y = Bit(4)

    # The subclass inherits the constraint via extends; it must not re-render it.
    assert "constraint legal {" not in Kid.to_sv_obj()

    class Same(CBase.specialize(W=ParamRef())):
        W = Parameter(Int)

        @constraint
        def extra(self):
            self.addr != 0

    sv = Same.to_sv_obj()
    assert "constraint extra {" in sv
    assert "constraint legal {" not in sv


def test_symbolic_range_without_array_fails_at_class_creation():
    with pytest.raises(ConstraintUnsupportedError, match="must index an array"):
        class Bad(SvObject):
            W = Parameter(Int)
            addr = Bit(8)

            @constraint
            def legal(self):
                for i in range(W):
                    self.addr < W

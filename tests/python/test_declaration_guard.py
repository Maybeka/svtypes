"""A class body must materialize every SvTypes declaration it names.

An uncalled bracket specification (``data = Bit[8]``) or a bare type class
(``data = Bit``) used to be accepted and then silently omitted from schema,
packing, randomization and generated target code.  These regressions cover the
guard that rejects those spellings, and the metadata escape hatch that keeps
ordinary class-level values available.
"""

from __future__ import annotations

import pytest

from svtypes import (
    Array,
    AssocArray,
    Bit,
    BitSigned,
    CovPoint,
    DeclarationError,
    DynArray,
    Enum,
    Int,
    Logic,
    LongInt,
    Object,
    Parameter,
    ParamRef,
    Queue,
    Real,
    Reg,
    RemoteRef,
    ShortReal,
    String,
    SvObject,
    SvStruct,
    bins,
    covergroup,
    constraint,
    rand_layer,
)


class Child(SvObject):
    value = Bit[4]()


class Header(SvStruct):
    value = Bit[4]()


UNCALLED_SPECS = (
    ("Bit[8]", lambda: Bit[8], "add the initializer call: Bit[8]()"),
    ("Logic[4]", lambda: Logic[4], "add the initializer call: Logic[4]()"),
    ("Reg[2]", lambda: Reg[2], "add the initializer call: Reg[2]()"),
    ("BitSigned[8]", lambda: BitSigned[8], "add the initializer call: Bit[8, Signed]()"),
    ("Array[Bit[8], 2]", lambda: Array[Bit[8], 2], "Array[Bit[8], 2]()"),
    ("DynArray[Int]", lambda: DynArray[Int], "DynArray[Int]()"),
    ("Queue[Int]", lambda: Queue[Int], "Queue[Int]()"),
    ("AssocArray[Bit[4], Int]", lambda: AssocArray[Bit[4], Int], "AssocArray[Bit[4], Int]()"),
    ("Object['Child']", lambda: Object["Child"], "Object['Child']()"),
    ("RemoteRef[...]", lambda: RemoteRef["sv://tb/Driver"], "add the initializer call: RemoteRef['"),
    ("Parameter[Int]", lambda: Parameter[Int], "Parameter[Int]()"),
    ("Enum[Bit[4]]", lambda: Enum[Bit[4]], "Enum[Bit[width]] subclass"),
)

BARE_TYPES = (
    ("Bit", lambda: Bit, "use Bit() for a single bit or Bit[width](...)"),
    ("Logic", lambda: Logic, "use Logic() for a single bit or Logic[width](...)"),
    ("Reg", lambda: Reg, "use Reg() for a single bit or Reg[width](...)"),
    ("Int", lambda: Int, "use Int()"),
    ("LongInt", lambda: LongInt, "use LongInt()"),
    ("String", lambda: String, "use String()"),
    ("Real", lambda: Real, "use Real()"),
    ("ShortReal", lambda: ShortReal, "use ShortReal()"),
    ("Array", lambda: Array, "use Array[ElementType, size]()"),
    ("DynArray", lambda: DynArray, "use DynArray[ElementType]()"),
    ("Queue", lambda: Queue, "use Queue[ElementType]()"),
    ("AssocArray", lambda: AssocArray, "use AssocArray[KeyType, ValueType]()"),
    ("RemoteRef", lambda: RemoteRef, 'use RemoteRef["target"]()'),
    ("Enum", lambda: Enum, "use an Enum[Bit[width]] subclass"),
    ("Parameter", lambda: Parameter, "use Parameter[Int]()"),
    ("Object", lambda: Object, 'use Object["Target"]()'),
    ("SvObject", lambda: SvObject, 'use Object["Target"]() or a concrete SvObject subclass'),
    ("SvStruct", lambda: SvStruct, 'use Object["Target"]() or a concrete SvObject subclass'),
    ("Child", lambda: Child, "use Child()"),
    ("Header", lambda: Header, "use Header()"),
)


@pytest.mark.parametrize("label, build, expected", UNCALLED_SPECS, ids=[item[0] for item in UNCALLED_SPECS])
def test_uncalled_bracket_specification_is_rejected(label, build, expected) -> None:
    with pytest.raises(DeclarationError) as error:
        type("Guarded", (SvObject,), {"field": build()})

    message = str(error.value)
    assert "Guarded.field declares no field" in message
    assert expected in message
    assert 'prefix the name with "_"' in message


@pytest.mark.parametrize("label, build, expected", BARE_TYPES, ids=[item[0] for item in BARE_TYPES])
def test_bare_type_class_is_rejected(label, build, expected) -> None:
    with pytest.raises(DeclarationError) as error:
        type("Guarded", (SvObject,), {"field": build()})

    message = str(error.value)
    assert "Guarded.field declares no field" in message
    assert expected in message


def test_guard_also_covers_struct_bodies() -> None:
    with pytest.raises(DeclarationError, match="declares no field"):
        type("GuardedStruct", (SvStruct,), {"field": Bit[8]})


def test_materialized_declarations_are_untouched() -> None:
    class Declared(SvObject):
        packed = Bit[8]()
        wide = Logic[4]()
        historical = Reg[2]()
        scalar = Int()
        text = String()
        fixed = Array[Int, 2]()
        dynamic = DynArray[Int]()
        fifo = Queue[Int]()
        table = AssocArray[Bit[4], Int]()
        handle = Object["Child"]()
        external = RemoteRef["sv://tb/Driver"]()
        member = Child()
        struct_value = Header()
        one = Bit()
        one_logic = Logic()
        W = Parameter[Int](8)

        @constraint
        def bounded(self):
            self.packed != 0

        @rand_layer(10)
        def layer(self):
            self.packed
            self.bounded

    names = [name for name, _ in Declared._SvObject__svtypes_members]
    assert names == [
        "packed",
        "wide",
        "historical",
        "scalar",
        "text",
        "fixed",
        "dynamic",
        "fifo",
        "table",
        "handle",
        "external",
        "member",
        "struct_value",
        "one",
        "one_logic",
    ]
    assert [name for name, _ in Declared._SvObject__svtypes_params] == ["W"]


def test_ordinary_class_metadata_is_not_a_declaration() -> None:
    class Metadata(SvObject):
        data = Bit[8]()
        COUNT = 4
        NAME = "packet"
        FLAGS = (True, False)
        TABLE = {"a": 1}
        LIMIT = None

        def helper(self) -> int:
            return 1

        class Local:
            pass

    assert [name for name, _ in Metadata._SvObject__svtypes_members] == ["data"]


def test_underscore_prefix_keeps_class_level_metadata() -> None:
    class Annotated(SvObject):
        data: Bit[8] = Bit[8]()
        _Payload = Bit[8]
        _alias = Object["Child"]
        _one = Bit

    assert [name for name, _ in Annotated._SvObject__svtypes_members] == ["data"]
    assert Annotated._Payload is Bit[8]


def test_guard_inspects_the_own_class_body_only() -> None:
    class Base(SvObject):
        data = Bit[8]()

    # Injected after class creation: an inherited attribute is not a body
    # declaration, so a subclass must keep working.
    Base.Payload = Bit[8]

    class Sub(Base):
        extra = Bit[4]()

    assert [name for name, _ in Sub._SvObject__svtypes_members] == ["data", "extra"]


def test_param_ref_forwarding_namespace_is_not_rejected() -> None:
    class Template(SvObject):
        W = Parameter[Int]()
        data = Bit[8]()

    class Forwarding(Template.specialize(W=ParamRef())):
        W = Parameter[Int]()

    assert Forwarding._SvObject__svtypes_param_refs == {"W": "W"}
    assert [name for name, _ in Forwarding._SvObject__svtypes_members] == ["data"]


def test_coverage_declaration_dsl_is_not_a_type_declaration() -> None:
    class Covered(SvObject):
        opcode = Bit[8]()

        @covergroup
        def control(self):
            class point(CovPoint, source=self.opcode):
                zero = bins[0]
                one = bins[1]

    assert [name for name, _ in Covered._SvObject__svtypes_members] == ["opcode"]

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

from svtypes import (
    Array,
    AssocArray,
    Bit,
    BitSigned,
    DynArray,
    Enum,
    Int,
    Logic,
    LogicSigned,
    Object,
    Parameter,
    Queue,
    Reg,
    RegSigned,
    RemoteRef,
    Signed,
    Unsigned,
)
from svtypes.schema import schema_descriptor
from svtypes import DeclarationError


def test_packed_bracket_specs_are_cached_and_construct_existing_values() -> None:
    assert Bit[8] is Bit[8]
    assert BitSigned[8] is Bit[8, Signed]
    assert Bit[8, Unsigned]().signed is False
    assert Bit[8](0x1FF).value == 0xFF
    assert Bit[8](value=0x1FF).value == 0xFF
    with pytest.raises(TypeError, match="either positionally"):
        Bit[8](1, value=None)
    assert LogicSigned[4]("1x0z").width == 4
    assert Reg[4]().sv_declaration_style == "reg"
    assert RegSigned[4]().signed is True


def test_bracket_annotation_is_metadata_and_cannot_declare_a_field_by_itself() -> None:
    from svtypes import SvObject

    class Annotated(SvObject):
        data: Bit[8] = Bit[8]()

    assert Annotated().data.width == 8

    with pytest.raises(DeclarationError, match="no field value"):
        class MissingInitializer(SvObject):
            data: Bit[8]

    namespace = {"SvObject": SvObject, "Bit": Bit}
    with pytest.raises(DeclarationError, match="no field value"):
        exec(
            "from __future__ import annotations\n"
            "class DeferredMissingInitializer(SvObject):\n"
            "    data: Bit[8]\n",
            namespace,
        )


def test_collection_bracket_specs_materialize_descriptor_templates() -> None:
    assert Array[Bit[8], 2]().value == [0, 0]
    assert Array[Bit[4], (2, 3)]().value == [[0, 0, 0], [0, 0, 0]]
    assert Array[Array[Bit[4], 3], 2]().value == [[0, 0, 0], [0, 0, 0]]
    assert DynArray[Bit[8]]().value == []
    assert Queue[Bit[8]]().value == []
    assert AssocArray[Bit[8], Logic[4]]().value == {}


def test_legacy_type_declaring_constructors_are_rejected() -> None:
    with pytest.raises(TypeError, match=r"Bit\[width\]"):
        Bit(8)
    with pytest.raises(TypeError, match=r"Logic\[width\]"):
        Logic(8)
    with pytest.raises(TypeError, match=r"Reg\[width\]"):
        Reg(8)
    with pytest.raises(TypeError, match=r"Array\[ElementType, size\]"):
        Array(Bit[8](), 2)
    with pytest.raises(TypeError, match=r"DynArray\[ElementType\]"):
        DynArray(Bit[8]())
    with pytest.raises(TypeError, match=r"Queue\[ElementType\]"):
        Queue(Bit[8]())
    with pytest.raises(TypeError, match=r"AssocArray\[KeyType, ValueType\]"):
        AssocArray(Bit[8](), Bit[8]())


def test_handle_specs_keep_existing_descriptor_identity() -> None:
    from svtypes import ObjectRegistry, SvObject, svobj

    assert Object["Child"] is Object["Child"]
    assert Object["Child"](rand=True).cls_name == "Child"
    assert Object["Child"](rand=True).rand is True
    assert RemoteRef["transport.Device"](3).value.object_number == 3
    with pytest.raises(TypeError, match=r"Object\["):
        Object("Child")
    with pytest.raises(TypeError, match=r"RemoteRef\["):
        RemoteRef("transport.Device")

    registry = ObjectRegistry()

    @svobj(registry=registry, name="wire_child")
    class RegisteredChild(SvObject):
        pass

    class UnregisteredChild(SvObject):
        pass

    assert Object[RegisteredChild](registry=registry).cls_name == "wire_child"
    with pytest.raises(TypeError, match="registered SvObject"):
        Object[UnregisteredChild]


def test_parameter_bracket_specs_declare_type_and_optional_default() -> None:
    assert Parameter[Int]().dtype == "int"
    assert Parameter[Int](8).value == 8
    assert Parameter[type](Bit[8]).value is Bit[8]


def test_type_parameter_field_uses_symbolic_template_and_bound_specialization() -> None:
    from svtypes import SvObject

    class Holder(SvObject):
        T = Parameter[type]()
        data = T()

    assert "parameter type T" in Holder.to_sv_obj()
    assert "T data;" in Holder.to_sv_obj()
    assert "template <typename T>" in Holder.to_cpp_obj()
    assert "T data;" in Holder.to_cpp_obj()

    ByteHolder = Holder.specialize(T=Bit[8])
    instance = ByteHolder()
    instance.data.value = 0x1FF
    assert instance.data.value == 0xFF


def test_type_parameter_field_generates_a_compilable_cpp_template() -> None:
    from svtypes import SvObject

    class Holder(SvObject):
        T = Parameter[type]()
        data = T()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "model.hpp").write_text(
            "#pragma once\n#include \"svtypes.hpp\"\n\n" + Holder.to_cpp_obj(),
            encoding="utf-8",
        )
        (root / "main.cpp").write_text(
            """
#include <vector>
#include "model.hpp"
int main() {
  Holder<svtypes::BitValue<8, false>> source;
  source.data = 0xa5;
  std::vector<uint8_t> bytes; source.pack(bytes);
  Holder<svtypes::BitValue<8, false>> decoded;
  size_t offset = 0; decoded.unpack(bytes, offset);
  return offset == bytes.size() && decoded.data == 0xa5 ? 0 : 2;
}
""",
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                "g++", "-std=c++20", "main.cpp", "-I.",
                f"-I{Path.cwd() / 'svtypes_runtime' / 'cpp'}", "-o", "type_parameter_test",
            ],
            cwd=root,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        result = subprocess.run(["./type_parameter_test"], cwd=root, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def test_parameter_dependent_packed_layout_materializes_on_specialization() -> None:
    from svtypes import SvObject

    class Word(SvObject):
        width = Parameter[Int]()
        data = Bit[width](rand=True)

    assert "bit [(width) - 1:0] data" in Word.to_sv_obj()
    assert "svtypes::BitValue<width, false> data" in Word.to_cpp_obj()
    with pytest.raises(DeclarationError, match="parameterized template"):
        schema_descriptor(Word)

    ByteWord = Word.specialize(width=8)
    NineBitWord = Word.specialize(width=9)
    byte_value = ByteWord()
    nine_value = NineBitWord()
    assert byte_value.data.width == 8
    assert nine_value.data.width == 9
    assert schema_descriptor(ByteWord).encoding_fingerprint != schema_descriptor(NineBitWord).encoding_fingerprint

    class DoubledWord(SvObject):
        width = Parameter[Int]()
        data = Bit[lambda width: width * 2]()

    assert "(width * 2)" in DoubledWord.to_sv_obj()
    assert DoubledWord.specialize(width=6)().data.width == 12

    class Buffer(SvObject):
        width = Parameter[Int]()
        depth = Parameter[Int]()
        data = Array[Bit[width], depth](rand=True)

    assert "data [(depth)]" in Buffer.to_sv_obj()
    materialized = Buffer.specialize(width=5, depth=3)()
    assert materialized.data.value == [0, 0, 0]
    assert materialized.data._elem_template.width == 5

    class DerivedBuffer(SvObject):
        width = Parameter[Int]()
        depth = Parameter[Int](lambda width: width * 2)
        data = Array[Bit[width], depth]()

    derived = DerivedBuffer.specialize(width=4)()
    assert derived.data.value == [0] * 8
    assert derived.data._elem_template.width == 4

    class MatrixBuffer(SvObject):
        width = Parameter[Int]()
        depth = Parameter[Int]()
        data = Array[Bit[width], (2, depth)]()

    assert "data [(2)] [(depth)]" in MatrixBuffer.to_sv_obj()
    assert "std::array<std::array<svtypes::BitValue<width, false>, depth>, 2> data" in MatrixBuffer.to_cpp_obj()
    matrix = MatrixBuffer.specialize(width=3, depth=4)()
    assert matrix.data.value == [[0] * 4, [0] * 4]
    assert matrix.data._elem_template._elem_template.width == 3

    class VariableCollections(SvObject):
        width = Parameter[Int]()
        dyn = DynArray[Bit[width]]()
        queue = Queue[Bit[width]]()
        lookup = AssocArray[Bit[width], Logic[width]]()

    assert "dyn []" in VariableCollections.to_sv_obj()
    collections = VariableCollections.specialize(width=7)()
    assert collections.dyn._elem_template.width == 7
    assert collections.queue._elem_template.width == 7
    assert collections.lookup._key_template.width == 7
    assert collections.lookup._val_template.width == 7


def test_enum_bracket_base_selects_existing_storage_contract() -> None:
    class ByteState(Enum[Bit[8]]):
        IDLE = 0
        LAST = 255

    class SignedState(Enum[BitSigned[8]]):
        NEGATIVE = -1
        POSITIVE = 1

    assert ByteState().width == 8
    assert ByteState().signed is False
    assert SignedState().signed is True

    class ExplicitInt(Enum[Int]):
        IDLE = 0

    class ImplicitInt(Enum):
        IDLE = 0

    assert "typedef enum int" in ExplicitInt.to_sv_enum()
    assert "typedef enum {" in ImplicitInt.to_sv_enum()

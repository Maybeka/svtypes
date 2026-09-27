"""Frozen migration oracle for the bracket-style descriptor API.

The expected values were recorded from the public pre-migration constructors.
Keeping the baseline as data, rather than calling the removed API alongside
the new one, lets the strict migration prove observable compatibility without
retaining an executable compatibility entrance.
"""

from svtypes import (
    Array,
    AssocArray,
    Bit,
    BitSigned,
    DynArray,
    Int,
    Logic,
    LogicSigned,
    Object,
    Parameter,
    Queue,
    Reg,
    RegSigned,
    RemoteRef,
)


def _descriptor_contract(descriptor):
    return {
        "sv": descriptor.sv_decl("value"),
        "cpp": descriptor.cpp_decl("value"),
        "pack": descriptor.to_bytes() if hasattr(descriptor, "to_bytes") else None,
        "rand": getattr(descriptor, "rand", None),
        "plusarg": getattr(descriptor, "plusarg", None),
        "dump": getattr(descriptor, "dump", None),
        "cov": getattr(descriptor, "cov", None),
    }


PACKED_BASELINE = {
    "bit": {"sv": "bit [7:0] value", "cpp": "uint8_t value", "pack": b"\xa5", "rand": True, "plusarg": True, "dump": True, "cov": True},
    "bit_signed": {"sv": "bit signed [4:0] value", "cpp": "svtypes::BitValue<5, true> value", "pack": b"\x1d", "rand": True, "plusarg": True, "dump": True, "cov": True},
    "logic": {"sv": "logic [3:0] value", "cpp": "svtypes::LogicValue<4, false> value", "pack": b"\x08\x04\x01", "rand": True, "plusarg": True, "dump": True, "cov": True},
    "logic_signed": {"sv": "logic signed [4:0] value", "cpp": "svtypes::LogicValue<5, true> value", "pack": b"\x11\x08\x02", "rand": True, "plusarg": True, "dump": True, "cov": True},
    "reg": {"sv": "reg [3:0] value", "cpp": "svtypes::LogicValue<4, false> value", "pack": b"\x08\x04\x01", "rand": True, "plusarg": True, "dump": True, "cov": True},
    "reg_signed": {"sv": "reg signed [4:0] value", "cpp": "svtypes::LogicValue<5, true> value", "pack": b"\x11\x08\x02", "rand": True, "plusarg": True, "dump": True, "cov": True},
}


def test_packed_bracket_forms_match_the_frozen_pre_migration_baseline() -> None:
    values = {
        "bit": Bit[8](0xA5),
        "bit_signed": BitSigned[5](-3),
        "logic": Logic[4]("1x0z"),
        "logic_signed": LogicSigned[5]("1x0z1"),
        "reg": Reg[4]("1x0z"),
        "reg_signed": RegSigned[5]("1x0z1"),
    }
    for name, descriptor in values.items():
        assert _descriptor_contract(descriptor) == PACKED_BASELINE[name]


COLLECTION_BASELINE = {
    "array": {"sv": "bit [7:0] value [2]", "cpp": "std::array<uint8_t, 2> value", "pack": b"\0\0", "rand": True, "plusarg": False, "dump": True, "cov": False},
    "dyn": {"sv": "bit [7:0] value []", "cpp": "std::vector<uint8_t> value", "pack": b"\0\0\0\0", "rand": True, "plusarg": False, "dump": True, "cov": True},
    "queue": {"sv": "bit [7:0] value [$]", "cpp": "std::vector<uint8_t> value", "pack": b"\0\0\0\0", "rand": True, "plusarg": False, "dump": True, "cov": True},
    "assoc": {"sv": "logic [3:0] value [bit [7:0]]", "cpp": "std::map<uint8_t, svtypes::LogicValue<4, false>> value", "pack": b"\0\0\0\0", "rand": False, "plusarg": False, "dump": True, "cov": True},
    "object": {"sv": "rand Child value", "cpp": "Child* value = nullptr", "pack": None, "rand": True, "plusarg": None, "dump": None, "cov": None},
    "remote": {"sv": "svtypes_pkg::remote_ref value", "cpp": "svtypes::RemoteRefValue value{\"domain.Child\", 0}", "pack": b"\x07\0\0\0\0\0\0\0", "rand": False, "plusarg": False, "dump": True, "cov": False},
}


def test_collection_handle_remote_and_parameter_forms_match_the_frozen_baseline() -> None:
    values = {
        "array": Array[Bit[8], 2](),
        "dyn": DynArray[Bit[8]](),
        "queue": Queue[Bit[8]](),
        "assoc": AssocArray[Bit[8], Logic[4]](),
        "object": Object["Child"](rand=True),
        "remote": RemoteRef["domain.Child"](7),
    }
    for name, descriptor in values.items():
        assert _descriptor_contract(descriptor) == COLLECTION_BASELINE[name]

    assert Parameter[Int](9).sv_decl("W") == "parameter int W"
    assert Parameter[Int](9).to_sv_code(name="W") == "parameter int W = 32'd9;"

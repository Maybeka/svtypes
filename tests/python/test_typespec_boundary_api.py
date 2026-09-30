from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import get_type_hints

import pytest

from svtypes import (
    Array,
    AssocArray,
    Bit,
    Int,
    Logic,
    Queue,
    Object,
    Parameter,
    Reg,
    RemoteRef,
    SvObject,
    Signed,
    UnsupportedTypeError,
    is_materializable_type,
    is_type_spec,
    materialize_type_spec,
    type_spec_from_identity,
    type_spec_identity,
)
from svtypes.schema import schema_descriptor


class IdentityTemplate(SvObject):
    width = Parameter[Int]()
    element = Parameter[type](Bit[4])
    data = Bit[width]()


def test_public_boundary_api_has_a_narrow_and_stable_surface() -> None:
    specification = Array[Bit[8], 16]

    assert is_type_spec(specification)
    assert not is_type_spec(Int)
    assert is_materializable_type(specification)
    assert is_materializable_type(Int)
    assert not is_materializable_type(Bit)
    assert not is_materializable_type(list)

    identity = type_spec_identity(specification, location="transfer.payload")
    assert identity == {
        "kind": "collection",
        "name": "Array",
        "args": [
            {
                "kind": "specialization",
                "base": {"kind": "packed", "name": "Bit"},
                "args": [8, {"kind": "signedness", "value": "unsigned"}],
            },
            16,
        ],
    }
    # It is intentionally ordinary JSON, with no function repr or cache key.
    assert json.loads(json.dumps(identity)) == identity


def test_bracket_specs_are_retained_in_runtime_method_annotations() -> None:
    def transfer(opcode: Bit[8], peer: RemoteRef["sv://tb/Driver"]) -> Int:
        raise AssertionError("annotation-only fixture")

    annotations = get_type_hints(transfer)
    assert annotations["opcode"] is Bit[8]
    assert annotations["peer"] is RemoteRef["sv://tb/Driver"]
    assert annotations["return"] is Int


def test_public_boundary_api_materializes_existing_codecs() -> None:
    assert materialize_type_spec(Bit[8], location="argument opcode").width == 8
    assert materialize_type_spec(Int, location="return").width == 32
    assert materialize_type_spec(Object["Packet"], location="argument packet").cls_name == "Packet"
    reference = materialize_type_spec(RemoteRef["sv://tb/Driver"], location="argument peer")
    assert reference.target_type_name == "sv://tb/Driver"


def test_remote_ref_specs_are_cached_and_have_canonical_identity() -> None:
    assert RemoteRef["sv://tb/Driver"] is RemoteRef["sv://tb/Driver"]
    assert type_spec_identity(RemoteRef["sv://tb/Driver"]) == {
        "kind": "remote_ref",
        "target": "sv://tb/Driver",
    }


@pytest.mark.parametrize(
    "annotation",
    (
        Int,
        Bit[8],
        Bit[(2, 4)],
        Logic[(2, 4), Signed],
        Reg[(2, 4)],
        Array[Bit[8], 16],
        Array[Bit[(2, 4)], (2, 3)],
        Queue[Array[Reg[(2, 4)], (2, 3)]],
        AssocArray[Int, Bit[(2, 4)]],
        RemoteRef["sv://tb/Driver"],
    ),
)
def test_public_boundary_identity_round_trips_without_a_factory(annotation) -> None:
    identity = json.loads(json.dumps(type_spec_identity(annotation)))
    rebuilt = type_spec_from_identity(identity, location="manifest parameter")
    assert type_spec_identity(rebuilt) == identity
    assert type(materialize_type_spec(rebuilt, location="manifest parameter")) is type(
        materialize_type_spec(annotation, location="source parameter")
    )


def test_specialized_class_identity_reconstructs_from_template() -> None:
    concrete = IdentityTemplate.specialize(width=8)
    identity = json.loads(json.dumps(type_spec_identity(concrete), sort_keys=True))
    rebuilt = type_spec_from_identity(identity, location="manifest parameter")
    assert type_spec_identity(rebuilt) == identity
    assert rebuilt().data.width == 8
    # Object streams carry per-instance IDs; compare their encoding contract,
    # not the unrelated runtime identities assigned to two fresh instances.
    assert schema_descriptor(rebuilt).encoding_fingerprint == schema_descriptor(concrete).encoding_fingerprint


def test_specialized_class_identity_needs_no_generated_class_or_cache() -> None:
    concrete = IdentityTemplate.specialize(width=8, element=Array[Logic[(2, 4)], (2, 3)])
    identity = type_spec_identity(concrete)
    # Rebuild in a fresh interpreter, importing only the source template. JSON
    # key ordering must not make generated class names part of the contract.
    script = f"""
import json, sys
sys.path.insert(0, {str(Path(__file__).parent)!r})
from svtypes import type_spec_from_identity, type_spec_identity
identity = json.load(sys.stdin)
rebuilt = type_spec_from_identity(identity, location="manifest parameter")
assert type_spec_identity(rebuilt) == identity
assert rebuilt().data.width == 8
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        input=json.dumps(identity, sort_keys=True),
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_identity_preserves_reg_declaration_style() -> None:
    assert type_spec_identity(Reg[8]) != type_spec_identity(Logic[8])
    assert type_spec_identity(Reg[8])["declaration_style"] == "reg"


def test_public_boundary_api_rejects_non_boundary_and_symbolic_inputs() -> None:
    with pytest.raises(UnsupportedTypeError, match="argument data: type is not an approved"):
        materialize_type_spec(list, location="argument data")

    class Template(SvObject):
        width = Parameter[Int]()
        data = Bit[width]()

    with pytest.raises(UnsupportedTypeError, match="unbound parameterized type"):
        type_spec_identity(Template, location="argument data")

    Concrete = Template.specialize(width=8)
    assert is_materializable_type(Concrete)
    assert materialize_type_spec(Concrete, location="argument data").data.width == 8

"""Stable public schema and encoding descriptor helpers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType
from typing import Any, Mapping

from .base import TypeBase
from .errors import CompatibilityError, DeclarationError


SCHEMA_FORMAT_VERSION = 1
BINARY_FORMAT_VERSION = 1
OBJECT_ENVELOPE_VERSION = 2
GENERATOR_RUNTIME_ABI_VERSION = 1


def _stable_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _fingerprint(value: Mapping[str, Any]) -> bytes:
    return hashlib.sha256(_stable_json(value)).digest()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class EncodingDescriptor:
    unified_type_name: str
    encoding_fingerprint: bytes
    binary_format_version: int = BINARY_FORMAT_VERSION

    def __post_init__(self) -> None:
        if not self.unified_type_name:
            raise DeclarationError("unified_type_name cannot be empty")
        if len(self.encoding_fingerprint) != 32:
            raise DeclarationError("encoding_fingerprint must contain exactly 32 bytes")
        if not 0 <= self.binary_format_version <= 0xFFFF:
            raise DeclarationError("binary_format_version must fit in 16 bits")

    @property
    def encoding_fingerprint_hex(self) -> str:
        return self.encoding_fingerprint.hex()

    def to_dict(self) -> dict[str, Any]:
        return {
            "binary_format_version": self.binary_format_version,
            "unified_type_name": self.unified_type_name,
            "encoding_fingerprint": self.encoding_fingerprint_hex,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EncodingDescriptor":
        try:
            fingerprint = bytes.fromhex(str(value["encoding_fingerprint"]))
            return cls(
                unified_type_name=str(value["unified_type_name"]),
                encoding_fingerprint=fingerprint,
                binary_format_version=int(value["binary_format_version"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DeclarationError(f"invalid encoding descriptor: {exc}") from exc


@dataclass(frozen=True, slots=True)
class SchemaDescriptor:
    unified_type_name: str
    schema: Mapping[str, Any]
    encoding_schema: Mapping[str, Any]
    schema_fingerprint: bytes
    encoding_fingerprint: bytes

    @property
    def schema_fingerprint_hex(self) -> str:
        return self.schema_fingerprint.hex()

    @property
    def encoding_fingerprint_hex(self) -> str:
        return self.encoding_fingerprint.hex()

    @property
    def encoding_descriptor(self) -> EncodingDescriptor:
        return EncodingDescriptor(self.unified_type_name, self.encoding_fingerprint)

    def to_dict(self) -> dict[str, Any]:
        return {
            "unified_type_name": self.unified_type_name,
            "schema": _thaw(self.schema),
            "schema_fingerprint": self.schema_fingerprint_hex,
            "encoding_fingerprint": self.encoding_fingerprint_hex,
            "encoding_schema": _thaw(self.encoding_schema),
        }


def _render_param_text(name: str, value: Any) -> str:
    from .parameter import ParamRef

    if isinstance(value, ParamRef):
        return f"{name}:ref={value.name or name}"
    if isinstance(value, type):
        return f"{name}:type={value.__name__}"
    return f"{name}:{type(value).__name__}={value}"


def unified_type_name(codec_or_type: Any) -> str:
    """Return the stable symbolic identity used by public descriptors."""
    from .bit import Bit
    from .collection import AssocArray, DynArray, Queue, Array
    from .enum import Enum
    from .int import Int, LongInt
    from .logic import Logic
    from .object import ObjectDescriptor, SvObject
    from .real import Real, RealTime, ShortReal
    from .remote_ref import RemoteRef
    from .string import String

    if isinstance(codec_or_type, type):
        if issubclass(codec_or_type, (SvObject, Enum)):
            specialized_from = getattr(codec_or_type, "_SvObject__svtypes_specialized_from", None)
            if specialized_from is not None:
                base_name = unified_type_name(specialized_from).split("[", 1)[0]
            else:
                explicit = codec_or_type.__dict__.get("_svtypes_unified_type_name")
                base_name = explicit or f"{codec_or_type.__module__}.{codec_or_type.__qualname__}"
            overrides = getattr(codec_or_type, "_SvObject__svtypes_parameter_overrides", {})
            parameters = getattr(codec_or_type, "_SvObject__svtypes_params", [])
            if parameters:
                effective = {
                    name: overrides.get(name, parameter.value)
                    for name, parameter in parameters
                }
                rendered = ",".join(
                    _render_param_text(name, value)
                    for name, value in effective.items()
                )
                return f"{base_name}[{rendered}]"
            return base_name
        raise DeclarationError(f"unsupported type descriptor: {codec_or_type!r}")

    codec = codec_or_type
    if isinstance(codec, Int):
        return "svtypes.Int"
    if isinstance(codec, LongInt):
        return "svtypes.LongInt"
    if isinstance(codec, Bit):
        size = (
            "shape=(" + ",".join(str(part) for part in codec.shape) + ")"
            if codec.shape is not None
            else f"width={codec.width}"
        )
        signed = str(codec.signed).lower()
        return f"svtypes.Bit[{size},signed={signed},state={codec.state_domain}]"
    if isinstance(codec, Logic):
        size = (
            "shape=(" + ",".join(str(part) for part in codec.shape) + ")"
            if codec.shape is not None
            else f"width={codec.width}"
        )
        signed = str(codec.signed).lower()
        return f"svtypes.Logic[{size},signed={signed},state=4state]"
    if isinstance(codec, String):
        return "svtypes.String[encoding=utf-8]"
    if isinstance(codec, ShortReal):
        return "svtypes.ShortReal[ieee754=binary32]"
    if isinstance(codec, RealTime):
        return "svtypes.Real[ieee754=binary64]"
    if isinstance(codec, Real):
        return "svtypes.Real[ieee754=binary64]"
    if isinstance(codec, RemoteRef):
        return f"svtypes.RemoteRef[target={codec.target_type_name}]"
    if isinstance(codec, Enum):
        return unified_type_name(codec.__class__)
    if isinstance(codec, Array):
        return f"svtypes.Array[size={codec._size},elem={unified_type_name(codec._elem_template)}]"
    if isinstance(codec, Queue):
        return f"svtypes.Queue[elem={unified_type_name(codec._elem_template)}]"
    if isinstance(codec, DynArray):
        return f"svtypes.DynArray[elem={unified_type_name(codec._elem_template)}]"
    if isinstance(codec, AssocArray):
        return (
            "svtypes.AssocArray["
            f"key={unified_type_name(codec._key_template)},"
            f"value={unified_type_name(codec._val_template)}]"
        )
    if isinstance(codec, ObjectDescriptor):
        target = codec.registry.get(codec.cls_name)
        if target is None:
            namespace = getattr(codec.registry, "path", "$unit").replace("::", ".")
            return f"{namespace}.{codec.cls_name}"
        return unified_type_name(target)
    if isinstance(codec, SvObject):
        return unified_type_name(codec.__class__)
    raise DeclarationError(f"unsupported codec descriptor: {codec!r}")


def _policies(codec: TypeBase) -> dict[str, bool]:
    return {
        "cov": bool(codec.cov),
        "dump": bool(codec.dump),
        "pack_bytes": bool(codec.pack_bytes),
        "plusarg": bool(codec.plusarg),
        "rand": bool(codec.rand),
    }


def _descriptors(
    codec_or_type: Any,
    active: set[type[Any]],
    allow_template: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from .bit import Bit
    from .collection import AssocArray, DynArray, Queue, Array
    from .enum import Enum
    from .object import ObjectDescriptor, SvObject, SvStruct
    from .logic import Logic
    from .parameter import Parameter
    from .real import Real, ShortReal
    from .remote_ref import RemoteRef
    from .string import String

    codec = codec_or_type
    if isinstance(codec, type):
        if issubclass(codec, Enum):
            codec = codec()
        elif issubclass(codec, SvObject):
            if getattr(codec, "_SvObject__svtypes_is_template", False):
                if not allow_template:
                    raise DeclarationError(
                        f"{codec.__name__} is a parameterized template; bind every Parameter with specialize() first"
                    )
                # A template has no concrete source schema, but its positional
                # encoding layout is parameter-independent and identical to every
                # specialization, so it can still yield an encoding fingerprint.
                declared_fields = []
                encoding_fields = []
                for name, member in codec._SvObject__svtypes_members:
                    member_schema, member_encoding = _descriptors(member, active)
                    entry = {
                        "name": name,
                        "policies": _policies(member) if isinstance(member, TypeBase) else {},
                        "type": member_schema,
                    }
                    declared_fields.append(entry)
                    if not isinstance(member, TypeBase) or member.pack_bytes:
                        encoding_fields.append({"type": member_encoding})
                encoding = {
                    "kind": "object",
                    "binary_format_version": BINARY_FORMAT_VERSION,
                    "object_envelope_version": OBJECT_ENVELOPE_VERSION,
                    "fields": encoding_fields,
                }
                return {}, encoding
            if codec in active:
                ref = {"kind": "type_ref", "type_name": unified_type_name(codec)}
                return ref, ref
            active.add(codec)
            try:
                declared_fields = []
                encoding_fields = []
                for name, member in codec._SvObject__svtypes_members:
                    member_schema, member_encoding = _descriptors(member, active)
                    entry = {
                        "name": name,
                        "policies": _policies(member) if isinstance(member, TypeBase) else {},
                        "type": member_schema,
                    }
                    declared_fields.append(entry)
                    if not isinstance(member, TypeBase) or member.pack_bytes:
                        # Field names are part of the normalized source schema,
                        # not the positional object encoding layout.
                        encoding_fields.append({"type": member_encoding})
                params = []
                overrides = getattr(codec, "_SvObject__svtypes_parameter_overrides", {})
                for name, parameter in getattr(codec, "_SvObject__svtypes_params", []):
                    if isinstance(parameter, Parameter):
                        effective = overrides.get(name, parameter)
                        if isinstance(effective, Parameter):
                            is_type = effective.is_type_parameter
                            value = (
                                effective.value.__name__
                                if is_type and effective.is_bound
                                else (None if is_type else effective.value)
                            )
                        else:
                            is_type = isinstance(effective, type)
                            value = effective.__name__ if is_type else effective
                        entry = {"name": name, "value": value}
                        if is_type:
                            entry["kind"] = "type"
                        params.append(entry)
                kind = "struct" if issubclass(codec, SvStruct) else "object"
                common = {
                    "kind": kind,
                    "type_name": unified_type_name(codec),
                }
                schema = {
                    **common,
                    "base_type_name": (
                        unified_type_name(codec.__bases__[0])
                        if codec.__bases__ and issubclass(codec.__bases__[0], SvObject)
                        and codec.__bases__[0] not in (SvObject, SvStruct)
                        else None
                    ),
                    "fields": declared_fields,
                    "parameters": params,
                }
                if kind == "object":
                    from .constraint.ir import constraints_schema_entries
                    from .constraint.layer import layers_schema_entries

                    entries = constraints_schema_entries(
                        getattr(codec, "_SvObject__svtypes_constraint_irs", {})
                    )
                    if entries:
                        schema["constraints"] = entries
                    schema["rand_layers"] = layers_schema_entries(codec)
                encoding = {
                    "kind": kind,
                    "binary_format_version": BINARY_FORMAT_VERSION,
                    "object_envelope_version": OBJECT_ENVELOPE_VERSION if kind == "object" else None,
                    "fields": encoding_fields,
                }
                return schema, encoding
            finally:
                active.remove(codec)
        else:
            raise DeclarationError(f"unsupported schema type: {codec!r}")

    if isinstance(codec, ObjectDescriptor):
        ref = {"kind": "object_ref", "type_name": unified_type_name(codec)}
        return {**ref, "strict_set": codec.strict_set}, ref
    if isinstance(codec, SvObject):
        return _descriptors(codec.__class__, active)
    if isinstance(codec, Logic):
        common = {
            "kind": "bits",
            "planes": ["value", "x", "z"],
            "shape": list(codec.shape) if codec.shape is not None else None,
            "signed": codec.signed,
            "state_domain": "4state",
            "type_name": unified_type_name(codec),
            "width": codec.width,
        }
        schema = {
            **common,
            "policies": _policies(codec),
            "sv_declaration_style": codec.sv_declaration_style,
        }
        encoding = {**common, "binary_format_version": BINARY_FORMAT_VERSION}
        return schema, encoding
    elif isinstance(codec, Bit):
        common = {
            "kind": "bits",
            "shape": list(codec.shape) if codec.shape is not None else None,
            "signed": codec.signed,
            "state_domain": codec.state_domain,
            "type_name": unified_type_name(codec),
            "width": codec.width,
        }
    elif isinstance(codec, String):
        common = {"encoding": "utf-8", "kind": "string", "type_name": unified_type_name(codec)}
    elif isinstance(codec, ShortReal):
        common = {"format": "ieee754-binary32", "kind": "real", "type_name": unified_type_name(codec)}
    elif isinstance(codec, Real):
        common = {"format": "ieee754-binary64", "kind": "real", "type_name": unified_type_name(codec)}
    elif isinstance(codec, RemoteRef):
        common = {
            "kind": "remote_ref",
            "nullable": True,
            "target_type_name": codec.target_type_name,
            "type_name": unified_type_name(codec),
            "width": 64,
        }
    elif isinstance(codec, Enum):
        common = {
            "items": [[name, int(value)] for name, value in codec.__class__._enum_map.items()],
            "kind": "enum",
            "type_name": unified_type_name(codec),
            "signed": codec.signed,
            "state_domain": codec.state_domain,
            "width": codec.width,
        }
    elif isinstance(codec, Array):
        elem_schema, elem_encoding = _descriptors(codec._elem_template, active)
        common = {"kind": "array", "size": codec._size, "type_name": unified_type_name(codec)}
        schema = {**common, "element": elem_schema, "policies": _policies(codec)}
        encoding = {**common, "binary_format_version": BINARY_FORMAT_VERSION, "element": elem_encoding}
        return schema, encoding
    elif isinstance(codec, Queue):
        elem_schema, elem_encoding = _descriptors(codec._elem_template, active)
        common = {"kind": "queue", "type_name": unified_type_name(codec)}
        schema = {
            **common,
            "decoder_max_length": codec._max_length,
            "element": elem_schema,
            "policies": _policies(codec),
        }
        encoding = {**common, "binary_format_version": BINARY_FORMAT_VERSION, "element": elem_encoding}
        return schema, encoding
    elif isinstance(codec, DynArray):
        elem_schema, elem_encoding = _descriptors(codec._elem_template, active)
        common = {"kind": "dyn_array", "type_name": unified_type_name(codec)}
        schema = {
            **common,
            "decoder_max_length": codec._max_length,
            "element": elem_schema,
            "policies": _policies(codec),
        }
        encoding = {**common, "binary_format_version": BINARY_FORMAT_VERSION, "element": elem_encoding}
        return schema, encoding
    elif isinstance(codec, AssocArray):
        key_schema, key_encoding = _descriptors(codec._key_template, active)
        value_schema, value_encoding = _descriptors(codec._val_template, active)
        common = {"kind": "assoc_array", "ordering": "encoded-key-bytes", "type_name": unified_type_name(codec)}
        schema = {
            **common,
            "decoder_max_length": codec._max_length,
            "key": key_schema,
            "policies": _policies(codec),
            "value": value_schema,
        }
        encoding = {
            **common,
            "binary_format_version": BINARY_FORMAT_VERSION,
            "key": key_encoding,
            "value": value_encoding,
        }
        return schema, encoding
    else:
        raise DeclarationError(f"unsupported schema codec: {codec!r}")

    schema = {**common, "policies": _policies(codec)}
    if isinstance(codec, String):
        schema["decoder_max_bytes"] = codec._max_bytes
    encoding = {**common, "binary_format_version": BINARY_FORMAT_VERSION}
    return schema, encoding


def schema_descriptor(codec_or_type: Any) -> SchemaDescriptor:
    schema, encoding = _descriptors(codec_or_type, set())
    type_name = unified_type_name(codec_or_type)
    return SchemaDescriptor(
        unified_type_name=type_name,
        schema=_freeze(schema),
        encoding_schema=_freeze(encoding),
        schema_fingerprint=_fingerprint(schema),
        encoding_fingerprint=_fingerprint(encoding),
    )


def encoding_descriptor(codec_or_type: Any) -> EncodingDescriptor:
    return schema_descriptor(codec_or_type).encoding_descriptor


def checked_unpack(
    codec: TypeBase,
    data: bytes,
    received_descriptor: EncodingDescriptor | Mapping[str, Any],
) -> tuple[Any, int]:
    """Verify public encoding identity before invoking a bare value codec."""
    received = (
        received_descriptor
        if isinstance(received_descriptor, EncodingDescriptor)
        else EncodingDescriptor.from_dict(received_descriptor)
    )
    expected = encoding_descriptor(codec)
    if received.binary_format_version != expected.binary_format_version:
        raise CompatibilityError(
            "SvTypes binary format mismatch: "
            f"expected {expected.binary_format_version}, got {received.binary_format_version}"
        )
    if received.unified_type_name != expected.unified_type_name:
        raise CompatibilityError(
            "SvTypes unified type mismatch: "
            f"expected {expected.unified_type_name}, got {received.unified_type_name}"
        )
    if received.encoding_fingerprint != expected.encoding_fingerprint:
        raise CompatibilityError(
            "SvTypes encoding fingerprint mismatch for " + expected.unified_type_name
        )
    return codec.unpack(data)

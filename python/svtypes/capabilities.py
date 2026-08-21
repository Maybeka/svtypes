"""Runtime capability declarations and compatibility checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .errors import CompatibilityError, DeclarationError
from .schema import (
    BINARY_FORMAT_VERSION,
    GENERATOR_RUNTIME_ABI_VERSION,
    OBJECT_ENVELOPE_VERSION,
    SCHEMA_FORMAT_VERSION,
)


STABLE_CAPABILITIES = (
    "svtypes.checked-encoding-descriptor.v1",
    "svtypes.codec-context.v1",
    "svtypes.constraint-ir.v1",
    "svtypes.constraint-sample.v1",
    "svtypes.record-schema.v1",
    "svtypes.remote-reference.v1",
)


def _normalized_capabilities(values: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(sorted(set(values)))
    if any(not isinstance(value, str) or not value for value in normalized):
        raise DeclarationError("capability names must be nonempty strings")
    return normalized


@dataclass(frozen=True, slots=True)
class RuntimeCapabilities:
    """Machine-readable versions and deterministically ordered capabilities."""

    package_major_version: int = 1
    schema_format_version: int = SCHEMA_FORMAT_VERSION
    binary_format_version: int = BINARY_FORMAT_VERSION
    object_envelope_version: int = OBJECT_ENVELOPE_VERSION
    generator_runtime_abi_version: int = GENERATOR_RUNTIME_ABI_VERSION
    provided: tuple[str, ...] = STABLE_CAPABILITIES

    def __post_init__(self) -> None:
        if any(value < 0 for value in (
            self.package_major_version,
            self.schema_format_version,
            self.binary_format_version,
            self.object_envelope_version,
            self.generator_runtime_abi_version,
        )):
            raise DeclarationError("runtime version values must be nonnegative")
        object.__setattr__(self, "provided", _normalized_capabilities(self.provided))

    def to_dict(self) -> dict[str, object]:
        return {
            "binary_format_version": self.binary_format_version,
            "generator_runtime_abi_version": self.generator_runtime_abi_version,
            "object_envelope_version": self.object_envelope_version,
            "package_major_version": self.package_major_version,
            "provided": list(self.provided),
            "schema_format_version": self.schema_format_version,
        }


def runtime_capabilities() -> RuntimeCapabilities:
    return RuntimeCapabilities()


def require_runtime_compatible(
    required: Iterable[str],
    received: RuntimeCapabilities,
    expected: RuntimeCapabilities | None = None,
) -> None:
    """Reject incompatible runtime metadata before a value transfer.

    All current format and ABI fields require exact equality. Unknown provided
    capability names are intentionally ignored; every required name must be
    supplied by the received runtime.
    """
    expected = runtime_capabilities() if expected is None else expected
    for field in (
        "package_major_version",
        "schema_format_version",
        "binary_format_version",
        "object_envelope_version",
        "generator_runtime_abi_version",
    ):
        if getattr(expected, field) != getattr(received, field):
            raise CompatibilityError(
                f"SvTypes runtime compatibility failure: {field} "
                f"expected {getattr(expected, field)}, got {getattr(received, field)}"
            )
    missing = _normalized_capabilities(required)
    missing = tuple(value for value in missing if value not in received.provided)
    if missing:
        raise CompatibilityError(
            "SvTypes runtime compatibility failure: missing required capabilities "
            + ", ".join(missing)
        )

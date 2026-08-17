"""Public decoder resource limits."""

from dataclasses import dataclass

from .errors import DeclarationError


DEFAULT_MAX_DYNAMIC_LENGTH = 1_000_000
DEFAULT_MAX_OBJECT_COUNT = 100_000
DEFAULT_MAX_NESTING_DEPTH = 256
DEFAULT_MAX_INPUT_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_PACKED_BITS = 1_048_576


@dataclass(frozen=True, slots=True)
class DecodeLimits:
    max_dynamic_length: int = DEFAULT_MAX_DYNAMIC_LENGTH
    max_object_count: int = DEFAULT_MAX_OBJECT_COUNT
    max_nesting_depth: int = DEFAULT_MAX_NESTING_DEPTH
    max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES

    def __post_init__(self) -> None:
        for name, value in (
            ("max_dynamic_length", self.max_dynamic_length),
            ("max_object_count", self.max_object_count),
            ("max_nesting_depth", self.max_nesting_depth),
            ("max_input_bytes", self.max_input_bytes),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise DeclarationError(f"DecodeLimits.{name} must be a positive integer")

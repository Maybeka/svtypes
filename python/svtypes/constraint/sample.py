"""svtypes.constraint-sample.v1 bit stream."""

from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass

from ..errors import ConstraintError
from ..enum import Enum
from ..logic import Logic, LogicValue

UINT64 = (1 << 64) - 1


def uint64_le(value: int) -> bytes:
    return int(value & UINT64).to_bytes(8, "little", signed=False)


def effective_seed(seed: int, call_index: int) -> int:
    digest = hashlib.sha256(uint64_le(seed) + uint64_le(call_index)).digest()
    return int.from_bytes(digest[:8], "little", signed=False)


class BitStream:
    """Per-call SHA-256 byte stream. Leftover bytes are never reused."""

    def __init__(self, seed: int) -> None:
        self.seed = seed & UINT64
        self.i = 0

    def draw_bits(self, width: int) -> int:
        if width < 1:
            raise ConstraintError("cannot draw a non-positive number of bits")
        nbytes = (width + 7) // 8
        nblock = (nbytes + 31) // 32
        chunks = bytearray()
        for _ in range(nblock):
            chunks.extend(hashlib.sha256(uint64_le(self.seed) + uint64_le(self.i)).digest())
            self.i += 1
        raw = int.from_bytes(bytes(chunks[:nbytes]), "little", signed=False)
        return raw & ((1 << width) - 1)

    def draw_enum(self, members: list[int]) -> int:
        count = len(members)
        if count < 1:
            raise ConstraintError("enum must have at least one member")
        if count == 1:
            return members[0]
        limit = (2**32 // count) * count
        while True:
            value = self.draw_bits(32)
            if value < limit:
                return members[value % count]


def sample_unconstrained(desc: Any, stream: BitStream) -> Any:
    from ..bit import Bit

    if isinstance(desc, Enum):
        members = [int(item) for item in desc.__class__._enum_items]
        return desc._normalize(stream.draw_enum(members))
    bits = stream.draw_bits(desc.width)
    if isinstance(desc, Logic):
        return LogicValue(desc.width, bits, 0, 0)
    return desc._normalize(bits)


def validate_seed(seed: Any) -> int:
    if type(seed) is not int:
        raise TypeError("RandomContext seed must be an int, not bool or another type")
    if seed < 0 or seed >= 2**64:
        raise ValueError("RandomContext seed must satisfy 0 <= seed < 2**64")
    return seed


@dataclass(frozen=True, slots=True)
class RandomizeStatus:
    ok: bool
    reason: str
    state_path: str | None = None
    active_constraints: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LayeredRandomizeStatus:
    ok: bool
    reason: str
    failed_priority: int | None
    failed_aliases: tuple[str, ...]
    randomize_status: RandomizeStatus | None


class RandomContext:
    def __init__(self, seed: int | None = None, *, backend: str | None = None) -> None:
        if seed is not None:
            seed = validate_seed(seed)
        if backend not in (None, "smt"):
            raise ValueError(f"unsupported constraint backend: {backend!r}")
        self.seed = seed
        self.backend = backend or "smt"
        self.call_index = 0
        self._entropy = None if seed is not None else int.from_bytes(os.urandom(8), "little")

    def __enter__(self) -> "RandomContext":
        _push_context(self)
        return self

    def __exit__(self, *exc: object) -> None:
        _pop_context(self)

    def __copy__(self) -> "RandomContext":
        copied = RandomContext.__new__(RandomContext)
        copied.seed = self.seed
        copied.backend = self.backend
        copied.call_index = self.call_index
        copied._entropy = self._entropy
        return copied

    def __deepcopy__(self, memo: dict[int, Any]) -> "RandomContext":
        copied = self.__copy__()
        memo[id(self)] = copied
        return copied

    def consume_call(self) -> tuple[int, int]:
        index = self.call_index
        self.call_index += 1
        seed = self.seed if self.seed is not None else self._entropy
        return index, effective_seed(int(seed), index)

    def randomize(self, obj: Any) -> bool:
        from .randomize import randomize_object

        with self:
            return randomize_object(obj)

    def randomize_with(self, obj: Any, fn: Any) -> bool:
        from .randomize import randomize_object_with

        with self:
            return randomize_object_with(obj, fn)

    def bitstream(self) -> BitStream:
        _, seed = self.consume_call()
        return BitStream(seed)


_tls = threading.local()


def _stack() -> list[RandomContext]:
    stack = getattr(_tls, "stack", None)
    if stack is None:
        stack = []
        _tls.stack = stack
    return stack


def _push_context(ctx: RandomContext) -> None:
    _stack().append(ctx)


def _pop_context(ctx: RandomContext) -> None:
    stack = _stack()
    if not stack or stack[-1] is not ctx:
        raise RuntimeError("RandomContext stack is unbalanced")
    stack.pop()


def current_context() -> RandomContext:
    stack = _stack()
    if stack:
        return stack[-1]
    default = getattr(_tls, "default", None)
    if default is None:
        default = RandomContext()
        _tls.default = default
    return default

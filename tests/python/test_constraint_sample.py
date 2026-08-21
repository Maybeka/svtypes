from __future__ import annotations

import copy
import hashlib

import pytest

from svtypes import Bit, Enum, Logic, RandomContext, SvObject, constraint, schema_descriptor


class Three(Enum, width=8, signed=False):
    A = 1
    B = 2
    C = 3


def uint64_le(value: int) -> bytes:
    return int(value).to_bytes(8, "little", signed=False)


def effective_seed(seed: int, call_index: int) -> int:
    digest = hashlib.sha256(uint64_le(seed) + uint64_le(call_index)).digest()
    return int.from_bytes(digest[:8], "little", signed=False)


def draw_bits(seed: int, start_i: int, width: int) -> tuple[int, int]:
    nbytes = (width + 7) // 8
    nblock = (nbytes + 31) // 32
    chunks = bytearray()
    index = start_i
    for _ in range(nblock):
        chunks.extend(hashlib.sha256(uint64_le(seed) + uint64_le(index)).digest())
        index += 1
    raw = int.from_bytes(bytes(chunks[:nbytes]), "little", signed=False)
    return raw & ((1 << width) - 1), index


def test_unconstrained_sample_v1_vectors():
    class Wide(SvObject):
        wide = Bit(300)
        odd = Bit(7)

    ctx = RandomContext(seed=1)
    obj = Wide()
    with ctx:
        assert obj.randomize() is True
        first_wide = obj.wide.value
        first_odd = obj.odd.value
        assert obj.randomize() is True
        second_wide = obj.wide.value
    assert (first_wide, first_odd) != (second_wide, obj.odd.value)

    seed0 = effective_seed(1, 0)
    expected_wide, i = draw_bits(seed0, 0, 300)
    expected_odd, _ = draw_bits(seed0, i, 7)
    assert first_wide == expected_wide
    assert first_odd == expected_odd

    seed1 = effective_seed(1, 1)
    expected_wide2, _ = draw_bits(seed1, 0, 300)
    assert second_wide == expected_wide2


def test_enum_rejection_sampling_and_illegal_seeds():
    class Box(SvObject):
        color = Three()

    ctx = RandomContext(seed=2)
    obj = Box()
    with ctx:
        assert obj.randomize() is True
    members = [1, 2, 3]
    seed = effective_seed(2, 0)
    index = 0
    limit = (2**32 // 3) * 3
    chosen = None
    while True:
        value, index = draw_bits(seed, index, 32)
        if value < limit:
            chosen = members[value % 3]
            break
    assert int(obj.color.value) == chosen

    with pytest.raises(TypeError):
        RandomContext(seed=True)
    with pytest.raises(ValueError):
        RandomContext(seed=-1)
    with pytest.raises(ValueError):
        RandomContext(seed=2**64)
    with pytest.raises(ValueError, match="unsupported constraint backend"):
        RandomContext(backend="unknown")


def test_minimum_model_fallback_and_candidate_hit():
    class Exact(SvObject):
        addr = Bit(32)

        @constraint
        def legal(self):
            self.addr == 0x12345678

    obj = Exact()
    ctx = RandomContext(seed=99)
    with ctx:
        assert obj.randomize() is True
    assert obj.addr.value == 0x12345678

    class Tiny(SvObject):
        flag = Bit(1)

        @constraint
        def legal(self):
            self.flag == 1

    tiny = Tiny()
    with RandomContext(seed=5):
        assert tiny.randomize() is True
    assert tiny.flag.value == 1

    for seed in range(8):
        exact = Exact()
        with RandomContext(seed=seed):
            assert exact.randomize() is True
        assert exact.addr.value == 0x12345678


def test_rand_mode_zero_is_state_without_changing_ir_digest():
    class Pair(SvObject):
        a = Bit(8)
        b = Bit(8)

        @constraint
        def legal(self):
            self.a != self.b

    digest = schema_descriptor(Pair).schema["constraints"][0]["ir_digest"]
    obj = Pair()
    obj.b.value = 0x5A
    obj.b.rand_mode(0)
    ctx = RandomContext(seed=11)
    with ctx:
        assert obj.randomize() is True
    assert obj.b.value == 0x5A
    assert obj.a.value != 0x5A
    assert schema_descriptor(Pair).schema["constraints"][0]["ir_digest"] == digest

    unconstrained = Pair()
    unconstrained.legal.constraint_mode(0)
    unconstrained.b.value = 0xA5
    unconstrained.b.rand_mode(0)
    ctx = RandomContext(seed=11)
    with ctx:
        assert unconstrained.randomize() is True
    seed0 = effective_seed(11, 0)
    expected_a, _ = draw_bits(seed0, 0, 8)
    assert unconstrained.a.value == expected_a
    assert unconstrained.b.value == 0xA5
    assert schema_descriptor(Pair).schema["constraints"][0]["ir_digest"] == digest


def test_singleton_enum_consumes_no_rng():
    class Solo(Enum, width=8, signed=False):
        ONLY = 7

    class Box(SvObject):
        color = Solo()
        extra = Bit(8)

    obj = Box()
    ctx = RandomContext(seed=4)
    with ctx:
        assert obj.randomize() is True
    seed = effective_seed(4, 0)
    expected_extra, _ = draw_bits(seed, 0, 8)
    assert int(obj.color.value) == 7
    assert obj.extra.value == expected_extra


def test_random_context_copy_is_independent_and_seed_none_advances():
    class Wide(SvObject):
        wide = Bit(300)
        odd = Bit(7)

    ctx = RandomContext(seed=13)
    copied = copy.copy(ctx)
    first = Wide()
    second = Wide()
    assert ctx.randomize(first) is True
    assert copied.randomize(second) is True
    assert (first.wide.value, first.odd.value) == (second.wide.value, second.odd.value)
    assert ctx.call_index == 1
    assert copied.call_index == 1
    ctx.randomize(first)
    assert copied.call_index == 1
    assert ctx.call_index == 2

    entropy = RandomContext()
    obj = Wide()
    with entropy:
        assert obj.randomize() is True
        first_index = entropy.call_index
        assert first_index == 1
        assert obj.randomize() is True
        assert entropy.call_index == 2
    assert entropy.seed is None


def test_call_index_advances_on_unsat_and_state_xz():
    class Clash(SvObject):
        addr = Bit(8)

        @constraint
        def a(self):
            self.addr == 1

        @constraint
        def b(self):
            self.addr == 2

    ctx = RandomContext(seed=1)
    obj = Clash()
    with ctx:
        assert obj.randomize() is False
        assert ctx.call_index == 1
        assert obj.randomize() is False
        assert ctx.call_index == 2

    class StatePkt(SvObject):
        addr = Bit(8)
        status = Logic(8, rand=False)

        @constraint
        def legal(self):
            self.addr == self.status

    xz = StatePkt()
    from svtypes import LogicValue

    xz.status.value = LogicValue.from_string("10xx0001")
    with ctx:
        assert xz.randomize() is False
        assert ctx.call_index == 3
        assert xz.svtypes_randomize_status.reason == "state_xz"

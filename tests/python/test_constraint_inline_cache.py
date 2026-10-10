"""Inline IR caching follows live function identity and receiver type."""

import builtins
import gc
import importlib
from types import FunctionType
import weakref

import pytest

from svtypes import Bit, SvObject


runtime = importlib.import_module("svtypes.constraint.randomize")


class Narrow(SvObject):
    count = Bit[4](cov=False)


class Wide(SvObject):
    count = Bit[8](cov=False)


def first(item):
    item.count == 1


def second(item):
    item.count == 2


def impossible(item):
    item.count == 1
    item.count == 2


def fresh(template):
    return FunctionType(template.__code__, template.__globals__, template.__name__)


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch):
    monkeypatch.setattr(runtime, "_inline_cache", weakref.WeakKeyDictionary())


def test_discarded_function_id_reuse_cannot_select_old_constraints(monkeypatch):
    # Deterministically model valid address reuse, not allocator timing.
    monkeypatch.setattr(runtime, "id", lambda obj: 17 if isinstance(obj, FunctionType) else builtins.id(obj), raising=False)
    obj = Narrow()
    fn = fresh(first)
    ref = weakref.ref(fn)
    assert obj.randomize_with(fn)
    assert obj.count.value == 1
    del fn
    gc.collect()
    assert ref() is None
    assert len(runtime._inline_cache) == 0
    assert obj.randomize_with(fresh(second))
    assert obj.count.value == 2


def test_same_live_function_reuses_compilation(monkeypatch):
    parses, compiles = [], []
    parse, compile_ = runtime.parse_constraint_function, runtime.compile_block

    def counted_parse(fn):
        parses.append(fn)
        return parse(fn)

    def counted_compile(cls, decl):
        compiles.append(cls)
        return compile_(cls, decl)

    monkeypatch.setattr(runtime, "parse_constraint_function", counted_parse)
    monkeypatch.setattr(runtime, "compile_block", counted_compile)
    fn = fresh(first)
    for obj in (Narrow(), Narrow(), Narrow()):
        assert obj.randomize_with(fn)
        assert obj.count.value == 1
    assert len(parses) == len(compiles) == 1


def test_same_function_compiles_separately_for_receiver_types(monkeypatch):
    compiles = []
    compile_ = runtime.compile_block

    def counted_compile(cls, decl):
        compiles.append(cls)
        return compile_(cls, decl)

    monkeypatch.setattr(runtime, "compile_block", counted_compile)
    fn = fresh(second)
    for cls in (Narrow, Wide, Narrow, Wide):
        obj = cls()
        assert obj.randomize_with(fn)
        assert obj.count.value == 2
    assert compiles == [Narrow, Wide]
    assert runtime._inline_cache[fn][Narrow].vars[0].width == 4
    assert runtime._inline_cache[fn][Wide].vars[0].width == 8


def test_temporary_functions_and_attached_payloads_are_not_retained():
    class Payload:
        pass

    refs = []
    obj = Narrow()
    for _ in range(40):
        fn = fresh(first)
        payload = Payload()
        fn.payload = payload
        refs.append((weakref.ref(fn), weakref.ref(payload)))
        assert obj.randomize_with(fn)
        del fn, payload
    gc.collect()
    assert all(fn() is None and payload() is None for fn, payload in refs)
    assert len(runtime._inline_cache) == 0


def test_id_collision_cannot_convert_unsat_to_sat(monkeypatch):
    monkeypatch.setattr(runtime, "id", lambda obj: 17 if isinstance(obj, FunctionType) else builtins.id(obj), raising=False)
    obj = Narrow()
    assert obj.randomize_with(fresh(first))
    before = obj.count.value
    assert not obj.randomize_with(fresh(impossible))
    assert obj.count.value == before
    assert obj.randomize_with(fresh(second))
    assert obj.count.value == 2

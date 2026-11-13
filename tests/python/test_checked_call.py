import pytest

from svtypes import (
    Bit, Int, MemoryExternalFieldStorage, Queue, SvStruct, bind_external_value,
    borrow_element, read_value, write_value,
)
from svtypes.checked_call import CheckedAccessError, CopyoutError, Inout, Output, Ref, _invoke_checked


def test_inputs_and_nested_refs_use_the_current_formal():
    actual = Int(10)

    def change(target: Ref[Int]):
        target.value += 1

    def update(source: Int, destination: Inout[Int]):
        source.value = 50
        _invoke_checked(change, (destination,), {})
        assert actual.value == 10

    _invoke_checked(update, (actual, actual), {})
    assert actual.value == 11


def test_copyout_uses_signature_order_with_aliased_keyword_actuals():
    actual = Int(10)

    def update(first: Inout[Int], second: Inout[Int]):
        first.value += 1
        assert second.value == 10
        second.value += 2

    _invoke_checked(update, (), {"second": actual, "first": actual})
    assert actual.value == 12


def test_copyout_preparation_failure_does_not_publish_earlier_parameters():
    first = Int(1)
    storage = MemoryExternalFieldStorage()
    storage.seed("second", Int(), 2)
    second = bind_external_value(Int(), storage, "second", readonly=True)

    def update(one: Output[Int], two: Output[Int]):
        one.value = 100
        two.value = 200

    with pytest.raises(CopyoutError) as caught:
        _invoke_checked(update, (first, second), {})
    assert caught.value.phase == "preparation"
    assert caught.value.parameter == "two"
    assert caught.value.completed == ()
    assert first.value == 1
    assert read_value(second) == 2


def test_copyout_publication_failure_reports_completed_parameters():
    class FailingStorage(MemoryExternalFieldStorage):
        def write(self, key, descriptor, path, operation, payload):
            if key == "second":
                raise RuntimeError("storage refused second write")
            return super().write(key, descriptor, path, operation, payload)

    storage = FailingStorage()
    storage.seed("first", Int(), 1)
    storage.seed("second", Int(), 2)
    first = bind_external_value(Int(), storage, "first")
    second = bind_external_value(Int(), storage, "second")

    def update(one: Output[Int], two: Output[Int]):
        one.value = 100
        two.value = 200

    with pytest.raises(CopyoutError) as caught:
        _invoke_checked(update, (first, second), {})
    assert caught.value.phase == "publication"
    assert caught.value.parameter == "two"
    assert caught.value.completed == ("one",)
    assert read_value(first) == 100
    assert read_value(second) == 2


def test_unassigned_outputs_and_exception_copyout_behavior():
    destination, live = Int(7), Int(3)

    def never_assigned(output: Output[Int]):
        pass

    with pytest.raises(CopyoutError) as caught:
        _invoke_checked(never_assigned, (destination,), {})
    assert "has not been assigned" in str(caught.value.__cause__)

    def fail(output: Output[Int], reference: Ref[Int]):
        output.value = 8
        reference.value = 4
        raise ValueError("body failed")

    with pytest.raises(ValueError, match="body failed"):
        _invoke_checked(fail, (destination, live), {})
    assert destination.value == 7
    assert live.value == 4


def test_aggregate_copy_and_stable_element_ref():
    class Packet(SvStruct):
        words = Queue[Int]()

    packet = Packet()
    write_value(packet, {"words": [2, 3]})

    def update(formal: Inout[Packet]):
        value = formal.value
        value["words"].append(4)
        formal.value = value
        assert read_value(packet) == {"words": [2, 3]}

    _invoke_checked(update, (packet,), {})
    assert read_value(packet) == {"words": [2, 3, 4]}

    def advance(element: Ref[Int]):
        packet.words.pop_front()
        element.value += 10

    with borrow_element(packet.words, 1) as selected:
        _invoke_checked(advance, (selected,), {})
        assert selected.value == 13
    assert read_value(packet) == {"words": [13, 4]}


def test_incompatible_refs_ordinary_arguments_and_returned_formals_fail():
    def ref(actual: Ref[Bit[8]]):
        raise AssertionError("incompatible reference should fail before the call")

    with pytest.raises(CheckedAccessError, match="Ref declaration"):
        _invoke_checked(ref, (Int(),), {})

    def ordinary(value):
        return value

    with pytest.raises(CheckedAccessError, match="no variable carrier contract"):
        _invoke_checked(ordinary, ({"nested": [Int()]},), {})

    def leak(value: Int):
        return [value]

    with pytest.raises(CheckedAccessError, match="returned a variable carrier"):
        _invoke_checked(leak, (Int(),), {})


def test_call_result_rejects_carrier_captured_by_deferred_code():
    from functools import partial

    def closure(value: Int):
        return lambda: value.value

    def deferred(value: Int):
        return (value.value for _ in range(1))

    def apply(value: Int):
        return partial(str, value)

    for function in (closure, deferred, apply):
        with pytest.raises(CheckedAccessError, match="returned a variable carrier"):
            _invoke_checked(function, (Int(),), {})

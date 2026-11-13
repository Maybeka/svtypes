"""Ordinary Python syntax is not a Ref violation."""

from contextlib import contextmanager

import pytest

from svtypes import CheckedAccessError, Int, Queue, Ref, SvObject, run_checked


_module_key = lambda value: -value
_left, _right = (lambda value: value + 1), (lambda value: value + 2)
_multiline = (
    lambda value: (
        value + 3
    )
)


def test_module_lambdas_and_lambda_entries_use_the_correct_source():
    def main():
        return sorted([1, 3, 2], key=_module_key), _left(4), _right(4)

    assert run_checked(main) == ([3, 2, 1], 5, 6)
    assert run_checked(_module_key, 3) == -3
    assert run_checked(_multiline, 3) == 6


def test_value_assignments_unpacking_and_loops_keep_python_behavior():
    def main():
        calls = []

        def initial():
            calls.append("initial")
            return 3

        left = right = initial()
        first, *middle, last = range(5)
        result = [(index, value) for index, value in enumerate([10, 20])]
        for index, value in enumerate([10, 20]):
            left += index + value
        return left, right, first, middle, last, result, calls

    assert run_checked(main) == main()


def test_real_value_container_can_be_enumerated_and_unpacked():
    def main():
        words = Queue[Int]()
        words.value = [10, 20]
        result = 0
        for index, value in enumerate(words.value):
            result += index + value
        return result

    assert run_checked(main) == 31


def test_chained_attribute_and_item_assignments_evaluate_rhs_once():
    class Plain:
        pass

    obj = Plain()
    values = [0]
    calls = []

    def main():
        def rhs():
            calls.append("rhs")
            return 7

        obj.number = values[0] = rhs()
        return obj.number, values[0]

    assert run_checked(main) == (7, 7)
    assert calls == ["rhs"]


def test_lambda_and_lazy_generator_callbacks_work_after_entry_returns():
    def main():
        offset = 2
        key = lambda item: offset - item
        sequence = (key(item) for item in [1, 2, 3])
        return key, sequence

    key, sequence = run_checked(main)
    assert sorted([1, 2, 3], key=key) == [3, 2, 1]
    assert list(sequence) == [1, 0, -1]


def test_generator_send_throw_close_and_delegated_return():
    events = []

    def sequence():
        try:
            received = yield 1
            try:
                yield received
            except ValueError:
                yield 9
            return 42
        finally:
            events.append("closed")

    def delegated():
        result = yield from sequence()
        yield result

    values = run_checked(delegated)
    assert next(values) == 1
    assert values.send(7) == 7
    assert values.throw(ValueError("test")) == 9
    assert next(values) == 42
    with pytest.raises(StopIteration):
        next(values)
    assert events == ["closed"]
    values = run_checked(sequence)
    assert next(values) == 1
    values.close()
    assert events == ["closed", "closed"]


def test_generator_may_own_a_fresh_variable_without_exporting_its_slot():
    def sequence():
        local = Int(3)
        yield local.value
        local.value += 4
        yield local.value

    def main():
        values = sequence()
        first = next(values)
        second = next(values)
        values.close()
        return first, second

    assert run_checked(main) == (3, 7)


def test_yield_from_plain_iterable_preserves_throw_and_close():
    def sequence():
        yield from [1, 2]

    values = run_checked(sequence)
    assert next(values) == 1
    values.close()
    values = run_checked(sequence)
    assert next(values) == 1
    with pytest.raises(ValueError, match="injected"):
        values.throw(ValueError("injected"))


def test_tuple_targets_keep_attribute_and_item_assignment_order():
    class Plain:
        pass

    owner = Plain()
    values = [0, 0]

    def main():
        index = 0
        index, values[index], owner.result = [1, 7, 9]
        for owner.left, owner.right in [(2, 3)]:
            pass
        return values, owner.result, owner.left + owner.right

    assert run_checked(main) == ([0, 7], 9, 5)


def test_generator_rechecks_late_bound_captures_before_resuming():
    class Owner(SvObject):
        count = Int()

    owner = Owner()
    selected = 1

    def main():
        return (selected for _ in [0, 1])

    values = run_checked(main)
    assert next(values) == 1
    selected = owner.count
    with pytest.raises(CheckedAccessError):
        next(values)


def test_generator_first_iterable_runs_once_when_created():
    events = []

    def source(values):
        events.append("source")
        return values

    def main():
        return (value + 1 for value in source([1, 2]))

    values = run_checked(main)
    assert events == ["source"]
    assert list(values) == [2, 3]
    assert events == ["source"]


def test_generator_context_survives_yield_without_suspending_checked_scope():
    events = []

    @contextmanager
    def context():
        events.append("enter")
        try:
            yield 4
        finally:
            events.append("exit")

    def sequence():
        with context() as value:
            yield value
            yield value + 1

    values = run_checked(sequence)
    assert next(values) == 4
    assert events == ["enter"]
    assert run_checked(lambda_entry) == 3
    values.close()
    assert events == ["enter", "exit"]


def lambda_entry():
    return (lambda value: value + 1)(2)


def test_context_tuple_binding_is_visible_to_next_context():
    events = []

    @contextmanager
    def pair():
        events.append("pair enter")
        try:
            yield (2, 3)
        finally:
            events.append("pair exit")

    @contextmanager
    def selected(number):
        events.append(number)
        yield number + 1

    def main():
        with pair() as (left, right), selected(left) as value:
            return left + right + value

    assert run_checked(main) == 8
    assert events == ["pair enter", 2, "pair exit"]


def test_match_deletion_and_dynamic_ordinary_members():
    class Owner(SvObject):
        count = Int()

        def describe(self):
            return "owner"

    owner = Owner()
    owner.note = "hello"

    def main():
        values = {"kind": "word", "data": 7, "unused": 0}
        del values["unused"]
        match values:
            case {"kind": "word", "data": result} if result > 0:
                setattr(owner, "note", getattr(owner, "note") + "!")
            case _:
                return None
        setattr(getattr(owner, "count"), "value", result)
        description = getattr(owner, "describe")()
        note = owner.note
        del owner.note
        return description, note, owner.count.value, hasattr(owner, "note")

    assert run_checked(main) == ("owner", "hello!", 7, False)


def test_nested_decorators_and_classes_are_ordinary_python():
    def main():
        def decorated(function):
            def wrapper(value):
                return function(value) + 1
            return wrapper

        @decorated
        def twice(value):
            return value * 2

        class Plain:
            def number(self):
                return twice(3)

        return Plain().number()

    assert run_checked(main) == 7


def test_nested_class_keeps_descriptors_super_and_field_declarations():
    def main():
        class Base:
            @staticmethod
            def twice(value):
                return value * 2

            @classmethod
            def name(cls):
                return cls.__name__

            @property
            def number(self):
                return 3

        class Child(Base):
            def result(self):
                return super().number + self.twice(4)

        class Record(SvObject):
            count = Int()

        record = Record()
        record.count.value = 7
        return Child.twice(3), Child.name(), Child().result(), record.count.value

    assert run_checked(main) == (6, "Child", 11, 7)


def test_nested_class_cannot_keep_a_ref_in_method_closure():
    def main(value: Ref[Int]):
        class Saved:
            def read(self):
                return value.value

    with pytest.raises(CheckedAccessError):
        run_checked(main, Int())


def test_context_manager_body_still_rejects_carrier_storage():
    class Owner(SvObject):
        count = Int()

    owner = Owner()
    saved = []

    @contextmanager
    def context():
        saved.append(owner.count)
        yield 1

    def main():
        with context():
            pass

    with pytest.raises(CheckedAccessError):
        run_checked(main)
    assert saved == []


@pytest.mark.parametrize("operation", ["chain", "unpack", "loop", "lambda", "generator", "getattr", "match"])
def test_normal_syntax_does_not_allow_carrier_aliases(operation):
    class Owner(SvObject):
        count = Int()
        __match_args__ = ("count",)

    owner = Owner()

    def main():
        if operation == "chain":
            left = right = owner.count
        elif operation == "unpack":
            left, right = iter([owner.count, 1])
        elif operation == "loop":
            for index, value in [(0, owner.count)]:
                pass
        elif operation == "lambda":
            saved = lambda value=owner.count: value
        elif operation == "generator":
            return (owner.count for _ in [1])
        elif operation == "getattr":
            saved = getattr(owner, "count")
        else:
            match owner:
                case Owner(value):
                    pass

    with pytest.raises(CheckedAccessError):
        result = run_checked(main)
        if operation == "generator":
            next(result)


def test_ref_cannot_be_captured_by_lambda_or_generator():
    def lambda_capture(value: Ref[Int]):
        return lambda: value.value

    def generator_capture(value: Ref[Int]):
        return (value.value for _ in [1])

    for function in (lambda_capture, generator_capture):
        with pytest.raises(CheckedAccessError):
            run_checked(function, Int())


def test_hasattr_observes_a_slot_without_saving_its_carrier():
    class Owner(SvObject):
        count = Int()

    owner = Owner()

    def main(value: Ref[Int]):
        return hasattr(value, "value"), hasattr(owner, "count"), hasattr(owner, "missing")

    assert run_checked(main, Int()) == (True, True, False)


def test_match_may_inspect_a_carrier_without_binding_an_alias():
    def main():
        local = Int(7)
        match local:
            case Int(value=number):
                return number
            case _:
                return None

    assert run_checked(main) == 7


@pytest.mark.parametrize("builtin", [False, True])
def test_declared_slots_cannot_be_deleted(builtin):
    class Owner(SvObject):
        count = Int()

    owner = Owner()

    def main():
        if builtin:
            delattr(owner, "count")
        else:
            del owner.count

    with pytest.raises(CheckedAccessError, match="cannot be deleted"):
        run_checked(main)
    assert owner.count.value == 0



def test_caller_scoped_builtins_and_their_aliases_keep_the_callers_scope():
    def main():
        value = 7
        local_values = locals
        variable_values = vars
        evaluate = eval
        execute = exec
        result = {}
        execute("result['number'] = value + 2")
        return (locals()["value"], vars()["value"], local_values()["value"],
                variable_values()["value"], evaluate("value + 1"), result)

    assert run_checked(main) == main() == (7, 7, 7, 7, 8, {"number": 9})


def test_shadowed_builtin_names_remain_ordinary_callables():
    def main():
        locals = lambda: {"number": 11}
        super = lambda: 12
        return locals()["number"], super()

    assert run_checked(main) == (11, 12)


def test_annotated_classes_and_dataclasses_keep_fields_and_defaults():
    from dataclasses import dataclass, fields

    def main():
        class Plain:
            number: int = 7

        @dataclass
        class Record:
            first: int
            second: int = 8

        value = Record(7)
        return Plain.__annotations__, [field.name for field in fields(Record)], value.first + value.second

    assert run_checked(main) == main() == ({"number": int}, ["first", "second"], 15)


@pytest.mark.parametrize("inside", [False, True])
def test_generated_dataclass_constructor_does_not_depend_on_working_directory(tmp_path, monkeypatch, inside):
    import importlib.util

    project = tmp_path / "project"
    project.mkdir()
    path = project / "model.py"
    path.write_text("from dataclasses import dataclass\n"
                    "@dataclass\nclass Record:\n    value: int\n"
                    "def main():\n    return Record(7).value\n")
    spec = importlib.util.spec_from_file_location("checked_dataclass_model", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.chdir(project if inside else tmp_path)
    assert run_checked(module.main) == 7


def test_ordinary_annotations_are_not_runtime_contracts():
    def main(value: "TypeOnlyName") -> "AnotherTypeOnlyName":
        return value + 1

    assert run_checked(main, 4) == 5
    with pytest.raises(CheckedAccessError, match="no variable carrier contract"):
        run_checked(main, Int(4))


def test_future_annotations_accept_python_expressions_and_unresolved_return_typing(tmp_path):
    import importlib.util

    path = tmp_path / "future_annotations.py"
    path.write_text("from __future__ import annotations\n"
                    "events = []\n"
                    "def annotation():\n    events.append('evaluated')\n    return int\n"
                    "def main(value: annotation()) -> MissingName:\n    return value + 1\n")
    spec = importlib.util.spec_from_file_location("checked_future_annotations", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert run_checked(module.main, 4) == 5
    assert module.events == ["evaluated"]


def test_opaque_functions_are_allowed_but_parameter_and_return_violations_are_not():
    namespace = {"Ref": Ref, "Int": Int}
    exec("def ordinary(value):\n    return value + 1\n"
         "def update(value: Ref[Int]):\n    value.value += 3\n    return value.value\n"
         "def leak(value: Ref[Int]):\n    return value\n", namespace)
    assert run_checked(namespace["ordinary"], 4) == 5
    actual = Int(4)
    assert run_checked(namespace["update"], actual) == 7
    assert actual.value == 7
    with pytest.raises(CheckedAccessError, match="no variable carrier contract"):
        run_checked(namespace["ordinary"], actual)
    with pytest.raises(CheckedAccessError, match="returned a variable carrier"):
        run_checked(namespace["leak"], actual)


def test_ordinary_async_definition_and_execution_are_not_syntax_violations():
    import asyncio

    def main():
        async def calculate(value):
            return value + 1
        return calculate(4)

    assert asyncio.run(run_checked(main)) == 5


def test_deferred_async_work_may_not_retain_a_ref():
    async def calculate(value: Ref[Int]):
        return value.value

    with pytest.raises(CheckedAccessError, match="returned a variable carrier"):
        run_checked(calculate, Int(4))


def test_input_values_are_not_reevaluated_and_aggregate_inputs_remain_independent():
    def mutate(value: Ref[Queue[Int]]):
        value.value = [9]
        return 0

    def observe(carrier: Queue[Int], evaluated: Queue[Int], unused):
        carrier.value.append(10)
        return carrier.value, evaluated.value

    def main():
        values = Queue[Int]()
        values.value = [1]
        result = observe(values, values.value, mutate(values))
        return result, values.value

    assert run_checked(main) == (([9, 10], [1]), [9])


def test_indexed_input_evaluates_its_index_once_then_reads_at_call_entry():
    from svtypes import MemoryExternalFieldStorage, bind_external_value

    class Storage:
        def __init__(self):
            self.memory = MemoryExternalFieldStorage()

        def read(self, *args):
            return self.memory.read(*args)

        def write(self, *args):
            return self.memory.write(*args)

    storage = Storage()
    storage.memory.seed("words", Queue[Int](), [1, 2])
    values = bind_external_value(Queue[Int](), storage, "words")
    events = []

    def index():
        events.append("index")
        return 0

    def mutate():
        values[0].value = 9
        events.append("mutate")
        return 0

    def observe(value: Int, unused):
        return value.value

    def main():
        return observe(values[index()], mutate())

    assert run_checked(main) == 9
    assert events == ["index", "mutate"]



def test_parameter_contracts_may_be_produced_by_normal_python_expressions():
    calls = []

    def parameter_type():
        calls.append("annotation")
        return Ref[Int]

    def change(value: parameter_type()):
        value.value += 2

    actual = Int(3)
    run_checked(change, actual)
    assert actual.value == 5
    run_checked(change, actual)
    assert actual.value == 7
    assert calls == ["annotation"]

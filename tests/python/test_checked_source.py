import pytest
import gc
import weakref
from functools import wraps

from svtypes import (
    CheckedAccessError, Inout, Int, Object, ObjectRegistry, Queue, Ref, SvObject,
    run_checked,
)


@pytest.mark.parametrize('aggregate', [False, True])
def test_input_element_values_do_not_require_backend_borrowing(aggregate):
    from svtypes import MemoryExternalFieldStorage, bind_external_value

    class PlainStorage:
        def __init__(self):
            self.memory = MemoryExternalFieldStorage()

        def read(self, key, descriptor, path):
            return self.memory.read(key, descriptor, path)

        def write(self, key, descriptor, path, operation, payload):
            return self.memory.write(key, descriptor, path, operation, payload)

    storage = PlainStorage()
    codec = Queue[Queue[Int]]() if aggregate else Queue[Int]()
    storage.memory.seed('words', codec, [[10], [20]] if aggregate else [10, 20])
    words = bind_external_value(codec, storage, 'words')

    def scalar_pair(first: Int, second: Int):
        first.value += 1
        return first.value + second.value

    def aggregate_pair(first: Queue[Int], second: Queue[Int]):
        first.value.append(1)
        return first.value, second.value

    def main():
        if aggregate:
            return aggregate_pair(words[0], words[1])
        return scalar_pair(words[0], words[1])

    assert run_checked(main) == (([10, 1], [20]) if aggregate else 31)
    assert words.value == ([[10], [20]] if aggregate else [10, 20])


def test_input_carriers_are_copied_at_entry_after_argument_evaluation():
    def mutate(value: Ref[Int]):
        value.value = 99
        return 1

    def pair(first: Int, second: Int):
        return first.value + second.value

    def main():
        actual = Int(10)
        result = pair(actual, mutate(actual))
        return result, actual.value

    assert run_checked(main) == (100, 99)


@pytest.mark.parametrize('insert', [False, True])
def test_output_inout_paths_need_no_ref_borrow_and_publish_at_return(insert):
    from svtypes import MemoryExternalFieldStorage, Output, bind_external_value

    class AddressedStorage(MemoryExternalFieldStorage):
        value_arguments_by_path = True

    storage = AddressedStorage()
    codec = Queue[Int]()
    storage.seed('words', codec, [10, 20, 30])
    words = bind_external_value(codec, storage, 'words')

    def transfer(first: Output[Int], second: Inout[Int], whole: Ref[Queue[Int]]):
        first.value = 30
        second.value += 4
        assert whole.value == [10, 20, 30]
        if insert:
            whole.value.insert(0, 5)

    def main():
        transfer(words[0], words[1], words)
        return words.value

    assert run_checked(main) == ([30, 24, 20, 30] if insert else [30, 24, 30])

    def reference(value: Ref[Int]):
        value.value = 0

    def forbidden():
        reference(words[0])

    with pytest.raises(CheckedAccessError, match='stable element borrow'):
        run_checked(forbidden)


def test_compiled_cache_does_not_retain_finished_callable_defaults():
    class Marker:
        pass

    def main():
        marker = Marker()
        reference = weakref.ref(marker)

        def child(held=marker):
            assert held is not None

        child()
        del child, marker
        assert reference() is None
        gc.collect()
        assert reference() is None

    run_checked(main)


def test_checked_decorated_function_preserves_wrapper_source_and_closure():
    events = []
    factor = 3

    def original(value: Int):
        events.append('original')
        return value.value * factor

    def decorate(function):
        offset = 7

        @wraps(function)
        def wrapper(value: Int):
            events.append('wrapper')
            return function(value) + offset

        return wrapper

    wrapped = decorate(original)
    assert run_checked(wrapped, 4) == 19
    assert events == ['wrapper', 'original']


def test_checked_wrapped_initializer_keeps_generated_closure():
    events = []

    def make_initializer(cls):
        def initialize(instance):
            events.append(cls.__name__)

        return initialize

    def guard(initializer):
        @wraps(initializer)
        def initialize(instance):
            events.append('guard')
            return initializer(instance)

        return initialize

    class Item(SvObject):
        number = Int()

    Item.__init__ = guard(make_initializer(Item))

    def main():
        item = Item()
        item.number.value = 9
        return item.number.value

    assert run_checked(main) == 9
    assert events == ['guard', 'Item']


def test_entry_adapts_typed_function_and_keeps_python_defaults():
    def change(value: Int, step=3):
        value.value += step
        return value.value

    def update(value: Inout[Int], *, step=2):
        value.value += step

    def main():
        actual = Int(10)
        assert change(actual) == 13
        assert actual.value == 10
        update(actual, step=6)
        return actual.value

    assert run_checked(main) == 16


def test_entry_rejects_local_container_and_ordinary_call_escapes():
    def ordinary(value):
        return value

    def local_alias():
        actual = Int()
        alias = actual

    def container_alias():
        actual = Int()
        saved = [actual]

    def ordinary_argument():
        actual = Int()
        ordinary(actual)

    for function, error in (
        (local_alias, "cannot be stored"),
        (container_alias, "cannot be stored"),
        (ordinary_argument, "no variable carrier contract"),
    ):
        with pytest.raises(CheckedAccessError, match=error) as caught:
            run_checked(function)
        assert "test_checked_source.py:" in str(caught.value) or any(
            "test_checked_source.py:" in note for note in caught.value.__notes__
        )


def test_entry_handle_access_uses_stable_slot_and_handle_is_an_ordinary_value():
    registry = ObjectRegistry()

    class Child(SvObject):
        value = Int()

    class Owner(SvObject):
        child = Object["Child"](registry=registry)

    registry.register(Child)
    owner = Owner()
    child = Child()

    def read_handle(handle):
        return handle.value.value

    def main():
        assert owner.child.value is None
        owner.child.value = child
        saved = owner.child.value
        saved.value.value = 27
        owner.child.value = None
        return read_handle(saved)

    assert run_checked(main) == 27


@pytest.mark.parametrize("element_slot, expected", [(True, [21, 30]), (False, [20, 21])])
def test_distinct_augmented_assignment_targets_survive_structural_rhs(element_slot, expected):
    class Owner(SvObject):
        words = Queue[Int]()

    packet = Owner()
    packet.words.value = [10, 20, 30]
    evaluations = []

    def owner():
        evaluations.append("owner")
        return packet

    def index():
        evaluations.append("index")
        return 1

    def rhs():
        evaluations.append("rhs")
        packet.words.pop_front()
        return 1

    def element():
        owner().words[index()].value += rhs()

    def indexed_value():
        owner().words.value[index()] += rhs()

    run_checked(element if element_slot else indexed_value)
    assert packet.words.value == expected
    assert evaluations == ["owner", "index", "rhs"]


def test_local_element_ref_is_selected_before_later_argument_changes_container():
    words = Queue[Int]()
    words.value = [10, 20, 30]

    def shift():
        words.pop_front()
        return 5

    def modify(item: Ref[Int], delta):
        item.value += delta

    def main():
        modify(words[1], shift())

    run_checked(main)
    assert words.value == [25, 30]


def test_checked_collection_mutations_use_original_storage_and_preserve_survivors():
    from svtypes import Array, AssocArray, String, borrow_element

    words = Queue[Int]()
    words.value = [10, 20, 30]
    labels = AssocArray[String, Int]()
    fixed = Array[Int, 3]()
    fixed.value = [1, 2, 3]

    def main():
        words.value.insert(0, 5)
        words.value.append(40)
        assert words.value.pop(1) == 10
        words.value[2:3] = [31, 32]
        words.value.extend([50, 60])
        labels.value["mode"] = 6
        labels.value["mode"] += 1
        fixed.value[1:] = [8, 9]

    with borrow_element(words, 1) as unchanged:
        run_checked(main)
        assert unchanged.value == 20
    assert words.value == [5, 20, 31, 32, 40, 50, 60]
    assert labels.value == {"mode": 7}
    assert fixed.value == [1, 8, 9]


def test_checked_local_object_slots_start_null_and_enforce_declared_target():
    registry = ObjectRegistry()
    constructed = []

    class Target(SvObject):
        def __init__(self):
            super().__init__()
            constructed.append(self)

    class Derived(Target):
        pass

    registry.register(Target)

    def main():
        first = Object["Target"](registry=registry)
        second = Object["Target"](registry=registry)
        unresolved = Object["Unresolved"](registry=registry)
        assert first.value is None
        assert second.value is None
        assert unresolved.value is None
        assert len(constructed) == 0

        target = Derived()
        first.value = target
        second.value = first.value
        first.value = None
        try:
            second.value = SvObject()
        except TypeError:
            pass
        else:
            raise AssertionError("unrelated object was assigned")
        try:
            unresolved.value = target
        except TypeError:
            pass
        else:
            raise AssertionError("unresolved object was assigned")
        assert second.value is target
        assert unresolved.value is None
        return second.value

    assert run_checked(main) is constructed[0]
    assert len(constructed) == 1


def test_checked_external_paths_do_not_substitute_for_selected_element_identity():
    from svtypes import MemoryExternalFieldStorage, bind_external_value, read_value

    storage = MemoryExternalFieldStorage()
    codec = Queue[Int]()
    storage.seed("words", codec, [10, 20, 30])
    words = bind_external_value(codec, storage, "words")

    def change(item: Ref[Int]):
        item.value = 99

    def borrow_path():
        change(words[1])

    def augment_path():
        words[1].value += 1

    def indexed_value():
        words.value[1] += 1

    with pytest.raises(CheckedAccessError, match="stable element borrow"):
        run_checked(borrow_path)
    with pytest.raises(CheckedAccessError, match="stable element borrow"):
        run_checked(augment_path)
    assert read_value(words) == [10, 20, 30]
    run_checked(indexed_value)
    assert read_value(words) == [10, 21, 30]


class _StableWordsStorage:
    def __init__(self, cells):
        self.cells = cells
        self.acquired = []
        self.released = []

    def read(self, key, descriptor, path):
        raise AssertionError("the stable test must not read through a path")

    def write(self, key, descriptor, path, operation, payload):
        raise AssertionError("the stable test must not write through a path")

    def acquire_borrow(self, key, descriptor, path):
        from svtypes import FieldMember

        cell = self.cells[path.segments[0].index]
        if len(path.segments) == 2:
            assert isinstance(path.segments[1], FieldMember)
            cell = cell[path.segments[1].name]
        self.acquired.append(cell)
        return cell

    def read_borrow(self, token):
        return Int().pack(token[0])

    def write_borrow(self, token, payload):
        token[0], _ = Int().unpack(payload)

    def release_borrow(self, token):
        self.released.append(token)


@pytest.mark.parametrize("direction", [Inout, Ref])
def test_external_call_acquires_before_later_arguments_and_releases_on_return(direction):
    from svtypes import SvStruct, bind_external_value, field

    class Entry(SvStruct):
        value = Int()

    first, second, third = [10], [20], [30]
    storage = _StableWordsStorage([{"value": first}, {"value": second}, {"value": third}])
    words = bind_external_value(Queue[Entry](), storage, "words")

    def shift():
        assert storage.acquired == [second]
        storage.cells.pop(0)
        return 5

    def increment(target: Ref[Int], delta):
        target.value += delta

    def update(target: direction[Int], delta):
        assert storage.released == []
        increment(target, delta)
        assert second == ([20] if direction is Inout else [25])
        assert storage.released == []

    def exercise():
        update(target=field(words[1], "value"), delta=shift())
        assert storage.released == [second]

    run_checked(exercise)
    assert storage.cells == [{"value": [25]}, {"value": [30]}]
    assert storage.acquired == storage.released == [second]


def test_external_call_and_augmented_assignment_release_on_argument_or_rhs_failure():
    from svtypes import bind_external_value

    first, selected = [10], [20]
    storage = _StableWordsStorage([first, selected, [30]])
    words = bind_external_value(Queue[Int](), storage, "words")

    def fail():
        assert storage.acquired[-1] is selected
        raise ValueError("evaluation failed")

    def update(target: Ref[Int], rhs):
        raise AssertionError("the function body must not run")

    def main():
        try:
            update(words[1], fail())
        except ValueError:
            assert storage.released == [selected]
        try:
            words[1].value += fail()
        except ValueError:
            assert storage.released == [selected, selected]

    run_checked(main)
    assert storage.acquired == storage.released == [selected, selected]
    assert selected == [20]


def test_external_element_augmented_assignment_updates_original_slot_after_shift():
    from svtypes import bind_external_value

    first, selected, last = [10], [20], [30]
    storage = _StableWordsStorage([first, selected, last])
    words = bind_external_value(Queue[Int](), storage, "words")
    evaluations = []

    def index():
        evaluations.append("index")
        return 1

    def shift():
        evaluations.append("rhs")
        assert storage.acquired == [selected]
        storage.cells.pop(0)
        return 7

    def exercise():
        words[index()].value += shift()
        assert storage.released == [selected]

    run_checked(exercise)
    assert evaluations == ["index", "rhs"]
    assert storage.cells == [[27], [30]]



def test_checked_borrow_bookkeeping_does_not_probe_ordinary_python_objects():
    accesses = []

    class Ordinary:
        def __getattr__(self, name):
            accesses.append(name)
            return 6

        def __getitem__(self, index):
            return index + 3

    item = Ordinary()

    def use(value):
        return value.missing + value[2]

    def main():
        return use(item)

    assert run_checked(main) == 11
    assert accesses == ["missing"]


def _run_script(tmp_path, files):
    import os
    from pathlib import Path
    import subprocess
    import sys

    import svtypes

    for name, source in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")

    return subprocess.run(
        [sys.executable, "-B", str(tmp_path / "project" / "main.py")],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(Path(svtypes.__file__).resolve().parents[1])},
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def test_checked_project_imports_work_in_a_cycle_and_resolve_the_live_callable(tmp_path):
    result = _run_script(tmp_path, {
        "project/main.py": """
from svtypes import CheckedAccessError, Int, run_checked
from helpers import first

def run():
    variable = Int(10)
    first.bump(variable)
    return variable.value

assert run_checked(run) == 12
assert first.calls == 1
from helpers import first as same
assert first is same

first.bump = first.ordinary
try:
    run_checked(run)
except CheckedAccessError as exc:
    assert "no variable carrier contract" in str(exc)
else:
    raise AssertionError("the replaced callable inherited the old direction contract")
print("checked")
""",
        "project/helpers/__init__.py": "",
        "project/helpers/first.py": """
\"""Top-level setup must run only once.\"""
from __future__ import annotations
from svtypes import Int, Ref, SvObject

calls = 0
class Model(SvObject):
    count = Int()

def identity(delta):
    return delta

from . import second

def bump(value: Ref[Int]):
    global calls
    calls += 1
    value.value += second.delta()

def ordinary(value):
    raise AssertionError("an untyped function must not receive the carrier")

assert __doc__ == "Top-level setup must run only once."
assert Model().count.value == 0
""",
        "project/helpers/second.py": """
from .first import identity

def delta():
    return identity(2)
""",
    })
    assert result == "checked"


def test_checked_import_rejects_alias_before_module_side_effect_and_keeps_other_sources(tmp_path):
    result = _run_script(tmp_path, {
        "project/main.py": """
from pathlib import Path
from svtypes import checked_project
with checked_project(Path(__file__).parent):
    from pathlib import Path
    import sys
    from svtypes import CheckedAccessError

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dependency"))
    import external
    assert external.alias is external.original

    try:
        import broken
    except CheckedAccessError as exc:
        assert "broken.py:4" in str(exc)
    else:
        raise AssertionError("module carrier alias was accepted")
    assert "broken" not in sys.modules
    assert not Path("unexpected.txt").exists()

    try:
        import bad_default
    except CheckedAccessError as exc:
        assert "bad_default.py:4" in str(exc)
    else:
        raise AssertionError("a module function saved a carrier as a default")
    assert "bad_default" not in sys.modules
    print("checked")

""",
        "project/broken.py": """
from svtypes import Int
original = Int()
alias = [original]
open("unexpected.txt", "w").close()
""",
        "project/bad_default.py": """
from svtypes import Int
original = Int()
def update(value=original):
    pass
""",
        "dependency/external.py": """
from svtypes import Int
original = Int()
alias = original
""",
    })
    assert result == "checked"


def test_checked_run_uses_entry_project_for_later_imports(tmp_path):
    result = _run_script(tmp_path, {
        "project/main.py": """
from pathlib import Path
import sys
from svtypes import CheckedAccessError, run_checked

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "design"))
from entry import run

try:
    run_checked(run)
except CheckedAccessError as exc:
    assert "late.py:4" in str(exc)
else:
    raise AssertionError("late import bypassed the entry project's loader")
assert "late" not in sys.modules
print("checked")
""",
        "design/entry.py": """
def run():
    import late
""",
        "design/late.py": """
from svtypes import Int
original = Int()
alias = original
""",
    })
    assert result == "checked"


@pytest.mark.parametrize("external", [False, True])
def test_randomization_callbacks_run_checked_on_the_original_receiver(external):
    from svtypes import FieldIdentity, MemoryExternalFieldStorage, constraint

    events = []

    class Packet(SvObject):
        count = Int(rand=True)
        target = Int(rand=False)

        @constraint
        def limits(self):
            self.count == self.target

        def pre_randomize(self):
            super().pre_randomize()
            self.target.value = 7
            events.append(("pre", self))

        def post_randomize(self):
            events.append(("post", self))
            self.target.value = self.count.value + 1

    packet = Packet()
    if external:
        storage = MemoryExternalFieldStorage()
        storage.seed("count", Packet.count, 0)
        packet.bind_external_storage(storage, {FieldIdentity(Packet, "count"): "count"})

    def main():
        assert packet.randomize()
        return packet.count.value, packet.target.value

    assert run_checked(main) == (7, 8)
    assert events == [("pre", packet), ("post", packet)]


@pytest.mark.parametrize("post", [False, True])
def test_randomization_callbacks_cannot_save_carriers(post):
    class Packet(SvObject):
        count = Int(rand=True)

        def escape(self):
            self.saved = self.count

    packet = Packet()
    if post:
        packet.post_randomize = packet.escape
    else:
        packet.pre_randomize = packet.escape

    def main():
        packet.randomize()

    with pytest.raises(CheckedAccessError, match="cannot be stored"):
        run_checked(main)
    assert not hasattr(packet, "saved")

    # Leaving the entry restores regular invocation semantics.
    assert packet.randomize()
    assert packet.saved is packet.count


def test_user_scalar_codec_callbacks_keep_roundtrip_and_reject_escape():
    events = []
    saved = []
    fail = [False]

    class CheckedInt(Int):
        def pack(self, value):
            events.append("pack")
            if fail[0]:
                saved.append(self)
            return super().pack(value)

        def unpack(self, data):
            events.append("unpack")
            return super().unpack(data)

    class Packet(SvObject):
        count = CheckedInt()

    packet = Packet()
    packet.count.value = 12

    def main():
        payload = packet.to_bytes()
        packet.count.value = 31
        assert packet.from_bytes(payload) == len(payload)
        return packet.count.value

    assert run_checked(main) == 12
    assert events == ["pack", "unpack"]
    fail[0] = True
    with pytest.raises(CheckedAccessError, match="no variable carrier contract"):
        run_checked(main)
    assert saved == []


def test_callback_without_project_source_is_not_itself_a_violation():
    class Packet(SvObject):
        count = Int(rand=True)

    packet = Packet()
    packet.pre_randomize = eval("lambda: None")

    def main():
        packet.randomize()

    run_checked(main)
    assert packet.randomize()


def test_copy_callbacks_preserve_native_memo_and_check_replaced_method():
    from copy import copy, deepcopy

    events = []

    class Packet(SvObject):
        count = Int()

        def __copy__(self):
            events.append("shallow")
            return super().__copy__()

        def __deepcopy__(self, memo):
            events.append("deep")
            return super().__deepcopy__(memo)

    packet = Packet()
    packet.count.value = 6

    def main():
        shallow = copy(packet)
        copies = deepcopy([packet, packet])
        assert copies[0] is copies[1]
        assert copies[0] is not packet
        return shallow.count.value, copies[0].count.value

    assert run_checked(main) == (6, 6)
    assert events == ["shallow", "deep", "deep"]

    def escape(self, memo):
        self.saved = self.count
        return self

    Packet.__deepcopy__ = escape
    with pytest.raises(CheckedAccessError, match="cannot be stored"):
        run_checked(main)
    assert not hasattr(packet, "saved")


def test_checked_handle_index_is_a_fixed_slot_with_strict_ref_assignment():
    from svtypes import Array, AssocArray, String, svobj

    @svobj(name="CheckedIndexedChild")
    class Child(SvObject):
        count = Int()

    target = Child()
    replacement = Child()
    other = SvObject()
    fixed = Array[Object[Child], 2]()
    queue = Queue[Object[Child]]()
    keyed = AssocArray[String, Object[Child]]()

    def replace(slot: Ref[Object[Child]], next_value):
        slot.value = next_value

    def main():
        assert fixed[0].value is None
        fixed[0].value = target
        held = fixed[0].value
        queue.value.append(held)
        queue.value[0] = replacement
        keyed.value["active"] = held
        replace(keyed["active"], replacement)
        replace(fixed[0], None)
        try:
            keyed["active"].value = other
        except TypeError:
            pass
        else:
            raise AssertionError("unrelated handle was accepted")
        return held, queue[0].value, keyed["active"].value

    assert run_checked(main) == (target, replacement, replacement)
    assert fixed.value == [None, None]


def test_checked_struct_formals_project_locally_and_keep_fixed_member_slots():
    from svtypes import Output, SvStruct, field, read_value, write_value

    class Packet(SvStruct):
        value = Int()
        items = Queue[Int]()

    packet = Packet()
    write_value(packet, {"value": 2, "items": [4, 5]})

    def change(leaf: Ref[Int], owner: Ref[Packet]):
        owner.value = {"value": 10, "items": [7, 8]}
        leaf.value += 1
        owner.value["items"][0] += 2

    def update(owner: Inout[Packet]):
        change(field(owner, "value"), owner)
        assert read_value(packet) == {"value": 2, "items": [4, 5]}
        owner.value["value"] += 1
        owner.items.value.append(12)

    def build(owner: Output[Packet]):
        owner.value = {"value": 3, "items": [6]}
        owner.items[0].value += 4

    def main():
        update(packet)
        assert read_value(packet) == {"value": 12, "items": [9, 8, 12]}
        build(packet)
        return read_value(packet)

    assert run_checked(main) == {"value": 3, "items": [10]}


def test_checked_collection_ref_keeps_selected_slot_across_later_arguments():
    words = Queue[Int]()
    words.value = [10, 20, 30]

    def remove_first(target: Ref[Queue[Int]]):
        return target.value.pop(0)

    def increment(target: Ref[Int], delta):
        target.value += delta

    def update(target: Ref[Queue[Int]]):
        increment(target[1], remove_first(target))
        target.value[1] += 5

    def main():
        update(words)
        return words.value

    assert run_checked(main) == [30, 35]


def test_checked_external_struct_ref_reads_and_writes_only_its_leaf():
    from svtypes import FieldPath, MemoryExternalFieldStorage, SvStruct, bind_external_value

    class Packet(SvStruct):
        count = Int()
        other = Int()

    class RecordingStorage(MemoryExternalFieldStorage):
        def __init__(self):
            super().__init__()
            self.operations = []

        def read(self, key, descriptor, path):
            self.operations.append(("read", path))
            return super().read(key, descriptor, path)

        def write(self, key, descriptor, path, operation, payload):
            self.operations.append(("write", path))
            return super().write(key, descriptor, path, operation, payload)

    codec = Packet()
    codec.count.value = 2
    codec.other.value = 8
    storage = RecordingStorage()
    storage.seed("packet", codec, codec)
    bound = bind_external_value(codec, storage, "packet")

    def increment(packet: Ref[Packet]):
        packet.count.value += 3

    def main():
        increment(bound)

    run_checked(main)
    path = FieldPath().member("count")
    assert storage.operations == [("read", path), ("write", path)]
    assert storage.read("packet", bound._binding.root, path) == Int().pack(5)


def test_checked_borrowed_local_struct_ref_projects_into_the_original_element():
    from svtypes import SvStruct, borrow_element, read_value, write_value

    class Entry(SvStruct):
        count = Int()

    queue = Queue[Entry]()
    write_value(queue, [{"count": 3}, {"count": 7}])

    def mutate(entry: Ref[Entry]):
        assert queue.pop_front() == {"count": 3}
        entry.count.value += 5

    with borrow_element(queue, 1) as entry:
        run_checked(mutate, entry)
        assert entry.value == {"count": 12}
    assert read_value(queue) == [{"count": 12}]



def test_checked_nested_value_target_retains_selected_inner_container():
    from svtypes import read_value

    rows = Queue[Queue[Int]]()
    rows.value = [[1, 2], [3, 4], [5, 6]]

    events = []

    class PythonValue:
        def __init__(self):
            self.contents = [[1], [2]]

        @property
        def value(self):
            events.append("value")
            return self.contents

    plain = PythonValue()

    def index():
        events.append("index")
        return 1

    def shift():
        assert rows.value.pop(0) == [1, 2]
        return 5

    def main():
        rows.value[1][0] += shift()
        plain.value[index()][0] += 3
        return read_value(rows), plain.contents

    assert run_checked(main) == ([[8, 4], [5, 6]], [[1], [5]])
    assert events == ["value", "index"]


class _StableAggregateStorage(_StableWordsStorage):
    def __init__(self, cells):
        super().__init__(cells)
        self.child_requests = []

    def acquire_borrow_child(self, token, path):
        from svtypes import FieldIndex, FieldMember

        self.child_requests.append((token, path))
        for segment in path:
            if isinstance(segment, FieldMember):
                token = token[segment.name]
            elif isinstance(segment, FieldIndex):
                token = token[segment.index]
            else:
                raise AssertionError("unexpected test path")
        self.acquired.append(token)
        return token


def test_checked_external_aggregate_ref_uses_leaf_tokens_after_parent_moves():
    from svtypes import FieldPath, SvStruct, bind_external_value

    class Entry(SvStruct):
        count = Int()
        items = Queue[Int]()

    first = {"count": [1], "items": [[2]]}
    selected = {"count": [7], "items": [[9], [10]]}
    other = {"count": [11], "items": [[12]]}
    storage = _StableAggregateStorage([first, selected, other])
    entries = bind_external_value(Queue[Entry](), storage, "entries")

    def shift():
        assert storage.acquired == [selected]
        storage.cells.pop(0)
        return 4

    def update(item: Ref[Entry], delta):
        item.count.value += delta
        item.value["items"][1] += delta

    def main():
        update(entries[1], shift())

    run_checked(main)
    assert storage.cells == [{"count": [11], "items": [[9], [14]]}, other]
    assert storage.child_requests == [
        (selected, FieldPath().member("count")),
        (selected, FieldPath().member("count")),
        (selected, FieldPath().member("items").index(1)),
        (selected, FieldPath().member("items").index(1)),
    ]
    assert len(storage.acquired) == len(storage.released) == 5
    assert storage.released[-1] is selected


def test_explicit_external_subslot_lease_survives_parent_close_and_detachment():
    from svtypes import ExternalStorageClosedError, ExternalStorageError, FieldPath, SvStruct, bind_external_value
    from svtypes import borrow_element

    class Entry(SvStruct):
        count = Int()
        other = Object["ExternalBorrowedEntryOther"]()

    original, replacement = {"count": [7]}, {"count": [20]}
    storage = _StableAggregateStorage([original])
    codec = Queue[Entry]()
    bound = bind_external_value(codec, storage, "entries")
    outer = borrow_element(bound, 0)
    child = outer.child(FieldPath().member("count"))
    outer.close()
    storage.cells[0] = replacement
    child.value = 9
    assert child.value == 9
    assert storage.cells == [replacement]
    assert storage.released == [original]
    with pytest.raises(ExternalStorageClosedError):
        outer.child(FieldPath().member("count"))
    bound.close()
    with pytest.raises(ExternalStorageClosedError):
        _ = child.value
    child.close()
    assert storage.released == [original, original["count"]]

    readonly = bind_external_value(codec, storage, "entries", readonly=True)
    with borrow_element(readonly, 0) as selected:
        with selected.child(FieldPath().member("count")) as leaf:
            with pytest.raises(ExternalStorageError, match="read-only"):
                leaf.value = 5

    legacy = bind_external_value(codec, _StableWordsStorage([replacement]), "entries")
    with borrow_element(legacy, 0) as selected:
        with pytest.raises(ExternalStorageError, match="does not expose aggregate subslots"):
            selected.child(FieldPath().member("count"))


def test_checked_external_nested_value_target_keeps_the_original_inner_container():
    from svtypes import FieldPath, bind_external_value

    first, selected, other = [[1], [2]], [[3], [4]], [[5], [6]]
    storage = _StableAggregateStorage([first, selected, other])
    rows = bind_external_value(Queue[Queue[Int]](), storage, "rows")

    def shift():
        assert storage.acquired[0] is selected
        storage.cells.pop(0)
        return 7

    def main():
        rows.value[1][0] += shift()

    run_checked(main)
    assert storage.cells == [[[10], [4]], other]
    assert storage.child_requests == [(selected, FieldPath().index(0))] * 2
    assert storage.released[-1] is selected


def test_checked_identity_borrow_keeps_handle_slots_after_queue_shift():
    from svtypes import CodecSession, SvStruct, bind_external_value, svobj
    from svtypes.external_storage import _pack_value

    @svobj(name="BorrowIdentityChild")
    class Child(SvObject):
        count = Int()

    class Entry(SvStruct):
        child = Object[Child]()

    class IdentityTokens(_StableAggregateStorage):
        def read_borrow(self, token):
            return token[0]

        def write_borrow(self, token, payload):
            token[0] = payload

    session = CodecSession()
    original, replacement, other = (Child(session=session) for _ in range(3))
    original.count.value = 4
    selected = {"child": [_pack_value(Entry.child, original)]}
    following = {"child": [_pack_value(Entry.child, other)]}
    storage = IdentityTokens([{"child": [b"\x00"]}, selected, following])
    entries = bind_external_value(Queue[Entry](), storage, "entries", session=session)
    original.count.value = 41

    def shift():
        storage.cells.pop(0)
        return replacement

    def change(entry: Ref[Entry], referent):
        retained = entry.child.value
        entry.child.value = referent
        assert entry.child.value is replacement
        assert retained is original
        assert retained.count.value == 41

    def main():
        change(entries[1], shift())

    run_checked(main)
    assert storage.cells == [{"child": [_pack_value(Entry.child, replacement)]}, following]
    assert storage.released[-1] is selected
    assert len(storage.acquired) == len(storage.released) == 4


def test_checked_external_handle_collection_edits_use_existing_byte_operations():
    from svtypes import AssocArray, CodecSession, FieldOperation, MemoryExternalFieldStorage, String, bind_external_value, svobj

    @svobj(name="ExternalCollectionEditChild")
    class Child(SvObject):
        count = Int()

    class RecordingStorage(MemoryExternalFieldStorage):
        def __init__(self):
            super().__init__()
            self.operations = []

        def write(self, key, descriptor, path, operation, payload):
            result = super().write(key, descriptor, path, operation, payload)
            self.operations.append((key, operation))
            return result

    session = CodecSession()
    first, second, third = (Child(session=session) for _ in range(3))
    first.count.value = 4
    queue_codec = Queue[Object[Child]]()
    assoc_codec = AssocArray[String, Object[Child]]()
    storage = RecordingStorage()
    storage.seed("queue", queue_codec, [first])
    storage.seed("map", assoc_codec, {"initial": first})
    queue = bind_external_value(queue_codec, storage, "queue", session=session)
    by_key = bind_external_value(assoc_codec, storage, "map", session=session)
    first.count.value = 40

    def main():
        queue.push_back(None)
        queue.value.insert(2, second)
        queue.value[1:2] = [third, None]
        del queue.value[2]
        assert queue.pop_front() is first
        assert first.count.value == 40
        assert queue.value == [third, second]
        by_key.value["new"] = third
        assert by_key.value.pop("initial") is first
        assert by_key.value.pop("missing", second) is second
        del by_key.value["new"]
        assert by_key.value == {}

    run_checked(main)
    assert storage.operations == [
        ("queue", FieldOperation.APPEND),
        ("queue", FieldOperation.INSERT),
        ("queue", FieldOperation.DELETE),
        ("queue", FieldOperation.INSERT),
        ("queue", FieldOperation.INSERT),
        ("queue", FieldOperation.DELETE),
        ("queue", FieldOperation.POP),
        ("map", FieldOperation.SET),
        ("map", FieldOperation.POP),
        ("map", FieldOperation.DELETE),
    ]


def test_external_handle_edits_keep_existing_element_borrow_on_detached_slot():
    import struct

    from svtypes import CodecSession, FieldOperation, bind_external_value, borrow_element, svobj
    from svtypes.external_storage import _pack_value

    @svobj(name="ExternalEditedBorrowChild")
    class Child(SvObject):
        count = Int()

    class Storage(_StableWordsStorage):
        def read(self, key, descriptor, path):
            if len(path):
                return self.cells[path.segments[0].index][0]
            return struct.pack("<I", len(self.cells)) + b"".join(cell[0] for cell in self.cells)

        def write(self, key, descriptor, path, operation, payload):
            if operation is FieldOperation.APPEND:
                self.cells.append([payload])
                return
            index = path.segments[0].index
            if operation is FieldOperation.INSERT:
                self.cells.insert(index, [payload])
            elif operation in (FieldOperation.DELETE, FieldOperation.POP):
                self.cells.pop(index)
            else:
                raise AssertionError(operation)

        def read_borrow(self, token):
            return token[0]

        def write_borrow(self, token, payload):
            token[0] = payload

    session = CodecSession()
    target, inserted, replacement = (Child(session=session) for _ in range(3))
    element = Object[Child]()
    original_slot = [_pack_value(element, target)]
    storage = Storage([original_slot])
    queue = bind_external_value(Queue[Object[Child]](), storage, "queue", session=session)

    def main():
        queue.value.insert(0, inserted)
        assert queue[1].value is target
        del queue.value[1]
        queue.value.extend([replacement, None])
        assert queue.value == [inserted, replacement, None]

    with borrow_element(queue, 0) as selected:
        run_checked(main)
        assert selected.value is target
        selected.value = None
        assert selected.value is None
        assert original_slot == [b"\x00"]
    assert storage.released == [original_slot]
    assert len(storage.cells) == 3


def test_checked_constructor_calls_base_with_its_own_parameter_copy():
    class Base(SvObject):
        count = Int()

        def __init__(self, initial: Int):
            super().__init__()
            initial.value += 2
            self.count.value = initial.value

    class Derived(Base):
        other = Int()

        def __init__(self, initial: Int):
            super().__init__(initial)
            self.other.value = initial.value + 1

    def main():
        value = Derived(4)
        return value.count.value, value.other.value

    assert run_checked(main) == (6, 5)


def test_checked_constructor_rejects_saving_a_member_slot():
    escaped = []

    class Packet(SvObject):
        count = Int()

        def __init__(self):
            super().__init__()
            escaped.append(self.count)

    def main():
        return Packet()

    with pytest.raises(CheckedAccessError, match="no variable carrier contract"):
        run_checked(main)

    def replaced(self):
        SvObject.__init__(self)
        escaped.append(self.count)

    Packet.__init__ = replaced
    with pytest.raises(CheckedAccessError, match="no variable carrier contract"):
        run_checked(main)
    assert escaped == []


def test_checked_constructor_keeps_coverage_initialization_boundary():
    from svtypes import Bit, CoverInput, CovPoint, bins, coverage_init, covergroup

    class Packet(SvObject):
        opcode = Bit[2]()
        first = Bit[2](value=1)
        last = Bit[2](value=2)

        @covergroup
        def cg(self, lower: CoverInput[int], upper: CoverInput[int]):
            class opcode_cp(CovPoint, source=self.opcode):
                window = bins[lower:upper].split(max_bins=None)

        @coverage_init
        def configure(self, lower: int, upper: int):
            self.cg.instantiate(lower, upper)

        def __init__(self):
            super().__init__()
            self.configure(self.first.value, self.last.value)

    def main():
        packet = Packet()
        packet.opcode.value = 1
        packet.cg.sample()
        return packet.cg.instance.snapshot()["opcode_cp"]["hits"]

    assert run_checked(main) == {"window[1]": 1}


def test_checked_ref_rejects_same_wire_name_for_different_handle_classes():
    from svtypes import encoding_descriptor, field

    first_registry, second_registry = ObjectRegistry(), ObjectRegistry()
    first_registry.path = second_registry.path = "CheckedNominalScope"

    class First(SvObject):
        count = Int()

    class Second(SvObject):
        count = Int()

    first_registry.register(First, "Child")
    second_registry.register(Second, "Child")
    assert encoding_descriptor(Object[First]()) == encoding_descriptor(Object[Second]())
    assert encoding_descriptor(Queue[Object[First]]()) == encoding_descriptor(Queue[Object[Second]]())

    class Owner(SvObject):
        child = Object[Second]()

    owner = Owner()
    owner.child.value = Second()

    def read_slot(slot: Ref[Object[First]]):
        return slot.value

    def read_queue(slots: Ref[Queue[Object[First]]]):
        return slots.value

    with pytest.raises(CheckedAccessError, match="Ref declaration does not match"):
        run_checked(read_slot, field(owner, "child"))
    with pytest.raises(CheckedAccessError, match="Ref declaration does not match"):
        run_checked(read_queue, Queue[Object[Second]]())


def test_checked_context_binding_preserves_enter_exit_and_suppression():
    events = []

    class Context:
        def __init__(self, label):
            self.label = label

        def __enter__(self):
            events.append(("enter", self.label))
            return self.label

        def __exit__(self, kind, error, traceback):
            events.append(("exit", self.label, kind))
            return self.label == "outer"

    def main():
        with Context("outer") as first, Context("inner") as second:
            assert first == "outer" and second == "inner"
            raise ValueError("suppressed")
        return first, second

    assert run_checked(main) == ("outer", "inner")
    assert events == [
        ("enter", "outer"), ("enter", "inner"),
        ("exit", "inner", ValueError), ("exit", "outer", ValueError),
    ]


def test_checked_context_rejects_carrier_binding_but_releases_resource():
    events = []
    variable = Int(2)

    class Context:
        def __enter__(self):
            events.append("enter")
            return variable

        def __exit__(self, kind, error, traceback):
            events.append(("exit", kind))
            return True

    def main():
        with Context() as saved:
            raise AssertionError("a carrier must not reach the context binding")

    with pytest.raises(CheckedAccessError, match="cannot be stored"):
        run_checked(main)
    assert events == ["enter", ("exit", CheckedAccessError)]


def test_checked_nested_function_uses_original_signature_and_runs_as_callback():
    def main():
        offset = 2
        local = Int(6)

        def update(value: Inout[Int], /, *, step=1):
            value.value += step + offset

        def key(value):
            return offset - value

        update(local, step=3)
        assert sorted([3, 1, 2], key=key) == [3, 2, 1]
        return local.value, key

    value, key = run_checked(main)
    assert value == 11
    assert key(1) == 1


def test_checked_nested_function_rejects_captures_defaults_and_callback_escape():
    class Owner(SvObject):
        value = Int()

    owner = Owner()
    saved = []
    actual = Int()

    def captures(carrier: Ref[Int]):
        def nested():
            return carrier.value

    def default(carrier: Ref[Int]):
        def nested(value=carrier):
            return value

    def later_declaration():
        def nested():
            return variable.value
        variable = Int()

    def callback():
        def key(value):
            saved.append(owner.value)
            return value
        sorted([1], key=key)

    with pytest.raises(CheckedAccessError, match="cannot be stored"):
        run_checked(captures, actual)
    with pytest.raises(CheckedAccessError, match="cannot be stored"):
        run_checked(default, actual)
    with pytest.raises(CheckedAccessError, match="new variable must be declared"):
        run_checked(later_declaration)
    with pytest.raises(CheckedAccessError, match="no variable carrier contract"):
        run_checked(callback)
    assert saved == []


def test_checked_context_runs_project_generator_with_checked_body():
    from contextlib import contextmanager

    entered = []

    @contextmanager
    def context():
        entered.append(True)
        yield 1

    def main():
        with context() as value:
            return value

    assert run_checked(main) == 1
    assert entered == [True]


def test_checked_dynamic_reordering_preserves_element_slots():
    from svtypes import borrow_element

    queue = Queue[Int]()
    queue.value = [4, 1, 2]

    def main():
        def negative(value):
            return -value

        queue.value.reverse()
        assert queue.value == [2, 1, 4]
        queue.value.sort(key=negative)
        assert queue.value == [4, 2, 1]
        queue.value.remove(4)
        assert queue.value == [2, 1]
        queue.value.clear()
        assert queue.value == []

    with borrow_element(queue, 0) as first, borrow_element(queue, 2) as last:
        run_checked(main)
        assert first.value == 4
        assert last.value == 2
        first.value = 9
        assert queue.value == []


def test_checked_collection_method_evaluates_arguments_before_editing_contents():
    class Owner(SvObject):
        queue = Queue[Int]()

    owner = Owner()
    owner.queue.value = [1]

    def main():
        def replacement():
            owner.queue.value = [2]
            return 3

        owner.queue.value.append(replacement())
        return owner.queue.value

    assert run_checked(main) == [2, 3]


def test_checked_associative_update_prepares_values_and_keeps_existing_slots():
    from svtypes import AssocArray, String, borrow_element

    mapping = AssocArray[String, Int]()
    mapping.value = {"first": 1}

    def invalid():
        mapping.value.update({"first": 2, "second": "invalid"})

    def main():
        assert mapping.value.setdefault("first", 9) == 1
        mapping.value.update({"first": 2}, second=3)
        assert mapping.value == {"first": 2, "second": 3}
        assert mapping.value.setdefault("third", 4) == 4
        assert mapping.value.popitem() == ("third", 4)
        mapping.value.clear()

    with borrow_element(mapping, "first") as first:
        with pytest.raises((TypeError, ValueError)):
            run_checked(invalid)
        assert mapping.value == {"first": 1}
        run_checked(main)
        assert first.value == 2
        assert mapping.value == {}


def test_checked_external_collection_edits_validate_before_publishing():
    from svtypes import AssocArray, MemoryExternalFieldStorage, String, bind_external_value

    storage = MemoryExternalFieldStorage()
    queue_codec, map_codec = Queue[Int](), AssocArray[String, Int]()
    storage.seed("queue", queue_codec, [1, 2, 1])
    storage.seed("map", map_codec, {"first": 1})
    queue = bind_external_value(queue_codec, storage, "queue")
    mapping = bind_external_value(map_codec, storage, "map")

    def reorder():
        queue.value.reverse()

    def invalid():
        mapping.value.update({"first": 2, "second": "invalid"})

    def main():
        assert mapping.value.setdefault("first", 9) == 1
        mapping.value.update({"first": 2}, second=3)
        assert mapping.value.setdefault("third", 4) == 4
        key, value = mapping.value.popitem()
        assert value == {"first": 2, "second": 3, "third": 4}[key]
        mapping.value.clear()
        queue.value.remove(1)
        assert queue.value == [2, 1]
        queue.value.clear()
        assert mapping.value == {} and queue.value == []

    with pytest.raises(CheckedAccessError, match="reverse is unsupported"):
        run_checked(reorder)
    with pytest.raises((TypeError, ValueError)):
        run_checked(invalid)
    assert queue.value == [1, 2, 1]
    assert mapping.value == {"first": 1}
    run_checked(main)


def test_checked_argument_expansions_pass_values_and_reject_carriers():
    class Owner(SvObject):
        count = Int(12)
        queue = Queue[Int]()

    owner = Owner()
    owner.queue.value = [7]

    class Arguments:
        def __init__(self, carrier):
            self.carrier = carrier

        def keys(self):
            events.append("keys")
            return ("first",)

        def __getitem__(self, key):
            events.append(key)
            return owner.count if self.carrier else owner.count.value

    events = []

    def consume(first: Int, second: int = 0):
        events.append("called")
        return first.value + second

    def valid():
        return consume(*owner.queue.value, **{"second": 2}), consume(**Arguments(False))

    def invalid_position():
        return consume(*owner.queue)

    def invalid_keyword():
        return consume(**{"first": owner.count})

    class EscapeInKeys(Arguments):
        def keys(self):
            events.append(owner.count)
            return super().keys()

    def invalid_mapping():
        return consume(**Arguments(True))

    def invalid_method():
        return consume(**EscapeInKeys(False))

    assert run_checked(valid) == (9, 12)
    assert events == ["called", "keys", "first", "called"]
    events.clear()
    for invalid in (invalid_position, invalid_keyword, invalid_mapping, invalid_method):
        with pytest.raises(CheckedAccessError, match="variable carrier"):
            run_checked(invalid)
    assert events == ["keys", "first"]


def test_checked_project_import_checks_star_bindings_and_class_declarations(tmp_path):
    result = _run_script(tmp_path, {
        "project/main.py": """
from pathlib import Path
from svtypes import checked_project
with checked_project(Path(__file__).parent):
    import sys
    from svtypes import CheckedAccessError

    import declarations
    assert declarations.Model.word.value == 3
    assert declarations.delta == 2
    for name, diagnostic in (("bad_star", "(word)"), ("bad_class", "alias = word")):
        try:
            __import__(name)
        except CheckedAccessError as exc:
            assert diagnostic in str(exc)
        else:
            raise AssertionError("an import saved a variable carrier")
        assert name not in sys.modules
    print("checked")

""",
        "project/declarations.py": """
from svtypes import *
from constants import *
class Model(SvObject):
    word = Int(3)
    def read(self):
        return self.word.value
""",
        "project/constants.py": """
from svtypes import Int
__all__ = ["delta"]
delta = 2
word = Int(10)
""",
        "project/exported.py": """
from svtypes import Int
__all__ = ["word"]
word = Int(12)
""",
        "project/bad_star.py": """
from exported import *
raise AssertionError("import should stop at the star binding")
""",
        "project/bad_class.py": """
from svtypes import Int, SvObject
class Model(SvObject):
    word = Int(10)
    alias = word
raise AssertionError("import should stop at the class binding")
""",
    })
    assert result == "checked"


def test_checked_project_import_restores_rejected_bindings_when_caught(tmp_path):
    result = _run_script(tmp_path, {
        "project/main.py": """
from pathlib import Path
from svtypes import checked_project
with checked_project(Path(__file__).parent):
    import svtypes
    import catches
    assert catches.original.value == 1
    assert catches.allowed == 3
    assert not hasattr(catches, "alias")
    assert not hasattr(catches, "extra")
    assert not hasattr(catches, "bad_default")
    assert catches.caught == 4
    print("checked")

""",
        "project/exported.py": """
from svtypes import Int
__all__ = ["allowed", "alias"]
allowed = 3
alias = Int(10)
""",
        "project/catches.py": """
from svtypes import CheckedAccessError, Int
original = Int(1)
caught = 0
try:
    from exported import alias as original
except CheckedAccessError:
    caught += 1
try:
    from exported import *
except CheckedAccessError:
    caught += 1
try:
    from exported import alias as extra, missing
except ImportError as error:
    assert "variable carrier cannot be imported" in error.__notes__[0]
    caught += 1
try:
    def bad_default(value=original):
        pass
except CheckedAccessError:
    caught += 1
""",
    })
    assert result == "checked"


def test_checked_indirect_calls_use_the_actual_parameter_contract():
    from functools import partial

    class Add:
        def __call__(self, actual: Ref[Int], delta: Int, *, factor=1):
            actual.value += delta.value * factor

    def main():
        word = Int(1)
        add = Add()
        add(word, 2)
        partial(add, delta=3)(word, factor=2)

        def increase(actual: Ref[Int], delta: Int):
            actual.value += delta.value

        partial(increase, delta=4)(word)
        return word.value

    assert run_checked(main) == 13


def test_checked_callable_instances_and_partials_cannot_hide_carrier_aliases():
    from functools import partial

    class Owner(SvObject):
        count = Int()

    owner = Owner()
    saved = []

    def escape(actual):
        saved.append(actual.count)

    class Callback:
        def __call__(self, actual):
            escape(actual)

    def indirect():
        Callback()(owner)

    def wrapped():
        partial(escape, owner)()

    for function in (indirect, wrapped):
        with pytest.raises(CheckedAccessError, match="no variable carrier contract"):
            run_checked(function)
    assert saved == []


def test_checked_expansion_and_loops_check_project_iteration_protocols():
    class Owner(SvObject):
        word = Int()

    owner = Owner()
    events = []

    class Iterable:
        def __iter__(self):
            events.append("iter")
            return Iterator()

    class Iterator:
        def __init__(self):
            self.value = 0

        def __iter__(self):
            raise AssertionError("iter() must be called on the iterable only")

        def __next__(self):
            if self.value == 2:
                raise StopIteration
            self.value += 1
            return self.value

    class Sequence:
        def __getitem__(self, index):
            if index == 2:
                raise IndexError(index)
            return index + 1

    class BadIterable:
        def __iter__(self):
            events.append(owner.word)
            return iter([1, 2])

    class BadIterator:
        def __iter__(self):
            return self

        def __next__(self):
            events.append(owner.word)
            raise StopIteration

    class BadSequence:
        def __getitem__(self, index):
            events.append(owner.word)
            raise IndexError(index)

    def combine(first, second):
        return first + second

    def valid():
        return combine(*Iterable()), [item for item in Sequence()]

    def invalid_iter():
        return combine(*BadIterable())

    def invalid_next():
        return [item for item in BadIterator()]

    def invalid_index():
        return combine(*BadSequence())

    assert run_checked(valid) == (3, [1, 2])
    for function in (invalid_iter, invalid_next, invalid_index):
        with pytest.raises(CheckedAccessError, match="no variable carrier contract"):
            run_checked(function)
    assert events == ["iter"]


def test_checked_properties_and_constructors_follow_project_source():
    class Owner(SvObject):
        word = Int(4)

    owner = Owner()
    events = []

    class View:
        def __init__(self, source):
            self.source = source

        @property
        def value(self):
            return self.source.word.value

        @value.setter
        def value(self, value):
            self.source.word.value = value

        @property
        def missing(self):
            events.append("getter")
            raise AttributeError("missing")

        def __getattr__(self, name):
            events.append(name)
            return 2

    class Allocated(View):
        def __new__(cls, source):
            events.append("new")
            return object.__new__(cls)

    class AliasOnRead(View):
        @property
        def value(self):
            events.append(self.source.word)
            return 0

    class AliasOnWrite(View):
        @View.value.setter
        def value(self, value):
            events.append(self.source.word)

    class AliasOnInit:
        def __init__(self, source):
            events.append(source.word)

    class AliasOnNew:
        def __new__(cls, source):
            events.append(source.word)
            return object.__new__(cls)

    def valid():
        view = Allocated(owner)
        view.value += 3
        return getattr(view, "value"), view.missing

    def getter():
        return AliasOnRead(owner).value

    def setter():
        AliasOnWrite(owner).value = 2

    def initializer():
        AliasOnInit(owner)

    def allocator():
        AliasOnNew(owner)

    assert run_checked(valid) == (7, 2)
    for function in (getter, setter, initializer, allocator):
        with pytest.raises(CheckedAccessError, match="no variable carrier contract"):
            run_checked(function)
    assert events == ["new", "getter", "missing"]


def test_checked_python_item_access_runs_project_hooks():
    class Owner(SvObject):
        word = Int(1)

    owner = Owner()
    events = []

    class View:
        def __getitem__(self, key):
            events.append(("get", key))
            return owner.word.value

        def __setitem__(self, key, value):
            events.append(("set", key))
            owner.word.value = value

    class AliasOnRead:
        def __getitem__(self, key):
            events.append(owner.word)
            return 0

    class AliasOnWrite:
        def __setitem__(self, key, value):
            events.append(owner.word)

    def valid():
        view = View()
        view[3] += 2
        return owner.word.value

    def getter():
        return AliasOnRead()[0]

    def setter():
        AliasOnWrite()[0] = 5

    assert run_checked(valid) == 3
    for function in (getter, setter):
        with pytest.raises(CheckedAccessError, match="no variable carrier contract"):
            run_checked(function)
    assert events == [("get", 3), ("set", 3)]


def test_checked_descriptors_keep_python_lookup_order_and_guard_hooks():
    class Owner(SvObject):
        word = Int(3)

    owner = Owner()
    events = []

    class Read:
        def __get__(self, obj, objtype=None):
            events.append(("get", objtype))
            return 11 if obj is None else owner.word.value

    class Data(Read):
        def __set__(self, obj, value):
            events.append(("set", value))
            owner.word.value = value

    class Host:
        data = Data()
        read = Read()

        def __getattribute__(self, name):
            return super().__getattribute__(name)

        def __setattr__(self, name, value):
            super().__setattr__(name, value)

        def __getattr__(self, name):
            events.append(("missing", name))
            return 9

    class UnsafeData:
        def __get__(self, obj, objtype=None):
            events.append(owner.word)
            return 0

        def __set__(self, obj, value):
            events.append(owner.word)

    class UnsafeHost(Host):
        data = UnsafeData()

        def __getattr__(self, name):
            events.append(owner.word)

    class WithSuper(UnsafeHost):
        def inherited(self):
            return super().data

    def valid():
        host = Host()
        host.read = 7
        host.data += 2
        return Host.read, host.read, host.data, host.absent

    def get():
        return UnsafeHost().data

    def put():
        UnsafeHost().data = 10

    def missing():
        return UnsafeHost().absent

    def inherited():
        return WithSuper().inherited()

    def existence_check():
        return hasattr(UnsafeHost(), "data")

    def explicit_get():
        return object.__getattribute__(UnsafeHost(), "data")

    def explicit_set():
        object.__setattr__(UnsafeHost(), "data", 1)

    assert run_checked(valid) == (11, 7, 5, 9)
    assert events == [("get", Host), ("set", 5), ("get", Host), ("get", Host), ("missing", "absent")]
    events.clear()
    for function in (get, put, missing, inherited, existence_check, explicit_get, explicit_set):
        with pytest.raises(CheckedAccessError, match="no variable carrier contract"):
            run_checked(function)
    assert events == []


def test_checked_metaclass_forwards_to_checked_constructor():
    class Owner(SvObject):
        word = Int(3)

    owner = Owner()
    events = []

    class DefaultMeta(type):
        pass

    class ForwardMeta(type):
        def __call__(cls, value):
            events.append(("metaclass", value))
            return super().__call__(value + 1)

    class ExplicitMeta(type):
        def __call__(cls, value):
            return type.__call__(cls, value)

    class Valid(metaclass=ForwardMeta):
        def __init__(self, value):
            events.append(("init", value))
            self.value = value

    class Inherited(metaclass=DefaultMeta):
        def __init__(self, value):
            events.append(owner.word)

    class Unsafe(metaclass=ForwardMeta):
        def __init__(self, value):
            events.append(owner.word)

    class Explicit(metaclass=ExplicitMeta):
        def __new__(cls, value):
            events.append(owner.word)
            return object.__new__(cls)

    def valid():
        return Valid(7).value

    def inherited():
        Inherited(0)

    def forwarded():
        Unsafe(0)

    def explicit():
        Explicit(0)

    assert run_checked(valid) == 8
    assert events == [("metaclass", 7), ("init", 8)]
    events.clear()
    for function in (inherited, forwarded, explicit):
        with pytest.raises(CheckedAccessError, match="no variable carrier contract"):
            run_checked(function)
    assert events == [("metaclass", 0)]


def test_checked_metaclass_data_descriptor_precedes_class_member():
    events = []

    class MetaField:
        def __get__(self, cls, owner):
            events.append(("get", cls, owner))
            return 4

        def __set__(self, cls, value):
            events.append(("set", cls, value))

    class Meta(type):
        field = MetaField()

    class Host(metaclass=Meta):
        field = 100

    def main():
        Host.field += 2
        return Host.field

    assert run_checked(main) == 4
    assert events == [("get", Host, Meta), ("set", Host, 6), ("get", Host, Meta)]


def test_checked_composite_codec_keeps_wire_contract_and_rejects_escape():
    from svtypes import MemoryExternalFieldStorage, SvStruct, bind_external_value

    events = []
    saved = []
    behavior = ["valid"]

    class Record(SvStruct):
        word = Int()

        def pack(self, value, ctx=None):
            events.append(("pack", value.word.value))
            if behavior[0] == "alias":
                saved.append(value)
            return super().pack(value=value, ctx=ctx)

        def unpack(self, data, ctx=None):
            decoded, count = super().unpack(data, ctx)
            events.append(("unpack", decoded.word.value))
            if behavior[0] == "borrowed_result":
                return owner.record, count
            if behavior[0] == "mapping_result":
                return decoded.value, count
            if behavior[0] == "fresh_result":
                fresh = Record()
                fresh.word.value = decoded.word.value
                return fresh, count
            return decoded, count

    class Owner(SvObject):
        record = Record()
        queue = Queue[Record]()

    owner = Owner()
    owner.record.word.value = 12
    owner.queue.value = [owner.record]
    payload = owner.to_bytes()
    events.clear()

    def main():
        assert owner.to_bytes() == payload
        assert owner.from_bytes(payload) == len(payload)
        return owner.record.word.value, owner.queue[0].word.value

    def queue_roundtrip():
        data = owner.queue.to_bytes()
        assert owner.queue.from_bytes(data) == len(data)
        return owner.queue[0].word.value

    assert run_checked(main) == (12, 12)
    assert events == [("pack", 12), ("pack", 12), ("unpack", 12), ("unpack", 12)]
    assert run_checked(queue_roundtrip) == 12

    behavior[0] = "fresh_result"
    assert run_checked(main) == (12, 12)

    storage = MemoryExternalFieldStorage()
    storage.seed("record", Record(), owner.record)
    bound = bind_external_value(Record(), storage, "record")

    def external():
        bound.value = {"word": 9}
        return bound.value["word"]

    assert run_checked(external) == 9

    behavior[0] = "alias"
    with pytest.raises(CheckedAccessError, match="no variable carrier contract"):
        run_checked(main)
    assert saved == []

    behavior[0] = "borrowed_result"
    with pytest.raises(CheckedAccessError, match="returned a variable carrier"):
        run_checked(main)

    behavior[0] = "mapping_result"
    with pytest.raises(CheckedAccessError, match="new decoded structure and a byte count"):
        run_checked(main)

    behavior[0] = "valid"

    def ordinary_call():
        return owner.record.pack(owner.record)

    with pytest.raises(CheckedAccessError, match="no variable carrier contract"):
        run_checked(ordinary_call)


def test_checked_composite_copy_preserves_native_memo_and_rejects_escape():
    from copy import copy, deepcopy

    from svtypes import SvStruct

    behavior = ["valid"]
    copied_ids = []
    saved = []

    class Record(SvStruct):
        word = Int()

        def __copy__(self):
            cloned = super().__copy__()
            return cloned

        def __deepcopy__(self, memo):
            if behavior[0] == "save_memo":
                saved.append(memo)
            if behavior[0] == "old_result":
                return self
            cloned = super().__deepcopy__(memo)
            memo[id(self)] = cloned
            if behavior[0] == "save_clone":
                saved.append(cloned)
            copied_ids.append((id(self), id(memo)))
            return cloned

    class Owner(SvObject):
        record = Record()

        def __deepcopy__(self, memo):
            copied_ids.append((id(self), id(memo)))
            return super().__deepcopy__(memo)

    owner = Owner()
    owner.record.word.value = 9

    def main():
        values = deepcopy([owner, owner])
        assert values[0] is values[1] and values[0] is not owner
        values[0].record.word.value += 1
        return owner.record.word.value, values[1].record.word.value

    assert run_checked(main) == (9, 10)
    copied = dict(copied_ids)
    assert copied[id(owner)] == copied[id(owner.record)]

    def explicit_memo():
        return deepcopy(owner, memo={})

    with pytest.raises(CheckedAccessError, match="uses its own memo"):
        run_checked(explicit_memo)

    for bad in ("save_memo", "save_clone", "old_result"):
        behavior[0] = bad
        with pytest.raises(CheckedAccessError, match="variable carrier"):
            run_checked(main)
    assert saved == []

    behavior[0] = "valid"

    def ordinary_copy():
        return copy(owner.record)

    with pytest.raises(CheckedAccessError, match="no variable carrier contract"):
        run_checked(ordinary_copy)


def test_checked_copy_hook_cannot_return_an_empty_memo():
    from copy import deepcopy

    class Owner(SvObject):
        word = Int()

        def __deepcopy__(self, memo):
            return memo

    owner = Owner()

    def main():
        return deepcopy(owner)

    with pytest.raises(CheckedAccessError, match="returned a variable carrier"):
        run_checked(main)


def test_fixed_output_initializes_members_slices_and_nested_output_parameters():
    from svtypes import Array, Output, SvStruct, field

    class Pair(SvStruct):
        value = Int(77)
        other = Int(99)

    class Packet(SvStruct):
        pair = Pair()
        words = Array[Int, 3]()
        trailing = Queue[Int]()

    def assign(word: Output[Int]):
        word.value = 3

    def fill_pair(pair: Ref[Pair]):
        assign(field(pair, "value"))
        assert field(pair, "value").value == 3
        try:
            pair.other.value
        except CheckedAccessError as error:
            assert "packet.pair.other: Output has not been assigned" in str(error)
        else:
            raise AssertionError("an unwritten member was readable")
        pair.value["other"] = 5

    def fill(packet: Output[Packet]):
        fill_pair(packet.pair)
        packet.value["words"][::2] = [7, 9]
        packet.words[-2].value = 8
        packet.trailing.value = [11]
        assert packet.pair.value == {"value": 3, "other": 5}

    def main():
        packet = Packet()
        fill(packet)
        return packet.value

    assert run_checked(main) == {
        "pair": {"value": 3, "other": 5},
        "words": [7, 8, 9],
        "trailing": [11],
    }


def test_incomplete_fixed_output_prevents_all_copyout_with_member_path():
    from svtypes import Array, CopyoutError, Output, SvStruct

    class Packet(SvStruct):
        words = Array[Int, 2]()

    packet = Packet()
    packet.value = {"words": [1, 2]}
    other = Int(6)

    def fill(first: Output[Int], packet: Output[Packet]):
        first.value = 20
        packet.words[0].value = 40

    def main():
        fill(other, packet)

    with pytest.raises(CopyoutError) as caught:
        run_checked(main)
    assert caught.value.parameter == "packet"
    assert caught.value.phase == "preparation"
    assert caught.value.completed == ()
    assert "packet.words[1]: Output has not been assigned" in str(caught.value.__cause__)
    assert other.value == 6
    assert packet.value == {"words": [1, 2]}


def test_fixed_output_slice_prepares_before_writing_and_retains_borrowed_slot():
    from svtypes import Array, Output

    def replace_and_increment(item: Ref[Int], words: Ref[Array[Int, 2]]):
        words.value = [4, 5]
        item.value += 3

    def fill(words: Output[Array[Int, 2]]):
        try:
            words.value[:] = [7, "invalid"]
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("invalid slice element was accepted")
        try:
            words[0].value
        except CheckedAccessError as error:
            assert "words[0]: Output has not been assigned" in str(error)
        else:
            raise AssertionError("the valid prefix of an invalid slice was published")
        replace_and_increment(words[0], words)

    def main():
        words = Array[Int, 2]()
        fill(words)
        return words.value

    assert run_checked(main) == [7, 5]


def test_partial_initialization_of_external_output_publishes_only_actual_path():
    from svtypes import FieldIdentity, FieldOperation, FieldPath, MemoryExternalFieldStorage, Output, SvStruct

    class Pair(SvStruct):
        left = Int()
        right = Int()

    class Packet(SvStruct):
        pair = Pair()
        tag = Int()

    class Owner(SvObject):
        packet = Packet()

    class RecordingStorage(MemoryExternalFieldStorage):
        def __init__(self):
            super().__init__()
            self.operations = []

        def write(self, key, descriptor, path, operation, payload):
            self.operations.append((path, operation))
            return super().write(key, descriptor, path, operation, payload)

    original = Packet()
    original.value = {"pair": {"left": 1, "right": 2}, "tag": 9}
    storage = RecordingStorage()
    storage.seed("packet", Packet(), original)
    owner = Owner()
    owner.bind_external_storage(storage, {FieldIdentity(Owner, "packet"): "packet"})

    def fill(result: Output[Pair]):
        result.left.value = 4
        result.right.value = 7

    def main():
        fill(owner.packet.pair)
        return owner.packet.value

    assert run_checked(main) == {"pair": {"left": 4, "right": 7}, "tag": 9}
    assert storage.operations == [(FieldPath().member("pair"), FieldOperation.SET)]


def test_checked_errors_identify_original_return_and_assignment_expressions():
    class Owner(SvObject):
        word = Int(4)

    owner = Owner()

    def returned():
        return owner.word

    def invalid_assignment():
        owner.word.value = "bad"

    with pytest.raises(CheckedAccessError, match="returned a variable carrier") as caught:
        run_checked(returned)
    assert caught.value.__notes__[0] == f"{__file__}:{returned.__code__.co_firstlineno + 1}: return owner.word"

    with pytest.raises(TypeError) as caught:
        run_checked(invalid_assignment)
    assert caught.value.__notes__[0] == (
        f"{__file__}:{invalid_assignment.__code__.co_firstlineno + 1}: owner.word.value = 'bad'"
    )
    assert owner.word.value == 4


def test_checked_copyout_retains_return_site_and_preparation_cause():
    from svtypes import CopyoutError, Output

    actual = Int(3)

    def fill(output: Output[Int]):
        return None

    with pytest.raises(CopyoutError) as caught:
        run_checked(fill, actual)
    error = caught.value
    assert error.phase == "preparation"
    assert error.parameter == "output"
    assert error.completed == ()
    assert error.__notes__[0] == f"{__file__}:{fill.__code__.co_firstlineno + 1}: return None"
    assert isinstance(error.__cause__, CheckedAccessError)
    assert "output: Output has not been assigned" in str(error.__cause__)
    assert actual.value == 3


def test_checked_invalid_explicit_contract_retains_the_resolution_cause():
    def main(value: "Ref[MissingSvTypesAnnotation]"):
        raise AssertionError("the invalid signature must fail before execution")

    with pytest.raises(CheckedAccessError, match="cannot resolve the checked parameter annotation") as caught:
        run_checked(main, 0)
    assert isinstance(caught.value.__cause__, NameError)
    assert "MissingSvTypesAnnotation" in str(caught.value)
    assert caught.value.__notes__[0].startswith(f"{__file__}:{main.__code__.co_firstlineno}: ")


def test_checked_index_error_identifies_the_slot_selection():
    from svtypes import Array

    def main():
        data = Array[Int, 2]()
        data[6].value = 10

    with pytest.raises(IndexError) as caught:
        run_checked(main)
    assert caught.value.__notes__[0] == f"{__file__}:{main.__code__.co_firstlineno + 2}: data[6]"


def test_checked_ref_rejects_scalar_and_collection_types_with_the_same_encoding():
    from svtypes import AssocArray, encoding_descriptor

    class OtherInt(Int):
        pass

    def change(value: Ref[Int]):
        value.value = 8

    def change_sequence(value: Ref[Queue[Int]]):
        value[0].value = 8

    def change_mapping(value: Ref[AssocArray[Int, Int]]):
        value[0].value = 8

    scalar = OtherInt(3)
    sequence = Queue[OtherInt]()
    sequence.value = [3]
    mapping = AssocArray[OtherInt, Int]()
    mapping.value = {0: 3}

    assert encoding_descriptor(Int()) == encoding_descriptor(scalar)
    assert encoding_descriptor(Queue[Int]()) == encoding_descriptor(sequence)
    assert encoding_descriptor(AssocArray[Int, Int]()) == encoding_descriptor(mapping)
    for function, actual in ((change, scalar), (change_sequence, sequence), (change_mapping, mapping)):
        with pytest.raises(CheckedAccessError, match="Ref declaration does not match"):
            run_checked(function, actual)
    assert scalar.value == 3 and sequence.value == [3] and mapping.value == {0: 3}


def test_checked_higher_order_operations_apply_project_callback_contracts():
    from functools import partial, reduce
    from itertools import accumulate, groupby, starmap

    events = []

    def key(value: Int):
        events.append(value.value)
        return -value.value

    def add(left: Int, right: Int, delta=0):
        return left.value + right.value + delta

    def main():
        items = [2, 1, 3]
        assert sorted(items, key=key) == [3, 2, 1]
        assert items == [2, 1, 3]
        items.sort(key=key)
        assert items == [3, 2, 1]
        assert min(items, key=key) == 3
        assert max(items, key=key) == 1
        assert list(filter(None, [0, 1])) == [1]
        assert list(starmap(add, [(1, 2), (3, 4)])) == [3, 7]
        assert reduce(add, [1, 2, 3]) == 6
        assert list(accumulate([1, 2], func=add)) == [1, 3]
        assert [item for item in map(partial(add, delta=4), [1, 2], [3, 4])] == [8, 10]
        groups = groupby([1, 1, 2], key)
        return groups

    groups = run_checked(main)
    assert events == [2, 1, 3, 2, 1, 3, 3, 2, 1, 3, 2, 1]
    assert [(value, list(group)) for value, group in groups] == [(-1, [1, 1]), (-2, [2])]
    assert events[-3:] == [1, 1, 2]


def test_checked_higher_order_callbacks_reject_carrier_escape_eagerly_and_lazily():
    class Owner(SvObject):
        word = Int()

    owner = Owner()
    saved = []

    def leak(value):
        saved.append(owner.word)
        return value

    class Predicate:
        def __call__(self, value):
            return leak(value)

    def sort():
        return sorted([1], key=leak)

    def sort_in_place():
        data = [1]
        data.sort(key=leak)

    def eager_map():
        return list(map(leak, [1]))

    def lazy_map():
        return map(leak, [1])

    def lazy_filter():
        return filter(Predicate(), [1])

    for function in (sort, sort_in_place, eager_map):
        with pytest.raises(CheckedAccessError, match="no variable carrier contract") as caught:
            run_checked(function)
        assert any("leak" in site for site in caught.value.__notes__)
    for function in (lazy_map, lazy_filter):
        iterator = run_checked(function)
        with pytest.raises(CheckedAccessError, match="no variable carrier contract") as caught:
            next(iterator)
        assert any("leak" in site for site in caught.value.__notes__)
    assert saved == []
@pytest.mark.parametrize('operation', ['push_back', 'pop_front'])
def test_checked_queue_bound_mutator_cannot_be_saved_or_returned(operation):
    from svtypes import Queue, Int, run_checked, CheckedAccessError

    queue = Queue[Int]()
    queue.value = [1, 2]

    def save_push():
        saved = queue.push_back
        return saved

    def return_pop():
        return queue.pop_front

    with pytest.raises(CheckedAccessError, match='variable carrier'):
        run_checked({'push_back': save_push, 'pop_front': return_pop}[operation])
    assert queue.value == [1, 2]


def test_checked_queue_immediate_mutation_and_detached_value_method_still_work():
    from svtypes import Queue, Int, run_checked

    queue = Queue[Int]()
    queue.value = [1, 2]

    def main():
        queue.push_back(3)
        first = queue.pop_front()
        detached = queue.value
        append = detached.append
        append(4)
        return first, detached, queue.value

    assert run_checked(main) == (1, [2, 3, 4], [2, 3])

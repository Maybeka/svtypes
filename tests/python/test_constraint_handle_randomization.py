"""Recursive constrained-random regressions for ``rand`` class handles."""

from svtypes import (
    Array,
    AssocArray,
    Bit,
    DynArray,
    Object,
    Queue,
    SvObject,
    constraint,
    get_package,
    svobj,
)


handle_pkg = get_package("test_constraint_handle_randomization")


@svobj(registry=handle_pkg)
class HandleChild(SvObject):
    data = Bit(8)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.pre_count = 0
        self.post_count = 0

    @constraint
    def own_legal(self):
        self.data <= 10

    def pre_randomize(self):
        self.pre_count += 1

    def post_randomize(self):
        self.post_count += 1


@svobj(registry=handle_pkg)
class HandleParent(SvObject):
    child = Object("HandleChild", registry=handle_pkg, rand=True)
    other = Bit(8)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.pre_count = 0
        self.post_count = 0

    @constraint
    def cross_legal(self):
        self.other == self.child.data + 1

    def pre_randomize(self):
        self.pre_count += 1

    def post_randomize(self):
        self.post_count += 1


@svobj(registry=handle_pkg)
class PlainHandleParent(SvObject):
    child = Object("HandleChild", registry=handle_pkg)
    other = Bit(8)


@svobj(registry=handle_pkg)
class HandleNode(SvObject):
    data = Bit(4)
    next = Object("HandleNode", registry=handle_pkg, rand=True)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.pre_count = 0
        self.post_count = 0

    @constraint
    def own_legal(self):
        self.data <= 3

    def pre_randomize(self):
        self.pre_count += 1

    def post_randomize(self):
        self.post_count += 1


@svobj(registry=handle_pkg)
class SharedHandleParent(SvObject):
    left = Object("HandleNode", registry=handle_pkg, rand=True)
    right = Object("HandleNode", registry=handle_pkg, rand=True)

    @constraint
    def equal_aliases(self):
        self.left.data == self.right.data


@svobj(registry=handle_pkg)
class UnsatHandleParent(SvObject):
    child = Object("HandleChild", registry=handle_pkg, rand=True)

    @constraint
    def impossible(self):
        self.child.data == 1
        self.child.data == 2


@svobj(registry=handle_pkg)
class HandleRandcChild(SvObject):
    data = Bit(2, randc=True)


@svobj(registry=handle_pkg)
class HandleRandcParent(SvObject):
    child = Object("HandleRandcChild", registry=handle_pkg, rand=True)


@svobj(registry=handle_pkg)
class ContainerHandleParent(SvObject):
    fixed = Array(Object("HandleChild", registry=handle_pkg, rand=True), 1)
    dynamic = DynArray(Object("HandleChild", registry=handle_pkg, rand=True))
    queue = Queue(Object("HandleChild", registry=handle_pkg, rand=True))
    table = AssocArray(Bit(8), Object("HandleChild", registry=handle_pkg, rand=True))


def test_rand_handle_joins_parent_and_child_constraints_and_hooks():
    parent = HandleParent()
    child = HandleChild()
    parent.child = child

    assert parent.randomize()
    assert child.data.value <= 10
    assert parent.other.value == child.data.value + 1
    assert (parent.pre_count, parent.post_count) == (1, 1)
    assert (child.pre_count, child.post_count) == (1, 1)
    assert "rand HandleChild child;" in HandleParent.to_sv_obj()


def test_rand_handle_constraint_on_null_object_fails_without_allocating():
    parent = HandleParent()
    descriptor = HandleParent.child
    assert descriptor._cache_key not in parent.__dict__

    assert not parent.randomize()
    assert parent.svtypes_randomize_status.reason == "null_handle"
    assert parent.svtypes_randomize_status.state_path == "child"
    assert descriptor._cache_key not in parent.__dict__
    # SV calls pre_randomize for the root invocation even when solving fails.
    assert (parent.pre_count, parent.post_count) == (1, 0)


def test_plain_handle_is_not_recursively_randomized():
    parent = PlainHandleParent()
    child = HandleChild()
    child.data.value = 99
    parent.child = child

    assert parent.randomize()
    assert child.data.value == 99
    assert (child.pre_count, child.post_count) == (0, 0)
    assert "rand HandleChild child;" not in PlainHandleParent.to_sv_obj()


def test_shared_child_and_cycle_are_solved_once_by_canonical_identity():
    parent = SharedHandleParent()
    node = HandleNode()
    node.next = node
    parent.left = node
    parent.right = node

    assert parent.randomize()
    assert node.data.value <= 3
    assert parent.left is parent.right is node
    assert node.next is node
    assert (node.pre_count, node.post_count) == (1, 1)


def test_unsat_joint_handle_constraints_restore_child_value():
    parent = UnsatHandleParent()
    child = HandleChild()
    child.data.value = 9
    parent.child = child

    assert not parent.randomize()
    assert parent.svtypes_randomize_status.reason == "unsat"
    assert child.data.value == 9
    assert (child.pre_count, child.post_count) == (1, 0)


def test_child_rand_mode_is_respected_in_joint_solve():
    parent = HandleParent()
    child = HandleChild()
    child.data.value = 7
    child.data.rand_mode(0)
    parent.child = child

    assert parent.randomize()
    assert child.data.value == 7
    assert parent.other.value == 8


def test_child_constraint_mode_is_respected_in_joint_solve():
    parent = HandleParent()
    child = HandleChild()
    child.own_legal.constraint_mode(0)
    child.data.value = 99
    child.data.rand_mode(0)
    parent.child = child

    assert parent.randomize()
    assert child.data.value == 99
    assert parent.other.value == 100


def test_child_randc_state_belongs_to_the_child_across_joint_calls():
    parent = HandleRandcParent()
    child = HandleRandcChild()
    parent.child = child

    values = []
    for _ in range(4):
        assert parent.randomize()
        values.append(child.data.value)
    assert len(set(values)) == 4
    assert "data" in child._SvObject__svtypes_randc_state
    assert "child.data" not in parent._SvObject__svtypes_randc_state


def test_existing_rand_handle_elements_in_all_container_kinds_join_the_solve():
    parent = ContainerHandleParent()
    fixed = HandleChild()
    dynamic = HandleChild()
    queued = HandleChild()
    mapped = HandleChild()
    for child in (fixed, dynamic, queued, mapped):
        child.data.value = 99
    parent.fixed.value = [fixed]
    parent.dynamic.value = [dynamic]
    parent.queue.value = [queued]
    parent.table.value = {7: mapped}

    assert parent.randomize()
    for child in (fixed, dynamic, queued, mapped):
        assert child.data.value <= 10
        assert (child.pre_count, child.post_count) == (1, 1)

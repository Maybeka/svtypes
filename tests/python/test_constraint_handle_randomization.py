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
    rand_layer,
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


@svobj(registry=handle_pkg)
class ContainerCrossHandleParent(SvObject):
    dynamic = DynArray(Object("HandleChild", registry=handle_pkg, rand=True))
    target = Bit(8)

    @constraint
    def cross_legal(self):
        self.target == self.dynamic[0].data + 1


@svobj(registry=handle_pkg)
class ResizableHandleContainers(SvObject):
    dynamic = DynArray(Object("HandleChild", registry=handle_pkg, rand=True), max_length=4)
    queue = Queue(Object("HandleChild", registry=handle_pkg, rand=True), max_length=4)

    @constraint
    def sized(self):
        self.dynamic.size() == 3
        self.queue.size() == 3


@svobj(registry=handle_pkg)
class NullAfterHandleResize(SvObject):
    dynamic = DynArray(Object("HandleChild", registry=handle_pkg, rand=True), max_length=2)

    @constraint
    def invalid_access(self):
        self.dynamic.size() == 1
        self.dynamic[0].data <= 10


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


def test_container_handle_can_participate_in_a_parent_cross_constraint():
    parent = ContainerCrossHandleParent()
    child = HandleChild()
    parent.dynamic.value = [child]

    assert parent.randomize()
    assert child.data.value <= 10
    assert parent.target.value == child.data.value + 1


def test_null_container_handle_in_a_parent_constraint_reports_its_entry_path():
    parent = ContainerCrossHandleParent()
    parent.dynamic.value = [None]

    assert not parent.randomize()
    assert parent.svtypes_randomize_status.reason == "null_handle"
    assert parent.svtypes_randomize_status.state_path == "dynamic[0]"


def test_shared_container_handle_is_solved_once_by_identity():
    parent = ContainerHandleParent()
    child = HandleChild()
    parent.dynamic.value = [child]
    parent.queue.value = [child]

    assert parent.randomize()
    assert child.data.value <= 10
    assert (child.pre_count, child.post_count) == (1, 1)


def test_handle_dynamic_array_and_queue_resize_without_allocating_children():
    parent = ResizableHandleContainers()
    dynamic_child = HandleChild()
    queued_child = HandleChild()
    dynamic_child.data.value = 99
    queued_child.data.value = 99
    parent.dynamic.value = [dynamic_child]
    parent.queue.value = [queued_child]

    assert parent.randomize()
    assert parent.dynamic.value == [dynamic_child, None, None]
    assert parent.queue.value == [queued_child, None, None]
    assert dynamic_child.data.value <= 10
    assert queued_child.data.value <= 10
    assert (dynamic_child.pre_count, dynamic_child.post_count) == (1, 1)
    assert (queued_child.pre_count, queued_child.post_count) == (1, 1)


def test_rand_handle_dynamic_collection_renders_one_rand_qualifier():
    code = ResizableHandleContainers.to_sv_obj()

    assert "rand HandleChild dynamic [];" in code
    assert "rand HandleChild queue [$];" in code
    assert "rand rand HandleChild" not in code


def test_disabled_handle_collections_exclude_children_constraints_and_hooks():
    parent = ResizableHandleContainers()
    dynamic_child = HandleChild()
    queued_child = HandleChild()
    dynamic_child.data.value = 99
    queued_child.data.value = 99
    parent.dynamic.value = [dynamic_child, None, None]
    parent.queue.value = [queued_child, None, None]
    parent.dynamic.rand_mode(0)
    parent.queue.rand_mode(0)

    assert parent.randomize()
    assert dynamic_child.data.value == 99
    assert queued_child.data.value == 99
    assert (dynamic_child.pre_count, dynamic_child.post_count) == (0, 0)
    assert (queued_child.pre_count, queued_child.post_count) == (0, 0)


def test_layered_randomize_controls_existing_dynamic_handle_elements():
    class LayeredParent(SvObject):
        dynamic = DynArray(Object("HandleChild", registry=handle_pkg, rand=True), max_length=2)
        low = Bit(1)

        @constraint
        def low_legal(self):
            self.low == 1

        @rand_layer(10)
        def handles(self):
            self.dynamic

        @rand_layer(5)
        def lower(self):
            self.low
            self.low_legal

    parent = LayeredParent()
    child = HandleChild()
    child.data.value = 99
    parent.dynamic.value = [child]

    assert parent.layered_randomize()
    assert child.data.value <= 10
    # The higher-priority handle layer is the only batch that visits child.
    assert (child.pre_count, child.post_count) == (1, 1)


def test_shared_handle_remains_active_through_an_enabled_container_path():
    class SharedModeParent(SvObject):
        left = DynArray(Object("HandleChild", registry=handle_pkg, rand=True), max_length=1)
        right = Queue(Object("HandleChild", registry=handle_pkg, rand=True), max_length=1)

    parent = SharedModeParent()
    child = HandleChild()
    child.data.value = 99
    parent.left.value = [child]
    parent.right.value = [child]
    parent.left.rand_mode(0)

    assert parent.randomize()
    assert child.data.value <= 10
    assert (child.pre_count, child.post_count) == (1, 1)


def test_new_null_handle_from_size_randomization_reports_dereference():
    parent = NullAfterHandleResize()

    assert not parent.randomize()
    assert parent.svtypes_randomize_status.reason == "null_handle"
    assert parent.svtypes_randomize_status.state_path == "dynamic[0]"
    # A failed attempt restores the pre-call collection exactly.
    assert parent.dynamic.value == []

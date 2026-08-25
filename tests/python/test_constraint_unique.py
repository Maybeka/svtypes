from __future__ import annotations

import pytest

from svtypes import (
    Array,
    AssocArray,
    Bit,
    ConstraintSyntaxError,
    ConstraintTypeError,
    DynArray,
    Object,
    Queue,
    RandomContext,
    SvObject,
    constraint,
    get_package,
    svobj,
    unique,
)
from svtypes.constraint.eval import eval_bool
from svtypes.constraint.leaves import iter_class_leaves, leaf_unsigned, resolve_attr


class UniquePacket(SvObject):
    first = Bit(2)
    second = Bit(2)
    third = Bit(2)

    @constraint
    def legal(self):
        unique(self.first, self.second, self.third)


class UniqueArrayPacket(SvObject):
    words = Array(Bit(8), 4, rand=True)

    @constraint
    def legal(self):
        unique(self.words)


class UniqueDynPacket(SvObject):
    tag = Bit(8)
    data = DynArray(Bit(8), rand=True, max_length=4)

    @constraint
    def legal(self):
        self.data.size() == 3
        unique(self.tag, self.data)


class UniqueQueuePacket(SvObject):
    data = Queue(Bit(8), rand=True, max_length=4)

    @constraint
    def legal(self):
        self.data.size() == 2
        unique(self.data)


class UniqueEmptyPacket(SvObject):
    data = DynArray(Bit(8), rand=True, max_length=4)

    @constraint
    def legal(self):
        unique(self.data)


class UniquePigeonholePacket(SvObject):
    words = Array(Bit(1), 3, rand=True)

    @constraint
    def legal(self):
        unique(self.words)


unique_graph_pkg = get_package("test_constraint_unique_graph")


@svobj(registry=unique_graph_pkg)
class UniqueGraphChild(SvObject):
    words = Array(Bit(1), 2, rand=True)

    @constraint
    def legal(self):
        unique(self.words)


@svobj(registry=unique_graph_pkg)
class UniqueGraphParent(SvObject):
    child = Object("UniqueGraphChild", registry=unique_graph_pkg, rand=True)


@svobj(registry=unique_graph_pkg)
class UniqueGraphDynamicChild(SvObject):
    data = DynArray(Bit(3), rand=True, max_length=4)

    @constraint
    def legal(self):
        self.data.size() == 3
        unique(self.data)


@svobj(registry=unique_graph_pkg)
class UniqueGraphDynamicParent(SvObject):
    child = Object("UniqueGraphDynamicChild", registry=unique_graph_pkg, rand=True)


@svobj(registry=unique_graph_pkg)
class UniqueGraphMixedDynamicChild(SvObject):
    tag = Bit(3)
    data = DynArray(Bit(3), rand=True, max_length=4)

    @constraint
    def legal(self):
        self.data.size() == 3
        unique(self.tag, self.data)


@svobj(registry=unique_graph_pkg)
class UniqueGraphQueueChild(SvObject):
    data = Queue(Bit(3), rand=True, max_length=4)

    @constraint
    def legal(self):
        self.data.size() == 3
        unique(self.data)


@svobj(registry=unique_graph_pkg)
class UniqueGraphCollectionParent(SvObject):
    mixed = Object("UniqueGraphMixedDynamicChild", registry=unique_graph_pkg, rand=True)
    queue = Object("UniqueGraphQueueChild", registry=unique_graph_pkg, rand=True)


def test_unique_is_a_hard_scalar_constraint_in_python_and_sv():
    obj = UniquePacket()
    ir = UniquePacket._SvObject__svtypes_constraint_irs["legal"]
    assert ir.predicates[0].op == "unique"
    assert "unique {first, second, third};" in UniquePacket.to_sv_obj()
    with RandomContext(seed=501):
        for _ in range(32):
            assert obj.randomize() is True
            assert len({obj.first.value, obj.second.value, obj.third.value}) == 3
            env = {}
            widths = {}
            for path, desc, _declared in iter_class_leaves(UniquePacket):
                target = resolve_attr(obj, path)
                env[path] = leaf_unsigned(target, target.value)
                widths[path] = (desc.width, bool(desc.signed))
            assert eval_bool(ir.predicates[0], env, widths)


def test_unique_requires_two_scalars_or_one_collection():
    with pytest.raises(ConstraintSyntaxError, match="at least one"):
        class EmptyArgs(SvObject):
            first = Bit(2)

            @constraint
            def legal(self):
                unique()

    with pytest.raises(ConstraintTypeError, match="at least two scalar"):
        class OneScalar(SvObject):
            first = Bit(2)

            @constraint
            def legal(self):
                unique(self.first)


def test_unique_mixed_widths_uses_sv_equality_sizing():
    class Mixed(SvObject):
        narrow = Bit(2)
        wide = Bit(3)

        @constraint
        def legal(self):
            unique(self.narrow, self.wide)

    obj = Mixed()
    with RandomContext(seed=502):
        for _ in range(16):
            assert obj.randomize() is True
            assert obj.narrow.value != obj.wide.value


def test_unique_fixed_array_renders_native_sv_and_randomizes():
    ir = UniqueArrayPacket._SvObject__svtypes_constraint_irs["legal"]
    assert ir.predicates[0].op == "unique"
    assert ir.predicates[0].args[0].hint == "unpacked"
    assert "unique {words};" in UniqueArrayPacket.to_sv_obj()
    obj = UniqueArrayPacket()
    with RandomContext(seed=510):
        for _ in range(16):
            assert obj.randomize() is True
            assert len({word.value for word in obj.words}) == 4


def test_unique_dynamic_array_mixes_with_scalars_after_size():
    assert "unique {tag, data};" in UniqueDynPacket.to_sv_obj()
    obj = UniqueDynPacket()
    with RandomContext(seed=511):
        for _ in range(16):
            assert obj.randomize() is True
            assert len(obj.data) == 3
            values = [obj.tag.value, *[item.value for item in obj.data]]
            assert len(set(values)) == 4


def test_unique_queue_uses_current_elements():
    assert "unique {data};" in UniqueQueuePacket.to_sv_obj()
    obj = UniqueQueuePacket()
    with RandomContext(seed=512):
        for _ in range(8):
            assert obj.randomize() is True
            assert len(obj.data) == 2
            assert obj.data[0].value != obj.data[1].value


def test_unique_empty_dynamic_array_is_vacuously_true():
    obj = UniqueEmptyPacket()
    with RandomContext(seed=513):
        assert obj.randomize() is True
        assert len(obj.data) == 0


def test_unique_pigeonhole_array_is_unsat():
    obj = UniquePigeonholePacket()
    with RandomContext(seed=514):
        assert obj.randomize() is False


def test_unique_collection_in_rand_object_is_constrained_by_parent_randomize():
    obj = UniqueGraphParent()
    child = obj.child
    with RandomContext(seed=515):
        for _ in range(16):
            assert obj.randomize() is True
            assert child.words[0].value != child.words[1].value


def test_dynamic_unique_in_rand_object_is_constrained_by_parent_randomize():
    obj = UniqueGraphDynamicParent()
    child = obj.child
    with RandomContext(seed=516):
        for _ in range(16):
            assert obj.randomize() is True
            assert len(child.data) == 3
            assert len({item.value for item in child.data}) == 3


def test_mixed_dynamic_and_queue_unique_in_rand_objects_are_constrained_by_parent_randomize():
    obj = UniqueGraphCollectionParent()
    mixed = obj.mixed
    queue = obj.queue
    with RandomContext(seed=517):
        for _ in range(16):
            assert obj.randomize() is True
            mixed_values = [mixed.tag.value, *[item.value for item in mixed.data]]
            assert len(mixed.data) == 3
            assert len(set(mixed_values)) == 4
            assert len(queue.data) == 3
            assert len({item.value for item in queue.data}) == 3


def test_unique_rejects_associative_arrays_and_nested_dynamic_collections():
    with pytest.raises(ConstraintTypeError, match="associative arrays"):
        class AssocUnique(SvObject):
            table = AssocArray(Bit(8), Bit(8), rand=True)

            @constraint
            def legal(self):
                unique(self.table)

    with pytest.raises(ConstraintTypeError, match="nested dynamic"):
        class NestedDyn(SvObject):
            data = DynArray(DynArray(Bit(8), max_length=2), rand=True, max_length=2)

            @constraint
            def legal(self):
                unique(self.data)

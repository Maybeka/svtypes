"""Dynamic-array and queue constrained-random regressions."""

import pytest

from svtypes import AssocArray, Bit, ConstraintUnsupportedError, DeclarationError, DynArray, Queue, String, SvObject, constraint, rand_layer
from svtypes.constraint.modes import path_prefixes


class DynamicPacket(SvObject):
    length = Bit(4)
    data = DynArray(Bit(8), rand=True, max_length=8)

    @constraint
    def legal(self):
        self.data.size() == self.length
        self.length <= 4
        for i in range(self.data.size()):
            self.data[i] == i + 1


class QueuePacket(SvObject):
    data = Queue(Bit(8), rand=True, max_length=8)

    @constraint
    def legal(self):
        self.data.size() == 3
        for i in range(self.data.size()):
            self.data[i] == i


class ExistingDynamicPacket(SvObject):
    data = DynArray(Bit(8), rand=True, max_length=8)

    @constraint
    def legal(self):
        for i in range(self.data.size()):
            self.data[i] == 7


class UnsatDynamicPacket(SvObject):
    data = DynArray(Bit(8), rand=True, max_length=8)

    @constraint
    def legal(self):
        self.data.size() == 2
        for i in range(self.data.size()):
            self.data[i] == 1
            self.data[i] == 2


class EmptyOnlyDynamicPacket(SvObject):
    data = DynArray(Bit(8), rand=True, max_length=4)

    @constraint
    def legal(self):
        self.data.size() <= 4
        for i in range(self.data.size()):
            self.data[i] != self.data[i]


class AssocPacket(SvObject):
    table = AssocArray(Bit(8), Bit(8), rand=True)

    @constraint
    def legal(self):
        for key in self.table:
            self.table[key] == key + 1


class StringAssocPacket(SvObject):
    table = AssocArray(String(), Bit(8), rand=True)

    @constraint
    def legal(self):
        for key in self.table:
            self.table[key] == 5


def test_dynamic_array_size_and_foreach_render_as_sv():
    source = DynamicPacket.to_sv_obj()
    assert "data.size()" in source
    assert "foreach (data[i])" in source
    assert "i < data.size()" not in source


def test_dynamic_array_randomization_solves_size_before_elements():
    packet = DynamicPacket()
    assert packet.randomize()
    assert packet.data.size() == packet.length.value
    assert packet.data.size() <= 4
    assert packet.data.value == list(range(1, packet.data.size() + 1))


def test_queue_randomization_resizes_at_back_and_solves_elements():
    packet = QueuePacket()
    packet.data.value = [99]
    assert packet.randomize()
    assert packet.data.value == [0, 1, 2]


def test_unconstrained_dynamic_size_is_preserved():
    packet = ExistingDynamicPacket()
    packet.data.value = [1, 2]
    assert packet.randomize()
    assert packet.data.value == [7, 7]


def test_dynamic_randomize_failure_restores_original_container():
    packet = UnsatDynamicPacket()
    packet.data.value = [9]
    assert not packet.randomize()
    assert packet.data.value == [9]


def test_dynamic_size_search_retries_when_nonempty_elements_are_unsat():
    packet = EmptyOnlyDynamicPacket()
    assert packet.randomize()
    assert packet.data.size() == 0


def test_associative_array_randomizes_existing_values_by_python_key_iteration():
    packet = AssocPacket()
    packet.table.value = {1: 99, 7: 99}
    assert packet.randomize()
    assert packet.table.value == {1: 2, 7: 8}
    source = AssocPacket.to_sv_obj()
    assert "foreach (table[key])" in source


def test_associative_array_string_keys_randomize_existing_values():
    packet = StringAssocPacket()
    packet.table.value = {"a": 99, "brace}key": 99}
    assert packet.randomize()
    assert packet.table.value == {"a": 5, "brace}key": 5}


def test_associative_array_mode_path_accepts_brace_in_string_key():
    assert path_prefixes('table{@"brace}key"}') == ["table", 'table{@"brace}key"}']


def test_associative_array_size_is_not_a_random_constraint_variable():
    with pytest.raises(ConstraintUnsupportedError, match="associative-array size"):
        class BadAssocSize(SvObject):
            data = AssocArray(Bit(8), Bit(8))

            @constraint
            def legal(self):
                self.data.size() == 1


def test_dynamic_collection_layer_controls_existing_elements_individually():
    class LayeredDynamic(SvObject):
        data = DynArray(Bit(8), rand=True, max_length=4)

        @constraint
        def legal(self):
            self.data.size() == 2
            for i in range(self.data.size()):
                self.data[i] == i + 3

        @rand_layer(1)
        def top(self):
            self.data
            self.legal

    packet = LayeredDynamic()
    packet.data.value = [3]
    assert packet.data[0].rand_mode() == 1
    packet.data[0].rand_mode(0)
    assert packet.layered_randomize()
    assert packet.data.size() == 2
    assert packet.data[0].value == 3
    assert packet.data[0].rand_mode() == 0
    assert packet.data[1].rand_mode() == 1


def test_queue_layer_controls_existing_elements_individually():
    class LayeredQueue(SvObject):
        data = Queue(Bit(8), rand=True, max_length=4)

        @constraint
        def legal(self):
            self.data.size() == 2
            for i in range(self.data.size()):
                self.data[i] == i + 3

        @rand_layer(1)
        def top(self):
            self.data
            self.legal

    packet = LayeredQueue()
    packet.data.value = [3]
    packet.data[0].rand_mode(0)
    assert packet.layered_randomize()
    assert packet.data.size() == 2
    assert packet.data[0].value == 3
    assert packet.data[0].rand_mode() == 0
    assert packet.data[1].rand_mode() == 1

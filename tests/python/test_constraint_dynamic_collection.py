"""Dynamic-array and queue constrained-random regressions."""

import pytest

from svtypes import AssocArray, Bit, ConstraintUnsupportedError, DeclarationError, DynArray, Queue, SvObject, constraint, rand_layer


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


def test_dynamic_array_size_and_foreach_render_as_sv():
    source = DynamicPacket.to_sv_obj()
    assert "data.size()" in source
    assert "foreach (data[i])" in source


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


def test_associative_array_size_is_not_a_random_constraint_variable():
    with pytest.raises(ConstraintUnsupportedError, match="associative-array size"):
        class BadAssocSize(SvObject):
            data = AssocArray(Bit(8), Bit(8))

            @constraint
            def legal(self):
                self.data.size() == 1


def test_dynamic_collection_cannot_enter_a_rand_layer():
    with pytest.raises(DeclarationError, match="cannot participate in rand_layer"):
        class LayeredDynamic(SvObject):
            data = DynArray(Bit(8), rand=True)

            @rand_layer(1)
            def top(self):
                self.data

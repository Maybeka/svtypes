"""Foreach arithmetic indices retain expressions until dynamic expansion."""

import pytest

from svtypes import Array, Bit, ConstraintUnsupportedError, DynArray, Object, Queue, RandomContext, SvObject, constraint, get_package, svobj

index_package = get_package("arithmetic_index_randomization")


class AdjacentArray(SvObject):
    data = DynArray[Bit[8]](rand=True, max_length=16)

    @constraint
    def legal(self):
        self.data.size() == 8
        for i in range(self.data.size()):
            if i == 0:
                self.data[i] == 1
            if i > 0:
                self.data[i] == self.data[i - 1] + 1


class AdjacentQueue(SvObject):
    data = Queue[Bit[8]](rand=True, max_length=16)

    @constraint
    def legal(self):
        self.data.size() == 8
        for i in range(self.data.size()):
            if i == 0:
                self.data[i] == 1
            if i + 1 < self.data.size():
                self.data[i + 1] == self.data[i] + 1


class AdjacentFixed(SvObject):
    data = Array[Bit[8], 8](rand=True)

    @constraint
    def legal(self):
        for i in range(8):
            if i == 0:
                self.data[i] == 1
            if i > 0:
                self.data[i] == self.data[i - 1] + 1


class CompoundFixed(SvObject):
    data = Array[Bit[8], 8](rand=True)

    @constraint
    def legal(self):
        for i in range(8):
            self.data[-(-i)] == i + 1
            if i < 4:
                self.data[i * 2 + 1] == i * 2 + 2


def test_fixed_compound_indices_and_values_use_sv_integer_width():
    obj = CompoundFixed()
    assert obj.randomize()
    assert obj.data.value == list(range(1, 9))


@svobj(registry=index_package)
class IndexedChild(AdjacentArray):
    pass


class IndexedParent(SvObject):
    child = Object["IndexedChild"](registry=index_package, rand=True)
    tag = Bit[8]()

    @constraint
    def related(self):
        self.tag == self.child.data[7]


def test_arithmetic_index_child_is_in_parent_joint_solve():
    obj = IndexedParent()
    obj.child = IndexedChild()
    identity = obj.child
    with RandomContext(seed=607):
        for _ in range(5):
            assert obj.randomize()
            assert obj.child is identity
            assert obj.child.data.value == list(range(1, 9))
            assert obj.tag.value == 8


def test_dynamic_arithmetic_indices_empty_singleton_and_compound_state_guard():
    class Packet(SvObject):
        count = Bit[5](rand=False)
        enabled = Bit[1](rand=False)
        data = DynArray[Bit[8]](rand=True, max_length=16)

        @constraint
        def legal(self):
            self.data.size() == self.count
            for i in range(self.data.size()):
                if i == 0:
                    self.data[i] == 1
                if i > 0 and self.enabled == 1:
                    self.data[i] == self.data[i - 1] + 1

    obj = Packet()
    obj.enabled.value = 1
    for count in (0, 1, 8, 0):
        obj.count.value = count
        assert obj.randomize()
        assert obj.data.value == list(range(1, count + 1))


def test_multiply_and_unary_foreach_indices():
    class Packet(SvObject):
        data = DynArray[Bit[8]](rand=True, max_length=8)

        @constraint
        def legal(self):
            self.data.size() == 8
            for i in range(self.data.size()):
                self.data[-(-i)] == i + 1
                if i < 4:
                    self.data[i * 2 + 1] == i * 2 + 2

    obj = Packet()
    assert obj.randomize()
    assert obj.data.value == list(range(1, 9))


def test_state_dependent_index_reports_declaration_error():
    with pytest.raises(ConstraintUnsupportedError, match="cannot depend on random or state fields"):
        class Packet(SvObject):
            offset = Bit[4](rand=False)
            data = DynArray[Bit[8]](rand=True, max_length=8)

            @constraint
            def legal(self):
                self.data.size() == 8
                for i in range(self.data.size()):
                    self.data[i + self.offset] == 1


@pytest.mark.remote_sv
def test_remote_arithmetic_index_collections():
    import os
    from pathlib import Path
    from test_constraint_sv import _run_remote

    out = Path(__file__).resolve().parents[2] / ".tmp" / "arithmetic_index_sv"
    out.mkdir(parents=True, exist_ok=True)
    (out / "definitions.sv").write_text("\n".join([
        "package arithmetic_index_sv;", "import svtypes_pkg::*;",
        *(cls.to_sv_obj(level=1) for cls in (AdjacentArray, AdjacentQueue, AdjacentFixed, CompoundFixed)),
        "endpackage",
    ]))
    (out / "tb.sv").write_text("""module tb;
  import arithmetic_index_sv::*;
  initial begin
    AdjacentArray a;
    AdjacentQueue q;
    AdjacentFixed f;
    CompoundFixed c;
    a = new(); q = new(); f = new(); c = new();
    repeat (16) begin
      if (!a.randomize() || !q.randomize() || !f.randomize() || !c.randomize())
        $fatal(1, "arithmetic index randomize failed");
      if (a.data.size() != 8 || q.data.size() != 8)
        $fatal(1, "arithmetic index size mismatch");
      foreach (a.data[i]) if (a.data[i] != i + 1) $fatal(1, "array recurrence mismatch");
      foreach (q.data[i]) if (q.data[i] != i + 1) $fatal(1, "queue recurrence mismatch");
      foreach (f.data[i]) if (f.data[i] != i + 1) $fatal(1, "fixed recurrence mismatch");
      foreach (c.data[i]) if (c.data[i] != i + 1) $fatal(1, "fixed arithmetic mismatch");
    end
    $display("SVTYPES_ARITHMETIC_INDEX_PASS");
    $finish;
  end
endmodule
""")
    result = _run_remote(os.environ["SVTYPES_REMOTE_SV_HOST"],
        "bash -ilc 'cd $SVTYPES_REMOTE_SV_ROOT/.tmp/arithmetic_index_sv && "
        "$SVTYPES_REMOTE_SV_RUNNER compile definitions.sv tb.sv && $SVTYPES_REMOTE_SV_RUNNER run'")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SVTYPES_ARITHMETIC_INDEX_PASS" in result.stdout + result.stderr


@pytest.mark.parametrize("packet_type", [AdjacentArray, AdjacentQueue, AdjacentFixed])
def test_dynamic_adjacent_arithmetic_indices_randomize_and_render(packet_type):
    obj = packet_type()
    with RandomContext(seed=601):
        for _ in range(5):
            assert obj.randomize()
            assert obj.data.value == list(range(1, 9))
    text = packet_type.to_sv_obj()
    assert "?0" not in text
    if packet_type is not AdjacentFixed:
        assert "foreach (data[i])" in text
        assert "data[(" in text


def test_index_expression_affects_constraint_digest():
    from svtypes.constraint.ir import Expr, bv

    index = Expr("loopvar", ("i",), bv(32, True))
    one = Expr("int", (1,), bv(32, True))
    two = Expr("int", (2,), bv(32, True))
    left = Expr("indexed_field", ("data[?0]", Expr("sub", (index, one), bv(32, True))), bv(8))
    right = Expr("indexed_field", ("data[?0]", Expr("sub", (index, two), bv(32, True))), bv(8))
    assert left.to_stable() != right.to_stable()

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from svtypes import (
    Array,
    AssocArray,
    Bit,
    DynArray,
    Enum,
    Int,
    Logic,
    Object,
    ParamRef,
    Parameter,
    RandomContext,
    Reg,
    SvObject,
    SvStruct,
    Queue,
    constraint,
    dist,
    rand_layer,
    runtime_root,
    soft,
    solve_before,
    unique,
    get_package,
    svobj,
)
from svtypes.constraint.eval import eval_bool
from svtypes.constraint.leaves import iter_class_leaves, leaf_unsigned, resolve_attr


target_handle_pkg = get_package("target_handle_randomization")


@svobj(registry=target_handle_pkg)
class targetHandleChild(SvObject):
    data = Bit(8)

    @constraint
    def own_legal(self):
        self.data <= 10


@svobj(registry=target_handle_pkg)
class targetHandleParent(SvObject):
    child = Object("targetHandleChild", registry=target_handle_pkg, rand=True)
    other = Bit(8)

    @constraint
    def cross_legal(self):
        self.other == self.child.data + 1


@svobj(registry=target_handle_pkg)
class targetContainerHandleParent(SvObject):
    fixed = Array(Object("targetHandleChild", registry=target_handle_pkg, rand=True), 1)
    dynamic = DynArray(Object("targetHandleChild", registry=target_handle_pkg, rand=True))
    queue = Queue(Object("targetHandleChild", registry=target_handle_pkg, rand=True))
    table = AssocArray(Bit(8), Object("targetHandleChild", registry=target_handle_pkg, rand=True))
    target = Bit(8)

    @constraint
    def cross_legal(self):
        self.target == self.dynamic[0].data + 1


class CPacket(SvObject):
    addr = Bit(32)
    length = Bit(16)
    burst = Bit(1)
    data = Logic(32)
    legacy = Reg(8)
    signed_data = Logic(8, signed=True)
    limit = Bit(32, rand=False)

    @constraint
    def legal(self):
        0x1000 <= self.addr
        self.addr < self.limit
        self.addr % 64 == 0
        self.data & 1 == 0
        if self.burst:
            1 <= self.length
            self.length <= 256
        else:
            self.length == 1


class DiffMode(Enum, width=8, signed=False):
    READ = 0
    WRITE = 1
    IDLE = 2


class DiffHeader(SvStruct):
    addr = Bit(16)
    extra = Bit(8)


class DistPacket(SvObject):
    choice = Bit(4)
    base = Bit(4, rand=False)
    weight = Bit(4, rand=False)

    @constraint
    def legal(self):
        (self.choice + self.base) @ dist[
            5 @ 3,
            (8, 10) / (self.weight + 1),
            12,
        ]


class RandcPacket(SvObject):
    choice = Bit(2, randc=True)
    limit = Bit(2, rand=False)

    @constraint
    def legal(self):
        self.choice < self.limit


class UniquePacket(SvObject):
    first = Bit(2)
    second = Bit(2)
    third = Bit(2)

    @constraint
    def legal(self):
        unique(self.first, self.second, self.third)


class SoftPacket(SvObject):
    choice = Bit(32)

    @constraint
    def bounds(self):
        self.choice < 3

    @constraint
    def preferred(self):
        soft(self.choice == 2)


class SoftHardConflictPacket(SvObject):
    choice = Bit(32)

    @constraint
    def legal(self):
        self.choice == 0
        soft(self.choice == 1)


class SoftOrderPacket(SvObject):
    choice = Bit(32)

    @constraint
    def legal(self):
        self.choice < 3
        soft(self.choice == 0)
        soft(self.choice == 1)


class SoftBasePacket(SvObject):
    choice = Bit(32)

    @constraint
    def base_preference(self):
        soft(self.choice == 1)


class SoftDerivedPacket(SoftBasePacket):
    @constraint
    def derived_preference(self):
        soft(self.choice == 2)

    @constraint
    def bounds(self):
        self.choice < 3


class SolveBeforePacket(SvObject):
    first = Bit(2)
    second = Bit(2)

    @constraint
    def legal(self):
        solve_before(self.first, self.second)
        self.first < self.second


class DynamicCollectionPacket(SvObject):
    length = Bit(4)
    data = DynArray(Bit(8), rand=True, max_length=8)

    @constraint
    def legal(self):
        self.data.size() == self.length
        self.length <= 4
        for i in range(self.data.size()):
            self.data[i] == i + 1


class QueueCollectionPacket(SvObject):
    data = Queue(Bit(8), rand=True, max_length=8)

    @constraint
    def legal(self):
        self.data.size() == 3
        for i in range(self.data.size()):
            self.data[i] == i


class EmptyOnlyCollectionPacket(SvObject):
    data = DynArray(Bit(8), rand=True, max_length=4)

    @constraint
    def legal(self):
        self.data.size() <= 4
        for i in range(self.data.size()):
            self.data[i] != self.data[i]


class AssocCollectionPacket(SvObject):
    table = AssocArray(Bit(8), Bit(8), rand=True)

    @constraint
    def legal(self):
        for key in self.table:
            self.table[key] == key + 1


class DiffPacket(SvObject):
    addr = Bit(32)
    data = Logic(32)
    signed_data = Logic(8, signed=True)
    flag = Bit(8)
    mode = DiffMode()
    header = DiffHeader(rand=True)
    words = Array(Bit(8), 4)
    limit = Bit(32, rand=False)

    @constraint
    def legal(self):
        0x1000 <= self.addr
        self.addr < self.limit
        self.addr % 64 == 0
        self.data & 1 == 0
        self.signed_data != 0
        self.mode in (DiffMode.READ, DiffMode.WRITE)
        self.header.addr % 4 == 0
        for i in range(4):
            self.words[i] != 0
        self.flag == (1 if self.mode == DiffMode.READ else 2)


class DiffChild(DiffPacket):
    @constraint
    def legal(self):
        self.addr == 0x1000

    @constraint
    def extra(self):
        self.header.extra == 3


def test_sv_constraint_renderer_contains_pass_markers():
    text = CPacket.to_sv_obj()
    assert "rand logic [31:0] data;" in text
    assert "rand reg [7:0] legacy;" in text
    assert "rand logic signed [7:0] signed_data;" in text
    assert "constraint legal {" in text
    assert "32'h1000" in text
    assert "32'd4096" not in text
    legal = text.split("constraint legal {", 1)[1].split("static function", 1)[0]
    assert "if (" in legal
    assert "} else {" in legal
    assert "((!((burst" not in legal
    diff = DiffPacket.to_sv_obj()
    assert "constraint legal {" in diff
    assert "header.addr" in diff
    assert "words[0]" in diff
    assert "32'h1000" in diff
    assert "(mode inside {READ, WRITE})" in diff
    assert "(mode == READ)" in diff
    assert "(mode == 8'd0)" not in diff
    child = DiffChild.to_sv_obj()
    assert "extends DiffPacket" in child
    assert "constraint extra {" in child
    assert "words[0].rand_mode()" in diff
    assert "words.rand_mode()" not in diff
    assert "header.rand_mode()" in diff
    pkg = (runtime_root() / "sv" / "svtypes_pkg.sv").read_text()
    assert "svtypes.constraint-ir.v1" in pkg
    assert "svtypes.constraint-sample.v1" in pkg
    dynamic = DynamicCollectionPacket.to_sv_obj()
    assert "data.rand_mode()" not in dynamic


def _assert_enabled_predicates(obj):
    modes = obj._SvObject__svtypes_constraint_modes
    env = {}
    widths = {}
    for path, desc, _declared in iter_class_leaves(type(obj)):
        target = resolve_attr(obj, path)
        env[path] = leaf_unsigned(target, target.value)
        widths[path] = (desc.width, bool(desc.signed))
    for name, ir in type(obj)._SvObject__svtypes_constraint_irs.items():
        if modes.get(name, 1) != 1:
            continue
        for pred in ir.predicates:
            assert eval_bool(pred, env, widths), (type(obj).__name__, name, pred.to_stable())


def test_python_differential_sat_satisfies_generated_predicates():
    pkt = CPacket()
    pkt.limit.value = 0x2000
    diff = DiffPacket()
    diff.limit.value = 0x2000
    child = DiffChild()
    child.limit.value = 0x2000
    ctx = RandomContext(seed=21)
    with ctx:
        for _ in range(8):
            assert pkt.randomize() is True
            _assert_enabled_predicates(pkt)
            assert pkt.data.value.x_mask == 0 and pkt.data.value.z_mask == 0
            assert pkt.signed_data.value.x_mask == 0
            assert diff.randomize() is True
            _assert_enabled_predicates(diff)
            assert int(diff.mode.value) in (0, 1)
            assert diff.flag.value == (1 if int(diff.mode.value) == 0 else 2)
            assert all(word.value != 0 for word in diff.words)
            assert child.randomize() is True
            _assert_enabled_predicates(child)
            assert child.addr.value == 0x1000
            assert child.header.extra.value == 3


def _write_sv_sim(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    package = "\n".join(
        [
            "package constraint_sv_test;",
            "  import svtypes_pkg::*;",
            DiffMode.to_sv_enum(level=1),
            DiffHeader.to_sv_obj(level=1),
            CPacket.to_sv_obj(level=1),
            DiffPacket.to_sv_obj(level=1),
            DiffChild.to_sv_obj(level=1),
            "endpackage",
            "",
        ]
    )
    (out / "constraint_sv_test.sv").write_text(package)
    (out / "tb.sv").write_text(
        """`timescale 1ns/1ps
module tb;
  import svtypes_pkg::*;
  import constraint_sv_test::*;
  integer i;
  initial begin
    CPacket p;
    DiffPacket d;
    DiffChild c;
    p = new();
    d = new();
    c = new();
    p.limit = 32'h2000;
    d.limit = 32'h2000;
    c.limit = 32'h2000;
    for (i = 0; i < 8; i++) begin
      if (!p.randomize()) $fatal(1, "CPacket unsat");
      if (p.addr < 32'h1000) $fatal(1, "CPacket addr lo");
      if (!(p.addr < p.limit)) $fatal(1, "CPacket addr hi");
      if ((p.addr % 64) != 0) $fatal(1, "CPacket align");
      if (p.data[0]) $fatal(1, "CPacket data lsb");
      if (p.burst) begin
        if (!(p.length >= 16'd1 && p.length <= 16'd256)) $fatal(1, "CPacket burst len");
      end else begin
        if (p.length != 16'd1) $fatal(1, "CPacket single len");
      end
      if (!d.randomize()) $fatal(1, "DiffPacket unsat");
      if (d.addr < 32'h1000) $fatal(1, "Diff addr lo");
      if (!(d.addr < d.limit)) $fatal(1, "Diff addr hi");
      if ((d.addr % 64) != 0) $fatal(1, "Diff align");
      if (d.data[0]) $fatal(1, "Diff data lsb");
      if (d.signed_data == 0) $fatal(1, "Diff signed_data");
      if (!(d.mode == READ || d.mode == WRITE)) $fatal(1, "Diff mode");
      if ((d.header.addr % 16'd4) != 0) $fatal(1, "Diff header align");
      if (d.words[0] == 0 || d.words[1] == 0 || d.words[2] == 0 || d.words[3] == 0)
        $fatal(1, "Diff words");
      if (d.mode == READ) begin
        if (d.flag != 8'd1) $fatal(1, "Diff flag read");
      end else begin
        if (d.flag != 8'd2) $fatal(1, "Diff flag write");
      end
      if (!c.randomize()) $fatal(1, "DiffChild unsat");
      if (c.addr != 32'h1000) $fatal(1, "DiffChild addr");
      if (c.header.extra != 8'd3) $fatal(1, "DiffChild extra");
    end
    $display("SVTYPES_CONSTRAINT_PASS");
    $finish;
  end
endmodule
"""
    )


@pytest.mark.remote_target
def test_remote_target_constraint_simulation():
    host = os.environ.get("SVTYPES_target_HOST", "remote-target")
    reachable = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host, "true"],
        capture_output=True,
        text=True,
    )
    if reachable.returncode != 0:
        pytest.skip(f"remote SystemVerilog target host {host} is not reachable")
    out = Path(__file__).resolve().parents[2] / ".tmp" / "constraint_sv"
    if out.exists():
        shutil.rmtree(out)
    _write_sv_sim(out)
    remote = (
        "bash -ilc '"
        "cd /path/to/svtypes/.tmp/constraint_sv && "
        "rm -rf simv csrc simv.daidir && "
        "target -full64 -sverilog -timescale=1ns/1ps "
        "+incdir+/path/to/svtypes/svtypes_runtime/sv "
        "/path/to/svtypes/svtypes_runtime/sv/svtypes_pkg.sv "
        "constraint_sv_test.sv tb.sv -o simv && ./simv'"
    )
    result = subprocess.run(["ssh", host, remote], capture_output=True, text=True)
    log = result.stdout + result.stderr
    print(log)
    assert result.returncode == 0, log
    assert "SVTYPES_CONSTRAINT_PASS" in log, log


@pytest.mark.remote_target
def test_remote_target_dynamic_collection_simulation():
    """Dynamic-array and queue size/foreach constraints run in SystemVerilog target."""
    host = os.environ.get("SVTYPES_target_HOST", "remote-target")
    reachable = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host, "true"],
        capture_output=True,
        text=True,
    )
    if reachable.returncode != 0:
        pytest.skip(f"remote SystemVerilog target host {host} is not reachable")
    out = Path(__file__).resolve().parents[2] / ".tmp" / "dynamic_collection_sv"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "dynamic_collection_sv_test.sv").write_text(
        "\n".join(
            [
                "package dynamic_collection_sv_test;",
                "  import svtypes_pkg::*;",
                DynamicCollectionPacket.to_sv_obj(level=1),
                QueueCollectionPacket.to_sv_obj(level=1),
                EmptyOnlyCollectionPacket.to_sv_obj(level=1),
                AssocCollectionPacket.to_sv_obj(level=1),
                "endpackage",
                "",
            ]
        )
    )
    (out / "tb.sv").write_text(
        """`timescale 1ns/1ps
module tb;
  import svtypes_pkg::*;
  import dynamic_collection_sv_test::*;
  integer i, j;
  initial begin
    DynamicCollectionPacket d;
    QueueCollectionPacket q;
    EmptyOnlyCollectionPacket e;
    AssocCollectionPacket a;
    d = new(); q = new(); e = new(); a = new();
    a.table[8'd1] = 8'd99;
    a.table[8'd7] = 8'd99;
    for (i = 0; i < 32; i++) begin
      if (!d.randomize()) $fatal(1, "dynamic array unsat");
      if (d.data.size() != d.length || d.data.size() > 4)
        $fatal(1, "dynamic array size constraint");
      foreach (d.data[j]) if (d.data[j] != j + 1)
        $fatal(1, "dynamic array element constraint");
      if (!q.randomize()) $fatal(1, "queue unsat");
      if (q.data.size() != 3) $fatal(1, "queue size constraint");
      foreach (q.data[j]) if (q.data[j] != j)
        $fatal(1, "queue element constraint");
      if (!e.randomize()) $fatal(1, "empty-only dynamic array unsat");
      if (e.data.size() != 0) $fatal(1, "empty-only dynamic array size");
      if (!a.randomize()) $fatal(1, "associative array unsat");
      if (a.table[8'd1] != 8'd2 || a.table[8'd7] != 8'd8)
        $fatal(1, "associative array value constraint");
    end
    $display("SVTYPES_DYNAMIC_COLLECTION_PASS");
    $finish;
  end
endmodule
"""
    )
    remote = (
        "bash -ilc '"
        "cd /path/to/svtypes/.tmp/dynamic_collection_sv && "
        "rm -rf simv csrc simv.daidir && "
        "target -full64 -sverilog -timescale=1ns/1ps "
        "+incdir+/path/to/svtypes/svtypes_runtime/sv "
        "/path/to/svtypes/svtypes_runtime/sv/svtypes_pkg.sv "
        "dynamic_collection_sv_test.sv tb.sv -o simv && ./simv'"
    )
    result = subprocess.run(["ssh", host, remote], capture_output=True, text=True)
    log = result.stdout + result.stderr
    print(log)
    assert result.returncode == 0, log
    assert "SVTYPES_DYNAMIC_COLLECTION_PASS" in log, log


@pytest.mark.remote_target
def test_remote_target_rand_handle_randomization():
    """A pre-allocated rand class handle is solved with its parent in SystemVerilog target."""
    host = os.environ.get("SVTYPES_target_HOST", "remote-target")
    reachable = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host, "true"],
        capture_output=True,
        text=True,
    )
    if reachable.returncode != 0:
        pytest.skip(f"remote SystemVerilog target host {host} is not reachable")

    out = Path(__file__).resolve().parents[2] / ".tmp" / "rand_handle_sv"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "rand_handle_sv_test.sv").write_text(
        "\n".join(
            [
                "package rand_handle_sv_test;",
                "  import svtypes_pkg::*;",
                targetHandleChild.to_sv_obj(level=1),
                targetHandleParent.to_sv_obj(level=1),
                "endpackage",
                "",
            ]
        )
    )
    (out / "tb.sv").write_text(
        """`timescale 1ns/1ps
module tb;
  import svtypes_pkg::*;
  import rand_handle_sv_test::*;

  class HookChild extends targetHandleChild;
    int pre_count;
    int post_count;
    function void pre_randomize(); pre_count++; endfunction
    function void post_randomize(); post_count++; endfunction
  endclass

  class HookParent extends targetHandleParent;
    int pre_count;
    int post_count;
    function void pre_randomize(); pre_count++; endfunction
    function void post_randomize(); post_count++; endfunction
  endclass

  initial begin
    HookParent p;
    HookChild c;
    p = new();
    c = new();
    p.child = c;
    repeat (32) begin
      if (!p.randomize()) $fatal(1, "rand handle randomize unsat");
      if (c.data > 10 || p.other != c.data + 1)
        $fatal(1, "rand handle constraints violated");
    end
    if (p.pre_count != 32 || p.post_count != 32)
      $fatal(1, "parent hook count");
    if (c.pre_count != 32 || c.post_count != 32)
      $fatal(1, "child hook count");
    $display("SVTYPES_RAND_HANDLE_PASS");
    $finish;
  end
endmodule
"""
    )
    remote = (
        "bash -ilc '"
        "cd /path/to/svtypes/.tmp/rand_handle_sv && "
        "rm -rf simv csrc simv.daidir && "
        "target -full64 -sverilog -timescale=1ns/1ps "
        "+incdir+/path/to/svtypes/svtypes_runtime/sv "
        "/path/to/svtypes/svtypes_runtime/sv/svtypes_pkg.sv "
        "rand_handle_sv_test.sv tb.sv -o simv && ./simv'"
    )
    result = subprocess.run(["ssh", host, remote], capture_output=True, text=True)
    log = result.stdout + result.stderr
    print(log)
    assert result.returncode == 0, log
    assert "SVTYPES_RAND_HANDLE_PASS" in log, log


@pytest.mark.remote_target
def test_remote_target_rand_handle_container_randomization():
    """Pre-allocated rand handles in every unpacked container randomize in SystemVerilog target."""
    host = os.environ.get("SVTYPES_target_HOST", "remote-target")
    reachable = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host, "true"],
        capture_output=True,
        text=True,
    )
    if reachable.returncode != 0:
        pytest.skip(f"remote SystemVerilog target host {host} is not reachable")

    out = Path(__file__).resolve().parents[2] / ".tmp" / "rand_handle_container_sv"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "rand_handle_container_sv_test.sv").write_text(
        "\n".join(
            [
                "package rand_handle_container_sv_test;",
                "  import svtypes_pkg::*;",
                targetHandleChild.to_sv_obj(level=1),
                targetContainerHandleParent.to_sv_obj(level=1),
                "endpackage",
                "",
            ]
        )
    )
    (out / "tb.sv").write_text(
        """`timescale 1ns/1ps
module tb;
  import svtypes_pkg::*;
  import rand_handle_container_sv_test::*;

  initial begin
    targetContainerHandleParent p;
    targetHandleChild fixed, dynamic, queued, mapped;
    p = new();
    fixed = new(); dynamic = new(); queued = new(); mapped = new();
    p.fixed[0] = fixed;
    p.dynamic = new[1]; p.dynamic[0] = dynamic;
    p.queue.push_back(queued);
    p.table[8'd7] = mapped;
    repeat (32) begin
      if (!p.randomize()) $fatal(1, "rand handle container randomize unsat");
      if (fixed.data > 10 || dynamic.data > 10 || queued.data > 10 || mapped.data > 10)
        $fatal(1, "rand handle container child constraint violated");
      if (p.target != dynamic.data + 1)
        $fatal(1, "rand handle container parent constraint violated");
    end
    $display("SVTYPES_RAND_HANDLE_CONTAINER_PASS");
    $finish;
  end
endmodule
"""
    )
    remote = (
        "bash -ilc '"
        "cd /path/to/svtypes/.tmp/rand_handle_container_sv && "
        "rm -rf simv csrc simv.daidir && "
        "target -full64 -sverilog -timescale=1ns/1ps "
        "+incdir+/path/to/svtypes/svtypes_runtime/sv "
        "/path/to/svtypes/svtypes_runtime/sv/svtypes_pkg.sv "
        "rand_handle_container_sv_test.sv tb.sv -o simv && ./simv'"
    )
    result = subprocess.run(["ssh", host, remote], capture_output=True, text=True)
    log = result.stdout + result.stderr
    print(log)
    assert result.returncode == 0, log
    assert "SVTYPES_RAND_HANDLE_CONTAINER_PASS" in log, log


@pytest.mark.remote_target
def test_remote_target_dist_expression_simulation():
    """The same expression/range/weight dist IR accepted by Python compiles
    and constrains target-language randomization."""
    host = os.environ.get("SVTYPES_target_HOST", "remote-target")
    reachable = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host, "true"],
        capture_output=True,
        text=True,
    )
    if reachable.returncode != 0:
        pytest.skip(f"remote SystemVerilog target host {host} is not reachable")
    out = Path(__file__).resolve().parents[2] / ".tmp" / "dist_sv"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "dist_sv_test.sv").write_text(
        "\n".join(
            [
                "package dist_sv_test;",
                "  import svtypes_pkg::*;",
                DistPacket.to_sv_obj(level=1),
                "endpackage",
                "",
            ]
        )
    )
    (out / "tb.sv").write_text(
        """`timescale 1ns/1ps
module tb;
  import svtypes_pkg::*;
  import dist_sv_test::*;
  integer i, choice4, choice7;
  initial begin
    DistPacket p;
    p = new();
    p.base = 4'd1;
    p.weight = 4'd2;
    choice4 = 0;
    choice7 = 0;
    for (i = 0; i < 512; i++) begin
      if (!p.randomize()) $fatal(1, "dist unsat");
      if (!(p.choice == 4'd4 || p.choice == 4'd7 || p.choice == 4'd8 ||
            p.choice == 4'd9 || p.choice == 4'd11))
        $fatal(1, "dist support violation: %0d", p.choice);
      if (p.choice == 4'd4) choice4++;
      if (p.choice == 4'd7) choice7++;
    end
    if (choice4 <= (choice7 * 2))
      $fatal(1, "dist weight ratio unexpected: choice4=%0d choice7=%0d", choice4, choice7);
    $display("SVTYPES_DIST_PASS");
    $finish;
  end
endmodule
"""
    )
    remote = (
        "bash -ilc '"
        "cd /path/to/svtypes/.tmp/dist_sv && "
        "rm -rf simv csrc simv.daidir && "
        "target -full64 -sverilog -timescale=1ns/1ps "
        "+incdir+/path/to/svtypes/svtypes_runtime/sv "
        "/path/to/svtypes/svtypes_runtime/sv/svtypes_pkg.sv "
        "dist_sv_test.sv tb.sv -o simv && ./simv'"
    )
    result = subprocess.run(["ssh", host, remote], capture_output=True, text=True)
    log = result.stdout + result.stderr
    print(log)
    assert result.returncode == 0, log
    assert "SVTYPES_DIST_PASS" in log, log


@pytest.mark.remote_target
def test_remote_target_randc_cycle_simulation():
    """Generated `randc` follows the observable SV cycle and mode rules."""
    host = os.environ.get("SVTYPES_target_HOST", "remote-target")
    reachable = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host, "true"],
        capture_output=True,
        text=True,
    )
    if reachable.returncode != 0:
        pytest.skip(f"remote SystemVerilog target host {host} is not reachable")
    out = Path(__file__).resolve().parents[2] / ".tmp" / "randc_sv"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "randc_sv_test.sv").write_text(
        "\n".join(
            [
                "package randc_sv_test;",
                "  import svtypes_pkg::*;",
                RandcPacket.to_sv_obj(level=1),
                "endpackage",
                "",
            ]
        )
    )
    (out / "tb.sv").write_text(
        """`timescale 1ns/1ps
module tb;
  import svtypes_pkg::*;
  import randc_sv_test::*;
  integer i;
  bit [3:0] seen;
  bit [1:0] held;
  initial begin
    RandcPacket p;
    p = new();
    p.limit = 2'd3;
    seen = '0;
    for (i = 0; i < 3; i++) begin
      if (!p.randomize()) $fatal(1, "randc constrained unsat");
      if (seen[p.choice]) $fatal(1, "randc repeated before constrained cycle exhausted");
      seen[p.choice] = 1'b1;
    end
    if (!p.randomize()) $fatal(1, "randc reset unsat");
    if (p.choice >= 2'd3) $fatal(1, "randc constraint escaped");

    p.limit = 2'd0;
    if (p.randomize()) $fatal(1, "randc expected unsat");
    p.limit = 2'd3;
    if (!p.randomize()) $fatal(1, "randc did not recover from unsat");
    held = p.choice;
    p.choice.rand_mode(0);
    if (!p.randomize()) $fatal(1, "randc mode-off randomize failed");
    if (p.choice != held) $fatal(1, "randc mode-off changed value");
    p.choice.rand_mode(1);
    if (!p.randomize()) $fatal(1, "randc re-enable randomize failed");
    if (p.choice >= 2'd3) $fatal(1, "randc re-enable constraint escaped");
    $display("SVTYPES_RANDC_PASS");
    $finish;
  end
endmodule
"""
    )
    remote = (
        "bash -ilc '"
        "cd /path/to/svtypes/.tmp/randc_sv && "
        "rm -rf simv csrc simv.daidir && "
        "target -full64 -sverilog -timescale=1ns/1ps "
        "+incdir+/path/to/svtypes/svtypes_runtime/sv "
        "/path/to/svtypes/svtypes_runtime/sv/svtypes_pkg.sv "
        "randc_sv_test.sv tb.sv -o simv && ./simv'"
    )
    result = subprocess.run(["ssh", host, remote], capture_output=True, text=True)
    log = result.stdout + result.stderr
    print(log)
    assert result.returncode == 0, log
    assert "SVTYPES_RANDC_PASS" in log, log


@pytest.mark.remote_target
def test_remote_target_unique_scalar_simulation():
    host = os.environ.get("SVTYPES_target_HOST", "remote-target")
    reachable = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host, "true"],
        capture_output=True,
        text=True,
    )
    if reachable.returncode != 0:
        pytest.skip(f"remote SystemVerilog target host {host} is not reachable")
    out = Path(__file__).resolve().parents[2] / ".tmp" / "unique_sv"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "unique_sv_test.sv").write_text(
        "\n".join(
            [
                "package unique_sv_test;",
                "  import svtypes_pkg::*;",
                UniquePacket.to_sv_obj(level=1),
                "endpackage",
                "",
            ]
        )
    )
    (out / "tb.sv").write_text(
        """`timescale 1ns/1ps
module tb;
  import svtypes_pkg::*;
  import unique_sv_test::*;
  integer i;
  initial begin
    UniquePacket p;
    p = new();
    for (i = 0; i < 64; i++) begin
      if (!p.randomize()) $fatal(1, "unique unsat");
      if (p.first == p.second || p.first == p.third || p.second == p.third)
        $fatal(1, "unique violation");
    end
    $display("SVTYPES_UNIQUE_PASS");
    $finish;
  end
endmodule
"""
    )
    remote = (
        "bash -ilc '"
        "cd /path/to/svtypes/.tmp/unique_sv && "
        "rm -rf simv csrc simv.daidir && "
        "target -full64 -sverilog -timescale=1ns/1ps "
        "+incdir+/path/to/svtypes/svtypes_runtime/sv "
        "/path/to/svtypes/svtypes_runtime/sv/svtypes_pkg.sv "
        "unique_sv_test.sv tb.sv -o simv && ./simv'"
    )
    result = subprocess.run(["ssh", host, remote], capture_output=True, text=True)
    log = result.stdout + result.stderr
    print(log)
    assert result.returncode == 0, log
    assert "SVTYPES_UNIQUE_PASS" in log, log


@pytest.mark.remote_target
def test_remote_target_soft_constraint_simulation():
    host = os.environ.get("SVTYPES_target_HOST", "remote-target")
    reachable = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host, "true"],
        capture_output=True,
        text=True,
    )
    if reachable.returncode != 0:
        pytest.skip(f"remote SystemVerilog target host {host} is not reachable")
    out = Path(__file__).resolve().parents[2] / ".tmp" / "soft_sv"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "soft_sv_test.sv").write_text(
        "\n".join(
            [
                "package soft_sv_test;",
                "  import svtypes_pkg::*;",
                SoftPacket.to_sv_obj(level=1),
                SoftHardConflictPacket.to_sv_obj(level=1),
                SoftOrderPacket.to_sv_obj(level=1),
                SoftBasePacket.to_sv_obj(level=1),
                SoftDerivedPacket.to_sv_obj(level=1),
                "endpackage",
                "",
            ]
        )
    )
    (out / "tb.sv").write_text(
        """`timescale 1ns/1ps
module tb;
  import svtypes_pkg::*;
  import soft_sv_test::*;
  integer i;
  initial begin
    SoftPacket p;
    SoftHardConflictPacket h;
    SoftOrderPacket o;
    SoftDerivedPacket d;
    p = new(); h = new(); o = new(); d = new();
    for (i = 0; i < 32; i++) begin
      if (!p.randomize()) $fatal(1, "soft preferred unsat");
      if (p.choice != 2) $fatal(1, "soft preference not selected");
      if (!h.randomize()) $fatal(1, "soft hard conflict unsat");
      if (h.choice != 0) $fatal(1, "hard constraint did not override soft");
      if (!o.randomize()) $fatal(1, "same-block soft order unsat");
      if (o.choice != 1) $fatal(1, "later same-block soft did not win");
      if (!d.randomize()) $fatal(1, "derived soft order unsat");
      if (d.choice != 2) $fatal(1, "derived soft did not override base soft");
    end
    $display("SVTYPES_SOFT_PASS");
    $finish;
  end
endmodule
"""
    )
    remote = (
        "bash -ilc '"
        "cd /path/to/svtypes/.tmp/soft_sv && "
        "rm -rf simv csrc simv.daidir && "
        "target -full64 -sverilog -timescale=1ns/1ps "
        "+incdir+/path/to/svtypes/svtypes_runtime/sv "
        "/path/to/svtypes/svtypes_runtime/sv/svtypes_pkg.sv "
        "soft_sv_test.sv tb.sv -o simv && ./simv'"
    )
    result = subprocess.run(["ssh", host, remote], capture_output=True, text=True)
    log = result.stdout + result.stderr
    print(log)
    assert result.returncode == 0, log
    assert "SVTYPES_SOFT_PASS" in log, log


@pytest.mark.remote_target
def test_remote_target_solve_before_simulation():
    host = os.environ.get("SVTYPES_target_HOST", "remote-target")
    reachable = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host, "true"],
        capture_output=True,
        text=True,
    )
    if reachable.returncode != 0:
        pytest.skip(f"remote SystemVerilog target host {host} is not reachable")
    out = Path(__file__).resolve().parents[2] / ".tmp" / "solve_before_sv"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "solve_before_sv_test.sv").write_text(
        "\n".join(
            [
                "package solve_before_sv_test;",
                "  import svtypes_pkg::*;",
                SolveBeforePacket.to_sv_obj(level=1),
                "endpackage",
                "",
            ]
        )
    )
    (out / "tb.sv").write_text(
        """`timescale 1ns/1ps
module tb;
  import svtypes_pkg::*;
  import solve_before_sv_test::*;
  integer i;
  initial begin
    SolveBeforePacket p;
    p = new();
    for (i = 0; i < 64; i++) begin
      if (!p.randomize()) $fatal(1, "solve-before unsat");
      if (!(p.first < p.second)) $fatal(1, "solve-before hard constraint escaped");
    end
    $display("SVTYPES_SOLVE_BEFORE_PASS");
    $finish;
  end
endmodule
"""
    )
    remote = (
        "bash -ilc '"
        "cd /path/to/svtypes/.tmp/solve_before_sv && "
        "rm -rf simv csrc simv.daidir && "
        "target -full64 -sverilog -timescale=1ns/1ps "
        "+incdir+/path/to/svtypes/svtypes_runtime/sv "
        "/path/to/svtypes/svtypes_runtime/sv/svtypes_pkg.sv "
        "solve_before_sv_test.sv tb.sv -o simv && ./simv'"
    )
    result = subprocess.run(["ssh", host, remote], capture_output=True, text=True)
    log = result.stdout + result.stderr
    print(log)
    assert result.returncode == 0, log
    assert "SVTYPES_SOLVE_BEFORE_PASS" in log, log


@pytest.mark.remote_target
def test_remote_target_template_specialization_simulation():
    """The generated template definition is compiled, and both the emitted
    specialization and a direct Templated#(.WIDTH(...)) instance (resolved
    entirely by the target language) simulate correctly."""
    host = os.environ.get("SVTYPES_target_HOST", "remote-target")
    reachable = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host, "true"],
        capture_output=True,
        text=True,
    )
    if reachable.returncode != 0:
        pytest.skip(f"remote SystemVerilog target host {host} is not reachable")

    class Templated(SvObject):
        WIDTH = Parameter(Int)
        data = Bit(8, cov=True)

    Spec = Templated.specialize(WIDTH=4)
    MemberOnly = Templated.specialize(WIDTH=8)

    class Holder(SvObject):
        child = MemberOnly()

    out = Path(__file__).resolve().parents[2] / ".tmp" / "template_sv"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    package = "\n".join(
        [
            "package template_sv_test;",
            "  import svtypes_pkg::*;",
            Templated.to_sv_obj(level=1),
            Holder.to_sv_obj(level=1),
            "endpackage",
            "",
        ]
    )
    (out / "template_sv_test.sv").write_text(package)
    (out / "tb.sv").write_text(
        """`timescale 1ns/1ps
module tb;
  import svtypes_pkg::*;
  import template_sv_test::*;
  initial begin
    Templated#(.WIDTH(4)) p;
    Holder h;
    Templated#(.WIDTH(8)) direct;
    byte unsigned bytes[$];
    int o;
    p = new(); h = new(); direct = new();
    h.child = new();
    begin
      Templated#(.WIDTH(8))::Templated__svtypes_coverage cov;
      cov = new();
      cov.sample(direct);
    end
    if (!p.randomize()) $fatal(1, "template instance randomize");
    p.data = 8'h11;
    if (p.data !== 8'h11) $fatal(1, "spec field");
    h.child.data = 8'hAB;
    h.pack(bytes);
    h.child.data = 8'h00;
    o = 0;
    h.unpack(bytes, o);
    if (h.child.data !== 8'hAB) $fatal(1, "holder roundtrip");
    direct.data = 8'hCD;
    if (direct.data !== 8'hCD) $fatal(1, "direct template instance");
    $display("SVTYPES_TEMPLATE_PASS");
    $finish;
  end
endmodule
"""
    )
    remote = (
        "bash -ilc '"
        "cd /path/to/svtypes/.tmp/template_sv && "
        "rm -rf simv csrc simv.daidir && "
        "target -full64 -sverilog -timescale=1ns/1ps "
        "+incdir+/path/to/svtypes/svtypes_runtime/sv "
        "/path/to/svtypes/svtypes_runtime/sv/svtypes_pkg.sv "
        "template_sv_test.sv tb.sv -o simv && ./simv'"
    )
    result = subprocess.run(["ssh", host, remote], capture_output=True, text=True)
    log = result.stdout + result.stderr
    print(log)
    assert result.returncode == 0, log
    assert "SVTYPES_TEMPLATE_PASS" in log, log


@pytest.mark.remote_target
def test_remote_target_type_parameter_simulation():
    """A type-parameter template compiles; direct Kind#(.T(Payload))
    instances are resolved by the target language and simulate."""
    host = os.environ.get("SVTYPES_target_HOST", "remote-target")
    reachable = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host, "true"],
        capture_output=True,
        text=True,
    )
    if reachable.returncode != 0:
        pytest.skip(f"remote SystemVerilog target host {host} is not reachable")

    class Kind(SvObject):
        T = Parameter(type)
        data = Bit(8)

    class Payload(SvObject):
        x = Bit(4)

    KindSpec = Kind.specialize(T=Payload)

    out = Path(__file__).resolve().parents[2] / ".tmp" / "type_param_sv"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    package = "\n".join(
        [
            "package type_param_sv_test;",
            "  import svtypes_pkg::*;",
            Kind.to_sv_obj(level=1),
            Payload.to_sv_obj(level=1),
            "endpackage",
            "",
        ]
    )
    (out / "type_param_sv_test.sv").write_text(package)
    (out / "tb.sv").write_text(
        """`timescale 1ns/1ps
module tb;
  import svtypes_pkg::*;
  import type_param_sv_test::*;
  initial begin
    Kind#(.T(Payload)) k;
    Kind#(.T(Payload)) direct_kind;
    byte unsigned bytes[$];
    int o;
    k = new(); direct_kind = new();
    if (!k.randomize()) $fatal(1, "Kind instance randomize");
    k.data = 8'h33;
    direct_kind.data = 8'h44;
    k.pack(bytes);
    k.data = 8'h00;
    o = 0;
    k.unpack(bytes, o);
    if (k.data !== 8'h33) $fatal(1, "type-param roundtrip");
    if (direct_kind.data !== 8'h44) $fatal(1, "direct type-param instance");
    $display("SVTYPES_TYPE_PARAM_PASS");
    $finish;
  end
endmodule
"""
    )
    remote = (
        "bash -ilc '"
        "cd /path/to/svtypes/.tmp/type_param_sv && "
        "rm -rf simv csrc simv.daidir && "
        "target -full64 -sverilog -timescale=1ns/1ps "
        "+incdir+/path/to/svtypes/svtypes_runtime/sv "
        "/path/to/svtypes/svtypes_runtime/sv/svtypes_pkg.sv "
        "type_param_sv_test.sv tb.sv -o simv && ./simv'"
    )
    result = subprocess.run(["ssh", host, remote], capture_output=True, text=True)
    log = result.stdout + result.stderr
    print(log)
    assert result.returncode == 0, log
    assert "SVTYPES_TYPE_PARAM_PASS" in log, log


@pytest.mark.remote_target
def test_remote_target_template_constraint_on_parameterized_instance():
    """Constraints are rendered on the template with parameter names, so a
    direct Bound#(.WIDTH(8)) instance is constrained by the target-language
    solver."""
    host = os.environ.get("SVTYPES_target_HOST", "remote-target")
    reachable = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host, "true"],
        capture_output=True,
        text=True,
    )
    if reachable.returncode != 0:
        pytest.skip(f"remote SystemVerilog target host {host} is not reachable")

    class Bound(SvObject):
        WIDTH = Parameter(Int)
        addr = Bit(8)

        @constraint
        def legal(self):
            self.addr < WIDTH

    Spec = Bound.specialize(WIDTH=8)

    out = Path(__file__).resolve().parents[2] / ".tmp" / "template_constraint_sv"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    package = "\n".join(
        [
            "package template_constraint_sv_test;",
            "  import svtypes_pkg::*;",
            Bound.to_sv_obj(level=1),
            "endpackage",
            "",
        ]
    )
    (out / "template_constraint_sv_test.sv").write_text(package)
    (out / "tb.sv").write_text(
        """`timescale 1ns/1ps
module tb;
  import svtypes_pkg::*;
  import template_constraint_sv_test::*;
  integer i, hi;
  initial begin
    Bound#(.WIDTH(8)) t;
    t = new();
    hi = 0;
    for (i = 0; i < 32; i++) begin
      if (!t.randomize()) $fatal(1, "template instance unsat");
      if (t.addr >= 8) hi++;
    end
    $display("SVTYPES_REVIEW hi=%0d", hi);
    if (hi != 0) begin
      $display("SVTYPES_REVIEW_TEMPLATE_UNCONSTRAINED");
      $fatal(1, "template instance escaped constraint");
    end
    $display("SVTYPES_TEMPLATE_CONSTRAINT_PASS");
    $finish;
  end
endmodule
"""
    )
    remote = (
        "bash -ilc '"
        "cd /path/to/svtypes/.tmp/template_constraint_sv && "
        "rm -rf simv csrc simv.daidir && "
        "target -full64 -sverilog -timescale=1ns/1ps "
        "+incdir+/path/to/svtypes/svtypes_runtime/sv "
        "/path/to/svtypes/svtypes_runtime/sv/svtypes_pkg.sv "
        "template_constraint_sv_test.sv tb.sv -o simv && ./simv'"
    )
    result = subprocess.run(["ssh", host, remote], capture_output=True, text=True)
    log = result.stdout + result.stderr
    print(log)
    assert result.returncode == 0, log
    assert "SVTYPES_TEMPLATE_CONSTRAINT_PASS" in log, log
    assert "SVTYPES_REVIEW hi=0" in log, log


@pytest.mark.remote_target
def test_remote_target_paramref_forwarding_and_symbolic_for():
    """A ParamRef-forwarding subclass flattens to the original template, and a
    symbolic `for i in range(WIDTH)` constraint is solved by the target
    language on a Sub#(.WIDTH(4)) instance."""
    host = os.environ.get("SVTYPES_target_HOST", "remote-target")
    reachable = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host, "true"],
        capture_output=True,
        text=True,
    )
    if reachable.returncode != 0:
        pytest.skip(f"remote SystemVerilog target host {host} is not reachable")

    class Base(SvObject):
        WIDTH = Parameter(Int)
        addr = Bit(8)

        @constraint
        def legal(self):
            self.addr < WIDTH

    class Sub(Base.specialize(WIDTH=ParamRef())):
        WIDTH = Parameter(Int)
        words = Array(Bit(8), 4)

        @constraint
        def words_nonzero(self):
            for i in range(WIDTH):
                self.words[i] != 0

    Spec = Sub.specialize(WIDTH=4)

    out = Path(__file__).resolve().parents[2] / ".tmp" / "paramref_for_sv"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    package = "\n".join(
        [
            "package paramref_for_sv_test;",
            "  import svtypes_pkg::*;",
            Base.to_sv_obj(level=1),
            Sub.to_sv_obj(level=1),
            "endpackage",
            "",
        ]
    )
    (out / "paramref_for_sv_test.sv").write_text(package)
    (out / "tb.sv").write_text(
        """`timescale 1ns/1ps
module tb;
  import svtypes_pkg::*;
  import paramref_for_sv_test::*;
    integer i, j, bad;
  initial begin
    Sub#(.WIDTH(4)) t;
    t = new();
    bad = 0;
    for (i = 0; i < 32; i++) begin
      if (!t.randomize()) $fatal(1, "Sub instance unsat");
      if (t.addr >= 4) bad++;
      for (j = 0; j < 4; j++) if (t.words[j] == 0) bad++;
    end
    if (bad != 0) $fatal(1, "constraint escaped (%0d)", bad);
    $display("SVTYPES_PARAMREF_FOR_PASS");
    $finish;
  end
endmodule
"""
    )
    remote = (
        "bash -ilc '"
        "cd /path/to/svtypes/.tmp/paramref_for_sv && "
        "rm -rf simv csrc simv.daidir && "
        "target -full64 -sverilog -timescale=1ns/1ps "
        "+incdir+/path/to/svtypes/svtypes_runtime/sv "
        "/path/to/svtypes/svtypes_runtime/sv/svtypes_pkg.sv "
        "paramref_for_sv_test.sv tb.sv -o simv && ./simv'"
    )
    result = subprocess.run(["ssh", host, remote], capture_output=True, text=True)
    log = result.stdout + result.stderr
    print(log)
    assert result.returncode == 0, log
    assert "SVTYPES_PARAMREF_FOR_PASS" in log, log


@pytest.mark.remote_target
def test_remote_target_layered_randomize_simulation():
    host = os.environ.get("SVTYPES_target_HOST", "remote-target")
    reachable = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host, "true"],
        capture_output=True,
        text=True,
    )
    if reachable.returncode != 0:
        pytest.skip(f"remote SystemVerilog target host {host} is not reachable")

    class LayerPkt(SvObject):
        addr = Bit(32)
        payload = Bit(8)
        extra = Bit(8)
        lock = Bit(1, rand=False)

        @constraint
        def address_legal(self):
            self.addr % 64 == 0
            0x1000 <= self.addr
            self.addr < 0x2000

        @constraint
        def payload_ok(self):
            self.payload != 0

        @constraint
        def extra_fail(self):
            self.lock == 1

        @rand_layer(100)
        def address(self):
            self.addr
            self.address_legal

        @rand_layer(-50)
        def payload_data(self):
            self.payload
            self.payload_ok
            self.extra_fail

    class WordPkt(SvObject):
        words = Array(Bit(8), 4)

        @constraint
        def first_ok(self):
            self.words[0] != 0

        @constraint
        def second_eq(self):
            self.words[1] == self.words[0]

        @rand_layer(100)
        def first(self):
            self.words[0]
            self.first_ok

        @rand_layer(-50)
        def second(self):
            self.words[1]
            self.second_eq

    out = Path(__file__).resolve().parents[2] / ".tmp" / "layered_sv"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    package = "\n".join(
        [
            "package layered_sv_test;",
            "  import svtypes_pkg::*;",
            LayerPkt.to_sv_obj(level=1),
            WordPkt.to_sv_obj(level=1),
            "endpackage",
            "",
        ]
    )
    (out / "layered_sv_test.sv").write_text(package)
    (out / "tb.sv").write_text(
        """`timescale 1ns/1ps
module tb;
  import svtypes_pkg::*;
  import layered_sv_test::*;

  class HookPkt extends LayerPkt;
    int pre_count;
    int post_count;
    int priorities[$];
    bit use_layered_hook_actions;
    bit lock_seen_in_low_pre;
    bit [7:0] payload_seen_in_post;
    function void pre_randomize();
      pre_count++;
      if (!svtypes_layered_randomize_active()) $fatal(1, "HookPkt inactive pre");
      priorities.push_back(svtypes_layered_randomize_priority());
      if (use_layered_hook_actions && svtypes_layered_randomize_priority() == -50) begin
        if (lock != 1'b1) $fatal(1, "HookPkt high post did not precede low pre");
        lock_seen_in_low_pre = 1'b1;
      end
    endfunction
    function void post_randomize();
      post_count++;
      if (!svtypes_layered_randomize_active()) $fatal(1, "HookPkt inactive post");
      if (use_layered_hook_actions && svtypes_layered_randomize_priority() == 100)
        lock = 1'b1;
      else if (use_layered_hook_actions && svtypes_layered_randomize_priority() == -50)
        payload_seen_in_post = payload;
    endfunction
  endclass

  initial begin
    LayerPkt p;
    HookPkt h;
    HookPkt f;
    int addr_mode;
    int extra_mode;
    p = new();
    h = new();
    f = new();
    p.lock = 1'b1;
    h.lock = 1'b0;
    h.use_layered_hook_actions = 1'b1;
    f.lock = 1'b0;
    addr_mode = p.addr.rand_mode();
    extra_mode = p.extra.rand_mode();
    p.addr.rand_mode(0);
    p.extra.rand_mode(0);
    if (!p.layered_randomize()) $fatal(1, "LayerPkt layered unsat");
    if ((p.addr % 64) != 0) $fatal(1, "LayerPkt addr align");
    if (p.addr < 32'h1000 || !(p.addr < 32'h2000)) $fatal(1, "LayerPkt addr range");
    if (p.payload == 0) $fatal(1, "LayerPkt payload");
    if (p.addr.rand_mode() != 0) $fatal(1, "LayerPkt addr mode restore");
    if (p.extra.rand_mode() != 0) $fatal(1, "LayerPkt extra mode restore");
    p.addr.rand_mode(addr_mode);
    p.extra.rand_mode(extra_mode);

    if (!h.layered_randomize()) $fatal(1, "HookPkt layered unsat");
    if (h.pre_count != 3) $fatal(1, "HookPkt pre_count %0d", h.pre_count);
    if (h.post_count != 3) $fatal(1, "HookPkt post_count %0d", h.post_count);
    if (h.priorities.size() != 3 || h.priorities[0] != 100 ||
        h.priorities[1] != 0 || h.priorities[2] != -50)
      $fatal(1, "HookPkt layered priorities");
    if (h.svtypes_layered_randomize_active())
      $fatal(1, "HookPkt active after layered_randomize");
    if (h.lock != 1'b1 || !h.lock_seen_in_low_pre || h.payload_seen_in_post != h.payload)
      $fatal(1, "HookPkt priority-specific hook actions");

    if (f.layered_randomize()) $fatal(1, "failing layered should be 0");
    if (f.pre_count != 3) $fatal(1, "fail pre_count %0d", f.pre_count);
    if (f.post_count != 2) $fatal(1, "fail post_count %0d", f.post_count);
    if ((f.addr % 64) != 0) $fatal(1, "fail kept high-priority addr");

    begin
      WordPkt w;
      int w0_mode;
      w = new();
      w0_mode = w.words[0].rand_mode();
      w.words[0].rand_mode(0);
      if (!w.layered_randomize()) $fatal(1, "WordPkt layered unsat");
      if (w.words[0] == 0) $fatal(1, "WordPkt first word");
      if (w.words[1] != w.words[0]) $fatal(1, "WordPkt second uses first as state");
      if (w.words[0].rand_mode() != 0) $fatal(1, "WordPkt element mode restore");
      w.words[0].rand_mode(w0_mode);
    end
    $display("SVTYPES_LAYERED_PASS");
    $finish;
  end
endmodule
"""
    )
    remote = (
        "bash -ilc '"
        "cd /path/to/svtypes/.tmp/layered_sv && "
        "rm -rf simv csrc simv.daidir && "
        "target -full64 -sverilog -timescale=1ns/1ps "
        "+incdir+/path/to/svtypes/svtypes_runtime/sv "
        "/path/to/svtypes/svtypes_runtime/sv/svtypes_pkg.sv "
        "layered_sv_test.sv tb.sv -o simv && ./simv'"
    )
    result = subprocess.run(["ssh", host, remote], capture_output=True, text=True)
    log = result.stdout + result.stderr
    print(log)
    assert result.returncode == 0, log
    assert "SVTYPES_LAYERED_PASS" in log, log

"""Standalone SystemVerilog target coverage for generated SvTypes codecs (no SVX).

Existing remote tests in ``test_constraint_sv.py`` cover constrained-random,
templates, and layered randomization. M3/M4 example benches require SVX
channels. This module compiles generated SystemVerilog on the shared SystemVerilog target host
and checks pack/unpack byte parity, object-graph identity, field policies,
plusargs, coverage sampling, and truncated-stream failure.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from examples.milestone_3_svtypes_parity.tests.types import (
    BaseTx,
    Color,
    Inner,
    M3Tx,
    ManyTypesTx,
    ParamMemberTx,
    ParamTx,
)
from examples.milestone_4_graph.tests.types import (
    GraphNode,
    GraphPair,
    GraphQueue,
    GraphRefQueue,
)
from svtypes import (
    Array,
    Bit,
    DynArray,
    Enum,
    Int,
    Logic,
    LogicValue,
    Queue,
    RecordField,
    RecordSchema,
    Reg,
    RemoteRef,
    RemoteRefValue,
    STABLE_CAPABILITIES,
    SvObject,
    SvStruct,
    clear_object_registry,
    constraint,
    encoding_descriptor,
    svobj,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REMOTE_ROOT = "/path/to/svtypes"
WORKDIR_NAME = "target_core"
DEFAULT_HOST = "remote-target"


class SignedTone(Enum, width=8, signed=True):
    NEG = -1
    ZERO = 0
    POS = 1


@svobj
class Header(SvStruct):
    addr = Bit(16)
    extra = Bit(8)


@svobj
class CoreExtraTx(SvObject):
    flags = Logic(4)
    shaped_logic = Logic((2, 4))
    legacy = Reg(8)
    signed_logic = Logic(8, signed=True)
    shaped_bits = Bit((2, 8))
    header = Header()
    headers = Array(Header(), 2)
    q_headers = Queue(Header())
    handle = RemoteRef("acme.Device")
    skipped = Int(pack_bytes=False)
    hidden = Int(dump=False)
    empty_dyn = DynArray(Int())
    empty_q = Queue(Int())
    matrix = Array(Int(), (2, 2))
    signed_tone = SignedTone()


@svobj
class PlusargBox(SvObject):
    count = Int()


@svobj
class RandBox(SvObject):
    amount = Bit(8)

    @constraint
    def legal(self):
        1 <= self.amount
        self.amount <= 7


DriveRequest = RecordSchema(
    "target.core.drive.request",
    [
        RecordField("address", Bit(32)),
        ("data", Bit(64)),
    ],
    class_name="DriveRequest",
).build()
assert DriveRequest is not None


def _make_inner(a: int, b: int) -> Inner:
    inner = Inner()
    inner.a.value = a
    inner.b.value = b
    return inner


def _fill_m3() -> M3Tx:
    tx = M3Tx()
    tx.id.value = 11
    tx.data.value = [12, 13]
    tx.label.value = "base"
    tx.addr.value = 0x1234
    tx.serial.value = -55
    tx.color.value = Color.GREEN
    tx.ratio.value = 1.5
    tx.temp.value = 2.5
    tx.dyn.value = [1, 2, 3]
    tx.q.value = [4, 5]
    tx.inner.a.value = 6
    tx.inner.b.value = 7
    return tx


def _fill_many() -> ManyTypesTx:
    tx = ManyTypesTx()
    tx.u1.value = 1
    tx.u7.value = 0x7E
    tx.u9.value = 0x1AB
    tx.u33.value = 0x1_2345_6789
    tx.u65.value = 0x1_0123_4567_89AB_CDEF
    tx.s5.value = -7
    tx.s12.value = -1023
    tx.i32.value = -0x1234567
    tx.i64.value = -0x0102030405060708
    tx.color.value = Color.GREEN
    tx.text.value = "many-types"
    tx.fp64.value = -math.pi
    tx.fp32.value = 12.5
    tx.fixed_bits.value = [0, 1, 6, 7]
    tx.fixed_signed.value = [-1, -17, 15]
    tx.matrix.value = [[1, -2], [3, -4]]
    tx.dyn_bits.value = [0, 0x155, 0x3FF]
    tx.dyn_signed.value = [-1, -100, 127]
    tx.q_colors.value = [Color.RED, Color.BLUE, Color.GREEN]
    tx.assoc.value = {"apple": 11, "banana": -22}
    tx.q_inner.value = [_make_inner(10, 0x11), _make_inner(-20, 0x22)]
    tx.inner_arr.value = [_make_inner(30, 0x33), _make_inner(-40, 0x44)]
    tx.nested.a.value = 50
    tx.nested.b.value = 0x55
    return tx


def _fill_param_member() -> ParamMemberTx:
    tx = ParamMemberTx()
    tx.tag.value = 501
    tx.param_item.id.value = 502
    tx.param_item.payload.value = [0x101, 0x202, 0x303]
    tx.param_item.nested.a.value = 503
    tx.param_item.nested.b.value = 0xD5
    return tx


def _fill_extra() -> CoreExtraTx:
    tx = CoreExtraTx()
    tx.flags.value = LogicValue.from_string("10xz")
    tx.shaped_logic.value = LogicValue.from_string("10xz01zx")
    tx.legacy.value = LogicValue.from_string("10xxzzzz")
    tx.signed_logic.value = LogicValue.from_string("1xxxxxxx")
    tx.shaped_bits.value = 0xA5C3
    tx.header.addr.value = 0xBEEF
    tx.header.extra.value = 0x11
    tx.headers[0].addr.value = 0x1000
    tx.headers[0].extra.value = 0x21
    tx.headers[1].addr.value = 0x2000
    tx.headers[1].extra.value = 0x22
    qh = Header()
    qh.addr.value = 0x0001
    qh.extra.value = 0x02
    tx.q_headers.value = [qh]
    tx.handle.value = RemoteRefValue("acme.Device", 0x0102030405060708)
    tx.skipped.value = 99
    tx.hidden.value = 0x5A5A5A5A
    tx.empty_dyn.value = []
    tx.empty_q.value = []
    tx.matrix.value = [[9, 8], [7, 6]]
    tx.signed_tone.value = SignedTone.NEG
    return tx


def _write_hex(path: Path, data: bytes) -> None:
    path.write_text("".join(f"{byte:02x}\n" for byte in data), encoding="ascii")


def _read_hex(path: Path) -> bytes:
    return bytes(int(line.strip(), 16) for line in path.read_text(encoding="ascii").splitlines() if line.strip())


def _package_sv() -> str:
    assert DriveRequest is not None
    parts = [
        "package target_core_test;",
        "  import svtypes_pkg::*;",
        SignedTone.to_sv_enum(level=1),
        Color.to_sv_enum(level=1),
        Header.to_sv_obj(level=1),
        Inner.to_sv_obj(level=1),
        BaseTx.to_sv_obj(level=1),
        M3Tx.to_sv_obj(level=1),
        ManyTypesTx.to_sv_obj(level=1),
        ParamTx.to_sv_obj(level=1),
        ParamMemberTx.to_sv_obj(level=1),
        CoreExtraTx.to_sv_obj(level=1),
        PlusargBox.to_sv_obj(level=1),
        RandBox.to_sv_obj(level=1),
        DriveRequest.to_sv_obj(level=1),
        "  typedef class GraphNode;",
        "  typedef class GraphPair;",
        "  typedef class GraphQueue;",
        "  typedef class GraphRefQueue;",
        GraphNode.to_sv_obj(level=1),
        GraphPair.to_sv_obj(level=1),
        GraphQueue.to_sv_obj(level=1),
        GraphRefQueue.to_sv_obj(level=1),
        "endpackage",
        "",
    ]
    return "\n".join(parts)


def _tb_sv() -> str:
    extra_fp = encoding_descriptor(CoreExtraTx).encoding_fingerprint_hex
    m3_fp = encoding_descriptor(M3Tx).encoding_fingerprint_hex
    required = ", ".join(f'"{name}"' for name in STABLE_CAPABILITIES)
    return f"""`timescale 1ns/1ps
module tb;
  import svtypes_pkg::*;
  import target_core_test::*;

  function automatic void load_hex(string path, ref byte unsigned bytes[$]);
    integer fd;
    int unsigned val;
    bytes.delete();
    fd = $fopen(path, "r");
    if (fd == 0) $fatal(2, "cannot open %s", path);
    while ($fscanf(fd, "%h", val) == 1) bytes.push_back(val[7:0]);
    $fclose(fd);
  endfunction

  function automatic void store_hex(string path, byte unsigned bytes[$]);
    integer fd;
    fd = $fopen(path, "w");
    if (fd == 0) $fatal(2, "cannot open %s", path);
    foreach (bytes[i]) $fdisplay(fd, "%02x", bytes[i]);
    $fclose(fd);
  endfunction

  task automatic unpack_all(ref byte unsigned bytes[$], input sv_object obj);
    int offset;
    offset = 0;
    obj.unpack(bytes, offset);
    if (offset != bytes.size())
      $fatal(2, "%s unpack offset=%0d size=%0d", obj.svtypes_sprint(), offset, bytes.size());
  endtask

  task automatic run_tree();
    byte unsigned bytes[$];
    M3Tx m3;
    ManyTypesTx many;
    ParamMemberTx param_member;
    CoreExtraTx extra;
    Inner inner;
    DriveRequest drive;
    string sprint_text;

    m3 = new();
    load_hex("m3_in.hex", bytes);
    unpack_all(bytes, m3);
    if (m3.id != 11) $fatal(2, "m3 id");
    if (m3.data[0] != 12 || m3.data[1] != 13) $fatal(2, "m3 data");
    if (m3.label != "base") $fatal(2, "m3 label");
    if (m3.addr != 16'h1234) $fatal(2, "m3 addr");
    if (m3.serial != -55) $fatal(2, "m3 serial");
    if (m3.color != GREEN) $fatal(2, "m3 color");
    if (m3.ratio < 1.499999 || m3.ratio > 1.500001) $fatal(2, "m3 ratio %f", m3.ratio);
    if (m3.temp < 2.4999 || m3.temp > 2.5001) $fatal(2, "m3 temp %f", m3.temp);
    if (m3.dyn.size() != 3 || m3.dyn[2] != 3) $fatal(2, "m3 dyn");
    if (m3.q.size() != 2 || m3.q[1] != 5) $fatal(2, "m3 q");
    if (m3.inner.a != 6 || m3.inner.b != 8'd7) $fatal(2, "m3 inner");

    m3.id = 21;
    m3.data[0] = 22;
    m3.data[1] = 23;
    m3.label = "derived";
    m3.addr = 16'h4567;
    m3.serial = 99;
    m3.color = BLUE;
    m3.ratio = -3.25;
    m3.temp = -4.5;
    m3.dyn = new[2];
    m3.dyn[0] = 8;
    m3.dyn[1] = 9;
    m3.q.delete();
    m3.q.push_back(10);
    m3.q.push_back(11);
    m3.q.push_back(12);
    m3.inner.a = 13;
    m3.inner.b = 8'd14;
    bytes.delete();
    m3.pack(bytes);
    store_hex("m3_out.hex", bytes);

    many = new();
    load_hex("many_in.hex", bytes);
    unpack_all(bytes, many);
    if (many.u1 != 1'b1) $fatal(2, "many u1");
    if (many.u7 != 7'h7e) $fatal(2, "many u7");
    if (many.u9 != 9'h1ab) $fatal(2, "many u9");
    if (many.u33 != 33'h123456789) $fatal(2, "many u33");
    if (many.u65 != 65'h10123456789abcdef) $fatal(2, "many u65");
    if (many.s5 != -5'sd7) $fatal(2, "many s5");
    if (many.s12 != -12'sd1023) $fatal(2, "many s12");
    if (many.i32 != -32'sh01234567) $fatal(2, "many i32");
    if (many.i64 != -64'sh0102030405060708) $fatal(2, "many i64");
    if (many.color != GREEN) $fatal(2, "many color");
    if (many.text != "many-types") $fatal(2, "many text");
    if (many.fp64 < -3.141593 || many.fp64 > -3.141592) $fatal(2, "many fp64 %f", many.fp64);
    if (many.fp32 < 12.4999 || many.fp32 > 12.5001) $fatal(2, "many fp32 %f", many.fp32);
    if (many.fixed_bits[0] != 0 || many.fixed_bits[3] != 7) $fatal(2, "many fixed_bits");
    if (many.fixed_signed[0] != -1 || many.fixed_signed[1] != -17 || many.fixed_signed[2] != 15)
      $fatal(2, "many fixed_signed");
    if (many.matrix[0][0] != 1 || many.matrix[1][1] != -4) $fatal(2, "many matrix");
    if (many.dyn_bits.size() != 3 || many.dyn_bits[2] != 10'h3ff) $fatal(2, "many dyn_bits");
    if (many.dyn_signed.size() != 3 || many.dyn_signed[1] != -100) $fatal(2, "many dyn_signed");
    if (many.q_colors.size() != 3 || many.q_colors[1] != BLUE) $fatal(2, "many q_colors");
    if (!many.assoc.exists("apple") || many.assoc["apple"] != 11) $fatal(2, "many assoc apple");
    if (!many.assoc.exists("banana") || many.assoc["banana"] != -22) $fatal(2, "many assoc banana");
    if (many.q_inner.size() != 2 || many.q_inner[1].a != -20 || many.q_inner[1].b != 8'h22)
      $fatal(2, "many q_inner");
    if (many.inner_arr[0].a != 30 || many.inner_arr[1].b != 8'h44) $fatal(2, "many inner_arr");
    if (many.nested.a != 50 || many.nested.b != 8'h55) $fatal(2, "many nested");

    many.u1 = 1'b0;
    many.u7 = 7'h55;
    many.u9 = 9'h12a;
    many.u33 = 33'h111111111;
    many.u65 = 65'h1fedcba9876543210;
    many.s5 = -5'sd8;
    many.s12 = 12'sd1023;
    many.i32 = 32'h10203040;
    many.i64 = 64'h0102030405060708;
    many.color = BLUE;
    many.text = "sv-many-types";
    many.fp64 = 2.75;
    many.fp32 = -3.5;
    many.fixed_bits[0] = 7;
    many.fixed_bits[1] = 6;
    many.fixed_bits[2] = 1;
    many.fixed_bits[3] = 0;
    many.fixed_signed[0] = -16;
    many.fixed_signed[1] = 0;
    many.fixed_signed[2] = 15;
    many.matrix[0][0] = 9;
    many.matrix[0][1] = 8;
    many.matrix[1][0] = 7;
    many.matrix[1][1] = 6;
    many.dyn_bits = new[3];
    many.dyn_bits[0] = 1;
    many.dyn_bits[1] = 2;
    many.dyn_bits[2] = 10'h3fe;
    many.dyn_signed = new[4];
    many.dyn_signed[0] = -128;
    many.dyn_signed[1] = -1;
    many.dyn_signed[2] = 0;
    many.dyn_signed[3] = 127;
    many.q_colors.delete();
    many.q_colors.push_back(GREEN);
    many.q_colors.push_back(RED);
    many.assoc.delete();
    many.assoc["cat"] = 31;
    many.assoc["dog"] = -41;
    many.q_inner.delete();
    inner = new();
    inner.a = 101;
    inner.b = 8'ha1;
    many.q_inner.push_back(inner);
    inner = new();
    inner.a = -102;
    inner.b = 8'ha2;
    many.q_inner.push_back(inner);
    many.inner_arr[0] = new();
    many.inner_arr[0].a = 201;
    many.inner_arr[0].b = 8'hb1;
    many.inner_arr[1] = new();
    many.inner_arr[1].a = -202;
    many.inner_arr[1].b = 8'hb2;
    many.nested = new();
    many.nested.a = 303;
    many.nested.b = 8'hc3;
    bytes.delete();
    many.pack(bytes);
    store_hex("many_out.hex", bytes);

    param_member = new();
    load_hex("param_member_in.hex", bytes);
    unpack_all(bytes, param_member);
    if (param_member.tag != 501) $fatal(2, "param tag");
    if (param_member.param_item.MODE != 5) $fatal(2, "param MODE");
    if (param_member.param_item.id != 502) $fatal(2, "param id");
    if (param_member.param_item.payload[0] != 12'h101 ||
        param_member.param_item.payload[1] != 12'h202 ||
        param_member.param_item.payload[2] != 12'h303)
      $fatal(2, "param payload");
    if (param_member.param_item.nested.a != 503 || param_member.param_item.nested.b != 8'hd5)
      $fatal(2, "param nested");
    param_member.tag = 601;
    param_member.param_item.id = 602;
    param_member.param_item.payload[0] = 12'h111;
    param_member.param_item.payload[1] = 12'h222;
    param_member.param_item.payload[2] = 12'h333;
    param_member.param_item.nested.a = 603;
    param_member.param_item.nested.b = 8'he6;
    bytes.delete();
    param_member.pack(bytes);
    store_hex("param_member_out.hex", bytes);

    extra = new();
    load_hex("extra_in.hex", bytes);
    unpack_all(bytes, extra);
    if (extra.flags !== 4'b10xz) $fatal(2, "extra flags");
    if (extra.shaped_logic !== 8'b10xz01zx) $fatal(2, "extra shaped_logic");
    if (extra.legacy !== 8'b10xxzzzz) $fatal(2, "extra legacy");
    if (extra.signed_logic !== 8'b1xxxxxxx) $fatal(2, "extra signed_logic");
    if (extra.shaped_bits !== 16'ha5c3) $fatal(2, "extra shaped_bits");
    if (extra.header.addr != 16'hbeef || extra.header.extra != 8'h11) $fatal(2, "extra header");
    if (extra.headers[0].addr != 16'h1000 || extra.headers[1].extra != 8'h22)
      $fatal(2, "extra headers");
    if (extra.q_headers.size() != 1 || extra.q_headers[0].addr != 16'h0001)
      $fatal(2, "extra q_headers");
    if (extra.handle == null || extra.handle.object_number != 64'h0102030405060708)
      $fatal(2, "extra handle");
    if (extra.skipped != 0) $fatal(2, "pack_bytes=False leaked into stream");
    if (extra.empty_dyn.size() != 0 || extra.empty_q.size() != 0) $fatal(2, "extra empty");
    if (extra.matrix[0][0] != 9 || extra.matrix[1][1] != 6) $fatal(2, "extra matrix");
    if (extra.signed_tone != NEG) $fatal(2, "extra signed_tone");

    sprint_text = extra.svtypes_sprint();
    $display("SVTYPES_SPRINT %s", sprint_text);
    extra.svtypes_display();

    extra.flags = 4'b01zx;
    extra.shaped_logic = 8'b01zx10xz;
    extra.legacy = 8'b01zzzzzz;
    extra.signed_logic = 8'b0xxxxxxx;
    extra.shaped_bits = 16'h3c5a;
    extra.header.addr = 16'hcafe;
    extra.header.extra = 8'h33;
    extra.headers[0].addr = 16'h3000;
    extra.q_headers[0].extra = 8'h44;
    extra.handle.object_number = 64'h8877665544332211;
    extra.hidden = 32'h11111111;
    extra.matrix[1][0] = -5;
    extra.signed_tone = POS;
    bytes.delete();
    extra.pack(bytes);
    store_hex("extra_out.hex", bytes);

    drive = new();
    drive.address = 32'h10;
    drive.data = 64'h20;
    bytes.delete();
    drive.pack(bytes);
    store_hex("drive_out.hex", bytes);
  endtask

  task automatic run_graph();
    byte unsigned bytes[$];
    GraphPair pair;
    GraphNode cycle;
    GraphRefQueue refq;
    GraphQueue valq;

    clear_object_registry();
    pair = new();
    load_hex("pair_in.hex", bytes);
    unpack_all(bytes, pair);
    if (pair.left == null || pair.left != pair.right) $fatal(2, "pair shared identity");
    if (pair.left.data != 33) $fatal(2, "pair data");
    if (pair.left.next != null) $fatal(2, "pair next");
    pair.left.data = 44;
    bytes.delete();
    pair.pack(bytes);
    store_hex("pair_out.hex", bytes);

    clear_object_registry();
    cycle = new();
    load_hex("cycle_in.hex", bytes);
    unpack_all(bytes, cycle);
    if (cycle.next != cycle) $fatal(2, "self-cycle identity");
    if (cycle.data != 123) $fatal(2, "cycle data");
    cycle.data = 7;
    bytes.delete();
    cycle.pack(bytes);
    store_hex("cycle_out.hex", bytes);

    clear_object_registry();
    refq = new();
    load_hex("refq_in.hex", bytes);
    unpack_all(bytes, refq);
    if (refq.nodes.size() != 2 || refq.nodes[0] == null) $fatal(2, "refq size");
    if (refq.nodes[0] != refq.nodes[1]) $fatal(2, "refq shared identity");
    if (refq.nodes[0].data != 33) $fatal(2, "refq data");
    refq.nodes[0].data = 55;
    bytes.delete();
    refq.pack(bytes);
    store_hex("refq_out.hex", bytes);

    clear_object_registry();
    valq = new();
    load_hex("valq_in.hex", bytes);
    unpack_all(bytes, valq);
    if (valq.nodes.size() != 2 || valq.nodes[0] == null) $fatal(2, "valq size");
    if (valq.nodes[0] != valq.nodes[1]) $fatal(2, "valq shared identity");
    if (valq.nodes[0].data != 33) $fatal(2, "valq data");
    valq.nodes[0].data = 66;
    bytes.delete();
    valq.pack(bytes);
    store_hex("valq_out.hex", bytes);
  endtask

  task automatic run_policies();
    PlusargBox unused;
    RandBox rand_box;
    ManyTypesTx many;
    ManyTypesTx::ManyTypesTx__svtypes_coverage cov;
    runtime_capabilities caps;
    encoding_descriptor descriptor;
    string required[$];

    descriptor = CoreExtraTx::svtypes_encoding_descriptor();
    if (descriptor.encoding_fingerprint != "{extra_fp}")
      $fatal(2, "CoreExtraTx fingerprint mismatch");
    descriptor = M3Tx::svtypes_encoding_descriptor();
    if (descriptor.encoding_fingerprint != "{m3_fp}")
      $fatal(2, "M3Tx fingerprint mismatch");

    caps = CoreExtraTx::svtypes_runtime_capabilities();
    required = '{{{required}}};
    require_runtime_compatible(required, caps);

    many = new();
    cov = new();
    cov.sample(many);

    rand_box = new();
    if (!rand_box.randomize()) $fatal(2, "RandBox unsat");
    if (rand_box.amount < 8'd1 || rand_box.amount > 8'd7) $fatal(2, "RandBox range");

    unused = new();
    unused.apply_plusargs();
  endtask

  task automatic run_plusarg();
    PlusargBox box;
    box = new();
    box.apply_plusargs();
    if (box.count != 99) $fatal(2, "plusarg count=%0d", box.count);
    $display("SVTYPES_PLUSARG_PASS");
  endtask

  task automatic run_trunc();
    byte unsigned bytes[$];
    M3Tx m3;
    int offset;
    m3 = new();
    load_hex("trunc.hex", bytes);
    offset = 0;
    m3.unpack(bytes, offset);
    $fatal(2, "truncated unpack must not succeed");
  endtask

  initial begin
    string which;
    if (!$value$plusargs("SVTYPES_CASE=%s", which)) which = "core";
    if (which == "core") begin
      run_tree();
      run_graph();
      run_policies();
      $display("SVTYPES_CORE_PASS");
    end else if (which == "plusarg") begin
      run_plusarg();
    end else if (which == "trunc") begin
      run_trunc();
    end else begin
      $fatal(2, "unknown SVTYPES_CASE %s", which);
    end
    $finish;
  end
endmodule
"""


def _host_or_skip() -> str:
    host = os.environ.get("SVTYPES_target_HOST", DEFAULT_HOST)
    reachable = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host, "true"],
        capture_output=True,
        text=True,
    )
    if reachable.returncode != 0:
        pytest.skip(f"remote SystemVerilog target host {host} is not reachable")
    return host


def _ssh(host: str, command: str) -> subprocess.CompletedProcess[str]:
    remote = f"bash -ilc {command!r}"
    return subprocess.run(["ssh", host, remote], capture_output=True, text=True)


def _write_inputs(out: Path) -> None:
    m3 = _fill_m3()
    many = _fill_many()
    param_member = _fill_param_member()
    extra = _fill_extra()
    decoded_skip = CoreExtraTx()
    decoded_skip.from_bytes(extra.to_bytes())
    assert decoded_skip.skipped.value == 0
    assert extra.skipped.value == 99

    _write_hex(out / "m3_in.hex", m3.to_bytes())
    _write_hex(out / "many_in.hex", many.to_bytes())
    _write_hex(out / "param_member_in.hex", param_member.to_bytes())
    _write_hex(out / "extra_in.hex", extra.to_bytes())
    _write_hex(out / "trunc.hex", m3.to_bytes()[:4])

    clear_object_registry()
    child = GraphNode()
    child.data.value = 33
    child.next = None
    pair = GraphPair()
    pair.tag.value = 7
    pair.left = child
    pair.right = child
    refq = GraphRefQueue()
    refq.tag.value = 9
    refq.nodes.value = [child, child]
    valq = GraphQueue()
    valq.tag.value = 10
    valq.nodes.value = [child, child]
    cycle = GraphNode()
    cycle.data.value = 123
    cycle.next = cycle
    _write_hex(out / "pair_in.hex", pair.to_bytes())
    _write_hex(out / "refq_in.hex", refq.to_bytes())
    _write_hex(out / "valq_in.hex", valq.to_bytes())
    _write_hex(out / "cycle_in.hex", cycle.to_bytes())


def test_target_core_generated_sv_contains_feature_markers():
    extra = _fill_extra()
    decoded = CoreExtraTx()
    decoded.from_bytes(extra.to_bytes())
    assert decoded.flags.value == LogicValue.from_string("10xz")
    assert decoded.shaped_logic.value == LogicValue.from_string("10xz01zx")
    assert decoded.legacy.value == LogicValue.from_string("10xxzzzz")
    assert decoded.handle.value == RemoteRefValue("acme.Device", 0x0102030405060708)
    assert decoded.skipped.value == 0
    assert decoded.signed_tone.value == SignedTone.NEG
    assert decoded.header.addr.value == 0xBEEF
    assert decoded.matrix.value == [[9, 8], [7, 6]]

    text = _package_sv()
    assert "rand logic [3:0] flags;" in text
    assert "rand logic [1:0] [3:0] shaped_logic;" in text
    assert "rand reg [7:0] legacy;" in text
    assert "rand logic signed [7:0] signed_logic;" in text
    assert "rand bit [1:0] [7:0] shaped_bits;" in text
    assert "Header_t header;" in text
    assert "handle = new(\"acme.Device\")" in text
    assert "int skipped;" in text
    assert "int_packer::pack(skipped" not in text
    assert "signed_tone_cp: coverpoint item.signed_tone;" in text
    assert "constraint legal {" in text
    assert "class ParamTx #(" in text
    assert "ParamTx#(.MODE(32'd5)) param_item;" in text
    assert "GraphNode next;" in text
    assert "class DriveRequest" in text
    assert "typedef enum bit signed [7:0]" in text


@pytest.fixture(scope="module")
def target_core_sim():
    host = _host_or_skip()
    out = REPO_ROOT / ".tmp" / WORKDIR_NAME
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / "target_core_test.sv").write_text(_package_sv(), encoding="utf-8")
    (out / "tb.sv").write_text(_tb_sv(), encoding="utf-8")
    _write_inputs(out)
    result = _ssh(
        host,
        "cd "
        f"{REMOTE_ROOT}/.tmp/{WORKDIR_NAME} && "
        "rm -rf simv csrc simv.daidir && "
        "target -full64 -sverilog -timescale=1ns/1ps "
        f"+incdir+{REMOTE_ROOT}/svtypes_runtime/sv "
        f"{REMOTE_ROOT}/svtypes_runtime/sv/svtypes_pkg.sv "
        "target_core_test.sv tb.sv -o simv",
    )
    log = result.stdout + result.stderr
    print(log)
    assert result.returncode == 0, log
    return host, out


@pytest.mark.remote_target
def test_remote_target_core_types_graph_and_policies(target_core_sim):
    host, out = target_core_sim
    result = _ssh(host, f"cd {REMOTE_ROOT}/.tmp/{WORKDIR_NAME} && ./simv +SVTYPES_CASE=core")
    log = result.stdout + result.stderr
    print(log)
    assert result.returncode == 0, log
    assert "SVTYPES_CORE_PASS" in log, log
    assert "hidden=" not in log
    assert "flags=" in log or "SVTYPES_SPRINT" in log

    clear_object_registry()
    m3 = M3Tx()
    m3.from_bytes(_read_hex(out / "m3_out.hex"))
    assert m3.id.value == 21
    assert m3.data.value == [22, 23]
    assert m3.label.value == "derived"
    assert m3.addr.value == 0x4567
    assert m3.serial.value == 99
    assert m3.color.value == Color.BLUE
    assert abs(m3.ratio.value - (-3.25)) < 1e-12
    assert abs(m3.temp.value - (-4.5)) < 1e-6
    assert m3.dyn.value == [8, 9]
    assert m3.q.value == [10, 11, 12]
    assert m3.inner.a.value == 13
    assert m3.inner.b.value == 14

    clear_object_registry()
    many = ManyTypesTx()
    many.from_bytes(_read_hex(out / "many_out.hex"))
    assert many.u1.value == 0
    assert many.u7.value == 0x55
    assert many.u9.value == 0x12A
    assert many.u33.value == 0x1_1111_1111
    assert many.u65.value == 0x1_FEDC_BA98_7654_3210
    assert many.s5.value == -8
    assert many.s12.value == 1023
    assert many.i32.value == 0x10203040
    assert many.i64.value == 0x0102030405060708
    assert many.color.value == Color.BLUE
    assert many.text.value == "sv-many-types"
    assert abs(many.fp64.value - 2.75) < 1e-12
    assert abs(many.fp32.value - (-3.5)) < 1e-6
    assert many.fixed_bits.value == [7, 6, 1, 0]
    assert many.fixed_signed.value == [-16, 0, 15]
    assert many.matrix.value == [[9, 8], [7, 6]]
    assert many.dyn_bits.value == [1, 2, 0x3FE]
    assert many.dyn_signed.value == [-128, -1, 0, 127]
    assert many.q_colors.value == [Color.GREEN, Color.RED]
    assert many.assoc.value == {"cat": 31, "dog": -41}
    assert [(v.a.value, v.b.value) for v in many.q_inner] == [(101, 0xA1), (-102, 0xA2)]
    assert [(v.a.value, v.b.value) for v in many.inner_arr] == [(201, 0xB1), (-202, 0xB2)]
    assert many.nested.a.value == 303
    assert many.nested.b.value == 0xC3

    clear_object_registry()
    param_member = ParamMemberTx()
    param_member.from_bytes(_read_hex(out / "param_member_out.hex"))
    assert param_member.tag.value == 601
    assert param_member.param_item.MODE.value == 5
    assert param_member.param_item.id.value == 602
    assert param_member.param_item.payload.value == [0x111, 0x222, 0x333]
    assert param_member.param_item.nested.a.value == 603
    assert param_member.param_item.nested.b.value == 0xE6

    clear_object_registry()
    extra = CoreExtraTx()
    extra.from_bytes(_read_hex(out / "extra_out.hex"))
    assert extra.flags.value == LogicValue.from_string("01zx")
    assert extra.shaped_logic.value == LogicValue.from_string("01zx10xz")
    assert extra.legacy.value == LogicValue.from_string("01zzzzzz")
    assert extra.signed_logic.value == LogicValue.from_string("0xxxxxxx")
    assert extra.shaped_bits.value == 0x3C5A
    assert extra.header.addr.value == 0xCAFE
    assert extra.header.extra.value == 0x33
    assert extra.headers[0].addr.value == 0x3000
    assert extra.q_headers[0].extra.value == 0x44
    assert extra.handle.value == RemoteRefValue("acme.Device", 0x8877665544332211)
    assert extra.skipped.value == 0
    assert extra.hidden.value == 0x11111111
    assert extra.matrix.value == [[9, 8], [-5, 6]]
    assert extra.signed_tone.value == SignedTone.POS

    drive = DriveRequest()
    drive.from_bytes(_read_hex(out / "drive_out.hex"))
    assert drive.address.value == 0x10
    assert drive.data.value == 0x20

    clear_object_registry()
    pair = GraphPair()
    pair.from_bytes(_read_hex(out / "pair_out.hex"))
    assert pair.left is pair.right
    assert pair.left.data.value == 44

    clear_object_registry()
    cycle = GraphNode()
    cycle.from_bytes(_read_hex(out / "cycle_out.hex"))
    assert cycle.next is cycle
    assert cycle.data.value == 7

    clear_object_registry()
    refq = GraphRefQueue()
    refq.from_bytes(_read_hex(out / "refq_out.hex"))
    assert refq.nodes[0] is refq.nodes[1]
    assert refq.nodes[0].data.value == 55

    clear_object_registry()
    valq = GraphQueue()
    valq.from_bytes(_read_hex(out / "valq_out.hex"))
    assert valq.nodes[0] is valq.nodes[1]
    assert valq.nodes[0].data.value == 66


@pytest.mark.remote_target
def test_remote_target_plusarg_overrides_scalar(target_core_sim):
    host, _out = target_core_sim
    result = _ssh(
        host,
        f"cd {REMOTE_ROOT}/.tmp/{WORKDIR_NAME} && ./simv +SVTYPES_CASE=plusarg +count=99",
    )
    log = result.stdout + result.stderr
    print(log)
    assert result.returncode == 0, log
    assert "SVTYPES_PLUSARG_PASS" in log, log


@pytest.mark.remote_target
def test_remote_target_truncated_unpack_fails(target_core_sim):
    host, _out = target_core_sim
    result = _ssh(host, f"cd {REMOTE_ROOT}/.tmp/{WORKDIR_NAME} && ./simv +SVTYPES_CASE=trunc")
    log = result.stdout + result.stderr
    print(log)
    # SystemVerilog target `$fatal` terminates the simulation but leaves the simv process exit
    # code at 0, so the failure evidence is the underflow fatal message, not
    # the return code.
    assert "SVTYPES_CORE_PASS" not in log, log
    assert "unpack underflow" in log, log

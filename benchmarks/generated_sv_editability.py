#!/usr/bin/env python3
"""Compare a generated SV field visitor with explicitly expanded field codecs.

Uses the configured remote target and a shared checkout. A DPI clock measures
process CPU time inside one simulation; run without other target workloads.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import statistics
import subprocess
import tempfile
from time import perf_counter

from svtypes import Bit, DynArray, Object, ObjectRegistry, SvObject


class EditabilitySvBench(SvObject):
    first = Bit[32](rand=False, cov=False)
    second = Bit[32](rand=False, cov=False)
    third = Bit[32](rand=False, cov=False)


class EditabilitySvLargeBench(SvObject):
    first = Bit[32](rand=False, cov=False)
    data = DynArray[Bit[16]](rand=False, cov=False)


graph_registry = ObjectRegistry()


class EditabilitySvGraphChild(SvObject):
    value = Bit[16](rand=False, cov=False)
    next_node = Object["EditabilitySvGraphChild"](registry=graph_registry)


graph_registry.register(EditabilitySvGraphChild)


class EditabilitySvGraphBench(SvObject):
    first = Bit[32](rand=False, cov=False)
    left = Object["EditabilitySvGraphChild"](registry=graph_registry)
    right = Object["EditabilitySvGraphChild"](registry=graph_registry)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=10000)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--scenario", choices=("scalar", "large", "graph"), default="scalar")
    parser.add_argument("--length", type=int, default=4096, help="dynamic array elements in large scenario")
    args = parser.parse_args()
    if args.iterations <= 0 or args.runs <= 0 or args.length <= 0:
        parser.error("iterations, runs and length must be positive")
    root = Path(__file__).resolve().parents[1]
    tmp_root = root / ".tmp"
    tmp_root.mkdir(exist_ok=True)
    target = Path(tempfile.mkdtemp(prefix="editability-sv-bench-", dir=tmp_root))
    remote = os.environ["SVTYPES_REMOTE_SV_ROOT"].rstrip("/") + "/.tmp/" + target.name
    host = os.environ["SVTYPES_REMOTE_SV_HOST"]
    runner = shlex.split(os.environ["SVTYPES_REMOTE_SV_RUNNER"])
    model = {"scalar": EditabilitySvBench, "large": EditabilitySvLargeBench,
             "graph": EditabilitySvGraphBench}[args.scenario]
    typename = model._encoding_type_name()
    fingerprint = model._encoding_fingerprint_hex()
    generated = model.to_sv_obj()
    extra_declaration = counter_reset = counter_check = generated_counter = direct_counter = ""
    if args.scenario == "scalar":
        fields = [(name, f"svtypes_pkg::bit_packer#(type(this.{name}))")
                  for name in ("first", "second", "third")]
        initialize = "source.first = 32'h12345678; source.second = 32'h23456789; source.third = 32'h3456789a;"
        mismatch = "restored.second !== source.second || restored.third !== source.third"
        initial_validation = ""
    elif args.scenario == "large":
        fields = [("first", "svtypes_pkg::bit_packer#(type(this.first))"),
                  ("data", "svtypes_pkg::dyn_array_packer#(type(this.data[0]), "
                           "svtypes_pkg::bit_packer#(type(this.data[0])))")]
        initialize = f"source.data = new[{args.length}]; foreach (source.data[i]) source.data[i] = 16'(i);"
        mismatch = ("restored.data.size() != source.data.size() || restored.data[0] !== source.data[0] || "
                    "restored.data[source.data.size()-1] !== source.data[source.data.size()-1]")
        initial_validation = ("offset = 0; restored.unpack_body(bytes, offset); "
                              "if (restored.data !== source.data || offset != bytes.size()) "
                              "$fatal(1, \"initial array roundtrip mismatch\");")
    else:
        fields = [("first", "svtypes_pkg::bit_packer#(type(this.first))"),
                  ("left", "svtypes_pkg::object_packer#(EditabilitySvGraphChild)"),
                  ("right", "svtypes_pkg::object_packer#(EditabilitySvGraphChild)")]
        extra_declaration = "CountingGraphChild child;"
        initialize = ("child = new(); child.value = 123; child.next_node = child; "
                      "source.left = child; source.right = child;")
        mismatch = ("restored.left == null || restored.left != restored.right || "
                    "restored.left.next_node != restored.left || restored.left.value != 123")
        initial_validation = ("offset = 0; restored.unpack_body(bytes, offset); "
                              "if (restored.left == null || restored.left != restored.right || "
                              "restored.left.next_node != restored.left || child.unpack_calls != 1 || "
                              "offset != bytes.size()) "
                              "$fatal(1, \"initial graph identity mismatch\");")
        generated_counter = 'if (child.pack_calls != 1) $fatal(1, "generated child traversal count"); child.pack_calls = 0;'
        direct_counter = 'if (child.pack_calls != 1) $fatal(1, "direct child traversal count");'
        counter_reset = "child.pack_calls = 0; child.unpack_calls = 0;"
        counter_check = ('if (child.pack_calls != i + 1 || child.unpack_calls != i + 1) '
                         '$fatal(1, "extra child traversal");')
    pack_fields = "\n".join(f"    {codec}::pack(this.{name}, bytes);" for name, codec in fields)
    unpack_fields = "\n".join(f"    {codec}::unpack(this.{name}, bytes, offset);" for name, codec in fields)
    direct = f'''
  function void benchmark_direct_pack(ref byte unsigned bytes[$]);
    ensure_svtypes_object_number();
    svtypes_pkg::register_object(this);
    svtypes_pkg::pack_object_header("{typename}", "{fingerprint}", {len(fields)}, __svtypes_object_number, bytes);
{pack_fields}
  endfunction
  function void benchmark_direct_unpack(ref byte unsigned bytes[$], ref int offset);
    longint unsigned number;
    svtypes_pkg::unpack_object_header("{typename}", "{fingerprint}", 0, number, bytes, offset);
    __svtypes_object_number = number;
    svtypes_pkg::register_object(this);
{unpack_fields}
  endfunction
'''
    insertion = generated.rfind("endclass")
    if insertion < 0:
        raise RuntimeError("generated class terminator missing")
    generated = generated[:insertion] + direct + generated[insertion:]
    if args.scenario == "graph":
        generated = EditabilitySvGraphChild.to_sv_obj() + r'''
class CountingGraphChild extends EditabilitySvGraphChild;
  int pack_calls;
  int unpack_calls;
  virtual function void pack_body(ref byte unsigned bytes[$]);
    pack_calls++;
    super.pack_body(bytes);
  endfunction
  virtual function void unpack_body(ref byte unsigned bytes[$], ref int offset);
    unpack_calls++;
    super.unpack_body(bytes, offset);
  endfunction
endclass
''' + generated
    bench = r'''
import "DPI-C" function longint unsigned benchmark_cpu_ns();
module tb;
  initial begin
    MODEL_NAME source, restored;
    EXTRA_DECLARATION
    byte unsigned bytes[$], direct_bytes[$];
    int offset;
    bit direct_mode;
    longint unsigned started, elapsed;
    source = new(); restored = new();
    INITIALIZE
    source.pack_body(bytes);
    GENERATED_COUNTER
    source.benchmark_direct_pack(direct_bytes);
    DIRECT_COUNTER
    if (bytes != direct_bytes) $fatal(1, "wire mismatch");
    INITIAL_VALIDATION
    for (int run = 0; run < RUN_COUNT; run++) begin
      for (int phase = 0; phase < 2; phase++) begin
        direct_mode = (run % 2 == 0) ? (phase == 1) : (phase == 0);
        COUNTER_RESET
        started = benchmark_cpu_ns();
        for (int i = 0; i < ITERATIONS; i++) begin
          source.first = i;
          bytes.delete(); offset = 0;
          if (direct_mode) begin source.benchmark_direct_pack(bytes); restored.benchmark_direct_unpack(bytes, offset); end
          else begin source.pack_body(bytes); restored.unpack_body(bytes, offset); end
          if (restored.first !== source.first || MISMATCH || offset != bytes.size()) $fatal(1, "roundtrip mismatch");
          COUNTER_CHECK
        end
        elapsed = benchmark_cpu_ns() - started;
        $display("SVTYPES_BENCH %s %0d", direct_mode ? "direct" : "generated", elapsed);
      end
    end
    $display("SVTYPES_BENCHMARK_PASS");
    $finish;
  end
endmodule
'''.replace("ITERATIONS", str(args.iterations)).replace("RUN_COUNT", str(args.runs))
    bench = bench.replace("MODEL_NAME", model.__name__).replace("INITIALIZE", initialize).replace("MISMATCH", mismatch)
    bench = bench.replace("INITIAL_VALIDATION", initial_validation)
    bench = (bench.replace("EXTRA_DECLARATION", extra_declaration).replace("COUNTER_RESET", counter_reset)
             .replace("COUNTER_CHECK", counter_check).replace("GENERATED_COUNTER", generated_counter)
             .replace("DIRECT_COUNTER", direct_counter))
    if args.scenario == "graph":
        for call in ("source.pack_body(bytes);", "source.benchmark_direct_pack(bytes);",
                     "source.benchmark_direct_pack(direct_bytes);"):
            bench = bench.replace(call, "svtypes_pkg::begin_pack_graph(); " + call + " svtypes_pkg::end_pack_graph();")
    (target / "tb.sv").write_text(generated + "\n" + bench)
    (target / "benchmark_clock.c").write_text(r'''
#include <stdint.h>
#include <time.h>
uint64_t benchmark_cpu_ns(void) {
    struct timespec value;
    if (clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &value) != 0) return 0;
    return (uint64_t)value.tv_sec * 1000000000ull + (uint64_t)value.tv_nsec;
}
''')

    def execute(command):
        remote_command = f"cd {shlex.quote(remote)} && time -p {shlex.join(runner + command)}"
        started = perf_counter()
        result = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                                 host, "bash -ilc " + shlex.quote(remote_command)],
                                capture_output=True, text=True, timeout=180)
        elapsed = perf_counter() - started
        if result.returncode:
            (target / "private-error.log").write_text(result.stdout + result.stderr)
            raise RuntimeError(f"target operation failed; private log: {target / 'private-error.log'}")
        cpu = re.findall(r"(?m)^user ([0-9.]+)$", result.stderr)
        return elapsed, result.stdout, float(cpu[-1]) if cpu else None

    execute(["compile", "tb.sv", "benchmark_clock.c"])
    timings = {"generated": [], "direct": []}
    elapsed, output, cpu = execute(["run"])
    if "SVTYPES_BENCHMARK_PASS" not in output:
        raise RuntimeError("target benchmark did not report successful roundtrip")
    for mode, nanoseconds in re.findall(r"(?m)^SVTYPES_BENCH\s+(generated|direct)\s+(\d+)\s*$", output):
        timings[mode].append(int(nanoseconds) / 1e9)
    if any(len(values) != args.runs or min(values) <= 0 for values in timings.values()):
        raise RuntimeError("target benchmark did not report all CPU-time measurements")
    medians = {mode: statistics.median(values) for mode, values in timings.items()}
    paired_ratios = [generated / direct for generated, direct in
                     zip(timings["generated"], timings["direct"])]
    print(json.dumps({"scenario": args.scenario, "length": args.length if args.scenario == "large" else None,
                      "iterations": args.iterations, "runs": args.runs,
                      "median_cpu_seconds": medians,
                      "individual_cpu_seconds": timings,
                      "generated_over_direct": medians["generated"] / medians["direct"],
                      "paired_ratios": paired_ratios,
                      "median_paired_ratio": statistics.median(paired_ratios),
                      "process_wall_seconds": elapsed,
                      "process_user_seconds": cpu,
                      "includes_transport_and_startup": False}, sort_keys=True))
    shutil.rmtree(target)


if __name__ == "__main__":
    main()

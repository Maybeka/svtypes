"""Build and compare an actual generated C++ object's encode/decode paths.

Run with PYTHONPATH=python python benchmarks/generated_object_editability.py.
The build and its executable live only in a temporary directory.
"""

from pathlib import Path
import shutil
import subprocess
import tempfile

from svtypes import Bit, DynArray, Int, Object, ObjectRegistry, SvObject
from svtypes.codegen_artifacts import collect_support_artifacts


class EditabilityBenchObject(SvObject):
    _svtypes_unified_type_name = "benchmark.EditabilityBenchObject"
    first = Int(rand=False, cov=False)
    second = Int(rand=False, cov=False)
    payload = DynArray[Bit[16]](rand=False, cov=False)


_graph_registry = ObjectRegistry()


class EditabilityGraphChild(SvObject):
    _svtypes_unified_type_name = "benchmark.EditabilityGraphChild"
    value = Int(rand=False, cov=False)
    next_node = Object["EditabilityGraphChild"](registry=_graph_registry)


_graph_registry.register(EditabilityGraphChild)


class EditabilityGraph(SvObject):
    _svtypes_unified_type_name = "benchmark.EditabilityGraph"
    left = Object["EditabilityGraphChild"](registry=_graph_registry)
    right = Object["EditabilityGraphChild"](registry=_graph_registry)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    compiler = shutil.which("c++")
    if compiler is None:
        raise RuntimeError("C++ compiler required")
    with collect_support_artifacts() as artifacts:
        generated = EditabilityBenchObject.to_cpp_obj()
    support_size = sum(len(value) for value in artifacts.values())
    with collect_support_artifacts() as graph_artifacts:
        graph_child = EditabilityGraphChild.to_cpp_obj()
        graph = EditabilityGraph.to_cpp_obj()
    artifacts.update(graph_artifacts)
    inline = EditabilityBenchObject.to_cpp_obj()
    main_size = len(generated.encode())
    print(f"generated_main_bytes={main_size} generated_support_bytes={support_size} "
          f"support_share={support_size / (main_size + support_size):.4f} inline_bytes={len(inline.encode())}", flush=True)
    with tempfile.TemporaryDirectory(prefix="svtypes-editability-bench-") as directory:
        directory = Path(directory)
        for name, content in artifacts.items():
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        model = ('#include "svtypes.hpp"\n'
                 f'#define BENCH_TYPE_NAME "{EditabilityBenchObject._encoding_type_name()}"\n'
                 f'#define BENCH_FINGERPRINT "{EditabilityBenchObject._encoding_fingerprint_hex()}"\n'
                 f'#define GRAPH_TYPE_NAME "{EditabilityGraph._encoding_type_name()}"\n'
                 f'#define GRAPH_FINGERPRINT "{EditabilityGraph._encoding_fingerprint_hex()}"\n'
                 + generated + "\n" + graph_child + "\n" + graph)
        (directory / "benchmark_model.hpp").write_text(model)
        executable = directory / "bench"
        subprocess.run([compiler, "-std=c++20", "-O2", "-I", str(root / "svtypes_runtime/cpp"),
                        "-I", str(directory), str(root / "benchmarks/generated_object_editability.cpp"),
                        "-o", str(executable)], check=True)
        subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    main()

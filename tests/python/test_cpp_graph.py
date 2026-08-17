import subprocess
import tempfile
from pathlib import Path

from examples.milestone_4_graph.tests.types import GraphNode, GraphPair, GraphQueue, GraphRefQueue
from svtypes import clear_object_registry


def _compile_and_run(tmp_path: Path, source: str, model: str):
    (tmp_path / "model.hpp").write_text(
        "#pragma once\n"
        '#include "svtypes.hpp"\n\n'
        f"{model}\n",
        encoding="utf-8",
    )
    (tmp_path / "main.cpp").write_text(source, encoding="utf-8")

    compile_result = subprocess.run(
        ["g++", "-std=c++20", "main.cpp", "-I.", f"-I{Path.cwd()}", f"-I{Path.cwd() / 'svtypes_runtime' / 'cpp'}", "-o", "graph_test"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert compile_result.returncode == 0, compile_result.stderr

    run_result = subprocess.run(
        ["./graph_test"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert run_result.returncode == 0, run_result.stderr + run_result.stdout


def test_cpp_graph_reference_parity_shared_pair_and_ref_queue():
    clear_object_registry()

    child = GraphNode()
    child.data.value = 33
    child.next = None

    pair = GraphPair()
    pair.tag.value = 7
    pair.left = child
    pair.right = child

    queue = GraphRefQueue()
    queue.tag.value = 9
    queue.nodes.value = [child, child]
    value_decl_queue = GraphQueue()
    value_decl_queue.tag.value = 10
    value_decl_queue.nodes.value = [child, child]

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "pair_in.bin").write_bytes(pair.to_bytes())
        (tmp_path / "queue_in.bin").write_bytes(queue.to_bytes())
        (tmp_path / "value_decl_queue_in.bin").write_bytes(value_decl_queue.to_bytes())
        _compile_and_run(
            tmp_path,
            r'''
#include <cstdint>
#include <fstream>
#include <iostream>
#include <iterator>
#include <vector>
#include "model.hpp"

static std::vector<uint8_t> read_file(const char* path) {
    std::ifstream is(path, std::ios::binary);
    return std::vector<uint8_t>((std::istreambuf_iterator<char>(is)), std::istreambuf_iterator<char>());
}

static void write_file(const char* path, const std::vector<uint8_t>& bytes) {
    std::ofstream os(path, std::ios::binary);
    os.write(reinterpret_cast<const char*>(bytes.data()), bytes.size());
}

static void require(bool cond, const char* msg) {
    if (!cond) {
        std::cerr << msg << std::endl;
        std::exit(2);
    }
}

int main() {
    GraphPair pair;
    std::vector<uint8_t> pair_bytes = read_file("pair_in.bin");
    size_t offset = 0;
    pair.unpack(pair_bytes, offset);
    require(offset == pair_bytes.size(), "pair offset mismatch");
    require(pair.left != nullptr, "pair left null");
    require(pair.left == pair.right, "pair shared identity lost");
    require(pair.left->data == 33, "pair child data mismatch");

    pair.left->data = 44;
    std::vector<uint8_t> pair_out;
    pair.pack(pair_out);
    write_file("pair_out.bin", pair_out);

    GraphRefQueue queue;
    std::vector<uint8_t> queue_bytes = read_file("queue_in.bin");
    offset = 0;
    queue.unpack(queue_bytes, offset);
    require(offset == queue_bytes.size(), "queue offset mismatch");
    require(queue.nodes.size() == 2, "queue size mismatch");
    require(queue.nodes[0] != nullptr, "queue node null");
    require(queue.nodes[0] == queue.nodes[1], "queue shared identity lost");
    require(queue.nodes[0]->data == 33, "registry update mismatch");

    queue.nodes[0]->data = 55;
    std::vector<uint8_t> queue_out;
    queue.pack(queue_out);
    write_file("queue_out.bin", queue_out);

    GraphQueue value_decl_queue;
    std::vector<uint8_t> value_decl_queue_bytes = read_file("value_decl_queue_in.bin");
    offset = 0;
    value_decl_queue.unpack(value_decl_queue_bytes, offset);
    require(offset == value_decl_queue_bytes.size(), "value-decl queue offset mismatch");
    require(value_decl_queue.nodes.size() == 2, "value-decl queue size mismatch");
    require(value_decl_queue.nodes[0] != nullptr, "value-decl queue node null");
    require(value_decl_queue.nodes[0] == value_decl_queue.nodes[1], "value-decl queue shared identity lost");
    require(value_decl_queue.nodes[0]->data == 33, "value-decl queue data mismatch");

    value_decl_queue.nodes[0]->data = 66;
    std::vector<uint8_t> value_decl_queue_out;
    value_decl_queue.pack(value_decl_queue_out);
    write_file("value_decl_queue_out.bin", value_decl_queue_out);
    return 0;
}
''',
            f"{GraphNode.to_cpp_obj()}\n\n"
            f"{GraphPair.to_cpp_obj()}\n\n"
            f"{GraphQueue.to_cpp_obj()}\n\n"
            f"{GraphRefQueue.to_cpp_obj()}",
        )

        clear_object_registry()
        out_pair = GraphPair()
        out_pair.from_bytes((tmp_path / "pair_out.bin").read_bytes())
        assert out_pair.left is out_pair.right
        assert out_pair.left.data.value == 44

        out_queue = GraphRefQueue()
        out_queue.from_bytes((tmp_path / "queue_out.bin").read_bytes())
        assert out_queue.nodes[0] is out_queue.nodes[1]
        assert out_queue.nodes[0].data.value == 55

        out_value_decl_queue = GraphQueue()
        out_value_decl_queue.from_bytes((tmp_path / "value_decl_queue_out.bin").read_bytes())
        assert out_value_decl_queue.nodes[0] is out_value_decl_queue.nodes[1]
        assert out_value_decl_queue.nodes[0].data.value == 66


def test_cpp_graph_reference_parity_self_cycle():
    clear_object_registry()

    node = GraphNode()
    node.data.value = 123
    node.next = node

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "node_in.bin").write_bytes(node.to_bytes())
        _compile_and_run(
            tmp_path,
            r'''
#include <cstdint>
#include <fstream>
#include <iostream>
#include <iterator>
#include <vector>
#include "model.hpp"

static void require(bool cond, const char* msg) {
    if (!cond) {
        std::cerr << msg << std::endl;
        std::exit(2);
    }
}

int main() {
    std::ifstream is("node_in.bin", std::ios::binary);
    std::vector<uint8_t> bytes((std::istreambuf_iterator<char>(is)), std::istreambuf_iterator<char>());

    GraphNode node;
    size_t offset = 0;
    node.unpack(bytes, offset);
    require(offset == bytes.size(), "node offset mismatch");
    require(node.data == 123, "node data mismatch");
    require(node.next == &node, "self-cycle identity lost");

    node.data = 124;
    std::vector<uint8_t> out;
    node.pack(out);
    std::ofstream os("node_out.bin", std::ios::binary);
    os.write(reinterpret_cast<const char*>(out.data()), out.size());
    return 0;
}
''',
            GraphNode.to_cpp_obj(),
        )

        clear_object_registry()
        out_node = GraphNode()
        out_node.from_bytes((tmp_path / "node_out.bin").read_bytes())
        assert out_node.next is out_node
        assert out_node.data.value == 124


def test_cpp_object_reference_errors_are_clear():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _compile_and_run(
            tmp_path,
            r'''
#include <cstdint>
#include <exception>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>
#include "model.hpp"

static void append_u64(std::vector<uint8_t>& bytes, uint64_t value) {
    for (int i = 0; i < 8; ++i) {
        bytes.push_back(static_cast<uint8_t>((value >> (i * 8)) & 0xffu));
    }
}

static void expect_error(const std::vector<uint8_t>& bytes, const std::string& needle) {
    GraphNode* node = nullptr;
    size_t offset = 0;
    try {
        svtypes::object_packer<GraphNode>::unpack(node, bytes, offset);
    } catch (const std::runtime_error& err) {
        if (std::string(err.what()).find(needle) != std::string::npos) {
            return;
        }
        std::cerr << "wrong error: " << err.what() << std::endl;
        std::exit(2);
    }
    std::cerr << "missing expected error: " << needle << std::endl;
    std::exit(2);
}

int main() {
    std::vector<uint8_t> zero = {2};
    append_u64(zero, 0);
    expect_error(zero, "id 0");

    std::vector<uint8_t> unresolved = {2};
    append_u64(unresolved, 0x0003000000001234ULL);
    expect_error(unresolved, "unresolved object reference");

    GraphPair pair;
    std::vector<uint8_t> mismatch = {2};
    append_u64(mismatch, pair.__svtypes_object_number);
    expect_error(mismatch, "type mismatch");
    return 0;
}
''',
            f"{GraphNode.to_cpp_obj()}\n\n{GraphPair.to_cpp_obj()}",
        )


def test_cpp_registry_drops_destroyed_object_entries():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _compile_and_run(
            tmp_path,
            r'''
#include <cstdint>
#include <iostream>
#include "model.hpp"

static void require(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << std::endl;
        std::exit(2);
    }
}

int main() {
    uint64_t number = 0;
    {
        GraphNode node;
        number = node.__svtypes_object_number;
        require(svtypes::get_object(number) == &node, "missing live registry entry");
    }
    require(svtypes::get_object(number) == nullptr, "destroyed object remains registered");
    return 0;
}
''',
            GraphNode.to_cpp_obj(),
        )


def test_cpp_codec_sessions_isolate_registry_and_allocator_state():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _compile_and_run(
            tmp_path,
            r'''
#include <cstdint>
#include <iostream>
#include "model.hpp"

static void require(bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << std::endl;
        std::exit(2);
    }
}

int main() {
    svtypes::CodecSession first;
    svtypes::CodecSession second;
    uint64_t number = 0;
    {
        svtypes::CodecSessionScope first_scope(first);
        GraphNode first_node;
        number = first_node.__svtypes_object_number;
        require(svtypes::get_object(number) == &first_node, "first session entry missing");
        {
            svtypes::CodecSessionScope second_scope(second);
            require(svtypes::get_object(number) == nullptr, "session registry leaked");
            GraphNode second_node;
            require(second_node.__svtypes_object_number == number, "session allocator not isolated");
            require(svtypes::get_object(number) == &second_node, "second session entry missing");
        }
        require(svtypes::get_object(number) == &first_node, "first session entry changed");
    }
    return 0;
}
''',
            GraphNode.to_cpp_obj(),
        )

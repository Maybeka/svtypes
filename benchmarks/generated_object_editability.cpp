// Compared with the generated object's current methods, the direct path
// expands the same header, registration and three field operations explicitly.
// The accompanying Python driver generates benchmark_model.hpp.
#include "benchmark_model.hpp"
#include <atomic>
#include <chrono>
#include <cstdlib>
#include <new>

static std::atomic<uint64_t> allocation_count{0};

void* operator new(std::size_t size) {
    allocation_count.fetch_add(1, std::memory_order_relaxed);
    if (void* memory = std::malloc(size ? size : 1)) return memory;
    throw std::bad_alloc();
}
void operator delete(void* memory) noexcept { std::free(memory); }
void operator delete(void* memory, std::size_t) noexcept { std::free(memory); }

struct Measurement { double nanoseconds; double allocations; uint64_t checksum; };

static constexpr auto bench_fingerprint = svtypes::fingerprint_literal(BENCH_FINGERPRINT);
static constexpr auto graph_fingerprint = svtypes::fingerprint_literal(GRAPH_FINGERPRINT);

struct CountingGraphChild : EditabilityGraphChild {
    mutable unsigned packs = 0;
    unsigned unpacks = 0;
    void pack_body(std::vector<uint8_t>& bytes) const override {
        ++packs;
        EditabilityGraphChild::pack_body(bytes);
    }
    void unpack_body(const std::vector<uint8_t>& bytes, std::size_t& offset) override {
        ++unpacks;
        EditabilityGraphChild::unpack_body(bytes, offset);
    }
};

struct DirectGraph : EditabilityGraph {
    void pack_body(std::vector<uint8_t>& bytes) const override {
        svtypes::register_object(const_cast<DirectGraph*>(this));
        svtypes::pack_object_header(GRAPH_TYPE_NAME, graph_fingerprint, 2,
                                    __svtypes_object_number, bytes);
        svtypes::pack(left, bytes);
        svtypes::pack(right, bytes);
    }
    void unpack_body(const std::vector<uint8_t>& bytes, std::size_t& offset) override {
        uint64_t number;
        svtypes::unpack_object_header(GRAPH_TYPE_NAME, graph_fingerprint, 2, number, bytes, offset);
        __svtypes_object_number = number;
        svtypes::register_object(this);
        svtypes::unpack(left, bytes, offset);
        svtypes::unpack(right, bytes, offset);
    }
};

template<class Root>
Measurement measure_graph(std::size_t iterations) {
    EditabilityGraphChild child;
    child.value = 17;
    child.next_node = &child;
    Root source, restored;
    source.left = &child;
    source.right = &child;
    std::vector<uint8_t> bytes;
    bytes.reserve(512);
    uint64_t checksum = 0;
    auto allocations_before = allocation_count.load(std::memory_order_relaxed);
    auto start = std::chrono::steady_clock::now();
    for (std::size_t i = 0; i < iterations; ++i) {
        child.value = static_cast<int32_t>(i);
        bytes.clear();
        source.pack(bytes);
        std::size_t offset = 0;
        restored.unpack(bytes, offset);
        if (offset != bytes.size() || !restored.left || restored.left != restored.right ||
            restored.left->next_node != restored.left || restored.left->value != child.value)
            throw std::runtime_error("shared cyclic graph mismatch");
        checksum += static_cast<uint32_t>(restored.left->value);
    }
    auto elapsed = std::chrono::steady_clock::now() - start;
    auto allocations = allocation_count.load(std::memory_order_relaxed) - allocations_before;
    return {std::chrono::duration<double, std::nano>(elapsed).count() / iterations,
            static_cast<double>(allocations) / iterations, checksum};
}

template<bool Generated>
Measurement measure(std::size_t length, std::size_t iterations) {
    EditabilityBenchObject source, restored;
    source.payload.resize(length, 17);
    restored.payload.reserve(length);
    std::vector<uint8_t> bytes;
    bytes.reserve(128 + length * 2);
    uint64_t checksum = 0;
    auto allocations_before = allocation_count.load(std::memory_order_relaxed);
    auto start = std::chrono::steady_clock::now();
    for (std::size_t i = 0; i < iterations; ++i) {
        source.first = static_cast<int32_t>(i);
        source.second = -7;
        bytes.clear();
        std::size_t offset = 0;
        if constexpr (Generated) {
            source.pack_body(bytes);
            restored.unpack_body(bytes, offset);
        } else {
            svtypes::register_object(&source);
            svtypes::pack_object_header(BENCH_TYPE_NAME, bench_fingerprint, 3,
                                        source.__svtypes_object_number, bytes);
            svtypes::pack(source.first, bytes);
            svtypes::pack(source.second, bytes);
            svtypes::pack(source.payload, bytes);
            uint64_t number;
            svtypes::unpack_object_header(BENCH_TYPE_NAME, bench_fingerprint, 3, number, bytes, offset);
            restored.__svtypes_object_number = number;
            svtypes::register_object(&restored);
            svtypes::unpack(restored.first, bytes, offset);
            svtypes::unpack(restored.second, bytes, offset);
            svtypes::unpack(restored.payload, bytes, offset);
        }
        if (offset != bytes.size() || restored.first != source.first || restored.second != source.second ||
            restored.payload != source.payload) throw std::runtime_error("roundtrip mismatch");
        checksum += static_cast<uint32_t>(restored.first) + restored.payload.size();
    }
    auto elapsed = std::chrono::steady_clock::now() - start;
    auto allocations = allocation_count.load(std::memory_order_relaxed) - allocations_before;
    return {std::chrono::duration<double, std::nano>(elapsed).count() / iterations,
            static_cast<double>(allocations) / iterations, checksum};
}

int main() {
    {
        EditabilityBenchObject value;
        value.first = 5; value.second = -7; value.payload = {1, 2, 3};
        std::vector<uint8_t> generated_bytes, direct_bytes;
        value.pack_body(generated_bytes);
        svtypes::register_object(&value);
        svtypes::pack_object_header(BENCH_TYPE_NAME, bench_fingerprint, 3,
                                    value.__svtypes_object_number, direct_bytes);
        svtypes::pack(value.first, direct_bytes);
        svtypes::pack(value.second, direct_bytes);
        svtypes::pack(value.payload, direct_bytes);
        if (generated_bytes != direct_bytes) throw std::runtime_error("benchmark wire mismatch");
    }
    for (std::size_t length : {std::size_t(8), std::size_t(4096)}) {
        const std::size_t iterations = length == 8 ? 100000 : 2000;
        std::vector<Measurement> direct, generated;
        for (int repeat = 0; repeat < 7; ++repeat) {
            if (repeat % 2) {
                generated.push_back(measure<true>(length, iterations));
                direct.push_back(measure<false>(length, iterations));
            } else {
                direct.push_back(measure<false>(length, iterations));
                generated.push_back(measure<true>(length, iterations));
            }
            if (direct.back().checksum != generated.back().checksum)
                throw std::runtime_error("benchmark checksums differ");
        }
        auto by_time = [](const Measurement& a, const Measurement& b) { return a.nanoseconds < b.nanoseconds; };
        std::sort(direct.begin(), direct.end(), by_time);
        std::sort(generated.begin(), generated.end(), by_time);
        const auto a = direct[3], b = generated[3];
        std::cout << "length=" << length << " direct_ns=" << a.nanoseconds
                  << " generated_ns=" << b.nanoseconds << " ratio=" << b.nanoseconds / a.nanoseconds
                  << " direct_allocations=" << a.allocations << " generated_allocations=" << b.allocations
                  << " checksum=" << a.checksum << '\n';
    }
    {
        CountingGraphChild child;
        child.value = 17; child.next_node = &child;
        EditabilityGraph generated_root;
        DirectGraph direct_root;
        generated_root.left = generated_root.right = &child;
        direct_root.left = direct_root.right = &child;
        direct_root.__svtypes_object_number = generated_root.__svtypes_object_number;
        std::vector<uint8_t> generated_bytes, direct_bytes;
        generated_root.pack(generated_bytes);
        if (child.packs != 1) throw std::runtime_error("generated graph traversed child more than once");
        child.packs = 0;
        direct_root.pack(direct_bytes);
        if (child.packs != 1 || generated_bytes != direct_bytes) throw std::runtime_error("graph wire/traversal mismatch");
        EditabilityGraph restored;
        std::size_t offset = 0;
        restored.unpack(generated_bytes, offset);
        if (offset != generated_bytes.size() || child.unpacks != 1 || restored.left != restored.right ||
            restored.left != &child || restored.left->next_node != restored.left)
            throw std::runtime_error("generated graph decode traversal mismatch");
        std::vector<Measurement> direct, generated;
        for (int repeat = 0; repeat < 7; ++repeat) {
            if (repeat % 2) {
                generated.push_back(measure_graph<EditabilityGraph>(30000));
                direct.push_back(measure_graph<DirectGraph>(30000));
            } else {
                direct.push_back(measure_graph<DirectGraph>(30000));
                generated.push_back(measure_graph<EditabilityGraph>(30000));
            }
            if (direct.back().checksum != generated.back().checksum)
                throw std::runtime_error("graph checksums differ");
        }
        auto by_time = [](const Measurement& a, const Measurement& b) { return a.nanoseconds < b.nanoseconds; };
        std::sort(direct.begin(), direct.end(), by_time);
        std::sort(generated.begin(), generated.end(), by_time);
        const auto a = direct[3], b = generated[3];
        std::cout << "graph=shared_cycle direct_ns=" << a.nanoseconds
                  << " generated_ns=" << b.nanoseconds << " ratio=" << b.nanoseconds / a.nanoseconds
                  << " direct_allocations=" << a.allocations << " generated_allocations=" << b.allocations
                  << " checksum=" << a.checksum << '\n';
    }
}

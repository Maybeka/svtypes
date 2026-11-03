// Compare direct field expansion with the shared-list mechanism using real codecs.
// Build: c++ -std=c++20 -O2 -I svtypes_runtime/cpp benchmarks/codegen_editability.cpp -o <output>
#include "svtypes.hpp"
#include <chrono>
#include <utility>

struct Record {
    uint32_t first = 1;
    uint64_t second = 2;
    std::vector<uint16_t> payload;
    template<class Self, class Operation>
    static SVTYPES_INLINE_FIELD_VISITOR void fields(Self& self, Operation&& operation) {
        operation(self.first);
        operation(self.second);
        operation(self.payload);
    }
};

template<bool Visitor>
double measure(size_t length, size_t iterations, uint64_t& checksum) {
    Record source, restored;
    source.payload.resize(length, 17);
    std::vector<uint8_t> bytes;
    bytes.reserve(32 + length * 2);
    const auto start = std::chrono::steady_clock::now();
    for (size_t i = 0; i < iterations; ++i) {
        source.first = static_cast<uint32_t>(i);
        bytes.clear();
        size_t offset = 0;
        if constexpr (Visitor) {
            Record::fields(std::as_const(source), [&](const auto& value) SVTYPES_INLINE_FIELD_OPERATION { svtypes::pack(value, bytes); });
            Record::fields(restored, [&](auto& value) SVTYPES_INLINE_FIELD_OPERATION { svtypes::unpack(value, bytes, offset); });
        } else {
            svtypes::pack(source.first, bytes);
            svtypes::pack(source.second, bytes);
            svtypes::pack(source.payload, bytes);
            svtypes::unpack(restored.first, bytes, offset);
            svtypes::unpack(restored.second, bytes, offset);
            svtypes::unpack(restored.payload, bytes, offset);
        }
        if (offset != bytes.size() || restored.first != source.first || restored.payload != source.payload)
            throw std::runtime_error("benchmark roundtrip mismatch");
        checksum += restored.first + restored.second + restored.payload.size();
    }
    return std::chrono::duration<double, std::nano>(std::chrono::steady_clock::now() - start).count() / iterations;
}

int main() {
    uint64_t checksum = 0;
    for (size_t length : {size_t(8), size_t(4096)}) {
        std::vector<double> direct, visitor;
        const size_t iterations = length == 8 ? 100000 : 2000;
        for (int repeat = 0; repeat < 7; ++repeat) {
            // Alternate measurement order to reduce systematic warmup bias.
            if (repeat % 2) {
                visitor.push_back(measure<true>(length, iterations, checksum));
                direct.push_back(measure<false>(length, iterations, checksum));
            } else {
                direct.push_back(measure<false>(length, iterations, checksum));
                visitor.push_back(measure<true>(length, iterations, checksum));
            }
        }
        std::sort(direct.begin(), direct.end());
        std::sort(visitor.begin(), visitor.end());
        std::cout << "length=" << length << " direct_ns=" << direct[3]
                  << " visitor_ns=" << visitor[3] << " ratio=" << visitor[3] / direct[3] << '\n';
    }
    std::cout << "checksum=" << checksum << '\n';
}

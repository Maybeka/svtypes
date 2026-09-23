#pragma once
#include <vector>
#include <string>
#include <cstdint>
#include <cstring>
#include <array>
#include <map>
#include <algorithm>
#include <stdexcept>
#include <type_traits>
#include <unordered_map>
#include <unordered_set>
#include <iomanip>
#include <iostream>
#include <sstream>

namespace svtypes {

inline constexpr uint32_t max_dynamic_length = 1'000'000;

inline void require_dynamic_length(uint64_t length, const char* where) {
    if (length > max_dynamic_length) {
        throw std::runtime_error(
            std::string("SvTypes ") + where + " length exceeds decoder limit"
        );
    }
}

struct EncodingDescriptor {
    std::string unified_type_name;
    std::string encoding_fingerprint;
    uint16_t binary_format_version = 1;

    bool operator==(const EncodingDescriptor&) const = default;
};

inline void require_encoding_compatible(
    const EncodingDescriptor& expected,
    const EncodingDescriptor& received
) {
    if (expected.binary_format_version != received.binary_format_version) {
        throw std::runtime_error("SvTypes binary format mismatch");
    }
    if (expected.unified_type_name != received.unified_type_name) {
        throw std::runtime_error("SvTypes unified type mismatch");
    }
    if (expected.encoding_fingerprint != received.encoding_fingerprint) {
        throw std::runtime_error("SvTypes encoding fingerprint mismatch");
    }
}

struct RuntimeCapabilities {
    uint16_t package_major_version = 1;
    uint16_t schema_format_version = 1;
    uint16_t binary_format_version = 1;
    uint16_t object_envelope_version = 2;
    uint16_t generator_runtime_abi_version = 1;
    std::vector<std::string> provided{
        "svtypes.checked-encoding-descriptor.v1",
        "svtypes.codec-context.v1",
        "svtypes.constraint-ir.v1",
        "svtypes.constraint-sample.v1",
        "svtypes.external-field-storage.v1",
        "svtypes.record-schema.v1",
        "svtypes.remote-reference.v1",
    };
};

inline RuntimeCapabilities runtime_capabilities() { return {}; }

inline void require_runtime_compatible(
    const std::vector<std::string>& required,
    const RuntimeCapabilities& received,
    const RuntimeCapabilities& expected = runtime_capabilities()
) {
    if (expected.package_major_version != received.package_major_version ||
        expected.schema_format_version != received.schema_format_version ||
        expected.binary_format_version != received.binary_format_version ||
        expected.object_envelope_version != received.object_envelope_version ||
        expected.generator_runtime_abi_version != received.generator_runtime_abi_version) {
        throw std::runtime_error("SvTypes runtime compatibility version mismatch");
    }
    for (const auto& required_name : required) {
        if (std::find(received.provided.begin(), received.provided.end(), required_name) == received.provided.end()) {
            throw std::runtime_error("SvTypes runtime missing required capability: " + required_name);
        }
    }
}

struct RemoteRefValue {
    std::string target_type_name;
    uint64_t object_number = 0;

    bool is_null() const { return object_number == 0; }
    bool operator==(const RemoteRefValue&) const = default;
};

template <size_t Width, bool Signed = false>
struct LogicValue {
    static constexpr size_t byte_count = (Width + 7) / 8;
    static constexpr bool is_signed = Signed;
    std::array<uint8_t, byte_count> value{};
    std::array<uint8_t, byte_count> x{};
    std::array<uint8_t, byte_count> z{};

    bool operator==(const LogicValue&) const = default;
};

inline constexpr uint64_t SVTYPES_OBJECT_NUMBER_ORIGIN = 0x0003000000000000ULL;
struct SvObject;

struct CodecSession {
    uint64_t next_object_counter = 1;
    std::unordered_map<uint64_t, SvObject*> object_registry;

    uint64_t allocate_object_number() {
        return SVTYPES_OBJECT_NUMBER_ORIGIN | next_object_counter++;
    }

    void reset_object_number_allocator(uint64_t start = 1) {
        next_object_counter = start;
    }

    void clear() { object_registry.clear(); }
};

inline thread_local CodecSession default_codec_session;
inline thread_local CodecSession* active_codec_session_ptr = &default_codec_session;
// Compatibility view of the per-thread default session only.
inline std::unordered_map<uint64_t, SvObject*>& object_registry = default_codec_session.object_registry;

inline CodecSession& current_codec_session() {
    return *active_codec_session_ptr;
}

class CodecSessionScope {
public:
    explicit CodecSessionScope(CodecSession& session)
        : previous_(active_codec_session_ptr) {
        active_codec_session_ptr = &session;
    }

    CodecSessionScope(const CodecSessionScope&) = delete;
    CodecSessionScope& operator=(const CodecSessionScope&) = delete;

    ~CodecSessionScope() { active_codec_session_ptr = previous_; }

private:
    CodecSession* previous_;
};

inline thread_local std::vector<std::unordered_set<uint64_t>> svtypes_pack_context_stack;
inline void register_object(SvObject* obj);
inline void unregister_object_if_same(SvObject* obj);

inline uint64_t allocate_object_number() {
    return current_codec_session().allocate_object_number();
}

inline void reset_object_number_allocator(uint64_t start = 1) {
    current_codec_session().reset_object_number_allocator(start);
}

inline void clear_object_registry() {
    current_codec_session().clear();
}

inline bool unregister_object(uint64_t object_number) {
    return current_codec_session().object_registry.erase(object_number) != 0;
}

template <size_t Width, bool Signed = false>
struct BitValue {
    static constexpr size_t byte_count = (Width + 7) / 8;
    std::array<uint8_t, byte_count> bytes{};

    BitValue() = default;

    template <typename T, std::enable_if_t<std::is_integral_v<T>, int> = 0>
    BitValue(T value) {
        *this = value;
    }

    BitValue(std::initializer_list<uint8_t> init) {
        *this = init;
    }

    uint8_t& operator[](size_t index) { return bytes[index]; }
    const uint8_t& operator[](size_t index) const { return bytes[index]; }

    BitValue& operator=(std::initializer_list<uint8_t> init) {
        bytes.fill(0);
        size_t i = 0;
        for (uint8_t value : init) {
            if (i >= byte_count) break;
            bytes[i++] = value;
        }
        mask_unused_bits();
        return *this;
    }

    template <typename T, std::enable_if_t<std::is_integral_v<T>, int> = 0>
    BitValue& operator=(T value) {
        using U = std::make_unsigned_t<T>;
        U raw = static_cast<U>(value);
        for (size_t i = 0; i < byte_count; ++i) {
            bytes[i] = static_cast<uint8_t>((raw >> (i * 8)) & 0xffu);
        }
        mask_unused_bits();
        return *this;
    }

    uint64_t to_uint64() const {
        uint64_t raw = 0;
        constexpr size_t n = byte_count < sizeof(uint64_t) ? byte_count : sizeof(uint64_t);
        for (size_t i = 0; i < n; ++i) {
            raw |= static_cast<uint64_t>(bytes[i]) << (i * 8);
        }
        if constexpr (Width < 64) {
            raw &= ((uint64_t{1} << Width) - 1);
        }
        return raw;
    }

    int64_t to_int64() const {
        uint64_t raw = to_uint64();
        if constexpr (Signed && Width < 64) {
            uint64_t sign = uint64_t{1} << (Width - 1);
            if (raw & sign) {
                raw |= ~((uint64_t{1} << Width) - 1);
            }
        }
        return static_cast<int64_t>(raw);
    }

    operator uint64_t() const { return to_uint64(); }
    operator int64_t() const { return to_int64(); }

    template <typename T, std::enable_if_t<std::is_integral_v<T>, int> = 0>
    bool operator==(T other) const {
        if constexpr (Signed) {
            return to_int64() == static_cast<int64_t>(other);
        } else {
            return to_uint64() == static_cast<uint64_t>(other);
        }
    }

    template <typename T, std::enable_if_t<std::is_integral_v<T>, int> = 0>
    bool operator!=(T other) const {
        return !(*this == other);
    }

private:
    void mask_unused_bits() {
        constexpr size_t used_bits = Width % 8;
        if constexpr (used_bits != 0) {
            bytes[byte_count - 1] &= static_cast<uint8_t>((uint8_t{1} << used_bits) - 1);
        }
    }
};

// Base class for all modeled objects
struct SvObject {
    uint64_t __svtypes_object_number = allocate_object_number();
    CodecSession* __svtypes_registry_session = nullptr;

    SvObject() {
        register_object(this);
    }

    virtual ~SvObject() { unregister_object_if_same(this); }
    virtual void pack(std::vector<uint8_t>& buf) const = 0;
    virtual void unpack(const std::vector<uint8_t>& buf, size_t& offset) = 0;
    virtual void pack_body(std::vector<uint8_t>& buf) const = 0;
    virtual void unpack_body(const std::vector<uint8_t>& buf, size_t& offset) = 0;
    virtual std::string svtypes_sprint() const = 0;
    virtual void svtypes_display() const = 0;
};

inline thread_local std::unordered_set<uint64_t> svtypes_dump_seen;
inline thread_local size_t svtypes_dump_depth = 0;

inline bool begin_dump_object(uint64_t object_number) {
    if (svtypes_dump_depth == 0) svtypes_dump_seen.clear();
    bool repeated = !svtypes_dump_seen.insert(object_number).second;
    ++svtypes_dump_depth;
    return repeated;
}

inline void end_dump_object() {
    if (svtypes_dump_depth != 0) --svtypes_dump_depth;
    if (svtypes_dump_depth == 0) svtypes_dump_seen.clear();
}

inline std::string dump_value(const std::string& value) {
    std::ostringstream stream;
    stream << std::quoted(value);
    return stream.str();
}

inline std::string dump_value(const RemoteRefValue& value) {
    return value.is_null()
        ? "null"
        : "RemoteRef(" + value.target_type_name + "," + std::to_string(value.object_number) + ")";
}

inline std::string dump_value(const SvObject* value) {
    return value == nullptr ? "null" : value->svtypes_sprint();
}

template <typename T, std::enable_if_t<std::is_arithmetic_v<T>, int> = 0>
std::string dump_value(T value) {
    if constexpr (std::is_same_v<T, uint8_t> || std::is_same_v<T, int8_t>) {
        return std::to_string(static_cast<int>(value));
    }
    return std::to_string(value);
}

template <typename T, std::enable_if_t<std::is_enum_v<T>, int> = 0>
std::string dump_value(T value) {
    return std::to_string(static_cast<std::underlying_type_t<T>>(value));
}

template <size_t Width, bool Signed>
std::string dump_value(const BitValue<Width, Signed>& value) {
    if constexpr (Signed && Width <= 64) return std::to_string(value.to_int64());
    if constexpr (Width <= 64) return std::to_string(value.to_uint64());
    std::ostringstream stream;
    stream << "0x" << std::hex;
    for (auto it = value.bytes.rbegin(); it != value.bytes.rend(); ++it) {
        stream << std::setw(2) << std::setfill('0') << static_cast<unsigned>(*it);
    }
    return stream.str();
}

template <size_t Width, bool Signed>
std::string dump_value(const LogicValue<Width, Signed>& value) {
    std::ostringstream stream;
    stream << "Logic(value=0x" << std::hex;
    for (auto it = value.value.rbegin(); it != value.value.rend(); ++it) {
        stream << std::setw(2) << std::setfill('0') << static_cast<unsigned>(*it);
    }
    stream << ",x=0x";
    for (auto it = value.x.rbegin(); it != value.x.rend(); ++it) {
        stream << std::setw(2) << std::setfill('0') << static_cast<unsigned>(*it);
    }
    stream << ",z=0x";
    for (auto it = value.z.rbegin(); it != value.z.rend(); ++it) {
        stream << std::setw(2) << std::setfill('0') << static_cast<unsigned>(*it);
    }
    return stream.str() + ")";
}

template <typename T, size_t N>
std::string dump_value(const std::array<T, N>& value) {
    std::string result = "[";
    for (size_t index = 0; index < N; ++index) {
        if (index != 0) result += ", ";
        result += dump_value(value[index]);
    }
    return result + "]";
}

template <typename T>
std::string dump_value(const std::vector<T>& value) {
    std::string result = "[";
    for (size_t index = 0; index < value.size(); ++index) {
        if (index != 0) result += ", ";
        result += dump_value(value[index]);
    }
    return result + "]";
}

template <typename K, typename V>
std::string dump_value(const std::map<K, V>& value) {
    std::string result = "{";
    bool first = true;
    for (const auto& [key, item] : value) {
        if (!first) result += ", ";
        first = false;
        result += dump_value(key) + ": " + dump_value(item);
    }
    return result + "}";
}

inline void register_object(SvObject* obj) {
    if (obj != nullptr && obj->__svtypes_object_number != 0) {
        CodecSession& session = current_codec_session();
        if (obj->__svtypes_registry_session != nullptr &&
            obj->__svtypes_registry_session != &session) {
            auto old = obj->__svtypes_registry_session->object_registry.find(obj->__svtypes_object_number);
            if (old != obj->__svtypes_registry_session->object_registry.end() && old->second == obj) {
                obj->__svtypes_registry_session->object_registry.erase(old);
            }
        }
        session.object_registry[obj->__svtypes_object_number] = obj;
        obj->__svtypes_registry_session = &session;
    }
}

inline void unregister_object_if_same(SvObject* obj) {
    if (obj == nullptr || obj->__svtypes_object_number == 0) return;
    CodecSession* session = obj->__svtypes_registry_session;
    if (session == nullptr) return;
    auto existing = session->object_registry.find(obj->__svtypes_object_number);
    if (existing != session->object_registry.end() && existing->second == obj) {
        session->object_registry.erase(existing);
    }
    obj->__svtypes_registry_session = nullptr;
}

inline SvObject* get_object(uint64_t object_number) {
    auto& registry = current_codec_session().object_registry;
    auto it = registry.find(object_number);
    if (it == registry.end()) {
        return nullptr;
    }
    return it->second;
}

inline void begin_pack_graph() {
    svtypes_pack_context_stack.emplace_back();
}

inline void end_pack_graph() {
    if (svtypes_pack_context_stack.empty()) {
        throw std::runtime_error("SvTypes pack context stack underflow");
    }
    svtypes_pack_context_stack.pop_back();
}

class PackOperationGuard {
public:
    explicit PackOperationGuard(bool active = true) : active_(active) {
        if (active_) begin_pack_graph();
    }
    PackOperationGuard(const PackOperationGuard&) = delete;
    PackOperationGuard& operator=(const PackOperationGuard&) = delete;
    ~PackOperationGuard() {
        if (active_) svtypes_pack_context_stack.pop_back();
    }
private:
    bool active_;
};

// --- Primitive Packing (Little Endian) ---

inline void require_available(const std::vector<uint8_t>& buf, size_t offset, size_t count) {
    if (offset > buf.size() || count > buf.size() - offset) {
        throw std::runtime_error("SvTypes unpack underflow");
    }
}

inline void pack(const RemoteRefValue& value, std::vector<uint8_t>& buf) {
    for (size_t i = 0; i < 8; ++i) {
        buf.push_back(static_cast<uint8_t>((value.object_number >> (i * 8)) & 0xffu));
    }
}

inline void unpack(RemoteRefValue& value, const std::vector<uint8_t>& buf, size_t& offset) {
    require_available(buf, offset, 8);
    value.object_number = 0;
    for (size_t i = 0; i < 8; ++i) {
        value.object_number |= static_cast<uint64_t>(buf[offset + i]) << (i * 8);
    }
    offset += 8;
}

template <size_t Width, bool Signed>
void pack(const LogicValue<Width, Signed>& value, std::vector<uint8_t>& buf) {
    buf.insert(buf.end(), value.value.begin(), value.value.end());
    buf.insert(buf.end(), value.x.begin(), value.x.end());
    buf.insert(buf.end(), value.z.begin(), value.z.end());
}

template <size_t Width, bool Signed>
void unpack(LogicValue<Width, Signed>& value, const std::vector<uint8_t>& buf, size_t& offset) {
    constexpr size_t count = LogicValue<Width, Signed>::byte_count;
    require_available(buf, offset, count * 3);
    std::copy_n(buf.begin() + static_cast<std::ptrdiff_t>(offset), count, value.value.begin());
    offset += count;
    std::copy_n(buf.begin() + static_cast<std::ptrdiff_t>(offset), count, value.x.begin());
    offset += count;
    std::copy_n(buf.begin() + static_cast<std::ptrdiff_t>(offset), count, value.z.begin());
    offset += count;
}

template <typename T>
void pack_integral(T val, std::vector<uint8_t>& buf) {
    using U = std::make_unsigned_t<T>;
    U raw = static_cast<U>(val);
    for (size_t i = 0; i < sizeof(T); ++i) {
        buf.push_back(static_cast<uint8_t>((raw >> (i * 8)) & 0xffu));
    }
}

template <typename T>
void unpack_integral(T& val, const std::vector<uint8_t>& buf, size_t& offset) {
    require_available(buf, offset, sizeof(T));
    using U = std::make_unsigned_t<T>;
    U raw = 0;
    for (size_t i = 0; i < sizeof(T); ++i) {
        raw |= static_cast<U>(buf[offset + i]) << (i * 8);
    }
    val = static_cast<T>(raw);
    offset += sizeof(T);
}

// --- Overloads for various types ---

// Integers
template <typename T, std::enable_if_t<std::is_integral_v<T> && !std::is_same_v<T, bool>, int> = 0>
void pack(T v, std::vector<uint8_t>& b) { pack_integral(v, b); }

template <typename T, std::enable_if_t<std::is_integral_v<T> && !std::is_same_v<T, bool>, int> = 0>
void unpack(T& v, const std::vector<uint8_t>& b, size_t& o) { unpack_integral(v, b, o); }

template <typename E, std::enable_if_t<std::is_enum_v<E>, int> = 0>
void pack(E v, std::vector<uint8_t>& b) {
    using U = std::underlying_type_t<E>;
    pack_integral(static_cast<U>(v), b);
}

template <typename E, std::enable_if_t<std::is_enum_v<E>, int> = 0>
void unpack(E& v, const std::vector<uint8_t>& b, size_t& o) {
    using U = std::underlying_type_t<E>;
    U raw;
    unpack_integral(raw, b, o);
    v = static_cast<E>(raw);
}

inline void pack(float v, std::vector<uint8_t>& b) {
    uint32_t raw;
    std::memcpy(&raw, &v, sizeof(raw));
    pack(raw, b);
}

inline void unpack(float& v, const std::vector<uint8_t>& b, size_t& o) {
    uint32_t raw;
    unpack(raw, b, o);
    std::memcpy(&v, &raw, sizeof(v));
}

inline void pack(double v, std::vector<uint8_t>& b) {
    uint64_t raw;
    std::memcpy(&raw, &v, sizeof(raw));
    pack(raw, b);
}

inline void unpack(double& v, const std::vector<uint8_t>& b, size_t& o) {
    uint64_t raw;
    unpack(raw, b, o);
    std::memcpy(&v, &raw, sizeof(v));
}

inline void pack(const std::string& v, std::vector<uint8_t>& b) {
    require_dynamic_length(v.size(), "string");
    uint32_t len = static_cast<uint32_t>(v.size());
    pack(len, b);
    b.insert(b.end(), v.begin(), v.end());
}

inline void unpack(std::string& v, const std::vector<uint8_t>& b, size_t& o) {
    uint32_t len;
    unpack(len, b, o);
    require_dynamic_length(len, "string");
    require_available(b, o, len);
    v.assign(reinterpret_cast<const char*>(&b[o]), len);
    o += len;
}

inline uint8_t hex_nibble(char value) {
    if (value >= '0' && value <= '9') return static_cast<uint8_t>(value - '0');
    if (value >= 'a' && value <= 'f') return static_cast<uint8_t>(value - 'a' + 10);
    throw std::runtime_error("SvTypes encoding fingerprint is not lowercase hexadecimal");
}

inline uint8_t fingerprint_byte(const std::string& fingerprint, size_t index) {
    if (fingerprint.size() != 64) {
        throw std::runtime_error("SvTypes encoding fingerprint must contain 64 hexadecimal digits");
    }
    return static_cast<uint8_t>((hex_nibble(fingerprint[index * 2]) << 4) |
                                hex_nibble(fingerprint[index * 2 + 1]));
}

inline void pack_object_header(
    const std::string& type_name,
    const std::string& encoding_fingerprint,
    uint16_t field_count,
    uint64_t object_number,
    std::vector<uint8_t>& b
) {
    b.push_back('S');
    b.push_back('V');
    b.push_back('X');
    b.push_back('O');
    pack(static_cast<uint16_t>(2), b);
    pack(field_count, b);
    pack(object_number, b);
    pack(type_name, b);
    for (size_t i = 0; i < 32; ++i) {
        b.push_back(fingerprint_byte(encoding_fingerprint, i));
    }
}

inline void unpack_object_header(
    const std::string& expected_type_name,
    const std::string& expected_encoding_fingerprint,
    uint16_t expected_field_count,
    uint64_t& object_number,
    const std::vector<uint8_t>& b,
    size_t& o
) {
    require_available(b, o, 4);
    if (b[o] != 'S' || b[o + 1] != 'V' || b[o + 2] != 'X' || b[o + 3] != 'O') {
        throw std::runtime_error("SvTypes object header magic mismatch");
    }
    o += 4;
    uint16_t version;
    uint16_t field_count;
    std::string type_name;
    unpack(version, b, o);
    unpack(field_count, b, o);
    unpack(object_number, b, o);
    if (object_number == 0) {
        throw std::runtime_error("SvTypes non-null object envelope has id 0");
    }
    unpack(type_name, b, o);
    if (version != 2) {
        throw std::runtime_error("SvTypes object header version mismatch");
    }
    if (type_name != expected_type_name) {
        throw std::runtime_error(
            "SvTypes object type mismatch: expected " + expected_type_name +
            ", got " + type_name
        );
    }
    if (field_count != expected_field_count) {
        throw std::runtime_error("SvTypes object field-count mismatch");
    }
    require_available(b, o, 32);
    for (size_t i = 0; i < 32; ++i) {
        if (b[o + i] != fingerprint_byte(expected_encoding_fingerprint, i)) {
            throw std::runtime_error("SvTypes object encoding fingerprint mismatch");
        }
    }
    o += 32;
}

inline void pack_object_reference(uint64_t object_number, std::vector<uint8_t>& b) {
    if (object_number == 0) {
        throw std::runtime_error("SvTypes object reference has id 0");
    }
    b.push_back(2);
    pack(object_number, b);
}

inline uint64_t unpack_object_reference(const std::vector<uint8_t>& b, size_t& o) {
    uint64_t object_number;
    unpack(object_number, b, o);
    if (object_number == 0) {
        throw std::runtime_error("SvTypes object reference has id 0");
    }
    return object_number;
}

inline uint64_t peek_object_number(const std::vector<uint8_t>& b, size_t o) {
    require_available(b, o, 16);
    if (b[o] != 'S' || b[o + 1] != 'V' || b[o + 2] != 'X' || b[o + 3] != 'O') {
        throw std::runtime_error("SvTypes object header magic mismatch");
    }
    uint64_t object_number = 0;
    for (size_t i = 0; i < 8; ++i) {
        object_number |= static_cast<uint64_t>(b[o + 8 + i]) << (i * 8);
    }
    if (object_number == 0) {
        throw std::runtime_error("SvTypes non-null object envelope has id 0");
    }
    return object_number;
}

inline void pack_object_value(const SvObject* value, std::vector<uint8_t>& b) {
    PackOperationGuard implicit_guard(svtypes_pack_context_stack.empty());
    if (value == nullptr) {
        b.push_back(0);
        return;
    }

    register_object(const_cast<SvObject*>(value));
    auto& seen = svtypes_pack_context_stack.back();
    if (seen.find(value->__svtypes_object_number) != seen.end()) {
        pack_object_reference(value->__svtypes_object_number, b);
        return;
    }

    seen.insert(value->__svtypes_object_number);
    b.push_back(1);
    value->pack_body(b);
}

template <typename T>
struct object_packer {
    static_assert(std::is_base_of_v<SvObject, T>, "object_packer requires SvObject type");

    static void pack(const T* value, std::vector<uint8_t>& b) {
        pack_object_value(value, b);
    }

    static void unpack(T*& value, const std::vector<uint8_t>& b, size_t& o) {
        require_available(b, o, 1);
        uint8_t present = b[o++];
        if (present == 0) {
            value = nullptr;
            return;
        }
        if (present == 2) {
            uint64_t object_number = unpack_object_reference(b, o);
            SvObject* existing = get_object(object_number);
            if (existing == nullptr) {
                throw std::runtime_error("SvTypes unresolved object reference");
            }
            T* typed = dynamic_cast<T*>(existing);
            if (typed == nullptr) {
                throw std::runtime_error("SvTypes object reference type mismatch");
            }
            value = typed;
            return;
        }
        if (present != 1) {
            throw std::runtime_error("SvTypes invalid object presence marker");
        }

        uint64_t object_number = peek_object_number(b, o);
        SvObject* existing = get_object(object_number);
        if (existing != nullptr) {
            T* typed = dynamic_cast<T*>(existing);
            if (typed == nullptr) {
                throw std::runtime_error("SvTypes object reference type mismatch");
            }
            value = typed;
        } else {
            value = new T();
            value->__svtypes_object_number = object_number;
            register_object(value);
        }
        value->unpack_body(b, o);
    }
};

// Nested SvObject
inline void pack(const SvObject& v, std::vector<uint8_t>& b) { pack_object_value(&v, b); }
inline void unpack(SvObject& v, const std::vector<uint8_t>& b, size_t& o) { v.unpack(b, o); }

template <typename T, std::enable_if_t<std::is_base_of_v<SvObject, T>, int> = 0>
void pack(T* const& v, std::vector<uint8_t>& b) {
    object_packer<T>::pack(v, b);
}

template <typename T, std::enable_if_t<std::is_base_of_v<SvObject, T>, int> = 0>
void unpack(T*& v, const std::vector<uint8_t>& b, size_t& o) {
    object_packer<T>::unpack(v, b, o);
}

// Fixed Array (std::array)
template <size_t Width, bool Signed>
void pack(const BitValue<Width, Signed>& v, std::vector<uint8_t>& b) {
    b.insert(b.end(), v.bytes.begin(), v.bytes.end());
}

template <size_t Width, bool Signed>
void unpack(BitValue<Width, Signed>& v, const std::vector<uint8_t>& b, size_t& o) {
    require_available(b, o, BitValue<Width, Signed>::byte_count);
    std::copy_n(b.begin() + static_cast<std::ptrdiff_t>(o), BitValue<Width, Signed>::byte_count, v.bytes.begin());
    o += BitValue<Width, Signed>::byte_count;
}

template <typename T, size_t N>
void pack(const std::array<T, N>& v, std::vector<uint8_t>& b) {
    for (const auto& item : v) pack(item, b);
}
template <typename T, size_t N>
void unpack(std::array<T, N>& v, const std::vector<uint8_t>& b, size_t& o) {
    for (auto& item : v) unpack(item, b, o);
}

// Dynamic Array / Queue (std::vector)
template <typename T>
void pack(const std::vector<T>& v, std::vector<uint8_t>& b) {
    require_dynamic_length(v.size(), "vector");
    uint32_t len = static_cast<uint32_t>(v.size());
    pack(len, b); // 4-byte LE length header
    for (const auto& item : v) pack(item, b);
}
template <typename T>
void unpack(std::vector<T>& v, const std::vector<uint8_t>& b, size_t& o) {
    uint32_t len;
    unpack(len, b, o);
    require_dynamic_length(len, "vector");
    v.resize(len);
    for (auto& item : v) unpack(item, b, o);
}

// Associative Array (std::map)
template <typename K, typename V>
void pack(const std::map<K, V>& v, std::vector<uint8_t>& b) {
    require_dynamic_length(v.size(), "map");
    uint32_t len = static_cast<uint32_t>(v.size());
    pack(len, b);
    std::vector<std::pair<std::vector<uint8_t>, const V*>> ordered;
    ordered.reserve(v.size());
    for (const auto& [key, val] : v) {
        std::vector<uint8_t> encoded_key;
        pack(key, encoded_key);
        ordered.emplace_back(std::move(encoded_key), &val);
    }
    std::sort(
        ordered.begin(),
        ordered.end(),
        [](const auto& lhs, const auto& rhs) { return lhs.first < rhs.first; }
    );
    for (const auto& [encoded_key, value] : ordered) {
        b.insert(b.end(), encoded_key.begin(), encoded_key.end());
        pack(*value, b);
    }
}
template <typename K, typename V>
void unpack(std::map<K, V>& v, const std::vector<uint8_t>& b, size_t& o) {
    uint32_t len;
    unpack(len, b, o);
    require_dynamic_length(len, "map");
    v.clear();
    for (uint32_t i = 0; i < len; ++i) {
        K key;
        V val;
        unpack(key, b, o);
        unpack(val, b, o);
        v[key] = val;
    }
}

} // namespace svtypes

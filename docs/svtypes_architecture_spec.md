# SvTypes — Architecture Specification

## TL;DR

> **SvTypes** is the data definition layer of the SVX (SystemVerilog eXtensions) framework. It is a Python DSL that models hardware data structures once and generates equivalent SystemVerilog (packages/classes) and C++ (headers/structs) declarations, with binary serialization parity across those backends. DPI routing and any private C adaptation belong to SVX, not to SvTypes.

---

## 1. Context & Purpose

### 1.1 What is SVX?

SVX (SystemVerilog eXtensions) is a framework that brings Python into the SystemVerilog simulation ecosystem via the DPI (Direct Programming Interface). It comprises three layers:

```
┌──────────────────────────────────────────────────────────┐
│  PyUVM                                                     │
│  UVM-style verification framework in Python                │
│  Consumes SvTypes for sequence items, configs, transactions│
├──────────────────────────────────────────────────────────┤
│  SVX Bridge (DPI Integration)                              │
│  Connects SV simulator ↔ Python interpreter                │
│  Handles DPI call routing, memory, handle mapping          │
│  Consumes SvTypes for type-safe data marshaling            │
├──────────────────────────────────────────────────────────┤
│  SvTypes (Data Definition Layer)                           │
│  Define data structures once in Python                     │
│  Generate: SV classes/packages + C++ structs               │
│  Binary serialization parity: Python ≡ SV ≡ C++            │
└──────────────────────────────────────────────────────────┘
                    ↓ runs on top of ↓
┌──────────────────────────────────────────────────────────┐
│  SystemVerilog Simulator (SystemVerilog target, target provider, target provider)           │
└──────────────────────────────────────────────────────────┘
```

### 1.2 SvTypes' Role

SvTypes is the **single source of truth for all data structures** that flow through the SVX stack. A verification engineer defines a transaction type once:

```python
class OpEnum(Enum, width=8, signed=False):
    READ = 0
    WRITE = 1

@svobj
class MyTransaction(SvObject):
    addr = Bits(32)
    data = Bits(64)
    op   = OpEnum(OpEnum.READ)
```

And gets, for free:
- **Python**: Type-safe fields, value access, binary pack/unpack
- **SystemVerilog**: Equivalent `class` with `pack`/`unpack` methods, rand/cov annotations
- **C++**: Equivalent `struct` with `pack`/`unpack` methods, template support
- **Binary parity**: The same bytes from `tx.pack()` in Python equal `tx.pack(b)` in SV

### 1.3 Design Principles

| Principle | Meaning |
|---|---|
| **Define Once, Generate Many** | One Python definition → SV, C++, and one binary protocol |
| **Binary Contract First** | The serialization format IS the API contract between languages |
| **Layered Separation** | Model ≠ Codegen ≠ Runtime — each layer is independently testable |
| **Progressive Disclosure** | Simple things are simple; complex things (inheritance, templates, unions) are possible |
| **Simulator Agnostic** | Generated SV/C++ code has zero vendor-specific constructs |
| **Pythonic First** | The Python DSL feels natural to Python developers, not like writing SV in Python |

---

## 2. System Architecture

### 2.1 Layer Model

```
┌─────────────────────────────────────────────────────────┐
│                  USER API LAYER                           │
│  Public Python API: @svobj, Int(), Bits(), Package, etc. │
│  What the verification engineer writes                   │
├─────────────────────────────────────────────────────────┤
│                  CORE MODEL LAYER                         │
│  Type system: TypeBase → BuiltInType / UserDefinedType   │
│  Type registries, descriptors, metaclasses, IR builder   │
│  Serialization engine (Python-side pack/unpack)          │
│  Clear internal APIs between all components              │
├─────────────────────────────────────────────────────────┤
│               CODE GENERATION LAYER                       │
│  Deterministic SV/C++ renderers and multi-file output    │
│  Public schema descriptors and manifest generation       │
│  Package/namespace/scope management                      │
├─────────────────────────────────────────────────────────┤
│                  RUNTIME LAYER                            │
│  svtypes_pkg.sv   — SV package (base class + helpers)        │
│  svtypes.hpp  — C++ header (templates + base class)      │
│  Generated independently, distributable as standalone    │
└─────────────────────────────────────────────────────────┘
```

**Layer Dependency Rule**: Layers only depend downward. User API → Core Model → Codegen → Runtime. The Runtime layer has zero knowledge of the layers above it.

### 2.2 Component Diagram

```
                    ┌──────────────────┐
                    │   User writes:   │
                    │ @svobj / Int() / │
                    │ Bits() / Array() │
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │   Public API     │
                    │  (__init__.py)   │
                    │  Factory funcs,  │
                    │  decorators      │
                    └────────┬─────────┘
                             │
        ┌────────────────────┼────────────────────┐
        ▼                    ▼                     ▼
┌───────────────┐  ┌─────────────────┐  ┌──────────────────┐
│  Type System  │  │  Package/Scope  │  │  Object Model    │
│  - TypeBase   │  │  - Package      │  │  - SvObject      │
│  - BuiltInType│  │  - Scope        │  │  - SvStruct      │
│  - UserDefdTyp│  │  - get_package  │  │  - Enum          │
│  - Collection │  │  - collect_mod  │  │                  │
└───────┬───────┘  └────────┬────────┘  └────────┬─────────┘
        │                   │                     │
        └───────────────────┼─────────────────────┘
                            │
                   ┌────────▼─────────┐
                   │ Public schema and│
                   │ encoding descriptors │
                   │ plus validation  │
                   └────────┬─────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                    ▼
┌───────────────┐  ┌───────────────┐  ┌────────────────┐
│ SV renderer   │  │ C++ renderer  │  │ Schema/manifest│
│ .sv output    │  │ .hpp output   │  │ JSON output    │
└───────────────┘  └───────────────┘  └────────────────┘
```

### 2.3 Data Flow: From Definition to Generated Code

```
Python Definition
    │
    ▼
┌─────────────────────────────────────────┐
│ 1. DECORATOR / __init_subclass__        │
│    Registers type in Package/Scope      │
│    Builds _sv_fields, _sv_params lists  │
│    Validates type constraints           │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│ 2. IR BUILDER (on demand)               │
│    Walks type hierarchy (MRO)           │
│    Computes: field order, widths,       │
│      serialization offsets, template    │
│      params, generated declarations     │
│    Output: TypeIR object                │
│    Cached until type changes            │
└──────────────────┬──────────────────────┘
                   │
        ┌──────────┼──────────┐
        ▼          ▼
┌───────────┐ ┌───────────┐
│ 3a. SV    │ │ 3b. C++   │
│ Renderer  │ │ Renderer  │
│ → .sv     │ │ → .hpp    │
└───────────┘ └───────────┘
```

---

## 3. Type System

### 3.1 Type Hierarchy

```
TypeBase (abstract)
├── BuiltInType — SystemVerilog built-in types
│   ├── IntegerType
│   │   ├── Int          — 32-bit signed   → SV: int       C++: int32_t
│   │   └── LongInt      — 64-bit signed   → SV: longint   C++: int64_t
│   ├── Bits(width, signed) — arbitrary width → SV: bit [N-1:0]  C++: uintN_t
│   ├── LogicBits(width/shape) — four-state packed value with value/X/Z planes
│   ├── RealType
│   │   ├── Real         — 64-bit float    → SV: real      C++: double
│   │   └── ShortReal    — 32-bit float    → SV: shortreal C++: float
│   ├── String           — dynamic string  → SV: string    C++: std::string
│   ├── CollectionBase
│   │   ├── Array(elem, N)  — fixed        → SV: T [N]     C++: std::array<T,N>
│   │   ├── DynArray(elem)  — dynamic      → SV: T []      C++: std::vector<T>
│   │   ├── Queue(elem)     — queue        → SV: T [$]     C++: std::vector<T>
│   │   └── AssocArray(K,V) — associative  → SV: V [K]     C++: std::map<K,V>
│
└── UserDefinedType — User/composite types
    ├── SvObject         — class-based     → SV: class      C++: struct
    ├── SvStruct         — by-value         → SV: typedef struct packed  C++: struct
    └── Enum             — enumeration     → SV: typedef enum  C++: enum class
```

### 3.2 Type Metadata (TypeBase attributes)

Every type carries metadata that drives code generation:

| Attribute | Type | Purpose | Generates |
|---|---|---|---|
| `rand` | bool | Randomizable in SV? | `rand` modifier in SV class |
| `plusarg` | bool | Overridable by +plusargs? | `$value$plusargs` override block |
| `dump` | bool | Included in print/dump? | Print statement in `display()` |
| `cov` | bool | Coverage collected? | Coverpoint in covergroup |
| `pack_bytes` | bool | Included in serialization? | Packed in `pack()`/`unpack()` |
| `width` | int (ro) | Bit width (primitives) | Drives SV/C++ type selection |

### 3.3 Value Access Protocol

SvTypes enforces a strict access protocol that maps to SV semantics:

```python
# CORRECT: Always use .value for data access
obj.addr.value = 0xDEAD    # Setter enforces type rules, truncation
val = obj.addr.value       # Getter returns normalized Python type

# WRONG: Direct assignment is BLOCKED
obj.addr = 0xDEAD          # Raises AttributeError
```

This protocol is enforced by `__setattr__` on `SvObject` and `__set_name__` on `TypeBase`.

---

## 4. Serialization Protocol

### 4.1 Binary Format Specification

```
Serialization Order:
┌─────────────────────────────────────────────────────┐
│ [Base class fields first, in MRO order, then        │
│  derived class fields] ← Fixed-size types: raw bytes│
│                                                      │
│ Dynamic types interleaved with a 4-byte LE length    │
│ prefix before the data:                              │
│   [4-byte LE count] [element_0] [element_1] ...     │
│                                                      │
│ All multi-byte values: LITTLE-ENDIAN                 │
└─────────────────────────────────────────────────────┘
```

| Type | Encoding | Bytes |
|---|---|---|
| Int / LongInt / Byte | 2's complement LE | 4 / 8 / 1 |
| Bits(N) | Unsigned LE, padded to ceil(N/8) | ceil(N/8) |
| Real / ShortReal | IEEE 754 LE | 8 / 4 |
| String | 4-byte LE length + UTF-8 bytes | 4 + len |
| Array(T,N) | N × pack(T) | N × sizeof(T) |
| DynArray(T) / Queue(T) | 4-byte LE count + count × pack(T) | 4 + count × sizeof(T) |
| AssocArray(K,V) | 4-byte LE count + count × (pack(K) + pack(V)) sorted by K | 4 + count × (sizeof(K)+sizeof(V)) |
| SvObject | Recursive: base fields first, then derived | variable |

### 4.2 Cross-Language Parity Guarantee

```
pack(obj) in Python ≡ pack(obj) in SystemVerilog ≡ pack(obj) in C++

Verification: A binary blob produced by Python can be unpacked by SV,
              modified, repacked by SV, and unpacked back in Python
              with identical values.
```

---

## 5. Package & Scope Model

### 5.1 Mapping

| Python | SystemVerilog | C++ |
|---|---|---|
| Python package (directory) | SV `package` | C++ `namespace` |
| Python module (file) | Nested scope within package | Nested namespace |
| `__main__` module | `$unit` compilation scope | Global namespace |
| `@svobj` decorated class | `class` in package | `struct` in namespace |

### 5.2 Package API

```python
from svtypes import Package, get_package

# Option A: Explicit package creation
pkg = Package("my_packet_pkg")

# Option B: Auto-registration via module collection
pkg = get_package("my_packet_pkg")
pkg.collect_module("my_project.packets")   # Discovers @svobj types

# Option C: Explicit registration
pkg.register(MyTransaction)
pkg.add_parameter("DATA_WIDTH", Parameter(64))

# Generate all outputs
pkg.to_sv_code()     # → my_packet_pkg.sv
pkg.to_cpp_code()    # → my_packet_pkg.hpp
pkg.to_c_code()      # → my_packet_pkg_c.h  (DPI bridge API)
```

---

## 6. Code Generation Pipeline

### 6.1 Public schema descriptors

`schema_descriptor()` normalizes declared types into immutable public schema
and encoding descriptors. SHA-256 schema fingerprints include stable source and
generation policy; encoding fingerprints include only encoding-relevant data.
Backends currently render from the validated declaration model while exposing
the same public descriptors to transports and manifests.

### 6.2 Generator implementation

`python/svtypes/generator.py` coordinates deterministic package-level outputs:
one legal SV package, one C++20 header, one schema JSON file, and one managed
manifest. `mode="write"` replaces managed artifacts; `mode="check"` reports
differences without writing. The current implementation uses direct renderers
in `Scope` and the type classes; Jinja2, a template directory, C generation,
and DPI generation are not part of the 1.0 contract.

---

## 7. Runtime Layer

### 7.1 SystemVerilog Runtime (`svtypes_pkg.sv`)

```sv
package svtypes_pkg;
  virtual class sv_object;
    pure virtual function void pack(ref byte unsigned b[$]);
    pure virtual function void unpack(ref byte unsigned b[$], ref int offset);
  endclass

  function automatic void pack_length(int unsigned len, ref byte unsigned b[$]);
  function automatic int unsigned unpack_length(ref byte unsigned b[$], ref int offset);

  import "DPI-C" function void svx_pack_int(int v, ref byte unsigned b[$]);
  import "DPI-C" function void svx_unpack_int(ref int v, ref byte unsigned b[$], ref int offset);
  import "DPI-C" function void svx_pack_longint(longint v, ref byte unsigned b[$]);
  import "DPI-C" function void svx_unpack_longint(ref longint v, ref byte unsigned b[$], ref int offset);
  import "DPI-C" function void svx_pack_real(real v, ref byte unsigned b[$]);
  import "DPI-C" function void svx_unpack_real(ref real v, ref byte unsigned b[$], ref int offset);
  import "DPI-C" function void svx_pack_shortreal(shortreal v, ref byte unsigned b[$]);
  import "DPI-C" function void svx_unpack_shortreal(ref shortreal v, ref byte unsigned b[$], ref int offset);
  import "DPI-C" function void svx_pack_string(string v, ref byte unsigned b[$]);
  import "DPI-C" function void svx_unpack_string(ref string v, ref byte unsigned b[$], ref int offset);

  function automatic void pack_type(sv_object v, ref byte unsigned b[$]);
  function automatic void unpack_type(sv_object v, ref byte unsigned b[$], ref int offset);
endpackage
```

### 7.2 C++ Runtime (`svtypes.hpp`)

```cpp
namespace svtypes {
struct SvObject {
    virtual ~SvObject() = default;
    virtual void pack(std::vector<uint8_t>& buf) const = 0;
    virtual void unpack(const std::vector<uint8_t>& buf, size_t& offset) = 0;
};
// Template pack/unpack for: int32_t, int64_t, double, float, std::string,
// std::array, std::vector, std::deque, std::map — all LE
} // namespace svtypes
```

### 7.3 C API boundary

SvTypes 1.0 does not expose or generate a public C API. The supported native
runtime is C++20. DPI routing and any private C ABI adaptation belong to a
transport integration such as SVX and are not SvTypes data-model APIs.

---

## 8. Scope Boundaries

### 8.1 IN SCOPE

| # | Feature |
|---|---|
| 1 | Stable scalar, collection, `SvStruct`, `SvObject`, graph, and `RemoteRef` data contracts |
| 2 | Deterministic Package/Scope registration without factory override |
| 3 | SystemVerilog and C++20 code generation and runtime libraries |
| 4 | Codegen compile verification and canonical byte parity |
| 5 | Serialization/schema versioning and compatibility checks |
| 6 | `rand`, `cov`, `plusarg`, `dump`, and `pack_bytes` generation policies |
| 7 | Explicit enum width/signedness and multidimensional packed values |
| 8 | Atomic multi-file generation, check mode, schemas, and manifests |

### 8.2 OUT OF SCOPE

| Feature | Phase |
|---|---|
| Package/Scope factory override | Not supported; use an explicit independent registry |
| Public generated C API or DPI imports | Not supported in SvTypes 1.0 |
| SV interface/modport/clocking generation | Not supported |
| UVM base classes | Owned by a future UVMX layer, not SvTypes |
| Constraint solver | Post-1.0 |
| VS Code extension | Not planned |
| Qualified Verilator target | Not supported for 1.0 |

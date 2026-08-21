# SvTypes: SystemVerilog/C++ Modeling DSL

## Purpose
SvTypes is a Python-based Domain-Specific Language (DSL) designed to model hardware (SystemVerilog) and software (C++) data structures from a single Python source of truth.

### Primary Goals
1.  **Unified Modeling**: Define data structures once in Python and generate matching SystemVerilog `class`/`package` and C++ `struct`/`namespace` code.
2.  **Data Exchange**: Provide a consistent serialization framework (pack/unpack) to transfer data between SystemVerilog, C++, and Python via byte streams.
3.  **Strict Modeling**: Enforce a strict access pattern (`.value`) to distinguish between the modeling object and its underlying data, mimicking hardware signal/variable behavior.

---

## Architecture

### 1. Package & Scope Equivalence
The project uses the Python module system to define scopes:
-   **Python Package/Module**: Maps to a SystemVerilog `package` and a C++ `namespace`.
-   **Leaf Name Mapping**: The last part of a Python module name (e.g., `pkg` from `a.b.pkg`) determines the SV/C++ name.
-   **Compilation Unit (\$unit)**: Python scripts executed outside of a package map to the SV `$unit` scope and the C++ global namespace.
-   **Registry**: `svtypes.scope` maintains a global registry of packages and automates object discovery via `collect_module()`.

### 2. Type System (`TypeBase`)
All modeled types inherit from `TypeBase`, which provides the foundation for:
-   **Basic Types**: `Int`, `Bit`, `Real`, `String`, `Enum`, and `Parameter`.
-   **Strict Access**: Direct assignment to these types is disabled. Users must use the `.value` property (e.g., `obj.status.value = 1`).
-   **Codegen**: Every type implements `to_sv_code()` and `to_cpp_code()`.

### 3. Object Modeling (`SvObject`)
`SvObject` (formerly `SvObjectBase`) is the core class for modeling complex structures:
-   **Instance Independence**: Uses `__getattribute__` to clone class-level `TypeBase` attributes into the instance's `__dict__` upon first access, ensuring `obj1.x` and `obj2.x` are independent.
-   **Nesting**: Uses `ObjectDescriptor` to support nested `SvObject` instances.
-   **Parameterized Classes**: Supports SV-style parameters via the `Parameter` type and `specialize()` method.

### 4. Parameter Immutability (`ReadOnlyMetaclass`)
To ensure model integrity, parameters and class-level structures are protected:
-   **Immutability**: Once a `Parameter` is assigned a value (either at the class or instance level), it cannot be modified.
-   **Class Protection**: `ReadOnlyMetaclass` blocks direct assignment to any attribute in an `SvObject` class or `Package` instance.

---

## Type Implementations

### 1. The Foundation: `TypeBase` (`python/svtypes/base.py`)
All types inherit from `TypeBase`, which defines the core interface:
- **`value` Property**: Every type must implement a `value` property for data access. `TypeBase` does not implement the descriptor protocol (`__get__`/`__set__`), preventing accidental direct assignment at the class or instance level.
- **Serialization**: Defines `pack()` and `unpack()` methods to convert values to/from byte streams.
- **Code Generation**: Defines `to_sv_code()` and `to_cpp_code()` for hardware and software representation.

### 2. Basic Value Types
These types wrap standard Python values with hardware-specific constraints:
- **`Int`**: Models a 32-bit signed integer. Maps to SV `int` and C++ `int32_t`. Serialized as 4-byte little-endian.
- **`Bit(width_or_shape, signed=False)`**: Models an arbitrary-width two-state packed value. It maps to SV `bit`; a tuple shape becomes packed dimensions, for example `Bit((2, 8))` becomes `bit [1:0] [7:0]`.
- **`Logic(width_or_shape, signed=False)`**: Models an arbitrary-width four-state packed value. It maps to SV `logic`, preserves 0/1/X/Z in three byte planes, and is randomized as its SystemVerilog two-state projection.
- **`Reg(...)`**: Creates the same Python runtime type and byte representation as `Logic(...)`, while emitting the historical SV declaration spelling `reg`. Thus `type(Reg(8)) == type(Logic(8))`, and both values satisfy `isinstance(value, Reg)`.
- **`Array(element, size)`**: A real fixed-array class. A tuple shape is recursively expanded: `Array(Bit(8), (3, 2))` is represented exactly as `Array(Array(Bit(8), 2), 3)`; outer field options apply only to the outer array.
- **`Real`**: Models a 64-bit float. Maps to SV `real` and C++ `double`. Serialized as 8-byte IEEE 754.
- **`String`**: Models a variable-length string. Maps to SV `string` and C++ `std::string`.

### 3. Enumerations (`Enum`)
`Enum` declarations explicitly freeze their encoding width and signedness:

```python
class Status(Enum, width=8, signed=False):
    IDLE = 0
    BUSY = 1
```

- Supported widths are 8, 16, 32, and 64 bits.
- Every member must fit the declared signed or unsigned range.
- Duplicate numeric values are rejected, so aliases are not part of the stable contract.
- Unknown numeric values are rejected during decode with `DecodeError`.
- SV uses an explicitly sized `bit` enum base; C++ uses the matching fixed-width integer base.

### 4. Parameters (`Parameter`)
Specialized types that behave like constants in SV/C++:
- **Immutability**: Once assigned a value, it is locked. Further modifications to `.value` raise an error.
- **Codegen**: Generates SV `parameter` and C++ `static constexpr`.

### 5. Schema-defined generated records (`RecordSchema`)

Integrations can construct typed records without generating or importing Python
source code. `RecordSchema` accepts an explicit unified type name and ordered
`RecordField` values, then returns an unregistered `SvObject` class with the
normal schema, encoding fingerprint, pack/unpack, SV, and C++ generation behavior.

```python
from svtypes import Bit, RecordSchema

request_type = RecordSchema(
    "svx.generated.bus.drive.request",
    [("address", Bit(32)), ("data", Bit(64))],
    class_name="DriveRequest",
).build()
```

`RecordSchema(..., [])` is the explicit void convention and returns `None`: it
does not create an empty object envelope. The caller owns callable naming and
transport semantics; SvTypes only validates and materializes the supplied
record name and ordered fields.

### 5.1 运行时能力协商

`runtime_capabilities()` 返回包、模式、二进制、对象包络和生成运行时接口版本，
以及确定性排序的可提供能力名称。调用
`require_runtime_compatible(required, received)` 可在传输前执行共同的比较：
当前所有版本字段必须相同；未知的已提供能力会被忽略；缺少任一必需能力会失败。
同一规则由 Python、SystemVerilog 和 C++ 运行时提供。

调用和调度协议只应把它实际需要的能力名称列为必需项。记录的命名、函数重载、
接收者、异常和构造生命周期不属于 `RecordSchema` 或此协商接口。

### 5. Complex Objects (`SvObject` & `ObjectDescriptor`)
`SvObject` is the container for all other types:
- **Cloning Mechanism**: `SvObject.__getattribute__` clones class-level `TypeBase` attributes into the instance's `__dict__` on first access, ensuring instance independence.
- **`ObjectDescriptor`**: Handles nested `SvObject` instances, ensuring they are properly instantiated and linked.
- **Strict Access**: `SvObject.__setattr__` blocks direct assignment (e.g., `obj.x = 10` is banned), forcing the use of `obj.x.value = 10`.
- **Constrained random**: `@constraint` declares predicates. `@rand_layer(priority)` groups rand members and constraints, including fixed unpacked-array elements such as `self.words[0]`. `layered_randomize()` solves those groups from high priority to low, with unlisted members in implicit `builtin` (priority 0). Python returns `bool`; generated SV is `virtual function int layered_randomize()`. `randomize()` / `randomize_with()` / `layered_randomize()` cannot be overridden. Users may override `pre_randomize()` / `post_randomize()`.

---

## Type Mapping Summary

| Type | Python Base | SV Mapping | C++ Mapping | Width (Bit) |
| :--- | :--- | :--- | :--- | :--- |
| `Int` | `int` | `int` | `int32_t` | 32 |
| `Bit(w)` | `int` | `bit [w-1:0]` | `uintN_t` | `w` |
| `Logic(w)` | `LogicValue` | `logic [w-1:0]` | `LogicValue<w>` | `w` |
| `Reg(w)` | `LogicValue` | `reg [w-1:0]` | `LogicValue<w>` | `w` |
| `Real` | `float` | `real` | `double` | 64 |
| `String` | `str` | `string` | `std::string` | Variable |
| `Enum(width=..., signed=...)` | `IntEnum` member | explicitly sized `enum` | fixed-width `enum class` | 8/16/32/64 |
| `Parameter`| `TypeBase` | `parameter` | `static constexpr`| N/A |

---

## Example Usage

```python
from svtypes import SvObject, Int, Parameter, svobj

@svobj
class Header(SvObject):
    VERSION = Parameter()(1)
    id = Int()
    length = Int()

# Usage
h = Header()
h.id.value = 100
h.length.value = 20
```

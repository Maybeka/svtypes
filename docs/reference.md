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
- **`Bit[width_or_shape](...)`**: Models an arbitrary-width two-state packed value. It maps to SV `bit`; `Bit[2, 8]()` becomes `bit [1:0] [7:0]`. Use `BitSigned[...]` or `Bit[..., Signed]` for signed storage.
- **`Logic[width_or_shape](...)`**: Models an arbitrary-width four-state packed value. It maps to SV `logic`, preserves 0/1/X/Z in three byte planes, and is randomized as its SystemVerilog two-state projection. `LogicSigned[...]` and `Logic[..., Signed]` select signed storage.
- **`Reg[...](...)`**: Creates the same Python runtime type and byte representation as `Logic[...]`, while emitting the SV declaration spelling `reg`. Thus `type(Reg[8]()) == type(Logic[8]())`, and both values satisfy `isinstance(value, Reg)`.
- **`Array[element_spec, size](...)`**: A real fixed-array class. A tuple shape is recursively expanded: `Array[Bit[8], (3, 2)]()` is represented exactly as `Array[Array[Bit[8], 2], 3]()`; outer field options apply only to the outer array.
- **`Real`**: Models a 64-bit float. Maps to SV `real` and C++ `double`. Serialized as 8-byte IEEE 754.
- **`String`**: Models a variable-length string. Maps to SV `string` and C++ `std::string`.

`AssocArray` exposes read-only `key_codec` and `value_codec` properties for its
declared codecs, including when the array is empty. These are codec descriptors,
not entry values or detached copies; inspect them through public schema and
generation APIs rather than modifying their layout.

Single-bit shorthand: `Bit()`, `Logic()` and `Reg()` declare one bit. The positional argument of the unsubscripted form is the **value**, exactly as it is for `Bit[width](value)`, so `Bit(0)` and `Bit(1)` declare a single bit holding 0 or 1, and `Bit(rand=False)` declares a non-random single bit. Width comes only from the subscript: an integer that a single bit cannot represent exactly is rejected instead of being truncated (`Bit(8)` raises and points at `Bit[width]()`), because it is far more likely to be a legacy width. Signedness is selected by the `*Signed` families rather than a `signed=` keyword. The bracket spelling keeps its packed-value truncation semantics (`Bit[1](8)` is `0`).

Every SvTypes declaration in a class body must materialize. An uncalled bracket specification (`data = Bit[8]`) or a bare type class (`data = Bit`, `child = Packet`) is rejected at class creation with a repair hint, instead of being silently omitted from schema, packing, randomization and generated target code. Ordinary class-level metadata is unaffected: non-SvTypes values are always allowed, and a leading underscore keeps an SvTypes value as metadata (`_Payload = Bit[8]`).

The single-bit shorthand rejects the legacy `width=` keyword; use `Bit[1]()` instead. Both positional and `value=` integer initializers must be 0 or 1 for `Bit()`, `Logic()` and `Reg()`.

`type_spec_identity(type)` returns a JSON-compatible boundary identity; `type_spec_from_identity(identity, location=...)` rebuilds the annotation, including multidimensional packed/array shapes and `Reg` declaration style. A class returned by `specialize()` is identified by its source template and parameter bindings rather than its generated class name. The template (like other referenced classes) must be importable by module and qualified name; no generated-class registration or process-local cache is needed.

### 3. Enumerations (`Enum`)
`Enum` declarations explicitly freeze their encoding width and signedness:

```python
class Status(Enum[Bit[8]]):
    IDLE = 0
    BUSY = 1
```

- `Enum[Bit[width]]` supports every positive bit width; `Enum[Int]` and `Enum[LongInt]` select SV `int` and `longint`. Bare `Enum` uses SV's default signed `int` base.
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
    [("address", Bit[32]()), ("data", Bit[64]())],
    class_name="DriveRequest",
).build()
```

`RecordSchema(..., [])` is the explicit void convention and returns `None`: it
does not create an empty object envelope. The caller owns callable naming and
transport semantics; SvTypes only validates and materializes the supplied
record name and ordered fields.

### 5.1 Generated SV expressions

`sv_type_expression(codec)`, `sv_packer_expression(codec)`, and
`sv_declaration(codec, name)` are stable rendering entry points for an
integration that generates a typed adapter around an existing SvTypes codec.
The first two return a type or packer expression; the declaration form also
places unpacked dimensions correctly after `name`.

`sv_declaration(codec, name, include_initializer=True)` also includes an explicit
initializer supplied by `Bit`, `Int`, `LongInt`, `Logic`, `String`, `Real`,
`ShortReal`, `Enum`, `SvStruct`, or fixed `Array` elements. The default
remains declaration-only. `codec.sv_initializer()` returns the original declared
initializer (or `None`), while `codec.sv_repr()` renders the current value;
changing a value does not change its declaration. Logic initializers preserve
all X/Z bits and signedness. String literals escape quotes, backslashes, control
characters and UTF-8 bytes. A packed struct typedef contains only member types;
its field initializer is a nested assignment pattern preserving declared member
defaults, including unspecified four-state members. Structs with no explicit
member defaults retain declaration-only output.
Fixed arrays repeat their element template's declared initializer through an
SV `default` assignment pattern, recursively for multiple dimensions. Later
changes to individual elements are not declaration defaults. Dynamic arrays,
queues and associative arrays retain their empty native defaults.

```python
from svtypes import Int, Queue, sv_declaration, sv_packer_expression

codec = Queue[Int]()
assert sv_declaration(codec, "history") == "int history [$]"
assert sv_packer_expression(codec) == "svtypes_pkg::queue_packer#(int, svtypes_pkg::int_packer)"
```

These functions do not define transport, ownership, or dispatch behavior.
They only preserve the SystemVerilog spelling and codec pairing used by normal
SvTypes generated code.

For nested collection adapters, use `sv_codegen_context(codec, prefix=...)`.
It yields dependency-ordered local `typedef` declarations. Emit them in the
scope where type and packer expressions rendered inside the context are used.
Equivalent codec instances share aliases; leaving the context restores the
previous rendering state, including after an exception. `prefix` must be an
SV identifier. The aliases are generation details and do not change wire data.

Generated object classes declare local typedefs for nested fixed arrays, queues,
dynamic arrays, and associative-array values before using them as packer type
arguments. Definitions are deterministic and deduplicated within the class;
field dimensions and the wire encoding are unchanged. These names are private
generation details, not new user-declared types.

### Codec Prototypes and Object Identity

Use `MyObject.codec_template(session=session)` when an object class is needed
only as a pack/unpack codec. Its object number is zero and it is not registered;
normal `MyObject(session=session)` construction still creates a live identity.
Creating a prototype and decoding values bypass user `__new__` / `__init__`,
including constructors requiring arguments. Only SvTypes codec state and
declared field defaults are initialized; ordinary user construction is unchanged.
`checked_unpack(codec, data, descriptor, session.unpack_context())` checks the
encoding before decoding into that session. Shared references and cycles remain
shared, and repeated decoding reuses already registered objects. Importing a
number with the session's origin advances local allocation beyond that number;
incompatible or explicitly duplicated live identities still raise an error.

### 5.2 运行时能力协商

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
- **`ObjectDescriptor`**: Handles nested `SvObject` instances, ensuring they are properly instantiated and linked. Use `Object["Child"](rand=True)` when an already allocated, non-null child must participate in the parent's constrained randomization; the generated declaration is `rand Child child`. The default is `rand=False` and does not allocate or randomize the handle.
- **Strict Access**: `SvObject.__setattr__` blocks direct assignment (e.g., `obj.x = 10` is banned), forcing the use of `obj.x.value = 10`.
- **Constrained random**: `@constraint` declares predicates. `@rand_layer(priority)` groups rand members and constraints, including fixed unpacked-array elements such as `self.words[0]`, or a complete `DynArray` / `Queue` member. Dynamic containers are handled per current element: entry-disabled elements stay disabled, and elements created during a layered call remain enabled. `layered_randomize()` solves those groups from high priority to low, with unlisted members in implicit `builtin` (priority 0). Python returns `bool`; generated SV is `virtual function int layered_randomize()`. `randomize()` / `randomize_with()` / `layered_randomize()` cannot be overridden. Users may override `pre_randomize()` / `post_randomize()`.

### 5.3 External field storage

`MyObject.refresh_declarations()` revalidates inherited fields and constraints
after a framework finishes assembling the class's bases, before any instances
are constructed. It preserves the existing `__init__` and does not execute user
class hooks. Refresh assembled bases before their children. This is not a live
object migration API: it does not update existing values or storage bindings.

`SvObject.bind_external_storage(storage, field_keys)` binds selected fields of
one object instance to an `ExternalFieldStorage`.  Keys are opaque to
SvTypes; the mapping is keyed by `FieldIdentity(declaring_type, name)` so an
inherited declaration cannot be confused with an equally named field.  Every
unbound field of that instance, and every field of every other instance,
continues to use ordinary local storage.

The backend receives a `FieldDescriptor`, an immutable typed `FieldPath`, a
`FieldOperation`, and already-normalized SvTypes bytes.  It never receives a
facade value or needs to reproduce SvTypes encoding.  Reads have no implicit
writable cache; indexed collection writes and associative-key writes are leaf
operations.  `MemoryExternalFieldStorage` is a standalone reference backend
for tests and simple embedders.

Indexed/keyed access to a nested list or mapping retains the same external
owner and extends its path. Iterating a sequence also retains bindings for
nested container elements: editing a retrieved row is an addressed write,
not a change to a detached Python copy. Traversal reads a snapshot of the
parent once; bound child accesses still read their current backend values.
Sequence slices return an ordinary outer list, while nested children retain
their original backend indices (including reversed slices) and close state.
Deep copies recursively snapshot values without copying the backend or opaque
keys, and the resulting values are detached from external storage.

`APPEND` addresses a container and encodes one element of that container.
`INSERT` addresses the new element's index and uses that addressed element's
codec directly, including when the element is itself a container.

Sequence slice assignment uses element `SET` and, for variable-length
sequences, `INSERT`/`DELETE` operations rather than writing the whole container.
Slice deletion uses descending element indices so remaining values retain their
positions. Extended slices keep normal Python length rules; fixed arrays cannot
change length. The resulting typed shape is validated before any write. These
compound operations are not a transaction if the backend fails during a write.

```python
storage.seed("packet.count", Packet.__dict__["count"], 3)
packet.bind_external_storage(
    storage,
    {FieldIdentity(Packet, "count"): "packet.count"},
)
assert packet.count.value == 3
packet.count.value = 0x103  # Bit width normalization still applies
```

`bind_external_value(descriptor, storage, key)` offers the same semantics for
a temporary value root.  `close()` invalidates the root and every derived
collection view.  External randomization solves a detached value snapshot and
publishes each bound root only after success; an unsuccessful solve leaves the
backend unchanged.
Result publication handles null, shared and cyclic handles, including collection
elements, without replacing already allocated live objects with trial clones.
Successful external randomization also commits the owner's `randc` cycle
history. Disabled fields retain their paused cycle; an unsuccessful solve does
not consume values from that history.
`pre_randomize()` runs on the original live receiver before the trial snapshot
is made. After a successful solve is published, `post_randomize()` runs on that
same receiver; an unsuccessful solve does not call it. Nested random-object
callbacks retain their live receivers, and layered callbacks observe the current
active flag and priority. Explicit callback writes are ordinary user side effects,
not trial writes: a later unsuccessful solve does not roll them back.
Value copies initialize SvTypes state directly rather than replaying a user
constructor. Randomization trial snapshots allocate no registered object
identity and do not create a foreign companion; ordinary object copies receive
their own codec identity.

---

## Type Mapping Summary

| Type | Python Base | SV Mapping | C++ Mapping | Width (Bit) |
| :--- | :--- | :--- | :--- | :--- |
| `Int` | `int` | `int` | `int32_t` | 32 |
| `Bit[w]()` | `int` | `bit [w-1:0]` | `uintN_t` | `w` |
| `Logic[w]()` | `LogicValue` | `logic [w-1:0]` | `LogicValue<w>` | `w` |
| `Reg[w]()` | `LogicValue` | `reg [w-1:0]` | `LogicValue<w>` | `w` |
| `Real` | `float` | `real` | `double` | 64 |
| `String` | `str` | `string` | `std::string` | Variable |
| `Enum[Bit[w]]` | `IntEnum` member | explicitly based `enum` | fixed-width or arbitrary-width wrapper | `w` |
| `Parameter`| `TypeBase` | `parameter` | `static constexpr`| N/A |

---

## Example Usage

```python
from svtypes import SvObject, Int, Parameter, svobj

@svobj
class Header(SvObject):
    VERSION = Parameter[Int](1)
    id = Int()
    length = Int()

# Usage
h = Header()
h.id.value = 100
h.length.value = 20
```

---

## Functional coverage

`@covergroup` declares a static functional-coverage template on an `SvObject`.
Use `CovPoint` and `Cross` nested classes to declare coverpoints, crosses, and
their `bins` / `ignore_bins` / `illegal_bins` / `transition_bins`.

```python
from svtypes import Bit, CovPoint, SvObject, bins, covergroup

class Packet(SvObject):
    opcode = Bit[2]()

    @covergroup
    def cg(self):
        class opcode_cp(CovPoint, source=self.opcode):
            read = bins[0]
            write = bins[1]
```

`CoverInput[T]` and `CoverRef[T]` provide typed covergroup constructor
formals. Instantiate embedded covergroups from the class's single
`@coverage_init` method. Tools materialize one declaration directly from its
CoverInput signature, without reading initializer actuals:

```python
layout = preview_coverage_layout(Packet, "cg", first=2, last=5)
```

`CoverRef` remains declaration-bound and is never a tool input. Class
`Parameter` references remain symbolic as `parameter_ref` in CoverageIR and
Design Manifest selectors; a Python instance layout materializes the bound
value as a constant, while generated SystemVerilog retains the parameter name.

Automatic `cov=True` coverage is compiled into the same CoverageIR pipeline as
an explicit covergroup. Scalar and container value domains receive deterministic
automatic bins; an `Object[...]` handle field does not receive a default
nullness coverpoint.
Fixed arrays receive one point per scalar leaf slot, including multiple fixed
dimensions; point names retain all indices, such as `matrix[0][1]`. Slot bounds
use `$size(...)` in generated SV, supporting both fixed and dynamic arrays.
When a fixed slot or explicit `cov_slots` slot contains a dynamic or associative
container, that slot's point samples the selected child's leaf value domain.
Missing outer slots and empty children contribute no samples.
Value-domain coverage of dynamic or associative containers traverses nested
containers and samples their leaf values, not associative keys or child
containers. Empty children contribute no samples. Python and generated SV use
the same traversal and leaf bins. Wide integer bin boundaries retain their
exact width and sign in generated SV.

`instance.get_coverage()` reports type coverage, while
`instance.get_inst_coverage()` reports the individual covergroup instance.
`instance.sample_count` records accepted samples and
`instance.has_illegal_hits()` reports whether an illegal bin was hit.

Use `CoverageDatabase.import_ucis()` only with explicit bindings to frozen
declarations. UCIS is an interchange projection with a documented loss report,
not a replacement for the SvTypes coverage database.

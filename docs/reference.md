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

## Python Codec Session Ownership

`CodecSession(..., retain_objects=True)` retains its registered objects until
`remove()`, `clear()` or `close()`, preserving the existing default. A non-owning
session is selected with `CodecSession(retain_objects=False)`; its read-only
`retain_objects` property reports the policy. The policy must be a boolean.

In a non-owning session, the identity registry uses weak references. Python
variables, graph fields and application/framework owners keep their objects live;
registration alone does not. A surviving object keeps the same object number
and repeated decoding resolves it to the same instance. Unreachable cycles are
reclaimed by ordinary Python garbage collection, without a promised collection
time. Collection removes the registry entry without rewinding the allocator or
automatically reusing a previously allocated or imported number. Explicit
allocator resets remain a caller-controlled operation, not a collection action.

`PackContext` and `UnpackContext` preserve in-flight graph objects. Shared edges,
cycles, collision checks, rollback and wire encoding are unchanged. A complete
graph can be decoded again after collection; a reference-only record for a
collected object raises an unresolved-reference error. An object number or raw
encoded bytes are not an owning reference. External storage or application
protocols that keep only numbers must provide an actual owner or use an owning
session. Explicit removal/clear/close remain available under both policies.
An explicit `PackContext` can span multiple pack calls and retains seen objects
until the context is released, preventing recycled Python `id()` values from
producing incorrect reference records. An unpack context releases its temporary
owners after each top-level success or failure; retaining an idle unpack context
does not keep the decoded graph alive.
An explicit `PackContext` may span multiple pack calls and therefore strongly
retains seen objects for its own lifetime, preventing recycled Python IDs from
producing false reference records. Releasing that context releases those
references. The unpack context clears its temporary ownership after top-level
success or failure; keeping the empty context does not retain decoded objects.

This policy applies to the Python registry only. It does not change portable SV
strong handles, select the experimental weak-reference SV backend, or dispose
foreign `RemoteRef` targets. Independently created sessions still require distinct
origins when their encoded objects share one receiving namespace.

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

#### Struct kinds and migration

`SvStruct` declares an unpacked value structure. Use `SvPackedStruct` for a
packed integral structure; it is also a Python subtype of `SvStruct`. Both are
non-null values, without an object envelope or object number. Unpacked members
may include supported containers, strings and class handles. Assigning `.value`
copies aggregate storage and preserves the references held by class handles.
Packed structures accept packed integral members only, with the first member
in the highest bits. Constraints and `unique()` treat their whole value as one
unsigned integral operand; modes on individual packed members are not exposed.

Existing definitions that require the historical packed behavior must change
their base to `SvPackedStruct`. Struct identities and source schemas now include
an explicit `packed` boolean. The fieldwise byte encoding is unchanged: packing
a struct does not combine separately encoded fields or add host padding.
Unpacked typedefs carry their member defaults; packed typedefs use a containing
field assignment pattern instead. See the [struct contract](SVTYPES_STRUCT_CONTRACT.md)
for integration, migration and the capability qualification status.

`sv_type_expression(codec)`, `sv_packer_expression(codec)`, and
`sv_declaration(codec, name)` are stable rendering entry points for an
integration that generates a typed adapter around an existing SvTypes codec.
The first two return a type or packer expression; the declaration form also
places unpacked dimensions correctly after `name`.

`sv_declaration(codec, name, include_initializer=True)` also includes an explicit
initializer supplied by `Bit`, `Int`, `LongInt`, `Logic`, `String`, `Real`,
`ShortReal`, `Enum`, `SvPackedStruct`, or fixed `Array` elements. The default
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
- **`ObjectDescriptor`**: Declares a fixed, nullable handle slot. Accessing `owner.child` returns the slot. Read or write its referent through `owner.child.value`. The initial value is `None`; construct the child explicitly. Handle elements use the same convention, for example `queue[0].value` or `table[key].value`. An external index is a path view. Use `borrow_element` to retain the selected element across structural edits. `Object["Child"](rand=True)` generates `rand Child child` and enables recursive randomization of an already assigned child.
- **Strict Access**: `SvObject.__setattr__` blocks direct assignment (e.g., `obj.x = 10` is banned), forcing the use of `obj.x.value = 10`.
- **Constrained random**: `@constraint` declares predicates. `@rand_layer(priority)` groups rand members and constraints, including fixed unpacked-array elements such as `self.words[0]`, or a complete `DynArray` / `Queue` member. Dynamic containers are handled per current element: entry-disabled elements stay disabled, and elements created during a layered call remain enabled. `layered_randomize()` solves those groups from high priority to low, with unlisted members in implicit `builtin` (priority 0). Python returns `bool`; generated SV is `virtual function int layered_randomize()`. `randomize()` / `randomize_with()` / `layered_randomize()` cannot be overridden. Users may override `pre_randomize()` / `post_randomize()`.

#### Unpacked structure values

In ordinary Python and in the checked entry, an unpacked structure's `.value`
reads a detached mapping from declared field names to values. Nested containers
and structures are copied as ordinary data. Object handles retain their identity.
Assign a complete mapping to `.value` to write the structure through its
declaration. To access a member named `value`, migrate `record.value.value`
to `field(record, "value").value`. Constraint and coverage declarations resolve
member names against the type declaration, so `self.record.value` in those
declarations names the declared member.

#### Checked Python variable access

Use `run_checked(main)` for a project function that uses checked variable slots.
A plain SvTypes type annotation is equivalent to `Input[T]`. `Input` copies
in the value, `Output` starts unassigned, and `Inout` copies in and writes
back. Output and Inout write back on normal return, in declaration order,
after preparing all writebacks. `Ref[T]` accesses the original slot for the
duration of the call and requires the same declaration type. Reading an
unassigned `Output` raises an error. A `CopyoutError` exposes `parameter`,
`phase` (`"preparation"` or `"publication"`), and the `completed` parameters.
A publication failure preserves earlier writes and chains the underlying error.
Actual arguments follow Python evaluation order. Input carriers are read and
copied at call entry, after all actual expressions have been evaluated; values
already obtained through `.value` or another expression are not reevaluated.
Indexed Input reads need ordinary field access, not stable element borrowing.
Aggregates are independent copies while object handle identity is preserved.
The exception notes include the source location and expression for a checked
operation, including an explicit return that triggers copyout. The underlying
error remains available through the exception cause.

An `Output` structure or fixed array can be initialized through its declared
member or element slots. An assigned slot can be read while other slots
are still unset. Reading the whole value or returning normally requires
all slots to be initialized. The error identifies the first missing path.
A dynamic collection needs a whole value assignment to establish its shape
before element edits.

```python
from svtypes import Inout, Int, run_checked

def increment(word: Inout[Int]) -> None:
    word.value += 1

def main() -> int:
    word = Int(10)
    increment(word)
    return word.value

assert run_checked(main) == 11
```

Pass `.value` to ordinary parameters. Checked code rejects storing a slot as
another variable, passing it to an untyped parameter, or returning it.
Use independent values in `*args` and `**kwargs`. Pass a slot directly to a
parameter with a declared direction. Importing SvTypes alone leaves ordinary
Python imports, loaders and bytecode caches unchanged. Use
`with checked_project(root):` to check fresh project imports explicitly;
`run_checked()` scopes imports to its entry project while it executes. Within
that scope, the project loader checks named and wildcard imports, class
declarations and function defaults for carrier aliases. Nested scopes restore
on exit; modules already in `sys.modules` are not reloaded. Place carrier
operations in the checked entry or its callees.
Tools that record source inputs may use `observe_module_sources(observer)` as
a context manager. The observer receives `(filename, source_bytes)` for each
source read by the checked module loader, before parsing or transformation.
Bytes retain the original encoding and line endings. The callback may raise to
reject a load, but cannot replace source or disable checks. Nested observers
compose within the current execution context and are removed on exit, including
exceptions. Cached modules and modules handled by other loaders produce no
observation; the tool remains responsible for those sources. The checked finder
leaves sources outside its scope to later import finders.
Framework integrations may delimit their implementation with
`with checked_library_sources(*sources):`, where each source is an existing exact
file or package directory. Within that context, framework implementation runs
normally instead of undergoing the user-source transformation. Nested scopes
restore on exit, including exceptions. User sources outside those paths remain
checked, including user constructors called by SvTypes constructor gateways.
Ordinary calls to typed framework functions use the same boundary: parameter
direction, formal lifetime, copyout and result-escape checks still surround the
unmodified function body. An untyped parameter still cannot receive a carrier.
Frameworks must route their own user callbacks through checked entry points;
this boundary is an explicit integration contract, not a security mechanism or
a way for application code to bypass the variable convention.
Framework trampolines that already apply their own checked parameter contract
may use `@checked_call_gateway`. The marker is effective only while its source
is inside `checked_library_sources`. It preserves the original function and
signature, passes actuals directly to the trampoline, and rejects carrier
results. The trampoline must apply the real parameter directions with
`run_checked` and route user callbacks through checked entry points. This avoids
binding parameters and publishing outputs twice; it does not grant an untyped
application helper permission to receive carriers outside that explicit scope.
Checked execution uses a denylist: Python behavior is allowed by default.
Only concrete violations of the variable convention are rejected: carrier
alias/storage/capture or escape, missing or invalid carrier parameter contracts,
replacement/deletion of declared slots, and invalid lifetime, type or permission
access. Ordinary annotations are not runtime constraints. Unavailable source or
an unrecognized syntax form is not evidence of a violation.
Ordinary module imports follow the same principle: `globals()`, `locals()`,
`vars()`, `eval()`, `exec()`, `super()` and their aliases execute in the original
caller frame, not an internal observer's scope after importing SvTypes.
Readable project source is instrumented to observe these operations; opaque and
external bodies execute normally, retaining parameter and return checks at the
boundary. This does not promise inspection inside opaque code. Class annotations,
dataclasses and caller-scoped builtins retain their Python behavior. Use
explicit parameter contracts for carrier forwarding; do not retain a Ref in a
closure or deferred generator. A generator may own freshly declared variables
and yield their values. Its checked scope is active only while it is resumed,
including `send`, `throw`, and `close`, not while it is suspended. Ordinary
asynchronous definitions and value-only work execute normally; deferred work must
not retain a Ref or another carrier beyond the synchronous parameter scope.
These checks enforce a usage convention,
not a sandbox against deliberate bypasses. The callback gateways cover `sorted`, `list.sort`, `min`,
`max`, `map`, `filter`, `functools.reduce`, and the callbacks of
`itertools.accumulate`, `dropwhile`, `filterfalse`, `groupby`,
`starmap`, and `takewhile`. Lazy callbacks still run checked when their
iterators are consumed after the entry returns.

SvTypes can call custom structure `pack` and `unpack` methods through its
standard encoding and decoding operations. Their existing signatures and
`super()` delegation apply inside the checked entry. The decoder can hand a
newly decoded structure to SvTypes together with the consumed byte count.
Its function body remains subject to the checked carrier rules.

Call `copy.deepcopy(owner)` inside the checked entry to copy an object graph.
Its copy hooks share the native recursion memo. A hook can forward the memo
to its base implementation and use it to register its new copy. The memo is
scoped to the copy hooks. Checked calls to `deepcopy()` create that memo
internally. Copy the value of a variable carrier through its declared value
interface.
`read_value(slot)` returns detached aggregate data with handle identities
preserved. Use `field(owner, "value")` to select a declared field named
`value`, and `write_value(slot, value)` for explicit value assignment.

Direct edits such as `queue.value.append(3)` and `table.value.update(entries)`
act on the original storage. Local dynamic sequences preserve element slots
when `reverse()` or `sort()` moves them. Fixed local arrays keep their slots
and assign the reordered values to them. Externally bound sequences reject
`reverse()` and `sort()`. Use the backend's own reorder operation if it
provides one and can preserve borrowed element identity.

Nested calls such as `rows.value[0].append(3)` also edit the declared child
storage. External child selection uses scoped borrowing where required; a
backend missing that capability raises an error instead of editing a snapshot.

Outside the checked entry, `with borrow_element(container, index_or_key) as slot:`
provides an `ElementBorrow`. Its `.value` reads or writes the selected element,
even after the element leaves the container. Use `forward()` for an independent
lease on the same element or `child(FieldPath(...))` for a declared subslot.
Closing a lease ends its access. In checked code, pass the selected element
directly to a `Ref[T]` parameter to borrow it for the call.

For external storage, use `isinstance(backend, ExternalBorrowStorage)` to
detect support for stable element borrowing. The backend resolves the field
path once in `acquire_borrow()`. It uses the same SvTypes bytes for
`read_borrow()` and `write_borrow()`, then releases the token with
`release_borrow()`. Stable subslots of an already borrowed aggregate require
`ExternalBorrowPathStorage`, which adds `acquire_borrow_child()` with an
independently released token. Ordinary path access remains available on
backends with only `ExternalFieldStorage`. A stable borrow on such a backend
raises an error. Leases honor the original binding's close and write permissions.

`ExternalBorrowFieldStorage` optionally exposes immediate operations below an
already borrowed aggregate. `read_borrow_path(token, path)` reads a relative
field; `write_borrow_path(token, path, operation, payload)` forwards the ordinary
`FieldOperation` and SvTypes bytes, including append/insert/delete/pop/resize.
Relative paths are evaluated per operation. This does not promise stable child
identity: forwarding a child variable still requires `ExternalBorrowPathStorage`
and an independently acquired token. A backend may implement either capability
or both. Without field-operation support, the existing child-token read/set
fallback remains available and structural edits remain explicitly rejected,
not implemented by whole-aggregate copyback. Borrow closure and readonly checks
precede field operations too.

Frameworks can inspect an existing slot with `external_storage_location(variable)`.
Local variables and independent Input/Output/Inout formals return `None`. An
external slot returns an immutable `ExternalStorageLocation`: `kind == "field"`
provides `storage`, root `descriptor`, `key` and exact `path`; `kind == "borrow"`
provides `storage`, leaf `descriptor` and the existing opaque `token`, never an
index/key path. Both provide `readonly` and `session`. Forwarded Ref formals
retain the original endpoint. The query itself reads no value, acquires no
lease and does not extend the formal, binding or borrow lifetime; all properties
and `require_open()` reject access after any source scope closes. Selecting a
member before the query may still read its parent according to normal access
rules. This is a framework integration API, not a way for checked business code
to save or return variable references. It does not relocate storage or create
backend borrowing support.


#### Scoped local slot storage

Frameworks can import `SlotIdentity`, `slot_identity()` and `relocated_slot()`
from `svtypes`; no experimental module or private binding mutation is needed.
These are integration operations, not additional permissions for checked business
code to save, capture or return carriers or storage metadata.

`slot_identity(variable)` queries a **local** slot without reading its value or
acquiring a lease. Compare the result with `==`, not Python `is`: an actual and
its valid Ref forwards compare equal and have equal hashes; equal-valued distinct
variables and independent Input/Output/Inout formals remain different. Even an
unassigned Output has an identity, but cannot yet be relocated. The identity
is not a serialized ID. `is_valid` reports its lifetime and `require_open()` checks
it. Comparison and hashing also check validity. Each identity expires with its
own source formal, borrow or temporary view; closing one forwarded lease does
not invalidate identities obtained through other still-open leases. An identity
holds weak references only, so retaining it does not extend source lifetime.
A plain local variable remains valid while its carrier exists. Remove identities
from framework maps before their scopes close. Copying an identity preserves it.

`relocated_slot(variable, create_storage)` is a synchronous context manager:

```python
from svtypes import Int, MemoryExternalFieldStorage, relocated_slot

value = Int(3)
backend = MemoryExternalFieldStorage()

def create_storage(initial, identity):
    backend.seed("counter", Int(), initial)
    return backend, "counter", lambda: None  # release backend resources here

with relocated_slot(value, create_storage) as temporary:
    value.value = 8
    assert temporary.value == 8
assert value.value == 8
```

The `StorageFactory` callable receives a detached initial value and scoped
identity, returning `(backend, key, release)`. It owns any partial allocation
until it returns a valid triple. The backend uses the existing
`ExternalFieldStorage` types, paths and byte encoding; the factory's closure can
supply its codec. The original carrier and existing/new Ref formals use the same
storage. Nested scopes for that slot reuse the first allocation and ignore the
inner factory; the last exit restores the latest value, closes the temporary
view and releases resources. Completed writes survive body exceptions and
synchronous unwinding due to cancellation. This is not an asynchronous Ref API
or a cross-thread storage synchronization mechanism.

Supported roots are local scalar/enum/packed values, RemoteRef value records,
structs and fixed/dynamic/queue/associative containers, including selected local
value elements and independent assigned value formals. Handles inside values
retain their referents and codec session; mixed sessions are rejected before
allocation. A bare object entity or object-handle slot is not a relocatable value
root. A root with active child borrows/Refs or independently externally bound
children is rejected before allocation; closing the last child lease permits
relocation. Detached old elements do not block their former container. Subslots
of an already relocated aggregate cannot be independently relocated. Existing
external fields/borrows continue using `external_storage_location()` and their
original endpoint; the local identity API does not invent equivalence for them.
Original readonly, closed-state and type checks remain in effect.

`StorageRelocationError` reports `phase`, `restored`, `body_error` and all
`failures` (`StorageRelocationFailure.phase`/`.cause`). Stages are `prepare`,
`readback`, `restore`, `close` and `release`. Cleanup continues after failures;
release is attempted once after a valid allocation. An exception in the body is
re-raised unchanged when cleanup succeeds; otherwise it remains available in
`body_error` and the exception chain. `restored=False` means the latest external
value could not be restored: the local slot may contain stale or partially
restored data and must not be treated as a successful result. No rollback or
cross-backend transaction is promised.

#### Optional checked-call storage policies

- `current_borrow_purpose()` returns `BorrowPurpose.EXPLICIT` for explicit
  acquisition, `OPERATION` for temporary checked operations, or `REF` for a
  real Ref actual. Context-local state restores on success and failure. A
  purpose is information, not permission or a substitute for stable identity.
- `ExternalRefValidationStorage` adds `validate_ref_borrow(token)`. It runs on
  **each** actual Ref binding, including pre-acquired leases and forwarding,
  before the body. Raising prevents entry and releases temporary acquisitions.
  Existing backends without the method keep their behavior.
- `ExternalValueParameterStorage` adds `value_arguments_by_path: bool`.
  Only explicit `True` opts into captured-address Output/Inout copyout. Indices
  and keys are evaluated once; formals remain independent, and successful return
  prepares all outputs before publishing them in declaration order. Structural
  changes can change the element at that address; there is no old-element
  identity promise. Exception/cancellation does not perform normal copyout.
  Borrowed-aggregate and relocation adapters preserve this policy. Backends
  without it retain selected-slot semantics. Real Ref still requires stable
  borrowing and passes validation; the flag does not grant Ref support.

Detect the two optional protocols with `isinstance(backend, ProtocolName)`;
check the policy value separately. No changes to ordinary Input are required:
actuals evaluate in Python order, then carriers are read/copied at call entry;
already evaluated values are not reevaluated. The former experimental private
flags/context variables are superseded by these public APIs. The old module
only re-exports the two storage functions for import migration.


### 5.3 External field storage

External collection `.value` indexing, slicing, iteration and `pop()` resolve
handles in the same binding session as direct slot reads. They return the
original referents without cloning them or replaying stale object fields from
the stored bytes. Select a session with `bind_external_value(..., session=session)`;
omitting it uses the default session. Struct views retain their storage binding
and permissions while their handles still point to the original objects.
Whole-object value copy reads handles from the source's actual storage and
preserves deep-copy sharing, nulls and cycles. Newly copied objects belong to
the destination session.

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
a temporary value root. Its optional `diagnostics="strict"` setting checks wire
fingerprints and expected field counts; the default is `"off"`, including handle
reads. Child views, element borrows and pop results inherit this setting.
Handle identity/session resolution, type selection, bounds and exact payload
consumption remain required. `close()` invalidates the root and every derived
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

`CoverageIR` and its nested selectors, expressions, parameters and options are
deeply immutable: mappings are read-only and sequences are tuples. Obtain detached
JSON data with `stable_dict()` / `definition_snapshot()`; editing a snapshot
cannot change a declaration or instance.

`CoverageDatabase.record(instance, logical_instance_key=...)` refreshes a snapshot
without adding the same samples again. `merge(other)` strictly checks declarations
across every matching type, even across different instance keys. Matching keys
also require matching instance layouts. Anonymous records remain independent,
including cross-run merges after file round-trips.

```python
report = master.merge(incoming, mode="master", reject_conflicts=False)
conflicts = report["conflicts"]
master.write("merged.svtcov")
```

Explicit master projection retains the master's definitions, hierarchy and bins,
adding only semantically compatible items. Changed points also block dependent
crosses. Group semantics, same-key input layouts and unknown types/named instances
produce conflicts rather than guessed matches. `reject_conflicts=True` fails the
entire operation on any conflict. Strict merge, projection and UCIS import prepare
their results before atomic publication. `merge_reports` returns detached copies
and is persisted. The current binary format writes v2 and reads v1/v2; parameterized
v1 records lacking concrete definitions cannot participate in projections requiring
them. `apply_baseline()` restores counts and finite sources, including cross-local
illegal evidence, but not transition histories or old objects.

`CoverageDatabase.import_ucis(xml, bindings={key: frozen_instance})` requires
explicit frozen-instance bindings and checks supported bin kinds/ranges, ordered
cross members/indices and scoring options. Parameterized arrays use materialized
instance bins. Missing supported bins or incompatible definitions reject the
whole import. The caller asserts source/iff correspondence through the binding;
arbitrary SV expressions are not reverse-translated. Missing out-of-subset counts
are explicitly recorded in the loss report and persisted `import_provenance`,
not claimed as proven zero hits. Import an existing key into a separate database
before merging. UCIS is an interchange projection, not a replacement for the
native SvTypes database.

## Optional decode diagnostics and generated-code editing

Ordinary object decoding defaults to `UnpackContext(diagnostics="off")`: it neither constructs an expected
digest nor compares the expected field count or encoding fingerprint. Format versions, input bounds,
type identity and reference resolution remain checked. Use
`codec.unpack(data, UnpackContext(diagnostics="strict"))` or
`target.from_bytes(data, context=UnpackContext(diagnostics="strict"))` for explicit strict decoding.
`checked_unpack()` remains strict, propagates that policy to nested objects, and restores a supplied
context after success or failure. Independent contexts can use different policies concurrently; sharing
one mutable context across concurrent operations is unsupported.

C++ provides `DecodeContext`, `DiagnosticPolicy::off/strict`, and `DecodeContextGuard`; the RAII scope
propagates policy to nested unpack calls with thread isolation. SV provides `decode_context` with
`DIAGNOSTICS_OFF/DIAGNOSTICS_STRICT`; `begin_decode_context(context)` returns the previous context,
which must be restored with `end_decode_context(previous)`. SV policy is simulation-process local,
and explicit scopes must be paired.

Running generated code does not require its artifact hashes to match source. During generation,
`generate()` protects edited managed files and conflicting unmanaged files: it returns their paths in
`failed` and publishes nothing. `overwrite_modified=True` explicitly permits replacement;
`mode="check"` always reports differences without writing. See the
[implementation record](SVTYPES_GENERATED_CODE_EDITABILITY_IMPLEMENTATION.md) for implementation and verification details.

SV generation uses explicit helper types by default, deriving supported bit/logic widths and array
dimensions from the actual field declaration with `$bits` and `$size`. This supports manual width
and fixed-size edits without `type(expression)`. Encoding category and signedness remain part of
the generated field's contract and must be updated when the field's category changes. Parameterized
classes retain explicit types where deriving a type constant is unsupported by the target; their
shape checks run only in an explicit strict diagnostic context. Editing these
fields requires updating the explicit codec type as well as the declaration;
turning diagnostics off does not make an incompatible SV ref connection legal
or automatically adapt the wire layout.
`to_sv_obj(use_type_reference=True)`, `to_sv_pkg(use_type_reference=True)`, and
`generate(..., use_type_reference=True)` select SystemVerilog `type(expression)` in helper code.

`generate()` defaults to `targets={"sv", "cpp"}`. Add `"schema"` for the full
source schema and `"coverage"` for the coverage observation manifest; these
metadata targets can also be requested independently. Code-only generation
skips source-schema traversal and its hashes. The small artifact ownership
manifest remains for publication and hand-edit protection. Multi-file output
places framework implementations, including layered randomization, in
`support/`; single-text APIs remain self-contained with a support section.

Class declaration and ordinary randomization/sampling do not prepare encoding
fingerprints. The first encoding, generated-code request or explicit encoding
descriptor query prepares the wire fingerprint once, without source schemas.
Later requests reuse it; registry changes and declaration refresh invalidate
affected caches lazily. The existing wire header still carries its fingerprint;
writing that protocol field is distinct from comparing it during decoding.
Ordinary randomization computes constraint cycle signatures only when randc
state is involved.

`generate()` can emit an unbound parameterized class as a target-language template. Since that
declaration has no concrete source schema, its name appears in `unbound_templates` in the schema
document and generation manifest; it is not included in `types` or the concrete fingerprint lists.
Bind its parameters before requesting a concrete `schema_descriptor()`.

The single-text `to_sv_obj()` and `to_cpp_obj()` interfaces do not create a manifest. An integration
that wants an explicit runtime compatibility check for their output can require
`svtypes.generated-field-visitors.v1`, `svtypes.optional-codec-diagnostics.v1`, and
`svtypes.generated-wire-helpers.v1`; SV output additionally requires
`svtypes.generated-field-operations.v1`. Pass these names to the relevant runtime's
`require_runtime_compatible` API. Ordinary compilation and execution do not perform this check.

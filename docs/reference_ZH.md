# SvTypes：SystemVerilog / C++ 建模 DSL 参考

> **中文正本。** 对应英文镜像：[reference.md](reference.md)。两个版本必须在同一变更中同步更新；同步校验记录见
> [translation_manifest.json](translation_manifest.json)。精确函数签名应以 Python 公共导出和测试为准。

## 用途

SvTypes 是以单一 Python 声明同时建模 hardware（SystemVerilog）和 software（C++）数据结构的 Domain-Specific
Language（DSL）。

主要目标：

1. **统一建模：** 在 Python 定义一次数据结构，生成对应的 SystemVerilog `class` / `package` 与 C++ `struct` /
   `namespace`。
2. **数据交换：** 以统一的 `pack` / `unpack` byte stream 在 SystemVerilog、C++、Python 间传递值。
3. **严格建模：** 通过 `.value` 区分 modeling object 与底层 data，避免普通 Python assignment 破坏硬件变量语义。

## 架构

### Package 与 Scope 对应

- Python package/module 对应 SystemVerilog `package` 和 C++ `namespace`。
- Python module name 的最后一段（例如 `a.b.pkg` 的 `pkg`）决定 SV/C++ 名称。
- 在 package 外执行的 Python script 对应 SV `$unit` scope 与 C++ global namespace。
- `svtypes.scope` 维护 global package registry；`collect_module()` 可自动发现 object。

### 类型系统：`TypeBase`

所有 modeled type 都继承 `TypeBase`，它提供：

- `Int`、`Bit`、`Real`、`String`、`Enum`、`Parameter` 等基础类型；
- 严格访问：禁止直接 assignment，必须写 `obj.status.value = 1`；
- codegen：每个 type 提供 SV/C++ 表示。

### 对象建模：`SvObject`

`SvObject` 是 complex structure 的核心：首次访问时，它把 class-level `TypeBase` template clone 到 instance，确保
`obj1.x` 与 `obj2.x` 独立。`ObjectDescriptor` 用于嵌套 `SvObject`；`Parameter` 和 `specialize()` 支持 SV-style
parameterized class。

### Parameter 不可变性

`Parameter` 一旦在 class/instance 赋值即冻结，后续 `.value` 修改报错。`ReadOnlyMetaclass` 阻止直接改写
`SvObject` class 或 `Package` 已建立的声明 attribute，从而保护 model integrity。

## 类型实现

### `TypeBase` 基础

`TypeBase` 规定每个 type 的 `.value`、`pack()` / `unpack()`、SV/C++ rendering 接口。它不实现 descriptor protocol，
避免 class 或 instance 层的 accidental direct assignment。

### 基础值类型

- `Int`：32-bit signed，映射 SV `int`、C++ `int32_t`，编码为 4-byte little-endian。
- `Bit[width_or_shape](...)`：任意宽度的 two-state packed value，映射 SV `bit`；例如 `Bit[2, 8]()` 生成
  `bit [1:0] [7:0]`。`BitSigned[...]` 或 `Bit[..., Signed]` 选择 signed storage。
- `Logic[width_or_shape](...)`：任意宽度 four-state packed value，映射 SV `logic`，以三个 byte plane 保留 0/1/X/Z；
  randomization 使用其 SystemVerilog two-state projection。`LogicSigned[...]`、`Logic[..., Signed]` 选择 signed storage。
- `Reg[...]()`：Python runtime type 与 byte representation 与 `Logic[...]` 相同，但 SV declaration spelling 为 `reg`；
  因而 `type(Reg[8]()) == type(Logic[8]())`，二者均满足 `isinstance(value, Reg)`。
- `Array[element_spec, size](...)`：真正的 fixed-array class。tuple shape 会递归展开：`Array[Bit[8], (3, 2)]()` 与
  `Array[Array[Bit[8], 2], 3]()` 等价；outer field option 只作用于 outer array。
- `Real`：64-bit float，映射 SV `real`、C++ `double`，编码为 8-byte IEEE 754。
- `String`：variable-length string，映射 SV `string`、C++ `std::string`。

单比特简写：`Bit()`、`Logic()`、`Reg()` 声明一个比特。无下标形式的位置参数是 **value**，与 `Bit[width](value)` 中该参数的
角色一致，因此 `Bit(0)`、`Bit(1)` 声明取值 0 或 1 的单比特，`Bit(rand=False)` 声明一个不参与随机的单比特。宽度只能来自
下标：单比特无法精确表示的整数会被拒绝而不是被截断（`Bit(8)` 报错并指向 `Bit[width]()`），因为它更可能是一个遗留的
位宽。signedness 由 `*Signed` 家族而非 `signed=` 关键字选择。括号写法保留其 packed-value 截断语义（`Bit[1](8)` 为 `0`）。

class body 中的每个 SvTypes 声明都必须物化。未调用的括号规格（`data = Bit[8]`）或裸类型类（`data = Bit`、
`child = Packet`）会在类创建期被拒绝并给出修复提示，而不是被静默地从 schema、packing、randomization 和生成的目标
代码中略过。普通 class-level metadata 不受影响：非 SvTypes 值始终允许，以前导下划线命名则把 SvTypes 值保留为
metadata（`_Payload = Bit[8]`）。

单比特简写拒绝遗留的 `width=` 关键字，请改用 `Bit[1]()`。`Bit()`、`Logic()` 和 `Reg()` 的位置参数及
`value=` 整数初值都必须是 0 或 1。

`type_spec_identity(type)` 返回 JSON 可序列化的边界类型身份；`type_spec_from_identity(identity, location=...)`
重建类型注解，包括多维 packed/array shape 和 `Reg` 声明样式。`specialize()` 返回的类通过源模板及参数绑定标识，
而非生成的类名。模板与其他引用的类一样，必须能够通过模块名及限定名称导入；不依赖生成类注册或进程内缓存。

### 枚举：`Enum`

Enum declaration 显式冻结 encoding width 与 signedness：

```python
class Status(Enum[Bit[8]]):
    IDLE = 0
    BUSY = 1
```

- `Enum[Bit[width]]` 支持任意 positive width；`Enum[Int]` / `Enum[LongInt]` 分别选择 SV `int` / `longint`；bare
  `Enum` 使用 SV default signed `int` base。
- member 必须落入 declared signed/unsigned range。
- duplicate numeric value 被拒绝，alias 不属于 stable contract。
- decode 遇到 unknown numeric value 抛出 `DecodeError`。
- SV 使用 explicit-sized `bit` enum base；C++ 使用 matching fixed-width integer base。

### 参数：`Parameter`

`Parameter` 是在 SV/C++ 中表现为 constant 的 specialization input：赋值后 immutable；分别生成 SV `parameter` 与
C++ `static constexpr`。

### Schema 定义的生成记录：`RecordSchema`

integration 不必生成或 import Python source，也可构造 typed record。`RecordSchema` 接收 explicit unified type name 与有序
`RecordField`，返回未注册 `SvObject` class；它仍具有正常 schema、encoding fingerprint、pack/unpack、SV/C++ generation 行为：

```python
from svtypes import Bit, RecordSchema

request_type = RecordSchema(
    "svx.generated.bus.drive.request",
    [("address", Bit[32]()), ("data", Bit[64]())],
    class_name="DriveRequest",
).build()
```

`RecordSchema(..., [])` 是 explicit void convention，返回 `None` 而不是 empty object envelope。调用者拥有 callable
naming 与 transport semantics；SvTypes 只验证并 materialize 给定 record name 与 ordered field。

### 生成 SV expression

`sv_type_expression(codec)`、`sv_packer_expression(codec)`、`sv_declaration(codec, name)` 是为已有 SvTypes codec
生成 typed adapter 的稳定 rendering entry point。前两项分别返回 type / packer expression；declaration 同时正确放置
unpacked dimension：

```python
from svtypes import Int, Queue, sv_declaration, sv_packer_expression

assert sv_declaration(Queue[Int](), "history") == "int history [$]"
assert sv_packer_expression(Queue[Int]()) == "svtypes_pkg::queue_packer#(int, svtypes_pkg::int_packer)"
```

这些函数不定义 transport、ownership、dispatch；只保留正常 SvTypes generated code 所用的 SystemVerilog spelling 与
codec pairing。

### 运行时能力协商

`runtime_capabilities()` 返回 package/schema/binary/object-envelope/generator-runtime ABI version，及 deterministic sorted
provided capability name。`require_runtime_compatible(required, received)` 在 transfer 前执行共同检查：当前所有 version field
必须相同；unknown provided capability 被忽略；缺任一 required capability 必须失败。Python、SystemVerilog、C++ runtime 都提供
同一规则。

调用/dispatch protocol 只应把真正需要的 capability 列为 required。record naming、function overload、receiver、exception、
construction lifecycle 不属于 `RecordSchema` 或该协商接口。

### 复杂对象：`SvObject` 与 `ObjectDescriptor`

`SvObject` 是其他 type 的 container：

- `SvObject.__getattribute__` 在 first access 时 clone class-level `TypeBase` attribute 到 instance `__dict__`，保证
  instance independence。
- `ObjectDescriptor` 处理 nested `SvObject` 的 instantiation/link。已分配的 non-null child 需要参与 parent constrained
  randomization 时，使用 `Object["Child"](rand=True)`；生成 `rand Child child`。默认 `rand=False`，不会 allocate 或 randomize
  handle。
- `SvObject.__setattr__` 阻止 `obj.x = 10`，强制使用 `obj.x.value = 10`。
- `@constraint` 声明 predicate。`@rand_layer(priority)` 把 rand member 与 constraint 分组；可列出 fixed unpacked-array
  element（如 `self.words[0]`）或整个 `DynArray` / `Queue`。dynamic container 逐 current element 处理：进入时 disabled 的
  element 保持 disabled，layered call 中创建的 element 保持 enabled。`layered_randomize()` 从 high 到 low priority 求解；未列出的
  member 归入 implicit `builtin`（priority 0）。Python 返回 `bool`；生成 SV 是 `virtual function int layered_randomize()`。
  用户不能 override `randomize()` / `randomize_with()` / `layered_randomize()`，但可 override `pre_randomize()` /
  `post_randomize()`。

### 外部字段存储

`SvObject.bind_external_storage(storage, field_keys)` 将一个 instance 的 selected field 绑定到 `ExternalFieldStorage`。
key 对 SvTypes opaque；mapping 以 `FieldIdentity(declaring_type, name)` 为 key，避免 inherited declaration 与同名 field 混淆。
该 instance 的 unbound field 和其他 instance 的全部 field 继续采用普通 local storage。

backend 获得 `FieldDescriptor`、immutable typed `FieldPath`、`FieldOperation`、已 normalize 的 SvTypes byte；它不会获得 facade
value，也不需复刻 SvTypes encoding。read 没有 implicit writable cache；indexed collection write 与 associative-key write 都是 leaf
operation。`MemoryExternalFieldStorage` 是 test / simple embedder 的 standalone reference backend：

```python
storage.seed("packet.count", Packet.__dict__["count"], 3)
packet.bind_external_storage(
    storage,
    {FieldIdentity(Packet, "count"): "packet.count"},
)
assert packet.count.value == 3
packet.count.value = 0x103  # Bit 宽度归一化仍然生效
```

`bind_external_value(descriptor, storage, key)` 为 temporary value root 提供同样语义。`close()` 使 root 及全部 derived
collection view 失效。external randomization 在 detached value snapshot 上求解，只有 success 后才发布每个 bound root；
unsuccessful solve 不改变 backend。

## 类型映射摘要

| 类型 | Python 基础值 | SV 映射 | C++ 映射 | Bit 宽度 |
|---|---|---|---|---|
| `Int` | `int` | `int` | `int32_t` | 32 |
| `Bit[w]()` | `int` | `bit [w-1:0]` | `uintN_t` | `w` |
| `Logic[w]()` | `LogicValue` | `logic [w-1:0]` | `LogicValue<w>` | `w` |
| `Reg[w]()` | `LogicValue` | `reg [w-1:0]` | `LogicValue<w>` | `w` |
| `Real` | `float` | `real` | `double` | 64 |
| `String` | `str` | `string` | `std::string` | 可变 |
| `Enum[Bit[w]]` | `IntEnum` member | explicit-base `enum` | fixed-width/arbitrary-width wrapper | `w` |
| `Parameter` | `TypeBase` | `parameter` | `static constexpr` | 不适用 |

## 使用示例

```python
from svtypes import SvObject, Int, Parameter, svobj

@svobj
class Header(SvObject):
    VERSION = Parameter[Int](1)
    id = Int()
    length = Int()

header = Header()
header.id.value = 100
header.length.value = 20
```

## 功能覆盖率

`@covergroup` 在 `SvObject` 上声明 static functional-coverage template。使用嵌套 `CovPoint` / `Cross` class 声明
coverpoint、cross 与 `bins` / `ignore_bins` / `illegal_bins` / `transition_bins`：

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

`CoverInput[T]` 与 `CoverRef[T]` 提供 typed covergroup constructor formal。embedded covergroup 必须从 class 的单一
`@coverage_init` method instantiate；tool 直接依据 CoverInput signature materialize declaration，而不读取 initializer actual：

```python
layout = preview_coverage_layout(Packet, "cg", first=2, last=5)
```

`CoverRef` 保持 declaration-bound，永远不是 tool input。class `Parameter` reference 在 CoverageIR/Design Manifest selector 中
保持 symbolic `parameter_ref`；Python instance layout 将绑定值 materialize 为 constant，而 generated SystemVerilog 保留
parameter name。

automatic `cov=True` coverage 进入与 explicit covergroup 相同的 CoverageIR pipeline。scalar/container value domain 会得到
deterministic automatic bin；`Object[...]` handle field 不会得到 default nullness coverpoint。

`instance.get_coverage()` 返回 type coverage；`instance.get_inst_coverage()` 返回 individual covergroup instance。
`instance.sample_count` 记录 accepted sample；`instance.has_illegal_hits()` 报告是否命中 illegal bin。

仅能使用显式 frozen declaration binding 调用 `CoverageDatabase.import_ucis()`。UCIS 是具有 documented loss report 的 interchange
projection，不是 SvTypes coverage database 的替代。

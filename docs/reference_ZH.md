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

`AssocArray` 的只读属性 `key_codec` 和 `value_codec` 返回声明的 key/value codec，
空数组同样可以查询。它们是 codec descriptor，不是条目值或独立副本；使用公共
schema 和生成接口查询，不应修改其布局。

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

嵌套容器 adapter 使用 `sv_codegen_context(codec, prefix=...)`：上下文返回
按依赖顺序排列的局部 `typedef` 声明。将这些声明放在同一作用域，再使用
上下文内渲染的 type/packer expression。等价 codec 实例共享别名；退出或异常
时恢复原渲染状态。`prefix` 必须是 SV 标识符；别名不改变数据编码。

`sv_type_expression(codec)`、`sv_packer_expression(codec)`、`sv_declaration(codec, name)` 是为已有 SvTypes codec
生成 typed adapter 的稳定 rendering entry point。前两项分别返回 type / packer expression；declaration 同时正确放置
unpacked dimension：

`sv_declaration(codec, name, include_initializer=True)` 还会包含 `Bit`、`Int`、
`LongInt`、`Logic`、`String`、`Real`、`ShortReal`、`Enum`、`SvStruct` 或固定 `Array` 元素
显式声明的初值；默认仍只输出声明。
`codec.sv_initializer()` 返回原始声明初值（或 `None`），而 `codec.sv_repr()`
返回当前值。运行时赋值不会改变声明初值；Logic 的初值保留 X/Z 和有符号属性。
String 字面量转义引号、反斜杠、控制字符及 UTF-8 字节。packed struct 的
typedef 只包含成员类型；字段初值通过嵌套赋值模式保留成员声明默认值，
包括未指定的四态成员。没有显式成员初值的结构体仍只输出声明。
固定数组通过 SV 的 `default` 赋值模式重复元素模板的声明初值，多维数组
逐层处理。之后对某个元素的赋值不属于声明默认值。动态数组、queue 和
关联数组仍保留原生的空容器默认值。

```python
from svtypes import Int, Queue, sv_declaration, sv_packer_expression

assert sv_declaration(Queue[Int](), "history") == "int history [$]"
assert sv_packer_expression(Queue[Int]()) == "svtypes_pkg::queue_packer#(int, svtypes_pkg::int_packer)"
```

这些函数不定义 transport、ownership、dispatch；只保留正常 SvTypes generated code 所用的 SystemVerilog spelling 与
codec pairing。

生成对象类时，嵌套固定数组、队列、动态数组及关联数组的值类型会先在类内
生成 typedef，再作为 packer 类型实参引用。定义顺序确定，同类内相同类型
去重，字段维度及二进制编码不变。这些名称只是内部生成细节，用户无需另行
声明这些类型。

### 解码模板与对象身份

只需要对象类作为 pack/unpack codec 时，使用
`MyObject.codec_template(session=session)`：其对象编号为零，不注册活对象，
也不消耗编号。普通 `MyObject(session=session)` 仍创建有身份的对象。
创建解码模板及解码值时跳过用户 `__new__` / `__init__`，包括必须传参的构造器。
只初始化 SvTypes codec 状态和声明字段默认值；普通用户实例化行为不变。
`checked_unpack(codec, data, descriptor, session.unpack_context())` 先检查编码
契约，再在指定会话中解码。共享引用和循环引用保留身份，重复解码复用已注册
对象。导入同 origin 的编号会推进本地分配器；不兼容或显式重复的活对象身份
仍报错。

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

`MyObject.refresh_declarations()` 用于框架在创建任何实例之前完成类的基类组装后，
重新校验继承字段与约束。它保留已有 `__init__`，不执行用户类钩子。
应先刷新已组装的基类，再刷新子类。它不是已有对象的迁移接口，不会更新
现存实例的值或外部存储绑定。

`SvObject.bind_external_storage(storage, field_keys)` 将一个 instance 的 selected field 绑定到 `ExternalFieldStorage`。
key 对 SvTypes opaque；mapping 以 `FieldIdentity(declaring_type, name)` 为 key，避免 inherited declaration 与同名 field 混淆。
该 instance 的 unbound field 和其他 instance 的全部 field 继续采用普通 local storage。

backend 获得 `FieldDescriptor`、immutable typed `FieldPath`、`FieldOperation`、已 normalize 的 SvTypes byte；它不会获得 facade
value，也不需复刻 SvTypes encoding。read 没有 implicit writable cache；indexed collection write 与 associative-key write 都是 leaf
operation。`MemoryExternalFieldStorage` 是 test / simple embedder 的 standalone reference backend：

按索引或键取得内层列表、映射时，保留同一外部 owner 并扩展路径。
遍历序列取得的嵌套容器也保留绑定：修改取出的行会形成寻址写入，
而不是只改变一个脱离后端的 Python 副本。遍历先读取一次父容器快照；
已绑定子容器的后续访问仍读取后端当前值。
序列切片返回普通的外层列表；其中的嵌套子容器保留原后端索引（包括反向切片）
及关闭状态。深复制递归提取值快照，不复制后端或不透明 key，所得值不再绑定外部存储。

`APPEND` 路径指向容器，编码该容器的一个元素。`INSERT` 路径指向新增元素
所在索引，直接使用该位置的元素 codec；元素本身是容器时也不会再降一级类型。

序列切片赋值使用逐元素 `SET`，可变长度序列还使用 `INSERT`/`DELETE`，
不会回写整个容器。切片删除按索引降序执行，避免剩余元素的位置偏移。
扩展切片遵循 Python 的长度规则，固定数组不能改变长度；所有写入之前
先验证结果的类型与形状。若后端在写入过程中失败，这些复合操作不保证事务回滚。

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
结果发布支持空、共享和循环 handle，包括容器内元素，不会用试算副本替换
已经分配的原始存活对象。
外部随机化成功后还会提交 owner 的 `randc` 周期历史。禁用字段保留暂停的
周期；失败求解不会消耗该周期中的值。
`pre_randomize()` 在创建试算快照前于原始存活实例上执行；成功发布求解结果后，
`post_randomize()` 在同一实例上执行，求解失败时不调用它。嵌套随机对象的回调
也保留原实例，分层回调可观察当前 active 标志与 priority。用户在回调中的显式
赋值属于普通副作用而非试算写入，不会因为后续求解失败而回滚。
值副本直接初始化 SvTypes 状态，不会重跑用户构造函数。随机化试算快照
不分配已注册的对象身份，也不创建外部语言的配套实例；普通对象副本仍具有
独立的 codec 身份。

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
固定数组按每个叶元素槽位生成覆盖点，多维固定数组的覆盖点名称保留完整
索引，例如 `matrix[0][1]`。生成 SV 的槽位边界使用 `$size(...)`，同时支持
固定数组和动态数组。
固定槽位或显式 `cov_slots` 槽位若包含动态或关联子容器，该槽位的覆盖点
采样所选子容器的叶值域；不存在的外层槽位与空子容器不产生样本。
动态或关联容器的 value-domain coverage 会逐层遍历嵌套容器并采样叶值，
而不是关联数组的 key 或内层容器本身。空子容器不产生样本。Python 与生成
SV 使用相同的遍历与叶值 bin；宽整数 bin 边界在生成 SV 中保持精确位宽和符号。

`instance.get_coverage()` 返回 type coverage；`instance.get_inst_coverage()` 返回 individual covergroup instance。
`instance.sample_count` 记录 accepted sample；`instance.has_illegal_hits()` 报告是否命中 illegal bin。

仅能使用显式 frozen declaration binding 调用 `CoverageDatabase.import_ucis()`。UCIS 是具有 documented loss report 的 interchange
projection，不是 SvTypes coverage database 的替代。

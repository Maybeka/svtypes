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

## Python 编码会话的所有权

`CodecSession(..., retain_objects=True)` 保持原有默认行为：登记表强持有对象，
直到 `remove()`、`clear()` 或 `close()`。使用 `CodecSession(retain_objects=False)`
可选择非拥有型会话；只读属性 `retain_objects` 返回所选策略，参数必须是布尔值。

非拥有型会话以弱引用登记对象。Python 变量、对象图成员和应用或框架的拥有者仍然
保持对象存活，单纯登记不会延长寿命。存活对象的对象编号不变，重复解码仍恢复同一
实例。不可达的循环引用由普通 Python 垃圾回收处理，不承诺具体回收时刻；回收后
登记项消失，但回收不会使分配器回退，也不会自动重新发放曾经分配或导入的编号。
显式重置分配器仍是调用者控制的操作，不属于回收行为。

`PackContext`、`UnpackContext` 保持操作期间的图对象；共享边、循环引用、冲突检查、
回滚和线格式不变。对象回收后可再次解码完整对象图，但只含已回收编号的引用记录会
报告无法解析引用。对象编号或编码字节本身不是拥有引用。外部存储或应用协议若只保留
编号，必须提供真实拥有者或使用拥有型会话。两种策略都支持显式移除、清空和关闭。
显式 `PackContext` 可跨多次 pack 调用，因而在其存活期间强持有所见对象，释放上下文后才
释放这部分引用，避免 Python `id()` 复用导致错误引用编码。解码上下文在顶层成功或失败后
清除临时拥有的对象；保留空上下文本身不会延长已解码对象寿命。

此策略只影响 Python 登记表，不改变可移植 SV 运行时的强句柄，不选择试验性的 SV
弱引用后端，也不释放外部 `RemoteRef` 目标。独立会话的对象编码若进入同一接收
命名空间，仍需使用不同的 origin。

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

#### 结构体种类与迁移

`SvStruct` 声明 unpacked 值结构体；packed integral 结构体使用
`SvPackedStruct`，它也是 `SvStruct` 的 Python 子类型。两者均非空，没有对象
envelope 或对象编号。unpacked 成员可以包含已支持的容器、字符串和 class handle。
通过 `.value` 赋值会复制聚合存储，并保留 handle 指向的对象引用。packed 仅接受
packed integral 成员，首成员位于最高位；约束和 `unique()` 将其整体视为一个
unsigned integral 操作数，不暴露 packed 成员的独立模式控制。

需要保留历史 packed 行为的定义应将基类改为 `SvPackedStruct`。结构体类型身份
和源 schema 现在包含显式 `packed` 布尔值。逐字段字节编码不变：不会将原来独立
编码的字段合并，也不增加宿主 padding。unpacked typedef 保留成员声明默认值；
packed typedef 则通过所在字段的赋值模式初始化。完整集成、迁移规则及 capability
验收状态见[结构体契约](SVTYPES_STRUCT_CONTRACT.md)。

嵌套容器 adapter 使用 `sv_codegen_context(codec, prefix=...)`：上下文返回
按依赖顺序排列的局部 `typedef` 声明。将这些声明放在同一作用域，再使用
上下文内渲染的 type/packer expression。等价 codec 实例共享别名；退出或异常
时恢复原渲染状态。`prefix` 必须是 SV 标识符；别名不改变数据编码。

`sv_type_expression(codec)`、`sv_packer_expression(codec)`、`sv_declaration(codec, name)` 是为已有 SvTypes codec
生成 typed adapter 的稳定 rendering entry point。前两项分别返回 type / packer expression；declaration 同时正确放置
unpacked dimension：

`sv_declaration(codec, name, include_initializer=True)` 还会包含 `Bit`、`Int`、
`LongInt`、`Logic`、`String`、`Real`、`ShortReal`、`Enum`、`SvPackedStruct` 或固定 `Array` 元素
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
- `ObjectDescriptor` 声明固定的可空 handle 槽。访问 `owner.child` 得到槽，
  使用 `owner.child.value` 读写所指对象。初值为 `None`，需要对象时显式构造。
  容器元素也通过 `queue[0].value` 或 `table[key].value` 取值。外部下标定位当前
  路径，跨结构变更保留已选元素时使用 `borrow_element`。
  `Object["Child"](rand=True)` 生成 `rand Child child`，并对已赋值的子对象执行递归随机化。
- `SvObject.__setattr__` 阻止 `obj.x = 10`，强制使用 `obj.x.value = 10`。
- `@constraint` 声明 predicate。`@rand_layer(priority)` 把 rand member 与 constraint 分组；可列出 fixed unpacked-array
  element（如 `self.words[0]`）或整个 `DynArray` / `Queue`。dynamic container 逐 current element 处理：进入时 disabled 的
  element 保持 disabled，layered call 中创建的 element 保持 enabled。`layered_randomize()` 从 high 到 low priority 求解；未列出的
  member 归入 implicit `builtin`（priority 0）。Python 返回 `bool`；生成 SV 是 `virtual function int layered_randomize()`。
  用户不能 override `randomize()` / `randomize_with()` / `layered_randomize()`，但可 override `pre_randomize()` /
  `post_randomize()`。

#### 非打包结构体的值

普通 Python 和受检入口中的非打包结构体通过 `.value` 读取独立映射，键为声明字段名。
嵌套容器和结构体递归转为普通数据，对象 handle 保留身份。将完整映射赋给 `.value`
时，按结构体声明校验并写入。访问名为 `value` 的字段时，将旧写法
`record.value.value` 改为 `field(record, "value").value`。约束和覆盖率声明按类型
声明解析字段名，其中的 `self.record.value` 指向名为 `value` 的字段。

#### Python 受检变量访问

使用 `run_checked(main)` 执行操作受检变量槽的项目函数。普通 SvTypes 类型标注
等价于 `Input[T]`。`Input` 拷入值，`Output` 初始未赋值，`Inout` 拷入并写回。
`Output` 和 `Inout` 只在正常返回时写回，先准备全部写入，再按声明顺序发布。
`Ref[T]` 在调用期间访问原槽，要求实际参数的声明类型一致。读取尚未赋值的
`Output` 会报错。`CopyoutError` 提供失败参数 `parameter`、阶段 `phase`
（`"preparation"` 或 `"publication"`）及已完成的参数 `completed`。
发布失败保留之前的写入，并保留底层异常作为原因。
实参表达式遵守 Python 的求值顺序。所有实参求值完毕后，在调用入口读取并拷入
Input 载体的值；已经通过 `.value` 或其他表达式取得的值不重新求值。
索引形式的 Input 只需要普通字段读取能力，不需要稳定元素借用；聚合值独立复制，
其中的对象句柄仍保持原对象身份。
受检操作的异常附注包含源码位置及表达式，包括触发写回的显式 return。
可通过异常的 cause 查看底层错误。

结构体和定长数组的 `Output` 可以通过声明的字段槽或元素槽逐项初始化。
已赋值的槽可以单独读取。读取整体值或正常返回时，需要所有槽均已初始化，
否则会指出第一个未赋值的路径。动态容器先通过整体赋值确定形状，再编辑元素。

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

向普通参数传递 `.value`。受检代码拒绝保存槽的别名、将槽传给无载体契约的参数，
或直接返回槽。`*args` 和 `**kwargs` 使用独立值，传递槽时直接传给有方向
契约的参数。仅导入 SvTypes 不改变普通 Python 的导入、加载器或字节码缓存。
需要检查项目导入时使用 `with checked_project(root):`；`run_checked()` 执行期间自动
为入口所属项目启用该作用域。范围内的模块加载器检查命名导入、通配导入、类体声明
和函数默认值中的载体别名。作用域支持嵌套，退出恢复；已在 `sys.modules` 中的模块
不重新加载。将载体操作放在受检入口或它调用的函数中。
需要记录源码输入的工具可以使用上下文管理器 `observe_module_sources(observer)`。
受检模块加载器在解析或变换前，针对每次读取的源码调用
`observer(filename, source_bytes)`；字节保留原始编码和换行。回调可以抛出异常
拒绝加载，但不能替换源码或关闭检查。观察者只作用于当前执行上下文，支持嵌套，
离开作用域（包括异常退出）后移除。已缓存模块及其他加载器处理的模块不产生
观察记录，由工具自行负责这些输入。受检查找器将范围外源码交给后续导入查找器。
框架集成可以使用 `with checked_library_sources(*sources):` 划定实现边界，
每项是已存在的精确源码文件或包目录。作用域内的框架实现正常执行，不做用户源码
变换；嵌套作用域在退出时恢复，包括异常退出。范围外的用户源码仍受检查，包括
通过 SvTypes 构造网关调用的用户构造器。普通的带类型契约框架函数调用也遵守此边界：
函数体不改写，但调用前后的参数方向、形参生命周期、copyout 和返回值逃逸检查保留。
无类型契约的参数仍不能接收载体。框架自行回调用户函数时，必须经受检入口
交接。该边界是显式的框架集成契约，不是安全隔离机制，也不是业务代码绕过变量
使用约定的方式。
已经自行执行受检参数契约的框架转接函数可以使用 `@checked_call_gateway`。
该标记仅在其源码位于 `checked_library_sources` 作用域内时生效，保留原函数
及签名，将原始实参直接交给转接函数，并拒绝载体返回值。转接函数必须通过
`run_checked` 应用真实参数方向，并经受检入口调用用户回调，从而避免重复绑定
参数及发布输出。此能力不允许作用域外无类型契约的业务辅助函数接收载体。
受检入口采用黑名单制度：默认允许 Python 行为，只拒绝明确的变量使用违规，
包括载体别名、保存、捕获或逃逸，缺失或非法的载体参数契约，替换或删除声明槽，
以及违反生命周期、类型和读写权限的访问。普通类型注解不是运行时约束；没有可检查
源码或遇到未识别的语法形式，本身不构成违规。
普通模块导入也遵守此原则：`globals()`、`locals()`、`vars()`、`eval()`、`exec()`、
`super()` 及其别名在原调用帧中执行，不因导入 SvTypes 而改用内部观察器的作用域。
可读取的项目源码通过变换观察这些操作；不透明代码和项目外代码正常执行，调用边界
仍检查参数和返回值，不承诺检查不透明函数体内部。类注解、dataclass 和依赖调用者
作用域的内置函数保留 Python 行为。载体转发使用显式参数契约；不得将 Ref 保存在闭包或
延迟生成器中。生成器可以拥有自己新声明的变量并产出其值；检查作用域只在恢复
执行时生效，包括 `send`、`throw` 和 `close`，不会跨暂停持有。普通异步定义和
仅使用值的异步工作正常执行；延迟执行不得将 Ref 或其他载体保留到同步形参作用域之外。
这是使用约定，不是防止刻意绕过的沙箱。高阶回调支持
`sorted`、`list.sort`、`min`、`max`、
`map`、`filter`、`functools.reduce`，以及 `itertools.accumulate`、
`dropwhile`、`filterfalse`、`groupby`、`starmap` 和 `takewhile`。
即使在受检入口返回后才消费迭代器，延迟回调也会接受检查。

通过 SvTypes 的编解码入口调用自定义结构体 `pack` 和 `unpack` 时，
原签名和 `super()` 转发在受检入口内仍可使用。解包回调可以将新解码的
结构体和已消费字节数交回 SvTypes，回调体仍遵守受检载体规则。

在受检入口中用 `copy.deepcopy(owner)` 复制对象图。复制回调共享原生递归
使用的 `memo`，可以将它传给基类实现，也可以用它登记自己新建的副本。
`memo` 的访问限于复制回调，由受检 `deepcopy()` 调用在内部创建。复制变量
载体里的值时，使用声明的值接口。
`read_value(slot)` 取得独立的复合值并保留 handle 身份；
`field(owner, "value")` 选取名为 `value` 的声明字段；
`write_value(slot, value)` 显式赋值。

`queue.value.append(3)` 和 `table.value.update(entries)` 等直接操作修改原存储。
`rows.value[0].append(3)` 等嵌套调用也修改声明的子存储；外部子槽按需使用作用域借用，
后端缺少所需能力时明确报错，不会转而修改快照。
本地动态序列的 `reverse()` 和 `sort()` 随元素移动原槽。本地定长数组保留原槽，
将重排后的值写入各槽。外部绑定的序列拒绝这两种操作。后端若提供能保留借用元素
身份的重排操作，可通过后端执行。

在受检入口之外，`with borrow_element(container, index_or_key) as slot:` 获取
`ElementBorrow`。即使元素已经离开容器，`slot.value` 仍读写原元素。
`forward()` 为同一元素创建独立借用，`child(FieldPath(...))` 为声明的子槽
创建借用。关闭借用会结束这份访问权限。在受检代码中，将选中的元素直接传给
`Ref[T]` 形参，即可在调用期间借用该元素。

外部存储通过 `isinstance(backend, ExternalBorrowStorage)` 检查是否支持稳定借用。
后端在 `acquire_borrow()` 时解析一次字段路径。`read_borrow()` 和
`write_borrow()` 使用现有的 SvTypes 字节编码，最后以 `release_borrow()` 释放
令牌。已借用聚合值的稳定子槽需要 `ExternalBorrowPathStorage`，它提供
`acquire_borrow_child()` 并返回独立释放的令牌。只提供 `ExternalFieldStorage`
的后端仍支持普通路径访问，申请稳定借用时会报错。借用遵守原绑定的关闭状态和写权限。

可选的 `ExternalBorrowFieldStorage` 为已借用聚合值提供即时相对路径操作。
`read_borrow_path(token, path)` 读取相对字段；
`write_borrow_path(token, path, operation, payload)` 转交普通的 `FieldOperation`
和 SvTypes 字节，包含 append/insert/delete/pop/resize。相对路径在每次操作时求值，
不承诺子元素的稳定身份：将子变量继续传给 Ref 仍需 `ExternalBorrowPathStorage`
和独立申请的令牌。后端可以实现其中一个或两个能力。没有相对字段操作能力时，
保留既有的子令牌读取/赋值路径，结构操作仍明确拒绝，不退化成整个聚合值复制写回。
这些操作也先检查借用关闭状态和只读权限。

框架可以通过 `external_storage_location(variable)` 查询已有槽的位置。
本地变量与独立的 Input/Output/Inout 形参返回 `None`。外部槽返回不可变的
`ExternalStorageLocation`：`kind == "field"` 提供 `storage`、根 `descriptor`、
`key` 和精确 `path`；`kind == "borrow"` 提供 `storage`、叶子 `descriptor`
与既有不透明 `token`，不提供下标或键路径。两者均提供 `readonly` 和 `session`。
转发的 Ref 形参保留原端点。查询本身不读值、不申请 lease，也不延长形参、绑定或
借用的生命周期；任一来源作用域关闭后，所有属性与 `require_open()` 均拒绝访问。
查询前取得成员仍可能依照普通访问规则读取父值。这是框架集成接口，不允许受检
业务代码借此保存或返回变量引用，也不实现存储重定位或后端稳定借用能力。


#### 本地变量槽的作用域存储

框架直接从 `svtypes` 导入 `SlotIdentity`、`slot_identity()` 和
`relocated_slot()`，无需实验模块或修改私有绑定属性。这些是框架集成接口，
不放宽受检业务代码保存、捕获或返回载体及存储元数据的禁令。

`slot_identity(variable)` 只查询**本地**槽身份，不读值、不取得租约。使用
`==` 比较，不使用 Python 的 `is`：原变量及有效 Ref 转发身份相等、哈希一致；
值相等的独立变量以及独立 Input/Output/Inout 形参身份不同。未赋值 Output
也有身份，但不能转绑。它不是可序列化 ID。`is_valid` 查询有效期，
`require_open()` 显式检查；比较和哈希也检查有效期。每份身份受其来源形参、
借用或临时视图的作用域约束；一份租约关闭，不影响通过另一份有效租约取得的身份。
身份只持有弱引用，不延长来源寿命；普通本地变量以载体的存活期为有效期。
框架应在来源关闭前从映射中移除身份。复制身份仍表示原身份。

`relocated_slot(variable, create_storage)` 是同步上下文管理器：

```python
from svtypes import Int, MemoryExternalFieldStorage, relocated_slot

value = Int(3)
backend = MemoryExternalFieldStorage()

def create_storage(initial, identity):
    backend.seed("counter", Int(), initial)
    return backend, "counter", lambda: None  # 在此释放后端资源

with relocated_slot(value, create_storage) as temporary:
    value.value = 8
    assert temporary.value == 8
assert value.value == 8
```

`StorageFactory` 接收独立初值和有作用域的身份，返回 `(backend, key, release)`。
工厂成功返回有效三元组前，自行负责部分分配的清理。后端沿用
`ExternalFieldStorage` 的类型、路径和字节编码，codec 可通过工厂闭包传入。
原载体及既有、后续形成的 Ref 共用同一存储。同槽嵌套复用首次分配，不调用内层工厂；
最后退出时恢复最新值、关闭临时视图并释放资源。函数体异常和取消引发的同步栈退出
均保留已经完成的写入。这不是异步 Ref 接口，也不提供跨线程存储同步。

支持本地标量、枚举、packed 值、RemoteRef 值记录、结构体、定长数组、动态数组、
队列和关联数组，包括选中的本地值元素和已赋值的独立值形参。值内的句柄保留所指
对象和编码会话；混合会话在分配前拒绝。裸对象实体及 Object 句柄槽不属于可转绑
的值根。聚合根仍有活动子槽借用/Ref 或独立外部绑定时，在分配前拒绝整体转绑；
最后一份子槽租约关闭后允许转绑，已脱离容器的旧元素不阻塞原容器。已转绑聚合根
的子槽不能再次独立转绑。已有外部字段和借用继续使用 `external_storage_location()`
及原端点，本地身份接口不为其猜测等价关系。只读、关闭和类型检查继续生效。

`StorageRelocationError` 提供 `phase`、`restored`、`body_error` 和全部
`failures`（各项为 `StorageRelocationFailure.phase`/`.cause`）。阶段包括
`prepare`（准备）、`readback`（读回）、`restore`（恢复本地值）、`close`（关闭视图）
和 `release`（释放资源）。清理出错后仍继续其余清理；有效分配返回后，释放尝试一次。
清理成功时原样重新抛出函数体异常；清理也失败时，函数体异常保留在 `body_error`
及异常链中。`restored=False` 表示未能恢复最新外部值，本地槽可能是旧值或部分
恢复结果，不能当作成功结果使用。不承诺回滚或跨后端事务。

#### 可选受检调用存储策略

- `current_borrow_purpose()` 对显式取得借用返回 `BorrowPurpose.EXPLICIT`，
  对受检临时操作返回 `OPERATION`，对真正 Ref 实参返回 `REF`。执行上下文局部状态
  在成功和异常后均恢复。用途只提供信息，不授予权限，也不替代稳定身份。
- `ExternalRefValidationStorage` 增加 `validate_ref_borrow(token)`。每次真正
  Ref 绑定都在函数体前校验，包括已有租约和继续转发；校验抛异常则不进入函数体，
  临时取得的租约正常释放。未实现此方法的旧后端保持现有行为。
- `ExternalValueParameterStorage` 增加 `value_arguments_by_path: bool`。只有显式
  `True` 才选择 Output/Inout 捕获地址写回：索引或键求值一次，形参仍然独立，
  正常返回时先准备全部输出，再按声明顺序发布。结构变化可能改变该地址的元素，
  不保证旧元素身份。异常或取消不执行普通 copyout。聚合借用和转绑适配器保留此
  策略。未启用时仍采用选中槽语义；真正 Ref 仍需要稳定借用和校验，不能用此标志
  获得 Ref 能力。

可用 `isinstance(backend, 协议名)` 检查上述两个可选协议，再检查策略值。
普通 Input 无需新标注：实参按 Python 顺序求值，调用入口再读取并拷入载体值；
已求值的数据不重新求值。原实验私有标志和上下文变量由这些公开接口替代；旧实验
模块仅保留两个存储函数的导入转接。


### 外部字段存储

外部容器的 `.value` 索引、切片、遍历及 `pop()` 与直接槽读取使用同一绑定会话
解析句柄，返回原对象，不复制对象或重放字节中保存的旧对象字段。独立绑定通过
`bind_external_value(..., session=session)` 选择会话；未指定时使用默认会话。
结构体视图保留其存储绑定和读写权限，其中的句柄仍指向原对象。
对象整体值复制从源对象的实际存储读取句柄，保留深复制的共享、空值和循环关系；
新复制的对象归属目标会话。

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

`bind_external_value(descriptor, storage, key)` 为 temporary value root 提供同样语义。
可选 `diagnostics="strict"` 检查 wire 指纹和预期字段数；默认 `"off"`，包括句柄读取。
子视图、元素借用和 pop 返回值继承该策略。句柄身份/会话解析、类型选择、边界及
完整消费 payload 的检查仍属于必要操作。`close()` 使 root 及全部 derived
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

`CoverageIR` 及其嵌套 selector、表达式、参数和选项深度不可变：映射只读、序列为 tuple。
使用 `stable_dict()` / `definition_snapshot()` 取得独立的 JSON 数据；修改快照不会修改声明或实例。

`CoverageDatabase.record(instance, logical_instance_key=...)` 刷新快照，不重复累加。
`merge(other)` 默认严格检查同类型的声明，即使实例键不同也不能容纳冲突定义；同键还检查实例布局。
匿名记录始终独立，包括经过文件保存/加载后的跨 run 合并。

```python
report = master.merge(incoming, mode="master", reject_conflicts=False)
conflicts = report["conflicts"]
master.write("merged.svtcov")
```

显式主库投影保留 master 的定义、层次与 bins，只累加语义一致的项。point 变化也阻止其依赖 cross
的累加；覆盖组级语义、同键 input 布局、未知类型/命名实例冲突均报告，不猜测匹配。
`reject_conflicts=True` 遇到冲突整次失败；严格合并、投影和 UCIS 导入均先准备后原子发布。
`merge_reports` 返回独立副本，报告随库保存。当前二进制格式 v2 写出并读取 v1/v2；参数化 v1
record 若无具体定义，不参与需要该定义的投影。`apply_baseline()` 恢复计数与有限来源（含 cross
局部 illegal），不恢复 transition 历史或旧对象。

`CoverageDatabase.import_ucis(xml, bindings={key: frozen_instance})` 要求显式冻结实例绑定，检查
子集内 bin 种类/范围、cross 成员顺序/索引及计分选项；参数化数组使用实际实例 bins。缺失受支持
bin 或不兼容定义会拒绝整次导入。source/iff 对应关系由绑定方保证，不反解析任意 SV 表达式。
子集外缺失计数明确记入 loss report 和持久化 `import_provenance`，不能当成已证实的零命中。
已存在同键时先导入独立库再 merge。UCIS 是互操作投影，不替代 SvTypes 原生数据库。

## 可选解码诊断与手改生成代码

普通对象解码默认使用 `UnpackContext(diagnostics="off")`，不比较预期字段数或 encoding fingerprint，
也不构造预期摘要；格式版本、输入边界、类型身份与引用解析仍检查。显式严格解码可使用
`codec.unpack(data, UnpackContext(diagnostics="strict"))`，或
`target.from_bytes(data, context=UnpackContext(diagnostics="strict"))`。
`checked_unpack()` 始终是严格入口，并向嵌套对象传播严格策略，完成或异常后恢复传入上下文。
独立上下文可以并发使用不同策略，不支持并发复用同一个可变上下文。

C++ 使用 `DecodeContext` 的 `DiagnosticPolicy::off/strict` 和 `DecodeContextGuard`，
在 RAII 作用域内向嵌套 unpack 传播，线程间隔离。SV 使用 `decode_context` 的
`DIAGNOSTICS_OFF/DIAGNOSTICS_STRICT`，通过 `begin_decode_context(context)` 保存前一上下文，
再以 `end_decode_context(previous)` 恢复；策略按仿真进程隔离，显式作用域需成对恢复。

默认不要求生成文件摘要与源码一致才能运行。生成时 `generate()` 会保护被手改的受管文件和
同名非受管文件：冲突路径返回在 `failed` 中，不发布任何新文件。
`overwrite_modified=True` 显式允许覆盖；`mode="check"` 始终只读报告差异。
实施与验证详情见
[实施记录](SVTYPES_GENERATED_CODE_EDITABILITY_IMPLEMENTATION.md)。

SV 生成默认使用显式辅助类型，并通过 `$bits` 和 `$size` 从实际字段声明派生支持的 bit/logic
位宽和数组尺寸；手改这些字段的位宽或固定长度不需要 `type(expression)`。编码类别和有符号
属性仍属于生成的字段契约，修改字段类别时需要同步调整相关设置。对于目标无法将成员形状作为
类型常量的参数化类，保留显式类型，形状检查只在显式严格诊断上下文中执行。
手改这些字段仍需同步修改声明及其显式 codec 类型；关闭诊断不会让不兼容的 SV ref
连接变成合法连接，也不会自动适配通信布局。
通过 `to_sv_obj(use_type_reference=True)`、`to_sv_pkg(use_type_reference=True)` 或
`generate(..., use_type_reference=True)` 可选择让辅助代码使用 SystemVerilog `type(expression)`。

`generate()` 默认 `targets={"sv", "cpp"}`，需要完整源 schema 时加入 `"schema"`，
需要覆盖率观察清单时加入 `"coverage"`；两个元数据目标也可独立请求。仅代码生成
不遍历源 schema 或计算其摘要，仍保留用于发布和手改保护的小型产物归属清单。
多文件输出将分层随机等框架实现收进 `support/`；单文本接口仍自包含，并划分辅助区。

类声明和普通随机/采样不准备编码指纹。首次编码、生成代码或显式查询编码描述时，
只计算 wire 指纹一次，不构建源 schema。后续复用缓存；注册身份变化或声明刷新只使
受影响缓存失效，下次请求时才重新准备。现有对象头仍携带指纹，写入协议字段与
解码时比较指纹是两件事。普通随机求解只在涉及 randc 状态时计算约束循环签名。

`generate()` 可将未绑定参数的类输出为目标语言模板。此时尚无具体的源 schema，其名称记录在
schema 文档和生成清单的 `unbound_templates` 中，不加入 `types` 或具体指纹列表。需要具体
`schema_descriptor()` 时应先绑定参数。

单文本 `to_sv_obj()` 和 `to_cpp_obj()` 不生成 manifest。需要显式检查其运行时兼容性的
集成方可要求 `svtypes.generated-field-visitors.v1`、`svtypes.optional-codec-diagnostics.v1`
和 `svtypes.generated-wire-helpers.v1`；SV 输出还要求
`svtypes.generated-field-operations.v1`。将这些名称传给相应运行时的
`require_runtime_compatible` 接口即可。普通编译和执行不会自动进行这项检查。

# SvTypes 方括号类型特化：可行性与场景分析

## 1. 目的与结论

本文评估一项**破坏性公开 API 调整**：把 SvTypes 的“类型规格”从
构造器位置移到方括号位置。例如，现有的 `Bit(8, rand=True)` 变为
`Bit[8](rand=True)`，并可同时写作字段标注 `field: Bit[8]`。

结论：该调整在技术上可行，且能够使 Python 声明更接近类型定义、减少类型
规格和字段标注之间的重复。不过它不能以简单的全文替换完成；必须新增一个
统一的运行时类型规格层，并完成一次主版本迁移。第 6 节已将 API 语义收敛为
实现规范；它也是后续实现和验收的唯一公开语义依据。

该调整的目标是“简洁、可阅读、运行时可验证的类型规格，并可作为上层框架
使用的标注”。它**不承诺**让 Python 静态检查器进行完整的位宽算术推导。
Pyright/Pylance 等工具可将 `Bit[8]` 显示为注解；实际字段规格只由其赋值的
`Bit[8]()` 等描述符决定。

## 2. 核心模型

### 2.1 类型规格和值实例分离

实现引入内部不可变的 `TypeSpec` 协议。`Bit[8]`、`Array[Bit[8], 16]` 等
返回的是可调用的规格对象；调用规格对象产生现有的 `TypeBase` 值/字段
描述符实例。

```python
class Packet(SvObject):
    opcode: Bit[8] = Bit[8](rand=True)
    bytes_: Array[Bit[8], 16] = Array[Bit[8], 16](rand=True)
    pending: Queue[Logic[32]] = Queue[Logic[32]](max_length=256)
```

规格对象必须：

- 被缓存；相同规格的相等性和身份稳定；
- 公开其基础类别及实参，供上层注解消费者、诊断和文档使用；
- 在调用时注入规格参数，拒绝再次传入冲突的类型参数；
- 不进入序列化、Schema、CoverageIR 或约束 IR。那些层继续接收实际
  `TypeBase` 描述符，因而既有二进制和生成语义不改变；
- 支持 `from __future__ import annotations` 下的延迟注解解析。

不得为每个 `Bit[8]` 生成 Python 子类。动态子类会牵连 deepcopy、pickle、
`Reg` 的“与 `Logic` 同一运行时类”保证、错误信息和类型身份；这些代价没有
对应的用户语义收益。规格对象足以提供本次所需的运行时正确性。

### 2.2 标注不是字段声明

`SvObject` / `SvStruct` **不得**把注解当作字段发现机制，也不得仅因出现
`field: Bit[8]` 就声明字段；没有赋值描述符的字段非法。字段必须仍由
`field = Bit[8](...)`、`field = Array[...]()` 等实际对象产生。

因此，下述标注是可选的元数据，供更高一层框架、静态分析器或反射消费者使用：

```python
class Packet(SvObject):
    opcode: Bit[8] = Bit[8](rand=True)
    # opcode = Bit[8](rand=True) 也同样是合法的 SvTypes 字段声明。
```

SvTypes 不从注解推断描述符，也不因延迟字符串注解而改变字段收集行为；但会
拒绝**明显是 SvTypes type spec 且没有同名赋值描述符**的注解。例如
`field: Bit[8]` 在 `SvObject` / `SvStruct` 体内是声明错误，不能静默变成一个
被 schema、编码、随机化和生成遗漏的字段。普通 Python metadata annotation
仍不受影响。运行时正确性来自规格调用：`Bit[8]()` 固定创建 8-bit 描述符，不能
再次提供相冲突的位宽。

## 3. 类型和场景覆盖

| 类别 | 当前公开形式 | 迁移后形式 | 可行性与保持的语义 |
| --- | --- | --- | --- |
| 二态 packed | `Bit(width_or_shape, signed=...)` | `Bit[width](...)`；signed 形式见第 6 节 | 高。宽度、shape、signed、radix、字段策略和截断规则不变。 |
| 四态 packed | `Logic(width_or_shape, signed=...)` | `Logic[width](...)`；signed 形式见第 6 节 | 高。`LogicValue` 的三平面编码不变。 |
| `reg` 样式 | `Reg(width_or_shape, ...)` | `Reg[width](...)`；signed 形式见第 6 节 | 中。必须继续满足 `Reg` 与 `Logic` 相同 Python 值类型、仅 SV 拼写不同。 |
| 固定数组 | `Array(elem, size, ...)` | `Array[element_spec, size](...)` | 高。仍以递归数组表达多维 shape，outer policy 仅属于 outer array。 |
| 动态数组/队列 | `DynArray(elem, ...)` / `Queue(elem, ...)` | `DynArray[element_spec](...)` / `Queue[element_spec](...)` | 高。`max_length` 是编解码资源限制，保留为构造关键字，不是类型实参。 |
| 关联数组 | `AssocArray(key, value, ...)` | `AssocArray[key_spec, value_spec](...)` | 高。键排序、序列化和随机语义不变。 |
| class handle | `Object("Child", ...)` | `Object[Child](...)` 或 `Object["Child"](...)` | 中。必须同时支持已定义类型和前向名称，且仍由 registry 解析。 |
| 外部 handle | `RemoteRef("domain.Type", ...)` | `RemoteRef["domain.Type"](...)` | 中。目标名称属于 codec 身份；字符串规格必须稳定、可比较。 |
| 定宽整型 | `Int()` / `LongInt()` | 保持不变 | 不需要实参：宽度和 signedness 是 SV 固定语义。 |
| 浮点/字符串 | `Real()` / `ShortReal()` / `RealTime()` / `String(...)` | 保持不变 | 它们没有独立 type spec；`String.max_bytes` 是资源上限，不是 SV 类型规格，不能变成 `String[N]`。 |
| 枚举 | `class E(Enum, width=8, signed=False)` | `class E(Enum[base_spec])`，例如 `Enum[Int]`；裸 `Enum` 默认 `Int` | 高。base spec 是 enum 的唯一 width/signed 来源；见第 3.2 节。 |
| 参数 | `Parameter(Int)`、`Parameter(type)` | `Parameter[Int](default)`、`Parameter[type](default)`；支持 dependent field spec | 高，但范围大：参数表达式、模板态 symbolic spec 与 specialization 物化必须同批完成，见第 3.3 节。 |
| object/struct | `SvObject()` / `SvStruct()` | 保持不变 | 它们是声明/值基类，不是带构造时规格的 codec。 |
| coverage / constraint | `CovPoint`、`Cross`、`@constraint` 等 | 保持不变 | 它们消费字段描述符，不能把 coverage DSL 改成第二套类型系统。 |

### 3.1 代表性场景

```python
# 标量、值与字段策略；标注可选，实际描述符不可省略
opcode: Bit[8] = Bit[8](value=0x21, rand=True)
state: Logic[4] = Logic[4]("10xz")

# 固定数组与嵌套数组
header: Array[Bit[8], 4] = Array[Bit[8], 4]()
matrix: Array[Array[Int, 2], 3] = Array[Array[Int, 2], 3]()

# 运行时长度/资源上限不是类型规格
payload: DynArray[Bit[8]] = DynArray[Bit[8]](max_length=1024, rand=True)
lookup: AssocArray[String, Bit[16]] = AssocArray[String, Bit[16]]()

# 前向 class handle
child: Object["Child"] = Object["Child"](rand=True)
foreign: RemoteRef["transport.Device"] = RemoteRef["transport.Device"]()
```

两种多维固定数组写法都合法，且规范等价：

```python
Array[Bit[8], (3, 2)]()
Array[Array[Bit[8], 2], 3]()
```

它们必须继续有相同的嵌套表示、字节序、SV 声明和 outer-only field-policy
归属。上述字段进入 Schema、随机化、自动覆盖率、约束、SV/C++ 生成时，应与
当前等价描述符产生完全相同的结果。

### 3.1.1 方括号实参与调用规则

为避免每种类型各自猜测实参，所有公开类型特化共用以下规则：

- `Bit`、`Logic` 与 `Reg` 接受 `width`，或一个 packed shape tuple；多个裸
  整数实参也表示 packed shape。因此 `Bit[8]` 与 `Bit[(8,)]` 等价，
  `Bit[2, 8]` 固定表示 shape `(2, 8)`，而不是 signed 的另一种拼写。
- 可选的最后一个 marker 为 `Signed` 或 `Unsigned`：
  `Bit[8, Signed]`、`Logic[(2, 8), Signed]`、`Reg[8, Unsigned]`。
  省略 marker 一律为 unsigned。`BitSigned[8]`、`LogicSigned[8]`、
  `RegSigned[8]` 是相应 signed 规格的别名，身份也必须相同。
- `Array[element, size]` 的 `element` 是 TypeSpec、enum class 或可物化的
  `SvObject` / `SvStruct` 类型；`DynArray[element]`、`Queue[element]` 与
  `AssocArray[key, value]` 同理。旧的运行时描述符实参，例如
  `Array(Bit(8), 4)`，不再接受。`max_length`、`rand`、初值及所有既有 field
  policy 仍是调用 `Array[...]()` 等时的关键字实参。
- `Object["Child"]` 是前向引用的规范形式；`Object[Child]` 只在 `Child` 已是
  已注册的 `SvObject` 类时允许，并立即规范化为 registry name。`RemoteRef` 保持
  当前语义，只接受 `RemoteRef["domain.Type"]` 的字符串 target。
- `Parameter[spec]` 的 `spec` 必填；不保留从 default 推断 `Parameter()` 的旧
  形式。普通 value parameter 的 default 是 `Parameter[Int](8)`，type parameter
  的 default 是 `Parameter[type](Bit[8])`。裸 `Parameter(Int)`、
  `Parameter(type)` 及 Parameter 实例调用绑定均在迁移后报错。

TypeSpec 调用只接收该类型原有的 value/field-policy 参数；任何已经固化在方括号
中的类型实参（width、shape、element、key/value、object target、parameter spec）
再次由调用位置提供，均应报出迁移诊断，而不是按位置猜测其含义。也就是说，
`Bit[8](initial_value)` 中的 value positional argument 仍遵从当前 `Bit` 的 value
语义，而 `Bit(8)`、`Array(Bit[8], 4)`、`Object("Child")` 等旧的**类型规格位置**
一律不是兼容入口。

每个 structural scalar slot 独立接受以下四类值：具体 Python integer、直接
`Parameter`、继承用 `ParamRef`，或符合第 3.3.1 节的 parameter expression
function。tuple shape 递归应用这一规则，因此 `Bit[(W, H)]` 合法；派生维度必须
写为 `Bit[(W, lambda W: W * 2)]`，不能在类体中直接写 `Bit[(W, W * 2)]`。所有
具体化后的 width/shape/dimension 继续按现有 declaration limit、正数和资源限制
校验；错误须包含字段路径及求值后的值。

### 3.2 `Enum[base_spec]`

Enum 的 storage 类型是 enum *类*的属性，而不是 `Color()` 实例的构造实参；
因此新语法应使用可作为 Python class base 的 enum 规格：

```python
class Opcode(Enum[Bit[8]]):
    READ = 0
    WRITE = 1

class ErrorCode(Enum[Int]):
    OK = 0
    FAILED = -1

# 与 SystemVerilog 一致：省略 enum base type 时默认 int。
class DefaultState(Enum):
    IDLE = 0
    BUSY = 1
```

`Enum[base_spec]` 在类创建时把 base spec 交给现有 enum 成员收集和范围校验，
随后冻结其 storage。它不为 enum 实例另设宽度或 signed 参数。

裸 `Enum` 等价于 `Enum[Int]`：使用 SystemVerilog enum 的默认 base type `int`
（32-bit signed）。显式 `Enum[Int]` 仍合法，用于需要由上层框架读取统一 base
spec 的场合。两者的 runtime storage、范围和编码相同，但 SV 源码保留用户的
声明形式：裸 `Enum` 生成 `typedef enum { ... } Name;`，显式 `Enum[Int]` 生成
`typedef enum int { ... } Name;`。

本次接受所有正位宽的二态 `Bit[...]` / signed `Bit[...]` 规格，以及 `Int` /
`LongInt`；`Logic[...]`、`Reg[...]` 与 user-defined base type 仍明确报错，直到
其四态/范围/编码契约被单独扩展。enum codec 改为 `(width + 7) // 8` 字节、掩码
未使用高位，并按实际 width 进行 signed normalization；不再限制为整字节或
8/16/32/64 位。

现有 8/16/32/64 限制的主要来源是 C++ renderer：它生成
`enum class Name : intN_t/uintN_t`，而 C++ enum 的 underlying type 必须是内建
整数类型，不能是任意宽度 `BitValue<W, Signed>`。本次改为两条 lowering：

- 8/16/32/64 位继续生成原生 `enum class`；
- 其他正位宽生成具名 enum-value wrapper `struct Name`，内部 storage 为
  `svtypes::BitValue<W, Signed>`，并生成 `Name::MEMBER` 常量、比较、dump、
  `pack(Name, ...)` 与 `unpack(Name, ...)`。

wrapper 保持 SvTypes 生成代码所需的声明、赋值、成员引用和编解码语义；它不是
C++ language enum，因而用户不能把它作为原生 `switch` case 或
`std::underlying_type` 的输入。这是 C++ 表示层的明确差异，不改变 Python/SV
enum 语义或 schema identity。超过 64 位的成员常量必须由按字节的 initializer
生成，不能截断为 C++ 整数 literal。

实现需要一个能参与 Python class-base 解析的规格适配层（例如
`__mro_entries__`）；这是 `Enum` 与普通可调用字段规格的唯一结构性差别。它
不会改变 enum 成员提取时机、`IntEnum` 值 API、SV typedef 或 C++ enum class
生成规则。

### 3.3 参数化类和 dependent field spec

dependent field spec 是**既有能力缺口**，并非本次 API 调整引入；现已决定把它
纳入本次破坏性 API 调整。当前参数化机制有三个已验证阶段：

1. 模板类定义时，`W = Parameter[Int]()` 是未绑定声明；模板可生成 SV/C++ 模板，
   但不能实例化；
2. `Template.specialize(W=4)` 创建一个 Python-side binding class；该 class
   不生成独立 SV/C++ 类，生成侧使用原模板的 target-language specialization；
3. 当前 specialization **继承**模板的字段描述符，且 encoding fingerprint
   有意与参数值无关。这是因为目前字段 layout 不依赖参数。

旧 API 的 `Bit(W)` 与新 API 的 `Bit[W]()` 都会遇到同一问题：在模板类定义阶段 `W` 是 `Parameter`，还不是
可用于 Python codec 的正整数；而在 specialization 后，继承的字段不会自动
重建成 `Bit[4]`。此外不同 `W` 会有不同 byte width、field schema、encoding
fingerprint 和 binary compatibility，不能继续共享当前 parameter-independent
fingerprint。

本次必须把 dependent field spec 作为完整能力实现：

- `Bit[W]`、`Array[T, W]` 等生成不可实例化的 symbolic spec，而非部分构造的
  `TypeBase`；
- 未绑定模板可把 symbolic spec 渲染为 SV/C++ 参数表达式，但不能 Python
  pack/unpack、构造 coverage layout、运行随机化或作为 concrete object field；
- `specialize()` 必须先解析/验证所有 dependent spec，再克隆并物化每个字段、
  collection element 和嵌套规格；
- specialization 的 schema / encoding descriptor / fingerprint 必须包含已绑定
  layout 参数；不兼容宽度之间的跨版本 decode 必须拒绝；
- `ParamRef` 转发、继承、type parameter、约束和 coverage source 都要定义其
  在模板态与物化态的可用性。

规范声明形态为：

```python
class MemoryLine(SvObject):
    W = Parameter[Int](8)
    DEPTH = Parameter[Int](lambda W: W * 4)
    data = Bit[W](rand=True)
    entries = Array[Bit[W], DEPTH]()
```

这里 `lambda W: W * 4` 是参数表达式函数；模板生成应保留其结果表达式，
`MemoryLine.specialize(W=16)` 则解析 `DEPTH` 为 64，并物化为
`Bit[16]` 和 `Array[Bit[16], 64]`。若用户显式 override `DEPTH`，以 override
为准。参数默认值需要依赖图、拓扑求值、循环依赖诊断和 `ParamRef` 转发解析，
而不再只是当前的单个不可变值。

#### 3.3.1 常量表达式层

公开 DSL 不暴露 `ConstExpr`，也禁止在参数结构位置直接书写涉及 `Parameter`
的复合 Python 表达式。直接 parameter reference 仍合法：`Bit[W]`、
`Array[Bit[W], DEPTH]`。任何派生表达式必须提供为显式参数函数：

```python
class MemoryLine(SvObject):
    W = Parameter[Int](8)
    DEPTH = Parameter[Int](lambda W: W * 4)
    words = Array[Bit[W], (lambda W: W * 4)]()
```

函数的形参名引用同一模板中已声明的 parameter。实现**不执行**该 lambda 或
函数；而是像现有 `@constraint` 前端一样定位其源文件、解析 Python AST，并只
接受一个受控表达式（lambda body，或普通函数的唯一 `return` expression）。
AST 直接编译到内部、不可公开依赖的 `ConstExpr` IR。该 IR 保存 parameter
reference、literal、unary/binary operation 和条件选择，同时既能按
已绑定实参求值，也能渲染为 target-language constant expression。因此函数形式
消除了公开表达式 DSL 和 Python 类体名称捕获问题，却不能、也不应消除内部
symbolic representation：未绑定模板仍需要生成 `W * 4`，而不是只能在 Python
specialization 后得到数值。

函数只能是参数 expression declaration：不允许 `*args`、`**kwargs`、默认形参、
闭包或全局 parameter 引用、语句体、属性/下标访问、任意函数调用或任意返回
对象。普通函数只能拥有一个 `return expression`，lambda 只能拥有一个 body
expression。形参必须精确匹配其引用的模板 parameters。AST 可接受整数字面量、
这些形参、`+` / `-` / `~`、`+ - * / % ** << >> & | ^`、比较、`not`、`and` / `or`
与 `a if condition else b`；`/` 依 SV 整型除法编译，`//` 不属于 DSL。每步求值
使用该 parameter 声明类型的 SV 整数宽度/符号规则，绝不采用 Python float
语义。结构位置（packed width、array dimension、范围、parameter default）最后
只接受求值结果为正整数的表达式。

与 constraint 一致，源不可得、同一源码行有无法唯一定位的多个 lambda、或 AST
超出受支持子集时，声明期报出带文件/行号的错误；不会退化为执行用户 Python
代码。`Parameter[Int]` 和 `Parameter[LongInt]` 接受这种 expression default；
`String` / `Real` / `ShortReal` parameter 保持现有 literal default 语义，不新增
symbolic arithmetic；`Parameter[type]` 仅接受直接 type spec、concrete class 或
`ParamRef`，不接受 expression function。若未来引入 SV constant-function DSL，
也必须编译为同一内部 IR。

#### 3.3.2 Type parameter

SV `parameter type` 可以作为字段类型，因此本次不能只处理 `W` 这样的数值
参数。`Parameter[type]` 的默认值/override 必须接受 SvTypes type spec 或
具体 `SvObject` / `SvStruct` 类型，并在 generated SV/C++ 中成为对应 type
parameter。

Python 中，类体的 `T = Parameter[type]()` 是参数对象；当前实现把 Parameter
实例调用用于原地绑定。该旧语义会与 type parameter 字段构造冲突，因此本次
迁移删除它，并直接使用 `T()`：

```python
class Holder(SvObject):
    T = Parameter[type](Bit[8])
    item = T()
```

`T()` / `T(rand=True)` 仅对 `Parameter[type]` 有效；它在模板态为 symbolic field，
在 specialization 后物化为 `Bit[8]()`、`Payload()` 等具体描述符。值参数
`W = Parameter[Int](...)` 不可调用为字段。

`T(*args, **kwargs)` 完整保留为目标类型的字段构造调用：模板阶段只捕获实参，
不得尝试以未知 `T` 验证；specialization 后以原有调用约定转发给已解析的
TypeSpec 或 object/struct 类。因此 `T(value=0, rand=True)` 与直接写成已解析类型的
同一调用具有相同初值、字段 policy 和错误行为。目标不接受的参数必须在
specialization 时以字段路径报错，而不是静默忽略。

这要求删除旧的“调用 Parameter 实例以原地绑定”的 API；参数绑定统一只发生在
`Parameter[spec](default)` 的声明默认值和 `specialize(...)` override。这样
`T()` 没有歧义：它永远不是参数绑定，而是 type parameter 的字段构造。

#### 3.3.3 模板态与具体态边界

- 未绑定或仍含未解析 layout/type 参数的模板：可生成 SV/C++ 模板与 symbolic
  declaration；不可实例化、pack/unpack、构建 concrete schema/encoding
  descriptor、randomize 或构建 Python coverage layout。保持当前不依赖 layout 的
  模板 fingerprint 行为；一旦 descriptor 中出现 symbolic layout，任何 schema 或
  encoding descriptor 查询都必须明确报错，不能产出空 schema 或共享假 fingerprint。
- 已完全 specialization 的类：字段、nested collection、object/type parameter
  都必须物化；随后与非参数化类具有相同 runtime、随机化与 coverage 能力。
- Schema 的 type identity 继续包含 parameter binding；encoding fingerprint 改为
  由**已物化的 layout**决定。无 layout 依赖的既有 specialization 继续共享
  fingerprint；`W=8` 与 `W=16` 的不同 concrete layout 不得共享。
- 继承和 `ParamRef` 先解析到最终模板参数环境，再物化字段；缺失、循环、dtype
  不匹配和不合法正尺寸必须在 specialization 时清晰报错。

SV/C++ 生成可以使用 symbolic template declaration；Python-side coverage、采样、
编码和任何 database identity 只接受 fully materialized specialization。不得为
未具体化模板新增“半 concrete”的公开 schema API。

#### 3.3.4 dependent consumer 的统一边界

target-language template 和 Python runtime 的边界按下表固定，避免同一 parameter
在不同消费者中被悄然采用不同语义：

| 消费者 | 未完全具体化的 template | 完全具体化的类 |
| --- | --- | --- |
| SV/C++ type declaration、packer、parameter declaration | 生成 symbolic expression / type parameter | 生成绑定后的 concrete layout |
| `@constraint` 与生成的约束 | 可引用 target-language parameter；保留在模板输出 | 以 materialized field 与已绑定 parameter 求解 |
| `@rand_layer`、`rand_mode` / `constraint_mode`、Python `randomize` | 不可调用 | 与非参数化类相同 |
| `CovPoint` / `Cross` 定义与生成的覆盖率声明 | 仅生成能由 target-language parameter 表示的声明 | 创建 concrete Python CoverageIR/layout 并可采样 |
| Schema、encoding、pack/unpack、external storage、UCIS database identity | 一律拒绝 | 允许，identity 基于 materialized layout |

若 coverage declaration、constraint 或 field 的任何部分无法以 target-language
parameter expression 表示，模板生成在声明期报错；不得降级为在 Python 中提前执行
parameter function。这样既保持 target-language 模板能力，也保持 Python runtime
只在 layout 可知后工作的既有模型。

这不是技术上不可行，而是一次 codec-layout 参数化设计；它与方括号 API 一同
实施，避免留下第二次破坏性迁移。

## 4. 影响范围

### 4.1 运行时代码

- `bit.py`、`logic.py`、`collection.py`、`object.py`、`remote_ref.py` 需要
  统一的 `__class_getitem__`/规格调用路径；不得让各类型各自实现不兼容的
  工厂。
- `parameter.py` 内部目前构造 `Bit(n_bits, ...)`；覆盖率前端也会按运行时
  literal 宽度构造 `Logic(...)`。内部代码必须走相同规格工厂，验证动态整数
  实参可用。
- `SvObject` 字段收集不读取类型注解；所有后续消费者继续看到既有 `TypeBase`、
  `ObjectDescriptor` 和 collection 实例。
- `Reg` 是风险最高的标量：实现不得把它变成与 `Logic` 不同的存储/编码类型。

### 4.2 语义消费者

以下模块不应因为语法迁移而重写其业务语义，但必须新增等价性回归：

- Schema / encoding descriptor / digest；
- Python 编解码和外部字段存储；
- SV 与 C++ 生成；
- 约束 AST、随机化、`randc`、动态容器和图随机化；
- 自动覆盖率、CoverageIR、SV coverage renderer、UCIS 导入导出；
- record schema、registry、specialize 和 package/scope。

### 4.3 公开材料和用户迁移

仓库当前至少存在 576 处 `Bit(...)`、42 处 `Logic(...)`、60 处 `Array(...)`、
59 处 `DynArray(...)`、35 处 `Queue(...)`、21 处 `AssocArray(...)`、59 处
`Object(...)`。这些包括回归测试、示例、benchmark、文档和源代码。

当前版参考文档、support matrix、架构说明、二进制格式说明、随机化与覆盖率
文档都使用旧写法。历史版本文档应保留其发布时语义，只在迁移指南中说明版本
边界；当前参考文档、示例和 support matrix 则必须整体切换，避免混用两种
公开语法。

## 5. 兼容性、迁移与验证措施

### 5.1 版本与旧 API 处理

这是主版本破坏性修改。正式版本不得静默接受 `Bit(8)` 后再猜测用户意图；
应报出明确迁移错误，例如“位宽移至 `Bit[8](...)`”。

无下标形式保留为单比特简写：它声明一个比特，且其位置参数是 **value**（与 `Bit[width](value)` 一致），
因此 `Bit(0)` / `Bit(1)` 合法。宽度只能来自下标，单比特无法精确表示的整数（如 `Bit(8)`）按上面的
迁移错误拒绝，不做截断——否则 `Bit(8)` 会静默退化成 1 比特。括号写法保留 packed-value 截断语义
（`Bit[1](8)` 为 `0`）。

可在开发分支提供独立、非默认的 AST 迁移工具，转换明显的字面量场景：

```python
Bit(8, rand=True)              -> Bit[8](rand=True)
Array(Bit(8), 4)               -> Array[Bit[8], 4]()
DynArray(Object("Child"))       -> DynArray[Object["Child"]]()
```

工具必须拒绝或标注以下需要人工确认的代码，而非生成可能改变语义的补丁：

- 宽度/shape/元素类型由变量、参数、工厂函数或条件表达式决定；
- positional `value`、`signed`、`radix` 的旧调用；
- 字面量 `0` / `1`：在遗留代码里它们是宽度，在当前 API 里它们是单比特的 value，两种读法都成立；
- 多维 `Array(T, (M, N))`；
- import alias、用户自己的同名 `Bit`、反射和 `getattr`。

### 5.2 验收矩阵

1. 每类规格的构造、冲突参数和非法实参测试；
2. 标注不产生字段、带赋值字段、未标注字段、延迟注解、继承与 forward
   reference；
3. enum 的 1、非字节对齐、标准 C++ 宽度及超过 64 位场景；分别验证 Python/SV
   storage、byte codec、native-enum/wrapper C++ lowering 和成员常量不截断；
4. 每个旧代表性 descriptor 与新 descriptor 的 `schema_descriptor`、
   `encoding_descriptor`、IR 及生成 SV/C++ 字节级等价；
5. 全量 Python 回归、构建 wheel、公开 examples；
6. 相关远程 SystemVerilog 一致性回归；
7. 对旧形式的错误消息和迁移工具 fixture；
8. 审查所有公开文档、测试、生成样例和 Git 暂存内容，确保没有旧 API 被误留为
   当前推荐写法。

### 5.3 旧/新 API 的最终语义一致性比对

最终验证必须不是“新 API 自己通过测试”，而是与迁移前旧 API 的可观察语义做
成对比对。实施前应在旧 API 的干净基线提交上生成并审阅一个受版本控制的中性
baseline fixture；迁移后，测试只使用新 API，并读取该 fixture 比较。

每个成对场景至少比较：

- 描述符的 normalized type metadata、schema、encoding descriptor、type name
  和 encoding fingerprint；
- 给定代表值的 pack bytes、unpack 值、截断/符号归一化和错误类别；
- SV/C++ 声明、类型表达式和 packer 表达式；
- 固定/多维/动态/队列/关联数组以及 object/remote handle 的嵌套场景；
- 有固定 seed 的约束随机化状态、失败分类和 layered mode 行为；
- 自动覆盖率、CoverageIR 和 coverage renderer 输入；
- 相关远程 SystemVerilog 一致性观测。

fixture 记录语义结果而不记录旧 API 的可执行代码；因此正式版本不需要也不得
保留旧构造入口。对 API 语法错误文本不做等价比较（旧语法在新版本应明确报错），
只比较同一模型的可观察数据与生成语义。

dependent parameter layout 是新增能力，没有旧 API 的等价实现；其验收不伪造
“旧/新”比较，而是分别验证 symbolic template 输出、每个 concrete
specialization 的 layout/schema/bytes、parameter default/override/`ParamRef`
组合、以及生成 SV/C++ 与 Python materialization 的一致性。

### 5.4 回退策略

在正式主版本发布前，改动只在独立分支上推进。若 schema、生成代码、随机化或
coverage 的可观察结果出现非预期差异，应撤回该分支而非在稳定版本中保留两套
构造语义。发布后，旧 API 不再是兼容层；迁移工具和迁移指南是唯一过渡措施。

## 6. 已锁定的实施语义

以下项目不能由实现自行猜测：

1. **signed 的表示。** 已确认两套无歧义写法都支持并 canonicalize 为同一规格：
   `BitSigned[8] is Bit[8, Signed]`、
   `LogicSigned[8] is Logic[8, Signed]`、
   `RegSigned[8] is Reg[8, Signed]`；
   `Bit[8] is Bit[8, Unsigned]`、`Logic[8] is Logic[8, Unsigned]`、
   `Reg[8] is Reg[8, Unsigned]`。
   `Bit[2, 8]` 固定表示 packed shape `(2, 8)`，绝不能同时表示 signed。
2. **多维固定数组。** 已确认
   `Array[Bit[8], (3, 2)]` 和
   `Array[Array[Bit[8], 2], 3]` 都合法且等价；field policy 仍只属于 outer
   array。
3. **`Reg` 特化。** 已确认 `Reg` 继续与 `Logic` 共享 runtime value type 和
   编码；仅生成的 SV 声明拼写为 `reg`。
4. **枚举。** 已确认采用 `Enum[base_spec]`，例如 `Enum[Int]`；裸 `Enum` 仍为
   SV 默认 `int`。所有正位宽二态 base 支持 Python/SV codec；C++ 的非
   8/16/32/64 位以 named value wrapper lowering 实现。四态和 user-defined base
   不在本次范围，见第 3.2 节。
5. **参数化尺寸。** 这是现有 `Bit(WIDTH)` 已有、而非新 API 引入的 deferred
   能力；现已确认随本次迁移一并解决。完整范围、symbolic `ConstExpr`、type
   parameter 和模板态边界见第 3.3 节。type parameter 直接以 `T()` / `T(...)`
   作为字段构造，旧 Parameter 实例调用绑定 API 被移除。
6. **句法严格度。** 已确认：一旦开始迁移，运行时立即禁止所有旧 constructor
   形式；只允许独立迁移工具读取并改写旧源码。
7. **`isinstance` 契约。** 已确认：不要求区分到 `Bit[8]` 这一级，规格对象方案
   保持 `isinstance(value, Bit)` 等现有类别级行为即可。
8. **handle target。** 已确认 `Object["Name"]` 保留 registry-name / forward
   reference 语义，`Object[Class]` 仅是已注册 class 的便利写法；`RemoteRef` 继续
   使用字符串 identity，不引入 class target 的第二套外部引用模型。
9. **模板查询。** 已确认只有完全物化的 specialization 能有 concrete
   schema/encoding/coverage identity；layout-independent 的既有 template fingerprint
   可保持现状，symbolic layout 则必须显式拒绝查询。
10. **参数表达式。** 已确认仅以可定位 lambda 或单-return 函数提供复合 parameter
    表达式，按第 3.3.1 节 AST 子集编译；不会执行用户函数，也不公开 ConstExpr。

## 7. 实施顺序

第 6 节已锁定 API 语义；在把本报告提升为正式 API 规格后，可按以下次序实施：

1. 写入正式 API 规格及迁移指南，锁定 signed、shape、array 和 `Reg` 语义；
2. 实现公共 `TypeSpec` 基础设施和 `Bit` / `Logic` / `Reg`，仅用新专门回归验证；
3. 扩展集合、`Object`、`RemoteRef`、`Enum` 和 `Parameter`，实现 symbolic
   `ConstExpr`、type parameter field 物化，并验证标注不会改变字段收集；
4. 迁移内部源码、测试、examples 与当前文档；
5. 运行完整本地、生成、远程一致性，以及第 5.3 节的旧/新语义基线比对；
6. 最后删除旧 constructor 入口、运行迁移工具测试，并准备主版本发布说明。

在第 2 步通过之前，不应改写现有测试或公开示例；在第 5 步通过之前，不应把
该 API 作为稳定接口发布。

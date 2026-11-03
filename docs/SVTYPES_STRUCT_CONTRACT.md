# Struct 类型与下游集成契约

**状态：导出与运行的数据契约已实现并通过本仓库验收；随机化扩展的目标能力门另行记录。**
本文确定结构体支持的接口和语义，供下游消费者准备类型识别、manifest、字段访问和生成适配。
它不是旧安装版本已经具备这些能力的声明；消费者应检查 capability，而不是仅比较包版本。
不得在旧版本上通过忽略错误、修改私有字段或把 struct 换成 object 的方式模拟支持。

SvTypes 是独立项目。SVX 可作为本契约的消费者；传输、调用调度和对象生命周期仍由消费者负责。

## 1. 公开类型与声明

两个类型均从 `svtypes` 顶层导出：

| Python 基类 | SystemVerilog 类型 | 含义 |
|---|---|---|
| `SvStruct` | `typedef struct { ... } Name_t;` | 默认的 unpacked 按值结构体 |
| `SvPackedStruct` | `typedef struct packed { ... } Name_t;` | 显式 packed 按值结构体 |

`SvPackedStruct` 是 `SvStruct` 的 Python 子类型，所以公共消费者可以用
`issubclass(T, SvStruct)` 或 `isinstance(codec, SvStruct)` 判断结构体。
需要区分两者时先判断 `SvPackedStruct`，不能先走 `SvObject` 的 class-handle 分支。
这是 Python 实现上的类型分类关系，不表示生成 SV 结构体继承。

```python
from svtypes import Bit, Queue, String, SvObject, SvStruct, SvPackedStruct

class Header(SvPackedStruct):
    opcode = Bit[4]()
    length = Bit[12]()

class TransactionData(SvStruct):
    header = Header()
    label = String()
    payload = Queue[Bit[8]]()

class Packet(SvObject):
    data = TransactionData(rand=False)
```

- 结构体字段仍使用已物化声明，如 `Header()`，而不是新的 factory/字典 DSL。
- 方法边界类型仍使用具体类型 `Header`、`TransactionData`；容器规格可包含它们，
  如 `Queue[TransactionData]`、`Array[Header, 4]`。
- 只有具体的用户类型可物化；裸 `SvStruct`、`SvPackedStruct` 是声明基类，不是值边界类型。
- 不为结构体引入 class 的 `new`、对象编号、继承或可空值语义。用户定义的结构体不能互相
  继承，也不能从结构体继承产生 SV class；字段组合是复用结构体的方式。
- 结构体不是约束块、covergroup 或方法的宿主。随机约束、随机层和覆盖组继续声明在所属 class。

## 2. 成员、默认值与按值行为

### 2.1 成员合法性

`SvStruct` 接受 SvTypes 已支持、在 SV unpacked struct 中合法的成员，包括 integral/enum、
packed/unpacked struct、String、Real/ShortReal、固定/动态/关联数组、Queue，以及合法的
class handle 或 RemoteRef。不能因为成员不是 packed integral 就拒绝整个结构体。
成员是否可随机化、能否作为 coverage 源，是与其能否作为结构体成员分开的判断。

`SvPackedStruct` 只接受 SV packed struct 合法的 packed integral 成员及嵌套 packed struct。
不能容纳 unpacked struct、unpacked Array、String、Real、动态容器或 class handle。
现有 `Bit[shape]`/`Logic[shape]` 是 packed 维度，不能与 `Array[...]` 的 unpacked 维度混淆。
packed struct 整值默认 unsigned；成员自身的 signedness 不会自动改变整个结构体的 signedness。
首个声明成员位于整值的最高位，后续成员依次向低位排列。

`SvPackedStruct` 提供只读的 `width`、`signed`、`state_domain` 属性。`.value` 仍是
结构化的值，不改成整数；逐成员访问和整体约束同时存在。

不支持的 SV 类型应以原类型的支持边界明确报错，而不是把整个 unpacked struct 列为不支持。

### 2.2 初始化、赋值与复制

- 两种结构体均是非空的值，不得给结构体本身赋 `None`。内部 class handle 可为空。
- 成员默认值属于声明；修改运行时值不得改变声明、schema 或生成的初始化表达式。
- unpacked struct 的合法成员默认值按 SV 规则处理；packed typedef 中不得输出非法的成员
  默认赋值。packed 字段的显式默认值继续使用合法的整体 assignment pattern。
- `target.value = source` 要求兼容的具体结构体类型，执行结构体赋值：标量/嵌套结构体复制值，
  容器复制容器内容和结构；容器内或字段中的 class handle 复制引用，不克隆其指向的对象。
- `copy.deepcopy()` 是显式 Python 深复制操作，不作为 SV 结构体赋值的实现替代品。
- 成员名可以是 `value`。内部结构体复制和容器读写必须按实际类型处理，不能将这个成员
  误作整个结构体的 `.value` 接口；`copy.copy()`、嵌套深复制以及四种容器的赋值均应保留
  完整结构体。此时 `instance.value` 表示用户声明的成员，整体副本可通过 `copy.copy()` 获得。
- 修改一个按值副本的标量、容器内容，不修改原结构体；若二者的 handle 指向同一对象，
  通过该 handle 修改对象成员应由二者观察到。
- 结构体赋值和随机化本身不调用 `new`，不制造额外 class 对象或生命周期事件。

## 3. 公共类型识别和 manifest 身份

沿用现有公开 API，不要求下游读取 `_SvObject__...`、成员存储键或 renderer 私有字段：

```python
codec = materialize_type_spec(TransactionData, location="method.argument")
identity = type_spec_identity(TransactionData)
restored_type = type_spec_from_identity(identity, location="method.argument")
description = schema_descriptor(codec)
```

`is_materializable_type()` 必须接受两种具体结构体及以它们为元素的合法容器规格。
物化 codec 不得生成结构体对象 ID、注册 class 实例或调用用户自定义构造流程。

### 3.1 边界类型身份

具体结构体的 `type_spec_identity()` 结果保留 `kind="struct"`，并**必须**包含布尔 `packed`：

```json
{
  "kind": "struct",
  "module": "example.models",
  "qualname": "TransactionData",
  "packed": false
}
```

`Header` 的对应结果为 `packed: true`。容器嵌套身份通过原有 `args` 递归携带该字段；
已有模板/参数身份形式继续使用，但结构体身份同样必须带 `packed`。

导入身份时必须验证实际类型的 packed 属性。不能仅靠 `module`/`qualname` 相同就接受
已由 unpacked 改为 packed 的定义。示例中的名称仅为说明，不是保留命名空间。

### 3.2 schema 与 encoding

`schema_descriptor(codec).schema` 的结构体节点仍使用 `kind="struct"`，新增必需字段
`packed: bool`；嵌套结构体节点也必须明确记录。现有 `type_name`、按声明顺序的 `fields`、
field policies 和参数信息继续保留。`SchemaDescriptor` 本身及 `to_dict()` 的接口不变。

packed 属性属于声明语义，参与 `schema_fingerprint`。编码仍是按字段调用既有 codec 的组合，
`encoding_schema` 不仅因为 packed 属性不同而增加位向量拼接或内存对齐策略。
在成员编码和字段顺序相同的前提下，packed/unpacked 可以有相同的字节布局与 encoding fingerprint；
这不意味着它们是相同的 SV 类型。消费者必须同时验证类型身份/源 schema，不能用编码指纹推断语言类型。

`unified_type_name()` 继续使用现有统一命名规则，不通过追加 `packed` 等临时字符串来创造另一套名称。
不同名称的两个类型仍然是不同类型，不因字段相同而可互换。

## 4. 编解码与跨语言边界

### 4.1 字节协议

- 结构体本身没有 class object envelope、presence marker、对象编号或 registry entry。
- 编码按声明的字段顺序组成，遵守现有 `pack_bytes` 字段策略、标量/容器编码与资源限制；
  packed/unpacked 不引入宿主 padding、`sizeof`、C bitfield 布局或 SV 仿真器 ABI。
- packed struct 的协议仍是逐字段编码，不改成“把整个 `$bits(T)` 向量直接输出”。
  例如两个 `Bit[4]` 字段仍分别按现有 Bit codec 编码，不擅自压成一个字节。
- 结构体内部的动态容器使用既有长度前缀；class handle 使用既有对象图编码；RemoteRef 继续
  使用其既有不透明引用编码。这些语义不会因为外面包了一层 struct 而改变。
- 嵌套 handle 的编码/解码必须共享同一次传输的对象图上下文，保留共享引用、循环引用和 null。
  结构体节点本身不因此成为有身份的 class 对象。
- 现有 `checked_unpack(..., context=...)` 是下游传入解码上下文的公共入口；其上下文必须
  传播到结构体内部需要它的字段，不得仅在最外层是 SvObject 时才生效。
- 截断、损坏的长度、类型不兼容与资源超限继续明确失败，不做隐式截断或 packed/unpacked cast。

### 4.2 SystemVerilog / C++ 生成

下游继续调用这些公开接口：

```python
sv_type_expression(codec)
sv_packer_expression(codec)
sv_declaration(codec, "argument")
sv_codegen_context(codec, prefix="adapter")
```

两种结构体都返回对应的 `Name_t` 类型及其 `Name_packer`；消费者不能通过类型名后缀猜测 packing。
容器维度位置、嵌套 typedef 依赖顺序和局部 alias 仍由公开生成 API 处理。

C++ 侧两者均为按成员建模的值 `struct`。**SV packed 不表示 C++ `#pragma pack` 或 ABI 字节兼容。**
必须调用生成的 pack/unpack，不能把结构体地址直接当协议字节。

SVX 等使用方法参数/返回值的消费者，应以生成类型和配套 packer 构建本地变量或适配记录，
通过现有字节协议交换值；不能因为 SV unpacked 类型的宿主内存布局不同而整体拒绝。
若某特定直接调用 ABI 无法传递该类型，消费者应改用已支持的序列化适配路径，或仅明确拒绝
该 ABI 路径，不能声称 SvTypes 类型本身非法。

`RecordSchema` 的对象记录契约不变；不能为接入 struct 而把其返回类型偷偷改为结构体，
也不能把结构体封装成一个新的有对象身份的 record 来冒充同一种类型。

## 5. 随机、modes 与覆盖率

本节规定语义边界，具体算法由 SvTypes 实现；下游不复制求解器或自行推导私有 leaf 表。

- packed struct 是 integral 整值；其合法的随机声明和运算按整个 packed 值处理。
  packed 类型成员不能带独立的 SV `rand`/`randc` 修饰符。
  本项目有意采用枚举项限制的兼容策略，具体差异见文末；独立 enum 和 unpacked enum
  成员仍遵守枚举域。该随机化策略不限制显式赋值和编解码的底层位模式。
- unpacked struct 不是 integral 整值。其 `rand` 声明及类型内成员 `rand`/`randc` 修饰遵守
  IEEE 1800 §18.4；不能把当前 packed 实现的成员规则直接套用到 unpacked。
  unpacked struct 整体不能声明为 `randc`，但合法成员可以声明为 `randc`。
- `FieldOptions` 的有效随机策略仍由 SvTypes 解析并导出；适配方不通过读取 Python 原始
  构造实参来推断最终 SV 修饰符。不会仅因为一个 String/Real 成员不可随机，就拒绝其作为值字段。
- class 宿主的约束可以引用合法的结构体成员、动态容器大小和元素；结构体本身不声明约束块。
- `unique()` 接受 packed struct 整值及叶元素为 packed integral 的合法数组；不能展开结构体
  成员后做成员间不等，这会改变原语义。真正的 unpacked struct 整体不属于合法比较项；
  其合法 integral 成员可以显式参与 unique。
- `rand_mode` 只对 SV 允许选择的随机变量生效。packed 成员不能被当成独立的 rand leaf 暴露
  可独立控制的模式；unpacked 的合法成员控制须按 SV 规则实现。分层随机继续遵守既定的
  modes 保存/恢复和动态元素策略，不为 struct 添加另一套生命周期。
- struct 整体不是 class handle，不能按 nullness 自动覆盖。unpacked 成员及容器元素的自动
  coverage 继续按其合法源类型生成；不能将整个 unpacked struct 当成整数值域。
  packed struct 的 integral 覆盖源按整体位值解释，保留已有显式成员覆盖能力。

## 6. 兼容性和接入顺序

这是一个**显式语义迁移**：历史 `SvStruct` 生成 packed SV，新的默认生成 unpacked SV。
不能承诺“旧源码不变且生成类型不变”。需要保留 packed 语义的定义应改为 `SvPackedStruct`；
相同字段的既有按字段字节布局不因该迁移被重写。

导出与运行数据契约的 capability 名为 **`svtypes.struct-kinds.v1`**，通过现有
`runtime_capabilities().provided` / `require_runtime_compatible()` 协商。
其范围为：packed/unpacked 类型识别与 identity/schema；逐成员编码与生成类型/packer；
参数、返回值、output/inout 所需的值 codec；字段整体/局部访问及按值复制；嵌套容器、
class handle 与 RemoteRef 身份；对应的兼容性校验。Python、SV、C++ 的能力声明保持一致。

此 capability 不表示所有随机求解后端能力均已验收。第 5 节的随机化语义仍按标准实现，
但其后端一致性单独验收；已明确的随机目标能力门不得阻塞数据类型的导出或传输，
也不得因数据契约通过而被视为解除。

支持新结构体契约的消费者必须：

1. 要求上述 capability；没有它时报告依赖版本不满足，不将缺失字段默认为 unpacked。
2. 在 boundary identity 和 struct schema 中检查显式 `packed` 布尔值。
3. 对重新导入的类型调用公开 identity/schema 验证，不能复用旧 manifest 或旧生成缓存。
4. 把旧协议支持作为单独的 legacy 分支；如果保留，旧版缺少 `packed` 的 struct 只能按
   当时的 packed 契约解释，不能与新分支混用。
5. 不假定只有 Python `SvObject` 子类型就一定有 object envelope；结构体分类优先。

新 capability 是本次接入的识别依据，不依赖尚未发布的版本号。schema、binary format 与
runtime ABI 的现有版本检查继续有效，不跳过；只有实际格式/ABI 发生不兼容变化时才调整对应版本，
不能把增加类型分类和改变宿主布局误当作需要整体重写编码协议。

## 7. 下游可先开展的工作

- 为两种 struct 建立共同的“按值结构体”分支，并通过 `packed` 区分生成类型和运算资格。
- 扩展方法参数、返回值、类字段与容器元素的类型识别，沿用公开物化、identity、schema 和生成接口。
- manifest/cache 加入 packing 语义，并覆盖模块/名称不变但 packed 改变的失效测试。
- 保持现有 handle/RemoteRef/session 体系，准备结构体成员路径及按值 read/write/copy 的适配。
- 准备 mock fixture 进行接口开发，但不能把 mock 跑通记为真实 SvTypes 集成验收。

本契约不要求下游重写 SvTypes，不新增 transport opcode、对象生命周期、调度器或 GUI 依赖。

## 8. 实现验收矩阵

| 项目 | 必须证明的行为 |
|---|---|
| 类型/物化 | 两个公开基类、合法成员与非法组合、具体类型和容器边界 |
| 身份 | packed 布尔值递归保留；错误 packing/旧身份不被静默接受 |
| 赋值 | 标量/嵌套/动态容器按值复制，内部 handle 保持引用语义 |
| 默认值 | 声明默认值不随运行时修改；SV 初始化合法且与 Python 对齐 |
| 编解码 | Python、生成 SV、C++ 字段字节一致；packed 迁移不压缩原字节布局 |
| 对象图 | struct 中 null、共享和循环 handle 的上下文传播，不为 struct 分配对象身份 |
| 外部存储 | 类字段、struct 成员路径、容器内 struct 的读写及复制正确 |
| 随机与 unique | packed 整值比较、unpacked 成员约束、动态元素、合法 randc/modes/layers |
| coverage | packed integral 源与 unpacked 成员源分开；Python/SV 计数、分母和结果一致 |
| 方法边界数据 | 真实类型的请求/结果记录与 output/inout 值副本、字段和容器往返；消费者的调用调度由其自行验收 |
| 兼容性 | 旧 packed 声明迁移、旧 manifest 拒绝与新 capability 检查 |

语义依据为 IEEE 1800 的 §7.2（struct）、§7.2.1（packed struct）、§18.4（随机变量）、
§18.5.5（uniqueness）和 §18.8（rand_mode）。
可核对的标准文本：[IEEE 1800-2017](https://fpga.mit.edu/6205/_static/F25/documentation/1800-2017.pdf)。
标准语义、实现状态和配置目标的可验证能力要分别记录，不以某个后端的限制重定义类型本身。

当前数据契约资格证据：本仓库最终固定代码完整回归 833 项通过，无 skip/xfail，包含生成 C++ 编译运行
及必需的远程 SV 验证。结构体定向回归覆盖两种 packing、身份拒绝、逐成员编码、按值副本、
局部外部存储路径、嵌套容器、共享/循环/null handle、RemoteRef、请求/结果记录和三侧 capability。
此结论不代替 SVX 的应用联调，也不将下列随机目标能力探针计作语义对拍通过。

新增的成员名冲突回归还覆盖 `value` 字段的普通/嵌套结构体复制、嵌套深复制、固定数组、
动态数组、队列、关联数组的值赋值与读取，以及队列追加和弹出。完整回归前曾在测试运行中
修改源码，导致源码定位失败；该轮不作为最终资格证据，以上计数来自代码固定后的重新验证。

### packed enum 随机化的有意兼容策略

按照用户确定的兼容策略，packed struct 中的 enum 成员随机化仅选择已声明枚举项。
这是对 IEEE 1800-2017 §18.4 的有意偏离：标准允许随机 packed struct 的 enum 成员
取其底层完整位域。本项目选择与配置目标的实际随机行为一致，不再将完整位域探针作为待解除的验收门。
整体 integral 比较和 unique 仍然保留；若整体约束要求某 enum 成员取非枚举值，随机化失败。
非枚举位模式的显式赋值、编解码与覆盖率采样不因此被禁止。独立 enum 的原有规则不变。

本次兼容策略已完成验收：结构体定向 44 项通过，完整回归 833 项通过，无 skip/xfail，
包含配置的远程 SV 验证。Python 对整体非枚举值约束返回失败，无约束随机结果限制在枚举项；
远程回归同时验证整体非枚举值约束失败，与所选策略一致。
本契约目前不提供 packed union 公共类型；未来若支持其随机化，应沿用此兼容方向并单独验证。

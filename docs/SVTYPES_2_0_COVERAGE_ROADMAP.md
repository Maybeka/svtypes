# SvTypes 2.0 功能覆盖率路线图

**状态：1.7 设计冻结完成；CoverageIR/自动 `cov` 基础切片已交付。** 本文件冻结 2.0 coverage
声明的载体、语义和 IR 契约；完整运行时与 SV parity 按 §8 在后续里程碑交付。

**路线图的解读优先级：**本文首先规定方向性原则、公开可观测语义、身份/merge 边界和里程碑
门槛；§5 中的声明片段说明这些契约应如何落到 DSL，但不是私有 parser、运行时对象布局或
1.10 前物理数据库格式的实现手册。实现可以调整内部表示与编译策略，前提是不得改变 §3 的分层
原则、已冻结的公开诊断和 Python/SV 对拍结果；若示例表达与这些原则发生歧义，以原则和可观测
契约为准。

## 1. 目标与非目标

2.0.0 的覆盖率目标是在 Python 中提供完整、可合并、可报告的**功能覆盖率**，并由同一
声明生成语义等价的 SystemVerilog functional coverage。它不是对所有 SV 验证能力的复刻。

2.0.0 包含：covergroup、coverpoint、显式 sample、bins、illegal/ignore bins、条件采样、
有界 transition（非数组式声明）、定长覆盖率数组、容器值域 coverpoint、cross、
instance 归属、权重/目标、报告、数据库持久化与受控合并、有限用例来源，以及 UCIS
interchange 适配。

2.0.0 不包含：SVA、代码覆盖率、断言覆盖率、覆盖率驱动的自动随机策略、仿真器私有
UCDB API、SVX/UVM/DPI 依赖、C++ coverage runtime、无界 transition 算子，或长度随
`size()` 变化的覆盖率数组，以及由 `FieldOptions.cov` 生成的 1.x **SV-only legacy nested
covergroup**。`cov` 本身迁移为 §2 的默认覆盖组声明。后续扩展
不应改变 2.0 已冻结的声明身份、bin 语义和数据库格式。

## 2. 当前基线与迁移原则

现有 `FieldOptions.cov` 控制自动字段覆盖；它目前只生成 SV nested covergroup，Python 没有
采样器、coverage database、报告或 merge。2.0 **保留该便利 API**，但把它迁移为新 coverage
声明编译器生成的默认覆盖组，而不是保留一个 SV-only collector。

- 每个具有有效 `cov=True` 字段的 `SvObject` 自动获得稳定声明名 `svtypes_auto_cov` 的默认
  covergroup；它与用户显式 `@covergroup` 并列，并同样进入 CoverageIR、Python runtime、数据库和
  SV renderer。`cov=False` 只排除该字段，不是错误，也不影响同类的显式覆盖组。
- 标量 `Bit` / `Logic` / `Enum` 字段产生直接字段 covpoint；固定 `Array(T, N)` 自动产生 N 个
  定长 slot point。`DynArray` / `Queue` 在未指定 `cov_slots` 时产生一个元素**值域** point：一次
  sample 将当前所有元素值送入同一 point；指定正整数 `cov_slots=N` 时改为前 N 个 slot point。
  `max_length` 是编解码资源限制，绝不隐式充当 `cov_slots`。`AssocArray(K, V)` 产生 value-domain
  point，只采样当前 value；动态 key 既不成为 slot，也不隐式进入 coverage universe。
- `cov_slots` 只允许作为 `DynArray` / `Queue` 的字段策略。需要 key 覆盖、key/value 关联、任意
  特定 key、非默认 bins、cross 或不同的容器语义时，用户必须使用显式 `@covergroup`；自动 `cov`
  不推测这些结构。
- 新的功能覆盖率声明进入 `CoverageIR` 和独立的 coverage declaration document；采样数据和
  hit counts 绝不进入该 document、对象 source schema 或 encoding fingerprint。
- 2.0 之前，若新 DSL 无法生成语义等价 SV，应在生成期明确失败；不能悄悄退化成仅 Python
  或仅 SV 行为。

## 3. 联合契约：声明、IR、数据库、后端

```text
Python coverage declaration
          │
          ▼
     CoverageIR ──────► coverage declaration document / coverage digest
       │      │                         │
       │      ├──► Python evaluator ────┼──► CoverageDatabase / report / merge
       │      │                         │
       │      ├──► SV covergroup code ──┼──► simulator coverage / UCIS export
       │      │                         │
       │      └──► UCIS XML adapter ────┘
       ▼
 deterministic semantic conformance vectors
 codec-synced dual-sample runs (Python DB ↔ target named bins)
```

`CoverageIR` 是唯一的声明语义来源。**覆盖组类型 ID**（`covergroup_type_id`）是 canonical
`sample_type` 与 covergroup 声明名的规范化组合：它标识“同一个 embedded covergroup 声明槽位”，
供数据库分组、报告和跨版本冲突诊断使用；它不随 bins 或表达式等语义演进改变。**声明语义摘要**
（`declaration_semantic_digest`）标识该槽位的某一个完整模板定义，并是跨数据库严格 merge 的兼容
判据。它不包含某个覆盖组实例的 `CoverInput` actual、`CoverRef` actual 或**实例 bin 布局**；这些属于
实例数据。特别地，`CrossQueueType` 的函数定义、形参和调用形属于声明模板；依 binding 求得的
concrete tuple queue 只属于实例 bin 布局，绝不进入声明语义摘要。`CoverageIR` 至少保存覆盖组类型 ID、sample 类型、采样
表达式、条件、bins（含 transition bin 定义）、bin 分类、定长数组槽形状、容器值域 point
形状、cross 成员、covergroup 构造参数签名、嵌套 `sample` 签名、
权重、目标和 instance policy。来源位置属于独立的 provenance：它用于
诊断、报告和 source diff，但**不得**参与声明语义摘要、覆盖组类型 ID、bin ID
或 merge 判定。这样仅移动/重排源码而不改变覆盖语义时，既有 coverage 数据库仍可合并。

本文的身份术语固定如下：

| 英文名 | 中文名 | 含义 |
|---|---|---|
| `covergroup_type_id` | **覆盖组类型 ID** | `sample_type + covergroup` 声明名的稳定身份；同一声明槽位在语义演进前后仍使用它分组与诊断。 |
| `declaration_semantic_digest` | **声明语义摘要** | 某一完整 coverage 模板定义的规范化摘要；用于判断跨数据库 merge 是否兼容。 |
| `CoverGroupInstance` | **覆盖组实例** | 宿主类内部 embedded covergroup 成员的一次 `new(...)` 结果；保存 binding、采样状态和 transition 历史。 |
| `instance_layout_digest` | **实例布局摘要** | 由影响 bins/option 的已绑定 `CoverInput` actual 与 concrete `CrossQueueType` queue 规范化得到；只用于同一逻辑实例的跨 run 布局兼容性校验，不是声明身份或类型级 merge 键。 |
| `logical_instance_key` | **逻辑实例键** | 为跨 run per-instance database merge 提供的稳定对齐键；它独立于 SV `option.name` / `set_inst_name()` 的报告名称。 |
| `instance_id` | **数据库实例标识** | 覆盖率数据库中 per-instance record 的内部唯一标识；它是实现细节，不是用户可配置的名称或键。 |

由此固定下列方向性原则，后续 API、IR、数据库和 SV 生成均不得改变其边界：

1. **声明、类型和实例分层。** `@covergroup` 的受限 AST 定义声明；覆盖组类型 ID 标识该声明槽位；
   覆盖组实例是宿主对象在 `new()` 中构造出的运行时成员。不能把声明函数、覆盖组类型或覆盖组实例
   互称为“覆盖组”。
2. **模板语义与实例绑定分离。** 声明语义摘要只描述模板；`CoverInput` / `CoverRef` actual、实例 bin
   布局、命中与 transition 历史都是实例数据。不同 input actual 不会创造新的覆盖组类型或声明版本。
   同一 `logical_instance_key` 的跨 run per-instance merge 仍必须要求相同的实例布局摘要，不能因模板
   摘要相同而把不同 bin 布局的命中相加。
3. **语言身份、数据库对齐和报告显示分离。** SV 的实例身份来自宿主对象与 embedded 成员；
   `logical_instance_key` 只是 SvTypes 为跨 run 的 per-instance merge 增加的数据库对齐键；
   `option.name` / `set_inst_name()` 与 `option.comment` 只是可变的 SV 报告元数据。三者不得相互推导。
4. **声明驱动，数据不反向定义语义。** `CoverageIR` 及其声明语义定义快照是唯一语义来源；数据库只
   累积结果，provenance 和 renderer/报告标签只服务诊断与观测，不改变声明兼容性。
5. **遵循 SV 的计算边界。** 类型覆盖率（type coverage）与实例覆盖率（instance coverage）、`merge_instances` 与
   `per_instance` 分别按 SV 语义处理；只有已明确记录的 SvTypes 取舍（例如
   `cross_retain_auto_bins=0`）可以偏离，且必须由 Python/target 对拍证明。

语义部分进入独立、可序列化的 **coverage declaration document**；它以覆盖组类型 ID
和 canonical `sample_type` 引用对象类型，但不是该类型的对象 source schema，也不参与对象的
source identity、encoding schema 或数据编解码 fingerprint。一个对象类型可以没有、拥有一个或
拥有多个 coverage declaration document；增删或修改 covergroup 必须只改变声明语义摘要，
绝不改变该对象类型的 source-schema digest。每一个持久化数据库还必须保存可规范化、可校验的
CoverageIR 的**声明语义定义快照**（或等价的不可变 definition blob）；快照不含
provenance。只保存 digest 不足以在没有原始源码时恢复 bin 名、生成报告、导出 UCIS 或解释 merge
差异。

数据库只记录运行时数据：覆盖组类型定义引用、覆盖组实例、sample 数、bin hit count、
illegal hit、有限用例来源、waiver/exclusion 和 merge history。数据库不可反向定义语义。
每次 sample 的取值明细不进入覆盖率库；可选 sample 日志使用独立的分帧二进制并压缩。

## 4. 2.0 承诺语义面

在 DSL 语法评审结束时，2.0 必须支持下列最小闭环：

| 构造 | 2.0 承诺 |
|---|---|
| covergroup | 在 `SvObject` 子类内以 `@covergroup` 实例函数显式定义；函数名是稳定的覆盖组声明名及同名 embedded 成员名。可用 `CoverInput[T]` 声明构造 `CoverGroupInstance` 时绑定的 input 参数，并以可选的嵌套 `def sample(...)` 声明每次采样的输入；覆盖组实例显式 `.sample(...)` |
| coverpoint | 对 scalar/enum/合法派生表达式采样；命中规则与 SV 一致：ignore 优先丢弃，illegal 另计，重叠的 normal bins 全部加 hit，`default` 只吃未声明残余值；X/Z 默认不进 2-state bin 也不进 default，除非 bin 显式包含 X/Z |
| bins | singleton、闭区间 range、值集合、default、固定数量 array bins 与未定数量 array bins；bin 名唯一且稳定。没有 normal bins 时按 `CovPointOption.auto_bin_max` 创建确定性 SV-style automatic bins |
| bin 分类 | normal、ignore、illegal；illegal 保留计数并使报告明确失败，不丢 sample |
| 条件 | `iff` 在 point/cross 的采样时刻决定是否计入 |
| 槽位覆盖率数组 | 用 `class cov_a(CovPointArray, source=self.a, length=4):` 作定长声明；对定长数组、动态数组、队列均合法，并在 IR 中展开为 `cov_a[0]` 至 `cov_a[3]`。`sample` 时 `i >= size()` 的槽跳过：不加 hit、不创新 bin/实例。多出的元素不在该声明内。不支持 transition |
| 容器值域 coverpoint | `class cov_a_values(CovPoint, source=self.a):` 中，若 `self.a` 的静态类型是数组、动态数组或队列，则把当前存在的每个元素值打入同一 point。不支持 transition。packed 向量的同一写法仍采整个积分值，与容器值域不是同一构造 |
| transition | 仅标量/非数组式 coverpoint：有界定长序列（长度 2..K，**默认 K=8，硬上限 16**）及有上界的连续重复 `[*m:n]`（n 计入 K）。禁止 `[->]`、`[=]`、无上界 `[*]`、transition 再 cross |
| cross | coverpoint 的笛卡尔组合；固定下标只能引用已声明 `CovPointArray` 的槽位（如 `cov_data[0]`），不引入匿名 point。容器整体值域 point 不可作为成员，声明期报错；可用显式 tuple bins，或 SV 同形的 `CrossQueueType` 函数计算 normal/ignore bin；禁止无上限隐式爆炸 |
| 度量 | bin hit、point/group coverage、weight、goal、`at_least`；零权重不进入 group 聚合 |
| 生命周期 | 显式手动 sample；不把构造、序列化或 randomize 隐式变成 coverage sample |

数组式声明包括槽位覆盖率数组和容器值域 coverpoint。二者在声明期若带 transition 必须报错。
2.0 另拒绝或延后：wildcard/complex set bins、动态长度覆盖率
数组、自动 `coverpoint a.size()`、实时事件触发、cover property 和 code coverage。每项拒绝
都必须给出明确的声明期错误，而非产生 Python/SV 不同语义。

## 5. DSL 与数据库设计约束

本节定义实现与验证的约束。原标为“1.7 冻结”的条目现已在本节冻结；其 target 微型 fixture
是实现发布门槛，必须逐项证明下述已定语义，不得再把 fixture 结果用来改变公开语义。仅
§5.12 的 transition 历史推进保留为 1.8 的 target 对照与文档更新事项，不是 1.7 冻结门槛。

1. **声明载体（1.7 已冻结）**：覆盖率只可声明在 `SvObject` 子类内部。`@covergroup`
   修饰一个实例函数；函数名是覆盖组的稳定声明名及同名 embedded 成员名。编译器只解析该函数的受限 declaration AST，
   **绝不执行函数体或其中任意用户代码**；其中的 `self.opcode` 由解析器构造为 typed sample path，
   不需要 `field("opcode")`、字符串路径或 Python callback。函数体只能包含 `option`、
   `type_option`、`CovPoint`、`CovPointArray`、`Cross` 声明类，以及至多一个特殊的嵌套
   `def sample(...)`；控制流、赋值给外部状态、任意调用、动态 class base/名称和不能映射到声明语法
   的 AST 均为生成期错误。`CovPoint`、`CovPointArray` 和
   `Cross` 是唯一的声明基类，而非修饰符：类名
   分别是 point/cross 的稳定公开名称；类属性声明其语义。最小形式如下：

   ```python
   @covergroup
   def cg(self, max_opcode: CoverInput[U8]):
       class option(CoverGroupOption):
           per_instance = 1
           weight = 1
           goal = 100

       class type_option(CoverGroupTypeOption):
           merge_instances = 1

       def sample(opcode: U8, mode: Bit, valid: Bit) -> None:
           class opcode_cp(CovPoint, source=opcode, iff=valid):
               class option(CovPointOption):
                   at_least = 2
                   weight = 1
                   goal = 100

               common = bins[0, 2, 8:15]
               byte = bins[0:255].split(16)
               rise = transition_bins[0, 1]
               burst = transition_bins[0, repeat(1, 2, 4), 2]
               reserved = illegal_bins[3]
               masked = ignore_bins[4]
               other = default_bins

           class mode_cp(CovPoint, source=mode):
               read = bins[0]
               write = bins[1]

           class opcode_mode(Cross, members=(opcode_cp, mode_cp)):
               class option(CrossOption):
                   at_least = 1
                   weight = 1
                   goal = 100
                   cross_retain_auto_bins = 0

               common_read = bins[opcode_cp.common, mode_cp.read]
               byte0_write = ignore_bins[opcode_cp.byte[0], mode_cp.write]
   ```

   类内 `@covergroup` 是 SV embedded covergroup declaration：它隐式声明一个同名的受管理覆盖组成员。
   Python 中该成员提供特殊的 `.instantiate(*actuals, **named_actuals)` 接口，且只能在宿主 `__init__` 内调用
   一次；它映射到 SV 的 `name = new(...)`，完成该 embedded 成员的实例化与 input actual 绑定，
   而不是返回、替换或暴露一个可由类外构造的普通 Python 对象。SvTypes 不提供外部 `obj.cg(...)`
   factory，也不允许类外直接构造该覆盖组。`CoverInput[T]` 对应该 `.instantiate(...)` / SV `new(...)` 的
   `input` formal，actual 可来自宿主 `__init__` 的参数或宿主的静态成员；它在覆盖组构造时取值并固定，
   可用于 bins、`iff` 和 instance `option`。`CoverRef[T]` 对应 `ref` formal，但只接受宿主的静态、
   singular `TypeBase` 成员作为 actual；不接受宿主 `new()` 参数、值快照、lambda、getter、任意
   Python 对象成员、动态对象或动态容器元素。`ref` 指向的成员在每次 `.sample()` 时读取当前值。

   `CoverInput` actual 改变可改变某个 instance 的 bins/option，但不产生新的 covergroup type 或新的
   声明语义摘要。它形成该覆盖组实例的**实例 bin 布局**：`merge_instances=0` 时类型覆盖率按实例
   覆盖率的权重汇总；`merge_instances=1` 时按 LRM 的 bin 名并集汇总，重叠
   同名 bin 的 hit 累加。`type_option` 是 covergroup type 的静态属性，只能取声明期常量，禁止引用
   `CoverInput`，并在 `.instantiate(...)` 后冻结。嵌套 `sample` 对应 SV 的 `with function sample(input ...)`：只能有输入形参、不得有
   执行语句或返回值，其形参在声明类头与类体中是带类型的 sample value。无嵌套 `sample` 时，point/cross
   声明可直接引用 `self` 字段、`CoverInput` 与 `CoverRef`；覆盖组的 `.sample()` 没有业务值形参。

   `.instantiate(...)` 的 positional/named actual 必须与 `CoverInput` formal 按声明顺序和名称逐项匹配；
   缺失、重复、未知或类型不匹配均在宿主构造时失败。没有 `CoverInput` 的覆盖组只允许零参数
   `.instantiate()`。每个 embedded 覆盖组可以完全不实例化；此时它没有覆盖组实例、不会采样、不会
   产生数据库记录，且对它调用 `sample()`、coverage 查询或控制方法必须报明确的未实例化错误，不能
   自动零参数实例化。`CoverRef` 仍只能由声明引用宿主静态成员，不作为 `.instantiate(...)` actual 传入。

   存在嵌套 `sample` 时，所有 `CovPoint`、`CovPointArray` 与 `Cross` 声明必须全部位于该 `sample`
   函数体内；覆盖组外层只允许 `option`、`type_option`、嵌套 `sample` 与声明期辅助定义。没有嵌套
   `sample` 时，所有 point/cross 声明位于覆盖组外层。两种形态不得混用，以避免同一覆盖组拥有两套
   sample 域或不明确的 source 解析规则。

   **覆盖组内置方法：** `sample(...)`、`get_coverage()`、`get_inst_coverage()`、`start()`、`stop()` 与
   `set_inst_name(name)` 按 SV 内置 covergroup 方法提供。它们不是 SvTypes wrapper 的特权方法：在符合
   宿主类访问控制的任何位置，持有 embedded covergroup instance 的代码均可调用。`get_coverage()` 与
   `get_inst_coverage()` 返回 SV 同形的 `float` 百分比；`set_inst_name()` 具有 SV 的实例显示/报告命名
   语义，不被 SvTypes 限制为首次 sample 前调用。实例化后仅 `instance.option.name`、
   `instance.option.comment` 和 `set_inst_name()` 可修改；它们都是当前报告元数据，既不改变已采样
   hit，也不改变该实例的语言身份或 `logical_instance_key`。

   `source`、`iff`，以及 `CovPointArray` 的 `length` 必须作为声明类的关键字基类参数给出，不能
   写成类属性；`source` 是 scalar/enum/合法派生的 CoverageExpr、`CoverRef`，或数组、动态数组、队列的直接字段
   路径；后一种是容器值域 point，逐一采样当前存在的元素。packed vector 不属于容器值域，仍是
   一个 integral point。`CovPointArray` 的 `source` 必须是数组、动态数组或队列的直接字段路径，且
   `length` 是正的声明期整数。它为每个槽产生 §5.7 规定的 point node。`Cross.members` 也必须作为
   声明类的关键字基类参数给出，是有序 point reference：可引用 `CovPoint`、明确的
   `CovPointArray` 槽位 `array_name[index]`。数组 base name 与容器值域 point 都不能作为 cross
   成员。point 类体中 `name = bins[...]`、`name = ignore_bins[...]`、
   `name = illegal_bins[...]` 和 `name = default_bins` 分别声明 normal、ignore、illegal 和
   default bin；`default_bins` 每个 point 至多一次且不接受下标。cross 类体中同样以
   `name = bins[point.bin, ...]` 声明一个具名的静态 normal tuple、以
   `name = ignore_bins[point.bin, ...]` 声明一个静态 ignore tuple；由声明基类决定这两个
   sentinel 的 point/cross 语义，不能引入 `cross_bins` 或 `cross_ignore_bins` 专用拼写。二者的 selector 必须
   与 `members` 同序且逐项引用该成员的 normal 或 `default` bin。`iff` 是 `CovPoint`、
   `CovPointArray` 和 `Cross` 的关键字基类参数。每个 point/cross 最多一个嵌套
   `class option(CovPointOption)` 或 `class option(CrossOption)`；covergroup 可有一个
   `class option(CoverGroupOption)` 与一个 `class type_option(CoverGroupTypeOption)`。这些都是实际的
   声明成员，带有可供 LSP 检查的明确 option 类型；不使用裸 `option.xxx`、模块级共享 sentinel 或
   元类注入名称。各 option 类体只能为其允许字段赋声明期常量，或（仅 instance `option`）赋
   `CoverInput` 构成的受限表达式，不能定义方法。`at_least`、`weight` 和
   `goal` 分别对应 §4、§5.11、§5.14。公开 option 集合和性能界线如下；表中“冻结/报告期”指不在
   每次 sample 的 value-to-bin 热路径上增加循环、分配或反射。

   | 声明类 | 2.0 支持字段及缺省值 | 生效期与性能含义 |
   |---|---|---|
| `CoverGroupOption` | `name=None`（缺省使用 SvTypes 生成的隐式层次报告名）、`comment=""`、`per_instance=0`、`get_inst_coverage=0`、`weight=1`、`goal=100`、`at_least=1`、`auto_bin_max=64`、`detect_overlap=0`、`cross_num_print_missing=0` | `name` 与 `comment` 是仅有的运行期 instance option：可在任意 sample 前后更新，`set_inst_name()` 更新同一当前报告名称；它们绝不充当逻辑实例键。其余字段在 `.instantiate(...)` 时冻结；不进入每次 sample 的热路径。 |
   | `CovPointOption` | `comment=""`、`weight=1`、`goal=100`、`at_least=1`、`auto_bin_max=64`、`detect_overlap=0` | 均在 freeze 或汇总期生效；不改变单次 sample 的表达式求值次数。 |
   | `CrossOption` | `comment=""`、`weight=1`、`goal=100`、`at_least=1`、`cross_num_print_missing=0`、`cross_retain_auto_bins=0` | `cross_retain_auto_bins` 是唯一有意偏离 SV 的缺省值（SV 默认保留自动 cross bins）；其余字段的缺省值与 SV 一致。它只改变 freeze 的存活 tuple universe；其余只影响汇总或报告。 |
| `CoverGroupTypeOption` | `comment=""`、`weight=1`、`goal=100`、`merge_instances=0` | 类型级元数据/汇总策略；只允许声明期常量并在 `.instantiate(...)` 时冻结。`merge_instances` 只改变类型覆盖率汇总，不改变每次 sample 的命中规则。 |

   **option 生命周期：** 除 `CoverGroupOption.name`、`CoverGroupOption.comment` 与
   `set_inst_name()` 外，所有 `option` / `type_option` 均为声明期或 `.instantiate(...)` 期配置；
   实例化后的赋值必须报 `SVT-COV-OPTION-FROZEN`。`name` 与 `set_inst_name()` 写入同一当前报告名称，
   后一次写入覆盖前一次；`comment` 写入当前报告注释。两者可在任意 `.sample()` 前后修改，数据库导出
   时只保存当前名称/注释快照，不保存变更历史；先前和后续 hit 始终归属于同一个覆盖组实例。

   **明确不支持的 option / 设置方式：**

   - SV 的 `option.strobe` 与 `type_option.strobe`：2.0 没有 event-driven sampling 或 time-slot
     collapse；手动 `.sample()` 不应伪造 postponed-region 语义。
   - `option.distribute_first`：它只服务于 generic `with` bin expression；该 expression 不在 2.0
     的 source-only DSL 中。
   - `cross_auto_bin_max`（若目标工具提供）：2.0 以 §5.5 的固定成员/存活 tuple 上限和
     `cross_retain_auto_bins` 明确 bin universe，不能再引入会隐式截断或改变 cross 自动 bin 集合的
     第二个上限。
   - `illegal_bins_error` 及其他 simulator 私有 option：illegal hit 一律作为可报告数据库事实，
     失败策略由 SvTypes test/report policy 决定，不绑定某一仿真器的 fatal/warning 开关。
   - **泛化过程式 option 设置：**不支持。IEEE 1800 允许一部分实例/类型 option 在运行期赋值，
     但这会要求为不同的 option 维持不同的重算与数据库保留策略；配置 target 的观测为
     `at_least` 接受实例化后赋值却不使其生效、且不支持 `type_option` 的 scope-resolution
     访问。2.0 只保留上文定义的 `name`、`comment`、`set_inst_name()` 报告元数据更新；其他
     实例化后赋值一律报 `SVT-COV-OPTION-FROZEN`，不得静默接受。

   所有未列出的字段同样不是 2.0 DSL。字段的默认值、合法声明层级和覆盖率公式在实现前由
   Python/target fixture 锁定；未知字段必须在 freeze 失败，不能静默忽略。`CoverGroupOption.auto_bin_max` 是 group 的缺省值，
   `CovPointOption.auto_bin_max` 可逐 point 覆盖；有效值为正的声明期整数，未写时为 SV 的 `64`，
   只在 point 未声明 normal/default/transition bins 时生效。freeze 按 LRM 计算
   automatic bins：enum 每个枚举值一个；其他 integral point 创建
   `min(2^M, auto_bin_max)` 个 2-state bins，按值域顺序均匀分配，不能整除的剩余值进入最后一个
   bin；含 X/Z 的 sample 不进入 automatic bins。canonical 名为 SV 形式 `auto[value]` 或
   `auto[low:high]`，结果进入 IR/digest；renderer 发射同一确定性分割，不能依赖 simulator 默认。
   `ignore_bins` / `illegal_bins` 不阻止 automatic bins，普通 normal、`default_bins` 或
   `transition_bins` 则阻止它。`cross_retain_auto_bins` 是 `CrossOption` 特有的 `0` 或 `1`
   字段，缺省为 `0`，语义见 §5.5，其值进入 CoverageIR 和声明语义摘要。`name = transition_bins[...]` 是标量
   `CovPoint` 唯一的 transition 声明语法。连续重复固定写作 `repeat(term, m, n)`，表示 SV
   `term[*m:n]`；`repeat` 是 declaration sentinel，不是可在 CoverageExpr 中调用的 Python
   function。`term` 使用该 point 的 §5.13 比较域；`m`、`n` 为声明期整数且 `0 <= m <= n`。
   freeze 按重复次数从 `m` 到 `n` 展开并以该顺序写入同一 transition bin 的 semantic definition；
   每条展开序列的总长度都必须在 2..K 内，否则为 freeze 错误。无界重复、`[->]`、`[=]` 或任何
   不是 `repeat(term, m, n)` 的重复表示均为 freeze 错误。`CovPointArray`、容器值域 point 和
   `Cross` 上出现 `transition_bins` 均为 freeze 错误。bin 引用不能直接作为 cross 成员。point 和
   array base name 不得包含 `[` 或 `]`，并且不得重名；bin 名只须在所属 point/cross 内唯一。
   所有名称不得由对象地址、声明行号或隐式别名补全。

   Cross 还可在类体内定义 SV 同形的 `CrossQueueType` 函数，并在 `bins[...]` 或
   `ignore_bins[...]` 中调用它，例如：

   ```python
   class lane_mode(Cross, members=(lane_cp, mode_cp)):
       def make_matching(limit: int) -> CrossQueueType:
           result = CrossQueueType()
           for i in range(limit):
               result.push_back((i, i))
           return result

       matching = bins[make_matching(max_pairs)]
   ```

   函数返回一个与 cross member 顺序一致的 concrete value tuple queue；上例中的 `matching` 是
   **一个**具名 cross bin，queue 中每个 tuple 都属于该 bin，完全对应
   `bins matching = make_matching(max_pairs);` 的 SV 语义，而不是数组 bins 或每 tuple 一个 bin。
   `ignore_bins[make_ignored(...)]` 同理。它不是 Python callback：freeze 解析函数 AST，覆盖组实例
   binding 时由 SvTypes 的受限解释器求值，**绝不调用该 Python function object**。函数必须具有
   返回 `CrossQueueType` 的显式注解；只允许以下语法：局部标量变量的声明/赋值、
   `result = CrossQueueType()`、`result.push_back((value_0, ..., value_n))`、`return result`、
   declaration-time integer / `CoverInput`、`range()` 的有界 `for`，以及 `if`/`elif`/`else`。
   局部变量、循环边界、分支条件、函数调用实参和每个 `push_back` tuple 项均可使用 §5.2 的**完整
   CoverageExpr 运算符子集**；CrossQueueType 不另设更窄的 operator 白名单。每个 `push_back` tuple
   的 arity 必须等于 `members`，并逐项在相应 point 的比较域内。
   禁止 sample 形参、BinRef、`self` 的运行时字段、任意其他函数/方法调用、closure、列表/字典/推导式、
   `while`、递归、异常处理、I/O、随机/时间和外部状态写入。函数及其调用在覆盖组实例构造/绑定时求值，
   不能依赖每次 `.sample()` 的输入。每次计算必须在声明冻结/实例绑定期有限展开，queue 的
   tuple 个数计入独立的实例化资源预算；它不按 tuple 数消耗 §5.5 的 normal cross-bin
   限额。声明 IR 只保存函数 AST/调用形，实例 bin 布局保存已产生的 concrete tuple queue 与其 bin 名；SV renderer 发射同形的
   `function CrossQueueType ...` 和 `bins name = function(...);` / `ignore_bins name = function(...);`，
   并以 target conformance fixture 验证 Python 与 SV 对同一 binding 的 bin 内容和 coverage 相等。

   **当前目标能力门：** 配置的 SystemVerilog conformance target 不能识别 `CrossQueueType`，因而
   不能接受 IEEE 1800-2023 的上述函数形式。Python runtime 仍执行受限解释器；请求该 target 时
   必须以 `SVT-COV-SV-BACKEND` 在生成期失败，绝不能发射无效或退化的 SV。待配置支持此 LRM 构造的
   target 后，再解除该 gate 并完成本段规定的 Python/SV queue 对拍；
   这不改变 Python DSL、IR 或实例布局语义。

   在 declaration AST 中，`CovPointArray[index]` 只接受一个非负的 Python `int`，并规范化为
   不可执行的 `SlotRef`；它只能出现在 `Cross.members`，其 index 必须小于该 array 的 `length`。
   所以 `members = (cov_a[0], mode)` 是定义期引用，不读取 sample、不会生成 Python 容器访问；
   负数、切片、动态值、函数外使用或其他上下文使用都是 freeze 错误。

   `bins[...]` 的 array form 使用 `.split()`：`name = bins[range_or_values].split()` 对应 SV
   `bins name[] = {...}`，为 range/value list 中每个**具体 2-state 值**创建一个 bin，名称使用该
   值而非顺序下标：`name[value]`。它不读取 `auto_bin_max`。例如
   `byte = bins[1, 3:5, 9].split()` 产生五个 singleton bins：`byte[1]`、`byte[3]`、`byte[4]`、
   `byte[5]`、`byte[9]`。重复值会导致重复 bin identity，SvTypes 在 freeze 期报错而不静默合并。
   无参 `.split()` 具有固定的 SvTypes DSL 缺省值，等同于 `.split(max_bins=64)`；它不是可变全局
   设置。需要不同限制时写出 `name = bins[range_or_values].split(max_bins=N)`，其中 `N` 为正的
   声明期整数；需要严格无上限的 SV `bins name[]` 语义时显式写出 `split(max_bins=None)`。
   若具体值数不超过有效 `N`，仍按上述 `bins name[]` 语义展开；若超过 `N`，SvTypes 将其确定性地
   改写为等价值域上的 `bins name[N] = {...}`，并按下述 fixed-array 规则分配所有值。因而不会截断
   覆盖值域，而是将 singleton bins 压缩为 `N` 个 bins；Python IR、数据库与生成 SV 都使用压缩后
   的同一组 bins。压缩后的 bin 名为 `name[0]` 至 `name[N-1]`，不再沿用原本的 `name[value]`。
   `name = bins[range_or_values].split(count)` 对应 SV `bins name[count] = {...}`：`count` 为正的
   声明期整数，指定值按 source value-list 顺序均匀分配给 `count` 个 bins；不能整除时，剩余值全部
   进入最后一个 bin；`count` 大于值数时允许出现 empty bins，按 SV 覆盖率规则处理。有效
   `max_bins`（包括缺省 `64` 或显式 `None`）与实际展开/压缩后的 bins 都参与 IR/digest；同一
   source declaration 不会因环境全局配置而改变声明语义。`name` 本身不是 bin，
   cross 对未定数量 array 使用 `point.name[value]`、对固定数量 array 使用 `point.name[index]` 引用
   一个子 bin。分割数、端点、值顺序和每个展开集合进入 IR、
   bin identity 和 digest。

   `@covergroup` 产生的 declaration 的 `freeze()` 是唯一编译入口：它以确定性 pass 从受限 AST
   捕获每个节点的调用点为 provenance，解析 source、bins 和 members，生成 `CoverageIR`、声明语义摘要
   及 SV/观测 manifest。bound 覆盖组实例只能采已 freeze 的 declaration；若有嵌套 `sample`，其
   `.sample()` 业务实参与该签名逐项类型匹配，否则 `.sample()` 不接受业务值形参。`case_id` 是
   可选的用例来源元数据；报告名称由 `option.name` / `set_inst_name()` 管理。重复 freeze 得到字节相同的
   语义 IR；point、
   bin、cross 在 IR 中按显式名称的 Unicode code-point 顺序规范化，唯有 cross `members` 的用户
   顺序保留并参与语义。声明类可使用由其基类明确提供的受控接口；任意普通方法不会成为隐式
   采样、变换或 callback 逻辑。source file、line、column、声明顺序、Python class/module 名和
   renderer 名均只进 provenance 或 manifest，绝不进覆盖组类型 ID、bin ID 或声明语义摘要。缺失名称、空
   bins、重复名称、未解析成员、非法 sample 形参或不能发射等价 SV 的声明都在 freeze 时失败。
   「仅在 `SvObject` 子类内声明」是 2.0 有意的范围限制：不支持模块级/测试侧独立 declaration。
   sample 值由声明的 typed formals 或绑定 `self` 字段提供；需要任意 Python callback、运行时 wrapper
   或隐式类型转换的场景留待后续版本，不能绕过 declaration AST。
2. **表达式子集（1.7 已冻结）**：coverage 使用独立的 `CoverageExpr` typed AST；它可复用
   `ConstraintIR` 的节点实现，但没有求解、随机化或 constraint enable 语义。叶子仅为 `self` 的
   静态字段路径、嵌套 `sample` 的具名形参、`CoverInput` / `CoverRef` 绑定或显式类型的 integral/enum/4-state literal；允许静态 packed bit/part select、
   一元 `+ - ~ not`、二元算术 `+ - * / % **`、位运算 `& | ^ << >>`、比较
   `< <= == != >= >`、逻辑 `and or`、条件表达式 `a if cond else b`，以及显式
   `cast(target_type, expr)`。Python AST 的运算符由 typed IR 规范化为对应 SV 运算符，宽度与
   signedness 不得依赖 Python 整数语义。条件 `iff` 的结果必须是 1-bit 2-state boolean；point 值可以是
   integral、enum 或 packed 4-state 值。所有运算的宽度、signedness、枚举底层类型和 cast 都在
   AST 中显式；除同型运算外不作隐式扩宽、截断或 enum 转换。禁止调用、方法/属性动态查找、
   `size()` / `.num()`、动态索引、切片边界、数组/队列归约、字符串/real、赋值、随机状态、时间
   和任意 Python callback。`iff` 也可写作零参数 lambda，例如
   `iff=lambda: (valid == 1) and (mode != 3)`；freeze 只解析其函数体表达式并转换为相同的
   `CoverageExpr`，绝不调用 lambda。lambda 不得捕获任意外部状态、调用函数或包含超出本子集的
   节点。容器值域 point 仅可写成直接字段路径，槽位 point 仅可由其专用的
   定长槽声明产生；二者不是一般表达式。每个节点必须有同一规范化 IR 的 Python evaluator 和
   SV renderer；任一方不支持即 freeze 失败，而非回退。
3. **bin ID**：bin ID 必须由覆盖组类型 ID、coverpoint ID、显式/规范化 bin 定义组成，
   不得由 Python object address、声明顺序、任何运行时状态或 simulator bin ID 决定。
4. **instance identity**：SV 的 embedded covergroup instance 身份来自其宿主对象与 covergroup
   成员；`option.name` / `set_inst_name()` 是可修改的报告名称，不是该 instance 的语言身份。
   SvTypes 的 `logical_instance_key` 是为跨 run per-instance database merge 追加的稳定对齐键，
   不得由 `id(obj)`、SV handle 文本、随机对象编号或可修改的 instance name 推导。其实际绑定/注册
   机制须独立于 SV report name，并在 1.8 实现前冻结；无法提供稳定 key 的 instance 可在单 run
   参与 type coverage，但不得参与跨库 per-instance merge 或 Python/SV per-instance parity。
   `CoverGroupOption.per_instance` 见 §5.14，与 SV instance 身份规则独立。
5. **cross 成员与自动 bin 保留（1.7 已冻结）**：一个 cross 最多 8 个成员；一个 cross 最多
   **65,536** 个 normal cross bin；同一 covergroup type 最多 **1,048,576** 个 normal cross bin。
   freeze 先以成员可计分 bin（normal 加 `default`）的有限笛卡尔积建立候选 tuple，再应用以下规则。
   三项上限均在 Python 与 SV renderer 前检查，超限必须以 `SVT-COV-CROSS-LIMIT` 失败；不得静默
   截断、删减或改变 bin 集合。该资源保护边界不是 SV 语义上限：target 已验证可生成并报告
   1,048,576 个自动 cross bin，但 SvTypes 仍须为 Python core、数据库与生成后端提供确定的预算。

   - 没有 cross 内的 `bins[...]` 时，完整候选积为自动 normal bins；`CrossOption.cross_retain_auto_bins` 不改变此
     情形。
   - 有 cross 内的 `bins[...]` 时，它们是具名 exact tuple。`CrossOption.cross_retain_auto_bins = 0`（SvTypes
     缺省）时，只有这些具名 tuple 可以成为 normal cross bins；`= 1` 时，未被具名 tuple 覆盖的
     候选 tuple 也作为自动 normal bins 保留，遵循 SystemVerilog 的默认保留逻辑。
   - cross 内的 `ignore_bins[...]` 从以上 normal 候选中移除 exact tuple；它只能按成员的 normal bin 或
     `default` bin 组成，不能读取 sample 值或调用函数。引用 point 的 ignore 或 illegal bin 的
     cross selector 不属于候选宇宙，必须为 freeze 错误。ignore 优先于具名/自动 normal bin。

   `CrossQueueType` 返回的 queue 不等同于成员 bin 名的笛卡尔候选。每个以 queue 声明的具名
   `bins[...]` 或 `ignore_bins[...]` 是**一个** cross bin；queue 中的每个 concrete value tuple 只是
   该 bin 的成员资格，不会各自创造 cross bin。其长度受
   实例化资源预算限制：每个 queue 至多 **65,536** 个 tuple，超出以
   `SVT-COV-CROSS-QUEUE-LIMIT` 失败；该固定上限不允许环境覆盖或静默截断。完成成员 point 的 `iff`、ignore/illegal
   分类后，queue normal bin 按归一化 sample 值判断命中，并可与普通 normal tuple bin 重叠而全部命中；
   queue ignore bin 优先，命中时按 §5.9 跳过所有 normal cross bin。queue bin 的名字、函数调用形和
   实例化后 tuple 内容分别进入声明模板或实例 bin 布局；后者不进入声明语义摘要。

   自动 bin 的 semantic ID 由覆盖组类型 ID、cross 显式名称和有序 `(member_point_id,
   member_bin_id)` tuple 规范化导出；其 SV/observation 可见名称由 manifest 映射，不是用户
   API 或 merge 键。重复 selector、同名 cross bin、同一 tuple 被多个具名 normal cross bin
   选中，或 normal/ignore selector 的成员、顺序、arity 不匹配，均为 freeze 错误。正常 cross bin 的
   重叠不因其来源是静态 selector 还是 `CrossQueueType` 而改变：均按 SV 的正常 bin 规则分别命中并
   分别计数。生成 SV 时，
   renderer 必须显式实现上述 `cross_retain_auto_bins` 语义：目标 SystemVerilog 支持该 option 时
   发射对应 option；否则以确定性显式 `ignore_bins`/normal-bin 展开得到相同的存活集合。任一策略
   不能由目标等价表达时，freeze 失败，不得回退为目标的默认自动保留行为。
   完成筛选后每一个存活 tuple 都是一个具名或 manifest 映射的确定性 cross bin，集合、成员顺序、
   `cross_retain_auto_bins` 和 selector 结果进入声明语义摘要；用户必须以 cross 内 `bins[...]` 和
   `cross_retain_auto_bins` 明确控制集合，不能依赖运行时未命中绕过限额。
6. **非法/豁免语义**：illegal hit 是数据库事实；waiver/exclusion 是单独、可审计的元数据，
   不能删除原始 hit。illegal 对 coverage 分子/分母的影响完全遵循 §5.14 的 LRM/target 对照结果。
7. **数组式覆盖率**：覆盖率形状一律定长。动态数组/队列只提供采样存在性：采不到的下标
   跳过，不扩展覆盖率数组。`class cov_a(CovPointArray, source=self.a, length=4):` 在 freeze 时
   展开为四个独立、可报告的 canonical point node：`cov_a[0]`、`cov_a[1]`、`cov_a[2]` 和
   `cov_a[3]`；每个 node 有自己的 point ID、由其继承并以该 point ID 区分的 bin ID、`iff`、
   `at_least`、weight 和 goal。`cov_a` 仅是 logical array declaration name，不是可命中的 point
   或 cross 成员。`Cross.members` 必须逐槽写出 `cov_a[i]`（`0 <= i < 4`）；引用 base name、越界
   槽或容器值域 point 均在 freeze 时失败。固定下标只能通过 `CovPointArray` 的已声明槽位引用，
   不引入匿名辅助 point 或额外的特殊构造。数组式声明不支持 transition。
8. **用例来源**：见 §6.3。只记录每个 bin 上**先命中的有限个** `case_id`（默认 3 个）；额满
   后的用例仍增加 hits，但不再写入来源表。
9. **cross 计分宇宙与优先级（1.7 已冻结）**：cross 在一次 group `sample()` 中按以下顺序
   计分，规则同时适用于 Python、SV、§5.15 manifest 和 §6 的 bin layout。
   1. 先求每个成员 point 的 `iff`；任一为假，或 cross 自己的 `iff` 为假，cross 整体跳过，
      不增加 cross bin hit 或 illegal 记录。
   2. 对其余成员按 §5.13 分类。任一成员命中 `ignore`，cross 整体跳过；任一成员命中
      `illegal`，只记录该成员的 illegal hit，cross 整体跳过，**不**另造 cross-illegal bin。
      因此 illegal 成员永不进入 cross 分子或分母。其后检查实例化已生成的
      `CrossQueueType` ignore bin；若归一化 sample value tuple 属于其中任一 queue，cross 同样整体跳过。
   3. 每个成员剩余的所有 normal 命中（包括 `default`）形成笛卡尔积；normal bins 重叠时，
      每个组合各加一次 hit。对每个组合，若不在存活集合或被 cross `ignore` 选择器排除则跳过；
      否则命中唯一对应的 cross bin。同一阶段还检查 `CrossQueueType` normal bin；命中其 concrete
      value tuple 的 queue bin 时各加一次 hit，并可与普通 normal tuple bin 重叠。cross 本身没有
      runtime value filter 或 `default` bin。
   不含 `CrossQueueType` 的存活集合在 declaration freeze 时固定；含 `CrossQueueType` 时，静态部分在
   freeze 时固定，queue bin 及其成员资格在该覆盖组实例 `.instantiate(...)` 后固定，二者共同构成该
   覆盖组实例的 cross 分母宇宙。被 `select` 排除、`ignore` 排除、成员非法或未采样导致不可达的
   tuple 都不在分母。cross 的 `at_least`、weight、goal 按 §5.14 应用于这些 bin。target fixture 必须最少覆盖：成员 iff false、cross iff false、
   ignore、illegal、default、两个重叠 normal bin、`select`、`ignore` 和多成员组合，并以
   §5.15 的具名 hit/百分比断言此顺序。
10. **transition 长度 K**：见 §4。默认 K=8，硬上限 16；声明超过上限必须报错。CoverageIR
    digest 记录各 transition bin 的实际展开序列与值域；未引用的全局默认 K 或限额配置不进入
    声明语义摘要。仅改默认/限额而既有声明的实际展开结果不变时，该覆盖组类型仍可 merge。
11. **一次 sample 的容器/槽位计数**：选择 A。一次用户 `sample()` 使 group/instance
    `sample_count` +1；每个有效元素/槽仅按命中情况增加 bin hit。槽位 `i >= size()` 或 `iff`
    为假时不加 hit、不加该槽 sample、不创新 bin。target 对拍使用测试专用的用户 sample 调用
    计数器，不把循环 `cg.sample()` 次数作为公开 `sample_count`。
12. **transition 历史推进（1.8 更新项）**：按 SystemVerilog LRM 的已定义语义实现。LRM 对
    transition bin 与 bin 级 `iff` 的交互未给出足以消除实现差异的完整规则；1.8 必须以项目目标
    target 的微型对照测试补全本项，并同步更新本文，使其成为 Python evaluator 与生成 SV 的
    共同规范。首次 sample 不得命中长度 ≥2 的 transition；每种 `iff`、default、ignore、illegal
    与 X/Z 情形均必须有 target 对照用例。它不阻塞 1.7 的声明语法冻结，但在该 1.8 证据完成前不得
    作为已完成的公开 transition 行为承诺。
13. **bin 值比较与规范化（1.7 已冻结）**：freeze 从 point 的静态结果类型取得唯一比较域：
    2-state integral 为 `(width, signedness)`，4-state packed 为 `(width, four_state)`，enum
    为其声明的底层 `(width, signedness)` 域。singleton、range endpoint 和 set member 必须在该
    域内；无类型 Python `int`、不同宽度/符号的 literal、不同 enum 类型、以及会截断或溢出的
    值均为声明错误。enum literal 与**同一底层宽度及 signedness**的显式 integral literal 都
    规范化为同一底层位模式，故 `Color.R` 与同型 `0` 相等。range 为闭区间，只允许 2-state
    endpoint 和 2-state sample；4-state singleton/set 采用逐位 4-state 相等，只有显式含
    X/Z 的 bin 才可命中 X/Z sample，range 不匹配含 X/Z 的 sample。

    分类优先级固定为 `iff` 跳过、`ignore`、`illegal`、所有显式 normal、`default`：`default`
    仅在没有 ignore、illegal 或显式 normal 命中时命中。normal bins 允许重叠且**全部命中**；
    这也适用于由 `Color.R` 和同型 `0` 定义的不同具名 bin，不采用 SV `unique` 或 first-match
    规则。ignore/illegal 内部或彼此重叠不改变其优先级；同一分类的多个 illegal bin 各自记录
    hit。每 point 至多一个 default bin，且 default 不携带 values。IR 按比较域和规范化值而非
    Python repr/enum member 名编码；transition 的每一项复用完全相同的比较与分类规则。target
    fixture 必须覆盖 enum/同型整数重叠、normal range/set 重叠、ignore-vs-illegal、default
    residual、X/Z singleton 与 X/Z range 不命中。
14. **point/group 覆盖率公式**：按 SystemVerilog LRM §19.11 实现，并用 target 对照。
    必须覆盖 point、cross、instance、type 的分子/分母、`at_least`、weight、goal、
    ignore/illegal/default、零分母与 `merge_instances`；保存原始 bin hit count。累计 bin 的
    covered 判定采用参与累计实例的最大 `at_least` 值。**LRM 条文与 target 观测冲突时，
    以 LRM 为公开语义，并把 target 的能力或行为差异记录为 capability gate**；不得把 target
    差异反向写成 SvTypes 语义。
    **`CoverGroupOption.per_instance` 缺省为 0（LRM）。** `merge_instances=0`（缺省）时类型
    覆盖率是各实例覆盖率的加权平均，类型层没有统一的 point/cross bin 表；`merge_instances=1` 时
    类型覆盖率才是按 bin 名合并的实例 bin 宇宙。`per_instance=1` 额外保存并报告各实例覆盖率；
    `per_instance=0` 时实现不必在持久化数据库中保存实例 coveritem 数据。Python、SV 和 UCIS export
    必须依此区分类型级百分比与类型级 bin hit，不能在加权平均情形伪造统一 type-bin count。renderer 显式发射相关 option，不依赖
    仿真器缺省或全局覆盖。
15. **SV 侧对拍观测协议**（协议已定）：
    1.9 的 target 对拍只消费由外部 target adapter 产生的**规范化 observation JSON**。adapter
    不属于 SvTypes 公开 API、运行时数据库格式或 UCIS 替代品；它的具体启动、数据库读取和报告解析
    均不进入本仓库。1.10 不得以该测试观测接口代替 UCIS XML interchange。
    observation JSON 的稳定测试形状为 `{"items": {label: {"hits": {bin: count},
    "illegal_hits": {bin: count}}}}`；`ignore` 不在其中。生成期 manifest 提供每个 `label`
    的语义映射；per-instance 用例另提供 `logical_instance_key`、实例布局摘要和由 harness 显式给出的
    `target_label`，不得从 handle、对象编号或报告名称推导。
    - **按汇总方式选择对拍证据。** `merge_instances=0`（缺省）时，类型覆盖率是实例覆盖率的
      加权平均，类型层没有统一 bin 表；对拍该百分比与非法命中，并以单实例 fixture 的具名 bin hit
      证明分类规则。`merge_instances=1` 时，类型覆盖率按 bin 名并集汇总；对拍类型层具名 bin
      hit、非法命中和 §5.14 覆盖率。两种方式都不要求 target 把类型覆盖率拆成实例覆盖率。
    - **`CoverGroupOption.per_instance = 1` 的用例按实例覆盖率对拍。** 仅这些用例打开该选项。
      Python 解析后的逻辑实例键与 target observation instance 标签经 observation manifest 一一对应，
      不要求字符串逐字相同。key 缺失、重复注册、一个 key 对应多个 SV 覆盖组实例，或一侧有
      键而另一侧没有，均为测试/绑定失败，不能改用类型覆盖率、SV handle 文本或对象
      编号凑合。
    - **运行时实例绑定（仅 instance coverage 用例）。** manifest 只定义生成单元内
      「逻辑实例键 ↔ SV 发射/observation 标签」的规则。每个覆盖组实例在**首次 sample
      之前**必须由测试 harness 用独立于对象 codec 的运行时元数据，把同一逻辑
      实例键注册到 Python DB 与 SV 覆盖组实例的创建/命名路径；之后该实例上的多次
      `sample()` 复用这次绑定，不必每个对象再绑一次。
      - 数据库逻辑实例键与 SV `option.name` / `set_inst_name()` 的报告名称分别保存并由
        manifest 关联；二者不得互相覆盖。该绑定元数据不进入 encoding schema 或
        CoverageIR 声明语义摘要。
      - pack/unpack 同步的是 sample 的对象值，不是 coverage instance。transition 历史、
        `sample_count` 和 bin hit 挂在已绑定的覆盖组实例上，不随每个被同步对象新建实例。
      - 一次 target run 可以包含多个覆盖组实例；observation JSON 按 manifest 分到各逻辑实例键，
        不得把不同键的实例覆盖率合并比较。
    - **Bin/instance 观测键由 codegen manifest 关联，不要求三套字符串逐字相同。**
      用户名称、编码后的 SV identifier 与 adapter observation 标签（如 cross 的 `bin_a,bin_b`）可以
      不同。CoverageIR 与 SV renderer 必须在生成 SV 的同时确定性写出一份 observation
      manifest（与生成代码同产物、同一次生成，禁止另行手维护或跨版本沿用）。manifest
      把 Python semantic ID、SV 发射名称、预期 observation instance/coverpoint/bin/cross 标签
      做成生成单元内的一一对应；adapter 只消费这份随代码产生的 manifest。
      - 任一侧出现歧义或碰撞（同一 semantic ID 对应多个 observation 标签、或反之）为生成期错误。
      - observation 中出现的具名 instance/bin（ignore 除外）若不在 manifest 中，或 manifest 中的
        可比对项在 observation 中缺失，测试失败（生成/观测不完整），不得跳过或猜测。
      - manifest 引用 CoverageIR 声明语义摘要，但其 renderer/adapter 字段**不**参与
        声明身份或 merge。adapter 标签是测试观测协议字段；adapter 变化是测试基础设施更新，
        不是 CoverageIR 变更。
      - `default` bin 与过滤后仍存在的具名 cross bin 必须出现在 manifest 的可比对集合中。
    - **Hit 是主证据。** 具名 bin hit 与 illegal count 必须与 Python DB 精确相等。覆盖率
      百分比用已冻结的 §5.14 在 Python 侧重算，再与 target 百分比做固定小数位交叉校验；
      百分比不得代替 hit 计数作为唯一判据。
    - **Ignore 不向 target observation 要 hit。** ignore 不要求独立命中记录。它只验证：未进入任何
      具名 bin hit、未进入覆盖率分母；illegal/named hit 仍与 target 精确比较。
    - **`sample_count`：** §5.11 选择 A，生成 SV 必须另行记录测试专用的「用户 sample 调用数」
      并纳入观测输出；该计数器不是 CoverageDatabase 字段，也不是公开 API。
    - **`case_id` 与有限来源表**仍只由 Python DB 维护，不从 target observation 恢复。

## 6. CoverageDatabase 与文件格式

### 6.1 核心对象

```text
CoverageDatabase
 ├── 数据库头与 run / merge 溯源
 ├── 覆盖组类型记录 (covergroup_type_id, declaration_semantic_digest, 声明语义定义快照)
 ├── 类型级汇总结果
 ├── 可选的覆盖组实例记录 (logical_instance_key, instance_layout_digest, 实例 bin 布局, hit / illegal / 来源, 当前报告名称/注释)
 └── 有限用例来源表
```

这是**逻辑数据模型**，不是 1.10 前的物理文件 schema。数据库必须保留足以按 LRM 规则报告和
merge 的信息：`merge_instances=0` 时，类型级公开结果是加权平均，绝不伪造一张 type bin 表；
`merge_instances=1` 时，类型级结果按 bin 名并集汇总。`per_instance=1` 时另保存可报告的覆盖组
实例结果及其实例 bin 布局；`per_instance=0` 时实现可以不持久化该公开实例记录，但仍必须保留
完成受支持 merge 所需的信息。若持久化实例记录，其报告名称/注释为导出时当前快照，只服务展示和
诊断；它们不参与声明兼容性、实例身份或 merge 对齐。illegal hit 始终是独立的诊断事实，不被解释成
weighted type bin。声明语义定义快照只含声明语义，不含 source location、实例 actual 或其他 provenance。

覆盖率库只存计数与有限来源，不存每次 sample 的取值。可选 sample 日志为独立分帧二进制
（字符串表 + 按帧 zstd/lz4），默认关闭，并设 `max_records` / `max_bytes`。二进制 coverage
database 的公开 chunk/schema/version 契约与 UCIS projection/loss-report 一并留待 **1.10** 冻结；
1.8–1.9 仅承诺内存 DB、严格 merge 语义和供测试使用的确定性 JSON snapshot，不对持久化二进制
格式作兼容性承诺。

### 6.2 merge 规则

- **普通严格 merge**：同一覆盖组类型 ID、同一声明语义摘要才可合并，counts 相加。
  任一同名覆盖组类型的声明语义不一致则整个 merge 失败；merge 键不含 encoding-schema fingerprint，
  也不含 provenance。
- **显式主数据库 merge**：用户指定 master database 后，master 的 CoverageIR snapshot、层次
  与 bin universe 是结果数据库唯一的声明定义。输入数据库仅对与 master 语义一致的 coverage item
  累加命中；不一致 item 不向 master 贡献数据，但 master 中该 item 仍完整保留。不得静默
  删除 item 或把不同语义的 counts 相加。结果必须记录为 master-projection merge，并输出每个
  不兼容 item 的定义差异。
- incompatibility 采用依赖闭包：point 的表达式、bins 或分类变化，会使依赖它的 cross 也不
  能从该输入库累计；报告必须同时列出根因 point 和受影响 cross。CI 可要求零 projection
  conflict，将任一投影失败升为失败。
- digest 相同则认为语义 snapshot 等价。若两侧 snapshot 的规范化语义一致而 provenance
  不同：兼容判定仍通过；provenance 保留先到库的记录，另一侧可写入 merge 诊断，不参与
  是否可合并。规范化语义不一致则失败——不得出现「digest 相同但语义 snapshot 冲突」。
- `merge_instances=1` 的 type coverage merge 按 bin 名合并类型级命中与非法记录；
  `merge_instances=0` 的 weighted type summary 不得由 type-bin count 伪造，数据库必须保留计算
  跨库加权汇总所需的内部信息。`CoverGroupOption.per_instance = 1` 时，只有
  `logical_instance_key` 与 `instance_layout_digest` 都相同的公开覆盖组实例记录才能相加；illegal
  随该记录合并。同一 key 而实例布局摘要不同属于严格 merge 不兼容；显式主数据库 merge 保留 master
  记录且不累计输入记录，并报告实际 binding/布局差异。不同 key 各自保留。该实例级校验不改变前述
  type coverage 的汇总规则。`CoverGroupOption.per_instance` 或 `merge_instances` 不同的覆盖组类型视为
  语义不一致，不得把不同计算方式的 coverage 相加。
- UCIS 或仿真器导入的数据必须标记来源、转换损失和原始覆盖组类型 ID；不可证明等价时保留为独立
  覆盖组类型，不自动与 Python 覆盖组类型合并。
- 用例来源按 §6.3 合并：先到先得，额满丢弃新 ID，hits 仍相加。

### 6.3 有限用例来源

每次 `sample()` 携带稳定 `case_id`（测试名或调用方场景 ID，禁止 `id(obj)`）。覆盖率库为
每个 bin 保留最多 `N` 个来源（`N` 可配置，**默认 3**）：

- 该 bin 第一次被某 `case_id` 命中，且该 bin 的来源数 `< N`：记入 `source_ids`。
- 额满之后，任何新的 `case_id` **仍增加 `hits`**，但不再写入来源表，也不挤掉已有记录。
- 同一 `case_id` 再次命中：只加 hits，来源集合不变。
- merge 两个库时，按已记录来源的先后做稳定并集，截断到 `N`；被截掉的 ID 不从另一侧
  「补回」。因此来源表是诊断线索，不是「所有打过这个 bin 的用例」的完备集合。
- 默认不存 per-case 命中次数。排除某用例后只能判断「来源表里是否还留下别人」，不能精确
  重算 hits。

报告可列出每 bin 的前 N 个用例；不得把额满后的来源描述成「未覆盖该 bin」。

## 7. UCIS 适配策略

UCIS 1.0 定义覆盖数据库抽象、API 和 XML interchange；SvTypes 不在 2.0 绑定任一仿真器的
私有 UCDB 库。适配层的优先级是：

1. `CoverageIR` ↔ SvTypes 内存 coverage database，并导出确定性 JSON snapshot 供 1.8–1.9
   阅读和测试断言；公开二进制 database format 在 1.10 才冻结。
2. `CoverageIR` / SvTypes database → UCIS XML 的 functional-coverage 可表示子集；导出必须
   保留覆盖组类型、scope、coverpoint、bin、weight、goal 与 count。
3. UCIS XML → SvTypes database 的受控导入；不能表示的属性保存在 extension/loss report，
   不伪造等价。
4. 外部 SystemVerilog target 的 UCIS/coverage export 可作为集成验证输入；其私有数据库仍由 target 拥有。

1.9 为 Python/SV parity 读取外部 adapter 的规范化 observation JSON；这只是测试观测通道。1.10 的 UCIS XML
import/export 才是 SvTypes 对外的覆盖率 interchange 能力，不能以 adapter 取代。

UCIS 的 XML interchange 与 API 是标准提供的互操作路径，且标准支持跨 run 合并；这正是
SvTypes 采用独立核心 DB 加适配层、而非直接定义为某个 simulator DB 的原因。

## 8. 从当前代码到 2.0.0 的里程碑

| 里程碑 | 交付 | 完成门槛 |
|---|---|---|
| 1.7 设计冻结 | coverage DSL、CoverageIR、bin ID/实例身份、数据库和 UCIS 映射规格；完成 §2 的 `cov` 自动声明迁移边界，并冻结 §5.1、§5.2、§5.5、§5.9、§5.13；交付不可变 IR、自动 `cov` 编译和静态 cross 限额基础 | 冻结条款均有对应的 Python/target fixture 计划；fixture 在承载其运行时或 renderer 能力的后续里程碑成为交付门槛。未冻结构造不实现公开 DSL |
| 1.8 Python core | CoverageIR、表达式 evaluator、embedded covergroup/point/bins/iff、automatic/array bins、宿主 `__init__` 内的 embedded `.instantiate(...)` `CoverInput` 实例化、静态成员 `CoverRef` binding、定长覆盖率数组与值域 point、有界 transition（非数组式）、内存 DB、有限用例来源、确定性 JSON test snapshot 与可选 sample 日志 | 单元测试覆盖命中、未命中、automatic bins 的 enum/整除/余数/XZ、array bins 的 `split()` / `split(count)` / empty bin、`CoverInput` snapshot 与不同 actual 的实例 bin 布局、`CoverInput` 不改变声明语义摘要、`CoverRef` 当前值读取、embedded covergroup 可不实例化，或仅在宿主 `__init__` 中以 `.instantiate(...)` 构造一次、未实例化成员方法调用失败、覆盖组内置方法、illegal、ignore、goal、数组槽跳过、定长 transition 与 `[*m:n]` 的命中/未命中、数组式声明带 transition 的声明期失败、来源额满、声明语义摘要相同而 provenance 不同的 merge、加权类型汇总与按 bin 名合并的类型结果、`CoverGroupOption.per_instance = 1` 时 illegal 按覆盖组实例分开、实例覆盖率用例下逻辑实例键缺失/重复注册失败、同一逻辑实例键而实例布局摘要不同的 merge 拒绝 |
| 1.9 cross 与 SV parity | cross、`CrossQueueType` 函数、实例策略、SV renderer、生成覆盖组、observation manifest、Python/SV conformance vectors、编解码同步双侧采样 | 固定 sample 向量下按 manifest 将 target 可观测的具名 bin/illegal 与 Python DB 精确对拍，coverage 百分比按 §5.14 交叉校验；`CrossQueueType` 的实例化后 concrete tuple queue、bin 名和 coverage 必须与生成 SV 相等；再用 SvTypes pack/unpack 把同一批对象同步到两侧，两边同时 `sample()`，大量样本后同样对拍（ignore 只验证未进 named hit/分母）；按 §5.15 的汇总方式选择 type 或 instance 覆盖率对拍；manifest 缺项或 target 多出未映射具名 bin 为失败；生成期拒绝不支持语义。**若 LRM 明确允许而当前 target 不能生成或观测某构造，路线图必须逐项记录 capability gate；Python 语义可交付，但该构造不得宣称 target parity 已完成。** |
| 1.10 UCIS bridge | 公开二进制 coverage database chunk/schema/version、UCIS XML export/import 子集、loss report、target 导出集成验证 | 二进制库 round-trip 与版本兼容策略、UCIS round-trip、跨 run merge、外部 UCIS 样本导入和不兼容诊断 |
| 2.0 RC | 性能基准、API/schema 冻结、文档/examples、全量 Python/target 回归 | 无未规划的 2.0 承诺；所有受支持语义有 Python 和 target 证据 |
| 2.0.0 | 随机与功能覆盖率终版 | 仅后续 bug fix，不再扩张功能覆盖率契约 |

## 9. 验证策略

每个 coverage 构造必须有同一批显式 sample vector：Python evaluator 产生期望的 per-bin hit
count 与 coverage；生成 SV 在 target 采样同一 vector，检查 named point/cross 的 hit/coverage。
UCIS bridge 另检查覆盖组类型 ID/bin ID、count、weight、goal、逻辑实例键和 merge 结果。

除此以外，1.9 必须有**编解码同步双侧采样**：用 SvTypes 已有的 Python↔SV 字节布局，把
同一批随机或录制对象同步到两侧，Python evaluator 与 target covergroup **对同一对象序列**
各自 `sample()`，再比较结果。target 侧以 §5.15 规定的 observation JSON 读取统计；这不是两份
手写向量各自重放，而是一份刺激、一次同步、两边同时统计。至少覆盖：标量 point、定长槽位
数组（含 `i >= size()` 跳过）、容器值域 point、有界 transition、`iff`、
ignore/illegal/`default`、以及 1.9 的 cross。比较以具名 bin hit 和 illegal count 精确
相等为主；coverage 百分比按 §5.14 在 Python 侧重算后与 target 做固定小数位交叉校验；
`sample_count` 遵循已冻结的 §5.11 与 §5.15 的观测通道；ignore 不要求 target 给出 hit，
只验证未进入具名 bin hit 且未进入覆盖率分母。按 §5.15 的汇总方式比较类型覆盖率；
`CoverGroupOption.per_instance = 1` 的用例比较实例覆盖率，并在首次 sample 前绑定同一逻辑实例键。对象编解码
不同步 instance。
任一侧独有的 named/illegal 命中视为失败。target 具名项不在 manifest 中、或 manifest
可比对项在 target 中缺失，同样失败，不得猜测映射。规模上须明显大于手写向量（建议每构造
不少于 10⁴ 次 sample，或等价的随机种子批），用于暴露槽位跳过、transition 历史和混宽
比较等手写向量不容易穷举的路径。同步失败（pack/unpack 不一致）不算 coverage 失败，
应先报编解码错误。

测试层级：

1. IR/identity/diagnostic 单元测试；
2. Python DB、merge、二进制库与确定性 JSON 导出、报告测试；
3. Python/SV conformance：固定 sample 向量重放；
4. Python/SV 编解码同步双侧采样（大量样本，比较 hit/coverage）；
5. UCIS XML import/export/round-trip；
6. 大 cross、长期 merge 和报告生成的性能与内存基准。

## 10. 后续验证计划

1.7 的规格门槛已写入 §2、§5.1、§5.2、§5.5、§5.9 与 §5.13。后续实现必须将它们
各自落实为 CoverageIR / CoverageDatabase 原型测试和以下不可替代的 target 微型 fixture；每项 fixture
在其对应的 1.8 或 1.9 公开能力交付前通过：

- §5.1–§5.2：同一已冻结 declaration 的 Python evaluator 与 SV renderer 使用同一 typed expression
  IR/声明语义摘要，且 manifest 正确引用该摘要；宿主 `__init__` 内的 `.instantiate(...)`/静态成员的
  `CoverInput` 实例化、静态成员 `CoverRef` 当前值、Python 宿主在 `__init__` 中实例化且生成 SV 宿主在
  `new()` 中执行对应构造、
  嵌套 `sample` 签名及调用、覆盖组内置方法、嵌套 `option` / `type_option`、
  automatic bins 的 enum/整除/余数/XZ、`iff`/度量/transition、split 的 canonical 子 bin、
  `repeat(term, m, n)` 的有界展开、`CovPointArray` 的槽名/ID 展开和单槽 cross 引用与 §5.1、§5.7
  一致；每个拒绝的 AST 节点在 freeze 期失败。
- §5.5、§5.9：65,536 个 normal bin 的单 cross 与 1,048,576 个 normal bin 的 covergroup type
  边界接受、各自加一后的 `SVT-COV-CROSS-LIMIT` 拒绝、无静默截断、静态 selector、成员/ cross `iff`、
  ignore、illegal、default、cross 内无 `bins[...]` 时的自动 tuple 名与重叠 normal 的具名 hit 和百分比
  符合 §5.9。
- §5.13：enum/同型整数、宽度或 signedness 不匹配的拒绝、重叠、分类优先级及 X/Z 的结果符合
  §5.13。
- §2：有效 `cov=True` 标量、固定数组、动态数组/队列（value-domain 与 `cov_slots`）和关联数组
  value-domain 均形成同一 `svtypes_auto_cov` CoverageIR；`cov=False` 字段不进入默认组。生成 SV 时
  该默认组取代 1.x legacy nested collector，并与显式 `@covergroup` 一同产生 coverage DB/声明语义摘要。

这些 fixture 不得改写已冻结语义；若目标不能表达或观测该语义，生成器必须在相应公开能力的
交付期拒绝该声明，并将该能力留在未支持集合。`CrossQueueType` 的实例化后 queue、资源预算、bin
membership 与 target 对拍属于 §8 的 1.9 cross 交付，不是 1.8 的门槛。1.9 的全面对拍继续按
§5.11、§5.14、§5.15 执行。

## 11. 1.7 实施详细设计

本节把已冻结的 1.7 规格落实为实现工作分解，供进入开发流程使用。它规定模块边界、编译阶段和
验收顺序，**不新增公开 DSL，也不替代 §3–§6 的语义契约**。实现过程中出现歧义时，依次以本文
已记录的设计倾向、SV LRM、配置 target 的最小实测为准；实测只确认观测，不得反向改写已冻结的
身份、merge 或 Python/SV 一致性边界。

### 11.1 范围、切换与交付切片

1.7 的代码目标是建立可验证的声明编译链和冻结门槛所需的最小原型，而不是提前宣称 1.8/1.9/1.10
交付完成。开发按下列切片推进，后一个切片只能消费前一个切片的公开内部契约：

1. **自动 `cov` 编译。** 在 `SvObject` 的声明收集阶段按字段的有效 `cov` 值构造稳定的
   `svtypes_auto_cov` CoverageIR；它不是旧 nested collector 的镜像，而是与显式 declaration 共用
   同一后端链。固定数组展开为定长 slot；动态数组/队列默认 value-domain，只有显式 `cov_slots=N`
   才展开 N 个 slot；关联数组只采样 value-domain。不得使用 `max_length`、当前容器长度、对象地址或
   任意运行时状态决定 declaration shape。
2. **声明前端与 CoverageIR。** 增加 coverage 专用前端，编译 `@covergroup` 的受限 AST、类型信息和
   provenance，产出不可变、可规范化的 CoverageIR。此阶段不执行用户函数、不创建命中计数，也不让
   renderer 直接重读 Python 函数体。
3. **语义原型。** 从同一 CoverageIR 构造 Python evaluator、最小内存数据库视图、SV renderer 和
   declaration document。先完成 §10 所列的 1.7 fixture；未能形成共同 IR 的构造不得进入任一后端。
4. **1.8/1.9 延后部分。** 完整 runtime API、持久化数据库、cross queue、UCIS 和全面 target parity
   仍按 §8 的里程碑实施。1.7 原型可使用测试私有的内存/JSON 载体，但不得把该载体承诺为 1.10 前的
   物理格式。

切换是实现载体而非 API 删除：2.0 generation 路径不再发射旧 nested collector，而是把 `cov` 自动组与
显式组共同发射并建立 Python coverage record。默认组与显式组各自具有独立、稳定的 declaration slot，
不得把它们隐式合并或让其中一方覆盖另一方。

### 11.2 建议模块边界

新增 coverage 包应与现有 `constraint` 包同样保持「受限 Python 前端 → typed IR → 多后端」的单向
依赖。建议的私有模块职责如下；文件名可调整，但职责和依赖方向不得倒置。

| 模块职责 | 输入 | 输出 / 禁止事项 |
|---|---|---|
| `coverage/declaration` | `@covergroup` 标记的类成员、宿主类型元数据 | 收集 declaration slot、嵌套 `sample` 和 source provenance；不执行函数体、不实例化 coverage。 |
| `coverage/frontend` | declaration 的 Python AST、闭包中允许的静态符号、宿主字段类型 | 受限语法诊断及 typed expression IR；不得调用用户 helper 或用运行时值推断声明。 |
| `coverage/ir` | typed expression、bins/options/cross 描述 | 冻结的 CoverageIR dataclass/variant；仅此层定义规范化、semantic ID 和 definition snapshot。 |
| `coverage/canonical` | CoverageIR 与实例布局描述 | canonical JSON/value tree、`covergroup_type_id`、声明摘要、bin ID、`instance_layout_digest`；不得混入 provenance、对象 encoding fingerprint 或报告标签。 |
| `coverage/runtime` | CoverageIR、宿主实例、`.instantiate(...)` actual | `CoverGroupInstance`、binding、采样状态和 evaluator 调度；不得重新编译声明。 |
| `coverage/database` | runtime 的结构化 sample result | 内存 records、有限来源、严格/master merge 与确定性测试 snapshot；1.7 不确定义持久化 chunk 格式。 |
| `coverage/sv` | CoverageIR 与 renderer manifest | SV covergroup 文本和 observation manifest；不得从 Python AST 或对象 descriptor 旁路生成 bins。 |
| `coverage/diagnostics` | 前端、绑定、资源、merge 错误 | 稳定 `SVT-COV-*` code、位置和结构化详情；不得以 Python traceback 充当用户诊断。 |

`object.py` 只负责在类创建时登记 coverage declaration slot、在宿主 `__init__` 的合法路径支持 embedded
成员，以及将 CoverageIR 交给生成器；它不应继续保有 `to_sv_obj()` 中按字段拼装 coverage collector
的逻辑。`generator.py` 负责将 declaration document、SV 和 manifest 作为同一次 generation 的关联
产物写出；coverage digest 不加入既有 schema/encoding manifest 字段的兼容性判定。

### 11.3 声明编译与冻结算法

类创建时收集带 `@covergroup` 的成员，但 AST 编译应延迟到宿主字段、继承和 parameter 信息稳定之后。
对每一个 declaration slot 按固定顺序执行下列步骤：

1. 确定 canonical `sample_type` 与 declaration name，先建立稳定的 `covergroup_type_id`；名称冲突、
   重复 slot、无效宿主类型在此阶段失败。
2. 解析 covergroup 函数体和可选嵌套 `sample` 函数。仅接受 §5.1 允许的声明节点、表达式和
   option 容器；所有其他语句、动态控制流、函数调用、赋值、反射及无法静态解析的 closure 均以明确的
   `SVT-COV-*` declaration 诊断拒绝。嵌套 `sample` 存在时，point/cross 必须全部位于它的专属作用域；
   不存在时必须全部位于外层，不能混合。
3. 结合宿主 descriptor 与 `CoverInput` / `CoverRef` 声明做类型检查，形成 typed expression IR。这里
   只记录表达式、静态成员引用和 formal 签名；不读取宿主当前字段值，也不计算任何 input actual。
4. 展开固定槽位 array point、验证 bin 名和 cross 成员，构造完整且有序的 template IR。资源上限在
   template 可知时立即检查；绑定后才可知的实例布局留给 `.instantiate(...)` 检查。
5. 对不含 provenance 的规范化 template value tree 做 SHA-256（固定 UTF-8、排序键、无空白差异），
   得到 `declaration_semantic_digest`；同时生成可回读的 definition snapshot。digest 是兼容性索引，
   snapshot 才是报告、diff 和后端重建的依据。
6. 为每个 point/bin/cross 生成由 type ID、稳定声明路径与 canonical 定义派生的 semantic ID。用户的
   SV 名称、编码后的 identifier 和 observation 标签在 renderer 阶段另行映射，不能反向成为 IR ID。

前端应把「语法不在子集」「名称/作用域不合法」「类型不匹配」「资源超过上限」分为不同诊断类别，
并总是携带 declaration name 和可用的 source span。诊断 code 的精确分配可在实现前登记；已冻结的
`SVT-COV-CROSS-LIMIT` 与 `SVT-COV-SLOTS` 名称不得改变。

### 11.4 embedded 生命周期与实例绑定

`@covergroup` 在类上形成一个 declaration slot，不在类创建、普通对象分配或首次访问时隐式创建
coverage instance。宿主 `__init__` 中对该同名成员的唯一一次 `.instantiate(*actuals, **named_actuals)`
才创建 `CoverGroupInstance`；这一 Python 调用映射 SV embedded covergroup 的 `name = new(...)`。实现
应通过构造期 token/状态机限制该入口，而不是依赖调用栈字符串：

```text
declared → host constructing → instantiated → active
                     └──────→ uninstantiated (允许对象构造结束后保留)
```

- 对同一 slot 第二次实例化、在 `__init__` 外实例化、从外部 factory 实例化、或在实例化前/未实例化时
  调用采样/覆盖率查询，均为明确 runtime/declaration 失败；不自动重建、不 lazy instantiate。
- `CoverInput` actual 在实例化处完成类型检查、不可变 snapshot 与 named/positional 绑定；只有影响
  bins 或冻结 option 的绑定结果进入实例布局 canonical value。`CoverRef` 只能绑定 §5.1 允许的静态、
  单数 `TypeBase` 宿主成员，并在每次 sample 读取其当前值；它不进入 declaration digest，也不因值变化
  重建布局。
- runtime 从 template IR 加已绑定 actual 构造 instance layout、计算 `instance_layout_digest`，再注册
  transition history 和 bin counters。实例不持有可变的 template 定义副本，避免某一对象的 binding
  污染同类型其他对象。
- `logical_instance_key` 由数据库/测试绑定层显式提供并在首次 sample 前校验；它不能由 Python
  `id()`、SV handle、`option.name` 或报告字符串推导。缺失/重复以及同 key、不同 layout 的 merge 行为
  严格遵从 §3、§6 与 §5.15。

### 11.5 evaluator、数据库与生成器的共同边界

evaluator 的输入必须是「冻结 template IR + instance layout + 一次 sample arguments + 当前 `CoverRef`
值 + per-instance history」，输出是结构化的 bin 分类结果，而非直接写数据库。分类顺序固定为：适用的
`iff` 判断、ignore、illegal、normal（重叠 normal 全部命中）、default 残余；X/Z 的规则按 §4 与
§5.13。数据库层只将结果累加为 hit、illegal、sample count 和有限 case source，并按 §6 负责 merge；
它不得重新求值表达式或重新解释 bins。

SV renderer 消费同一 template IR 和与实例化策略相符的 binding 信息，生成 embedded declaration、
SV `new(...)` 路径及采样调用。它同时输出 observation manifest，其中记录 Python semantic ID 到 SV
emitted name 与 observation 标签的映射。manifest 是 generated artifact：必须与 SV 文本一同重生、同一
generation transaction 成功才发布；不得手写、缓存跨版本复用，或作为 declaration digest 的输入。

1.7 原型至少提供可排序的 JSON snapshot，字段顺序和整数/logic 值编码固定，供单元测试比较；该
snapshot 需包含 type ID、声明摘要、definition snapshot、实例键（如适用）、实例布局摘要、bin hit 与
illegal 计数。它不得成为 1.10 物理 database format 的先例。严格 merge 先比较 type ID 与声明摘要，
`per_instance=1` 的同 key record 再比较 layout digest；master projection 的丢弃项必须保留结构化差异。

### 11.6 生成入口、迁移诊断与产物管理

generation 的准备阶段必须先完成全体待生成类型的自动 `cov` 默认组编译和显式 coverage declaration 编译，
再开始写任一输出。这样一个失败的 type 不会留下部分更新的 SV、schema、coverage declaration 或
manifest。复用 `generator.py` 现有的临时目录/原子发布机制，将 coverage declaration document 与
observation manifest 纳入同一 managed artifact 集；只请求非 SV target 时仍须运行同一声明编译，
避免用户通过目标选择绕过 2.0 诊断。

现有 `SvObject.to_sv_obj()` 内的 `coverage_fields` 拼装是 1.x SV-only legacy 路径。2.0 实施应先将其隔离为
兼容版本代码，再由 CoverageIR renderer 替换调用点；自动 `cov` 必须由 §2 的 `svtypes_auto_cov` 编译为
同一条 IR/renderer 路径，不能把新 `@covergroup` 翻译回该 collector，也不得继续使用其固定的
`option.per_instance = 1`。自动默认组的 bins 仅采用既定的自动语义；需要非默认 bins、cross 或容器 key
语义时，使用显式 `@covergroup`。

### 11.7 实施顺序与冻结验收

开发提交应按可回归的纵向小步组织，而不是先实现独立 Python 与 SV 两套规则后再试图对齐：

**实施记录：**第一切片已建立 `coverage` 私有包、coverage 专用诊断层、不可变 CoverageIR 与
canonical SHA-256 serializer，并以单元测试确认 type ID 与声明摘要分离、provenance/runtime actual
不进入定义快照。默认 `cov` 的 IR 编译已覆盖标量、固定数组槽位、动态/队列 value-domain 或显式槽位、
以及关联数组 value-domain；它尚未接入 `SvObject.to_sv_obj()` 或 generation 的最终 renderer。

1. 建立 `coverage` 私有包、错误基类/诊断格式、不可变 IR 和 canonical serializer；为 digest 不含
   provenance、actual、报告名建立纯单元测试。
2. 接入类声明收集与自动默认组编译，删除 2.0 路径对 `coverage_fields` 的依赖；覆盖显式
   `cov=True/False` 和由 `TypeBase._default_cov` 生效的隐式值。
3. 实现受限 AST 前端及 §5.1/§5.2 的 declaration tests；先拒绝未知节点，再逐项放开冻结子集，保证
   新 AST 节点不会静默改变语义。
4. 实现 embedded `.instantiate(...)` 状态机、`CoverInput` snapshot、`CoverRef` current-value 读取和
   最小 evaluator/database；用同一 template 验证不同 actual 产生不同布局但相同 declaration digest。
5. 实现 bins、automatic/array 展开、`iff`、分类优先级和有界 transition，先完成 §5.13 与 §10
   所列 Python oracle；cross 的 1.7 边界只实现静态 IR/limit 检查，完整 `CrossQueueType` 留给 1.9。
6. 从 IR 实现 SV renderer、declaration document 与 manifest，编写 §10 的最小 target fixture。每项 fixture
   必须同时断言生成失败路径或 named hit/illegal 观测，不能只以编译通过作为证据。
7. 最后接入严格/master merge、per-instance layout 校验及确定性 JSON snapshot；这些是 1.8 runtime
   原型及后续公开能力的验收项，不倒灌为 1.7 设计冻结的完成条件。

每一步至少包含：IR/diagnostic 单元测试、Python 行为测试、相关生成文本断言；涉及可生成 SV 的冻结
语义再加 target fixture。target 结果需同时确认进程退出码、仿真通过标记及 observation/manifest 可观测项。任一
LRM 行为与现有路线图预期不符时，先写独立最小 fixture 固化事实，再在讨论中提出只影响未冻结实现
选择的修订；不得用实现便利性改变已冻结 DSL 或跨库兼容规则。

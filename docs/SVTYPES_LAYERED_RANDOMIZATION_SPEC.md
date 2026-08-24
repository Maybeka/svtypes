# SvTypes 分优先级随机化规格与设计

**状态：Draft，等待批准。**
**范围：Python constrained-random 运行时与生成的 SystemVerilog。**
**非范围：本文件不包含实现改动。**

## 1. 目标

SvTypes 在普通 `randomize()` 之外增加 `layered_randomize()`。它把随机变量和约束划入若干**优先级组**，从高优先级到低优先级依次调用正常的 `randomize()`：已完成组的随机变量在后续组中保持为 state。

优先级是唯一决定顺序的属性；所谓“层名”只是用于声明、诊断和生成代码的 alias。相同 priority 的全部成员必须合并为一次 `randomize()`，不存在 alias 之间的额外排序。

普通 `randomize()` 仍按现有语义一次性处理全部当前启用的变量和约束；分优先级不改变它的求解集合或调用方式。

## 2. 术语与基本规则

### 2.1 优先级组

一个显式优先级组是：

```text
RandLayerDecl(alias: str, priority: int, variables: tuple[str, ...], constraints: tuple[str, ...])
```

- `priority` 必须为非零 `int`，不能是 `bool`；数值越大优先级越高。
- alias 即 `@rand_layer` 声明函数名，必须为非空字符串，并在最终类中唯一。同一 alias 不能对应不同 priority。
- 多个 alias 可以拥有同一 priority；它们的变量和约束合并为一个批次。
- 一个随机变量 target 和一个 constraint 各自最多属于一个显式优先级组。重复归属必须在类创建期报 `DeclarationError`。

### 2.2 `builtin`

每个实际 `SvObject` 类隐式拥有唯一默认组：

```text
alias = "builtin"
priority = 0
```

未被显式优先级组列出的可随机变量和约束自动属于 `builtin`。用户不得显式使用 priority `0`、alias `builtin` 或操作 builtin；这类声明一律在类创建期拒绝。

### 2.3 `rand_mode()` 粒度必须与 SV 一致

分优先级使用 `rand_mode()` / `constraint_mode()` 临时开关，因此 target 的粒度必须能生成等价、合法的 SV 调用：

- 可以控制直接声明的 `rand` / `randc` class member。
- 可以控制 SV 允许选择的 rand unpacked array element 和 rand unpacked struct member。固定数组元素以常量、范围内、非布尔整数索引声明，可递归用于多维 fixed array。
- packed vector 的 bit select 不是独立随机变量，不能单独分组或控制。
- 动态索引、slice、负/越界索引必须拒绝。同一数组的不同元素可以归入不同 priority，但同一元素不可重复归属。
- 无法生成合法 `target.rand_mode()` 的 SvTypes 内部 leaf path 必须在类创建期拒绝，不能为了 Python 求解器自行拆分。
- `DynArray` 与 `Queue` 可以以容器根名称作为一个 layer 成员，不能列出具体下标；其
  语义是运行时逐个控制现存元素，而不是对 non-singular 容器调用无参 `rand_mode()`。
  `AssocArray` 仍不能列入 layer，因为其运行时 key 没有可声明的稳定归属 target。

关闭的随机变量不被写回，其当前值作为 constraint solver 的 state；这与 SystemVerilog `rand_mode(0)` 一致。普通 `randomize()` 继续遵守调用前的 modes；`layered_randomize()` 对调用前 modes 的特殊处理见 §6。

## 3. 声明 API

### 3.1 `@constraint`

`@constraint` 保持现有无参接口，只定义 constraint 本身：

```python
@constraint
def address_legal(self):
    self.addr % 64 == 0
```

它不再、也不得声明 priority 或 alias。`@constraint()`、位置参数、关键字参数和额外 decorator 均必须拒绝。

### 3.2 `@rand_layer`

`@rand_layer` 是变量和约束的唯一分组入口。它采用与 constraint 相同的 class-level 声明形态；声明函数不在运行时执行。

```python
class Packet(SvObject):
    addr = Bit(32)
    length = Bit(16)
    payload = Bit(64)

    @constraint
    def address_legal(self):
        self.addr % 64 == 0

    @constraint
    def payload_legal(self):
        self.payload != 0

    @rand_layer(100)
    def address(self):
        self.addr
        self.length
        self.address_legal

    @rand_layer(-50)
    def payload_data(self):
        self.payload
        self.payload_legal
```

拟议签名：

```python
rand_layer(priority: int) -> Callable[[Callable], RandLayerDecl]
```

允许的形式：

| 写法 | alias | priority |
|---|---|---:|
| `@rand_layer(100) def address(...)` | `address` | 100 |
| `@rand_layer(-50) def payload_data(...)` | `payload_data` | -50 |

priority 为 `0`、`bool` priority、方法名为 `builtin`、重复参数及未知关键字必须拒绝。函数名就是最终 alias，`@rand_layer` 不提供 alias 参数。

声明函数必须恰好有一个 `self` 参数且 body 非空。body 的每个顶层语句必须是直接引用：

```python
self.<随机变量 target>
# 或
self.<constraint 名>
```

除 §4.2 所定义的同名 `super().<rand_layer_name>()` 外，禁止赋值、调用、控制流、运算、索引、嵌套 decorator 和其他表达式。类创建完成后再收集 layer，故 constraint 和 layer 的书写顺序不影响解析。

实例访问或调用 layer 声明均必须失败；它与 constraint declaration 一样不是可执行的普通 Python 方法。

## 4. 归属与跨优先级约束引用

### 4.1 成员归属

- 每个 `@rand_layer` body 可以混合列出随机变量与 constraints。
- 列出的变量必须是 `rand=True`、randomizable 且符合 §2.3 的 SV target 规则；`rand=False` 变量列入 layer 必须报错。
- 列出的 constraint 必须是同一最终类可见的有效 constraint。
- 未列出的变量和 constraints 自动加入 builtin。
- 继承后的最终有效声明决定成员归属；子类替换同名 constraint 时，替换后的 constraint 必须由子类最终 layer 表重新归属。

### 4.2 Layer 继承与 `super()`

未被子类同名 layer 声明覆盖的父类 layer 自动继承。子类可以声明同名同 priority 的 layer，并在其 body 中以 `super().<rand_layer_name>()` 引用直接父类/MRO 解析得到的同名 layer：

```python
class Base(SvObject):
    addr = Bit(32)

    @rand_layer(100)
    def address(self):
        self.addr

class Child(Base):
    length = Bit(16)

    @rand_layer(100)
    def address(self):
        super().address()
        self.length
```

`super().address()` 仅是 declaration parser 的特殊语法，不执行 Python 调用；它把父 layer 的最终成员并入当前 layer。规则如下：

- `super()` 必须是无参数形式，且调用名必须与当前 layer 方法名/alias 完全相同。
- 父类可见的同名 layer 必须存在，且 priority 必须与当前 layer 相同；否则类创建期报 `DeclarationError`。
- 只能引用同名 layer；`super().other_layer()`、`Base.address(self)`、普通方法调用和多个不同 super layer 调用均非法。
- 当前 layer 其他成员与父 layer 成员合并后，仍必须满足变量和 constraint 的唯一归属规则；重复引用同一父成员本身应去重，不视为跨 layer 重复。
- 不调用 `super().<name>()` 的同名子类声明替换父 layer 的成员集合，类似同名 constraint 替换；调用该语法则在父集合上扩展。

### 4.3 跨优先级引用

一个 priority 为 `P` 的 constraint 可以引用：本组变量、priority 等于或高于 `P` 的变量，以及非随机 state 字段。

若它引用 priority 低于 `P` 的随机变量，类仍可创建，但必须产生 `LayeredRandomizationPriorityWarning`。诊断必须包含 constraint 名、引用 variable path、引用方 priority/alias 与被引用方 priority/alias。

SvTypes 必须提供顶层全局策略：

```python
set_layered_randomization_reference_policy("warning")  # 默认
set_layered_randomization_reference_policy("error")
```

`"error"` 将上述 warning 升为类创建期异常。该设置应可恢复，以免测试或库调用泄漏到无关代码。

## 5. 生命周期与入口方法

### 5.1 Final API

以下 Python 入口均由 SvTypes 框架拥有，用户在 `SvObject` 子类中重定义任一入口必须得到 `DeclarationError`：

```python
randomize()
randomize_with(fn)
layered_randomize()
```

`randomize()` / `randomize_with()` 是 Python 对 SV 内置 class randomization 的映射，不生成 SV 方法。`layered_randomize()` 是 SvTypes 自定义方法：必须为每个生成的 SV class 输出默认实现。用户不得手改生成 SV 中的同名方法；应修改 Python 声明并重新生成。

Python `layered_randomize()` 保持 `bool` 返回值；生成的 SV 方法必须与内置 `randomize()` 一致，返回 `int`：成功为 `1`、失败为 `0`。

`layered_randomize()` 不接受任何位置参数或关键字参数；传参必须 `TypeError`。它不支持 inline constraint。模板仍不得实例化或随机化。

### 5.2 `pre_randomize` 与 `post_randomize`

普通 `randomize()` 和 `randomize_with()` 必须实现 SV 生命周期：

```text
pre_randomize()
  -> 求解和事务写回
  -> post_randomize()       # 仅成功时
```

- `pre_randomize()` 在每次 randomization 尝试时调用。
- `post_randomize()` 只在该次 randomization 成功写回后调用。
- 用户可重定义这两个 hook；如需保留基类处理，应按普通 SV/Python 继承约定调用 `super()`。
- hook 异常原样传播。求解器写回仍维持一次 `randomize()` 内的事务语义；用户在 hook 中主动修改的数据不属于求解器回滚范围。

由于分优先级过程对每个 priority 批次调用一次正常 `randomize()`，hooks 会被多次调用：每批次一次 `pre_randomize()`，每个成功批次一次 `post_randomize()`。

hook 可通过以下同名 Python/SV 方法判断当前调用是否由分层入口发起，并取得当前批次的
priority：

```python
if self.svtypes_layered_randomize_active():
    priority = self.svtypes_layered_randomize_priority()
```

- 在普通 `randomize()`，以及 `layered_randomize()` 返回后，`active()` 为 `False`。
- 在每个批次的 `pre_randomize()` 和成功的 `post_randomize()` 内，`active()` 为 `True`，
  `priority()` 为该批次的值，包括 builtin 的 `0` 和负 priority。
- `priority()` 在 `active()` 为 `False` 时返回 `0`，因此必须以 `active()` 区分 builtin
  priority `0` 与非分层调用。
- 这两个方法是框架拥有的方法，Python 子类不得重定义；生成的 SV 中也不得手工覆盖。

## 6. `layered_randomize()` 算法

对实际对象按以下步骤执行：

1. 枚举最终类的全部静态 SV 可控 rand target、最终有效 constraints，以及 layer 中列出的
   `DynArray` / `Queue`。
2. 精确快照静态 target 与 constraint 的 mode 表状态；对每个动态容器仅快照进入时已存在
   元素的逐元素 mode。两类快照都只用于退出恢复，不参与本次分优先级随机化的静态成员选择。
3. 临时关闭所有静态 rand target、动态容器的进入时元素和全部 constraint。
4. 将全部显式 priority 组与隐式 builtin priority `0` 一起按 priority 从高到低处理。
5. 对每个批次：
   1. 为属于该批次的全部静态变量和 constraints 开启 mode，不考虑进入方法时的 mode 值。
      对属于该批次的动态容器，只有进入时开启的元素开启；进入时关闭的元素保持关闭。
      随机过程中新增的动态元素保持开启。
   2. 调用一次普通 `randomize()`。
   3. 若返回 `False`，停止后续批次并进入退出流程。
   4. 若返回 `True`，关闭刚完成批次的成员，继续下一批次。
6. 无论正常返回、`False` 或异常，都在 `finally` 等价路径中恢复步骤 2 的 mode 快照。动态
   容器只按仍存在的原始编号恢复；新增元素保持开启。动态容器整体 mode 与尺寸不属于此算法。

因此：对于静态成员，进入前关闭的成员会在其声明的 priority 批次中照常参与随机化；调用前
modes 对其内部行为没有影响，只会在退出时被完整恢复。动态容器遵循前述逐元素例外。高 priority
成功值在低 priority 批次中是 state；失败不回滚此前成功批次的值；`RandomContext.call_index`
按每次实际 `randomize()` 推进；无显式 layer 时只执行一次 builtin 批次。若同时存在 priority
`100`、builtin `0` 和 priority `-50`，执行顺序必须是 `100 -> builtin -> -50`。

`pre_randomize()` 内主动修改 mode 仍遵循 SV hook 语义，并可影响它所在的那一次普通 `randomize()`；这不属于调用 `layered_randomize()` 前遗留的外部 mode 设置。无论 hook 如何修改，方法退出时仍恢复进入时快照。

## 7. 失败信息与状态

现有 `svtypes_randomize_status` 只有 `ok` 和 `reason`（`"sat"`、`"unsat"`、`"state_xz"`），不能报告 UNSAT 涉及的 constraint、state field path 或失败优先级。

本功能必须新增只读的分优先级状态：

```python
@dataclass(frozen=True, slots=True)
class LayeredRandomizeStatus:
    ok: bool
    reason: str
    failed_priority: int | None
    failed_aliases: tuple[str, ...]
    randomize_status: RandomizeStatus | None
```

- 首次调用前为 `None`。
- 全部成功时，`failed_priority is None` 且 `failed_aliases == ()`。
- 返回 `False` 时必须记录失败批次的 priority 和该批次全部 alias。
- `svtypes_randomize_status` 保持最后一次实际 `randomize()` 的状态。
- hook 或 backend 抛异常时异常原样传播，且不得把它伪报为 `unsat`。

同时应扩展普通 `RandomizeStatus` 的可选诊断：至少在 `state_xz` 时提供 `state_path`；若 backend 可给出 UNSAT 解释，可附约束名和摘要，但首版不承诺 UNSAT core。

## 8. SystemVerilog 生成

生成的每个 SV class 必须包含默认的：

```systemverilog
virtual function int layered_randomize();
```

其行为必须与 §6 对齐：保存/关闭 mode、按 priority 批次启用、调用内置 `randomize()`、记录失败、关闭批次并在所有退出路径恢复 mode。生成代码只对 §2.3 允许的 target 发射 `rand_mode()`。

生成的 `layered_randomize()` 会自然触发 SV 的 `pre_randomize()` / `post_randomize()`，每个批次各一次普通 randomization 调用。它必须与 SV 的内置 `randomize()` 一样保持 `virtual`。SV 仿真无需与 Python 抽样结果逐 bit 一致，但 mode、优先级、成功/失败和 hook 次数必须一致。

## 9. Schema 与 IR 身份

source schema 是类型的源级语义描述，并参与 `schema_fingerprint`。它必须记录 priority/alias、每个组的变量 target 与 constraint 名，以及隐式 builtin 的最终归属。改变这些内容必须改变 source schema 和 schema fingerprint。

`ir_digest` 是单一 constraint 的规范化 typed IR 哈希，用于识别约束谓词是否变化，并忽略源码空白、注释等非语义差异。priority、alias 与成员归属改变的是随机化调度，不是 constraint predicate；它们**不得**进入该 constraint 的 `ir_digest`。因此仅移动 constraint 到另一 priority 时 source schema 会改变，而其 `ir_digest` 保持不变。

encoding schema、encoding fingerprint 与二进制布局不因本功能改变。

## 10. 实现边界与验收

主要实现点：

- 新增 `@rand_layer` declaration、受限 AST parser（含同名 `super()` 合并）与类创建期收集/校验。
- 为最终类计算变量、constraint 与 builtin 的归属，并实现跨优先级诊断策略。
- 按 SV target 粒度调整/验证 runtime mode 管理。
- 为 `randomize()` / `randomize_with()` 增加 hook 生命周期并保护三个入口不可覆盖。
- 实现 Python `layered_randomize()`、精确 mode 恢复和 `LayeredRandomizeStatus`。
- 生成 SV `layered_randomize()` 并用远程 SystemVerilog target 编译与执行验证。
- 将分组数据纳入 source schema，但保持单 constraint `ir_digest` 不变。

最低验收覆盖：

1. `@rand_layer` API、函数名 alias、非法参数和 source 不可得错误。
2. 同 priority 合并为一次调用；全部 priority（含 builtin `0`）按数值从高到低执行。
3. 变量/constraint 唯一归属、继承、同名 layer 的 `super()` 合并和 constraint 替换。
4. 低 priority 引用 warning 与全局 error 升档。
5. 进入前关闭的成员仍按 `@rand_layer` / builtin 声明参与内部随机化，且所有正常/失败/异常路径精确恢复进入时 modes。
6. UNSAT、`state_xz`、hook/backend 异常的失败层级报告及成功值不回滚。
7. `pre_randomize` / `post_randomize` 的次数、成功条件和继承行为。
8. Python final-entry 拒绝与返回 `int` 的 SV virtual `layered_randomize()` 的 SystemVerilog target 编译/运行。
9. source schema 的分组变化、`ir_digest` 的谓词稳定性和 encoding identity 不变。
10. fixed array 元素 target 的 Python/SV 精确 mode、跨 priority state 与非法索引。

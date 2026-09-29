# SvTypes Cookbook：从 Python 模型到验证基础设施

本 cookbook 让你按步骤掌握 SvTypes 的核心心智模型：**声明是单一事实来源**。同一个 `SvObject`
声明可以驱动 Python 值、二进制编码、约束随机、功能覆盖率、生成的 SystemVerilog/C++ 与 schema。
不要把它当成把 Python 对象临时转换为字符串的库；字段声明会定义稳定的二进制布局与跨语言契约。

每章都有一个可运行文件，位于 [`examples/cookbook`](../examples/cookbook/README.md)。建议在仓库根目录逐章执行：

```sh
PYTHONPATH=python:. .venv/bin/python examples/cookbook/01_define_and_serialize.py
```

完成一章再进入下一章。示例使用断言，因此没有输出不代表没有检查：命令以零退出码结束才表示该章完成。
每个示例文件中的注释也是教程正文的一部分：先完整运行一次，再从上到下阅读“声明 → 操作 → 断言”三段，
最后只改练习所指定的一处并重跑。不要跳过断言；它们说明了该段代码应当保证的语义，而不是装饰性输出。

## 学习地图

| 先后 | 解决的问题 | 实操章 | 继续深入 |
|---:|---|---|---|
| 1 | 如何把协议字段变成跨语言稳定布局？ | 第 1 章 | `Bit`/`Logic`/`Enum`、schema descriptor |
| 2 | 如何表达可变数据和对象身份？ | 第 2 章 | `DynArray`/`Queue`/`AssocArray`、`Object`、`CodecSession` |
| 3 | 如何生成合法且有偏好的 stimulus？ | 第 3 章 | `dist`、`soft`、`unique`、`randc`、`solve_before` |
| 4 | 如何显式控制多阶段决策？ | 第 4 章 | `@rand_layer`、mode、hooks、继承 |
| 5 | 如何证明 stimulus 覆盖了目标空间？ | 第 5 章 | bins、cross、coverage database、UCIS |
| 6 | 如何让构建系统稳定消费模型？ | 第 6 章 | `Package`、`generate`、SV/C++ runtime、CI check |
| 7 | 如何安全跨进程和跨版本交换？ | 第 7 章 | fingerprint、`RemoteRef`、capability、`DecodeLimits` |

## 0. 准备环境与建立工作方式

建立虚拟环境并安装本地实践所需依赖：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install pytest z3-solver
```

以下命令运行 cookbook 全部实践：

```sh
for lesson in examples/cookbook/[0-9][0-9]_*.py; do
  PYTHONPATH=python:. .venv/bin/python "$lesson"
done
```

开发时把这三层分开理解：

1. **声明层：** `SvObject`、字段 descriptor、`@constraint`、`@covergroup`。这里决定语义。
2. **运行层：** Python 中的赋值、`pack` / `unpack`、`randomize`、`sample`。这里验证行为。
3. **发布层：** `generate(package, directory)` 输出已冻结声明的 SV、C++、schema、coverage manifest。

完整项目回归还包括配置好的远程 SystemVerilog conformance target；它是开发基础设施，不是 cookbook
的前置条件。请勿把本地 target 配置、日志或专有报告提交到项目。

## 1. 从字段声明到稳定字节流

运行 [`01_define_and_serialize.py`](../examples/cookbook/01_define_and_serialize.py)。它定义了 `Header`：

```python
class Header(SvObject):
    address = Bit[16]()
    length = Bit[8]()
    opcode = Opcode()
    payload = Array[Bit[8], 4]()
    retry_count = Int(rand=False)
```

### 你正在学习什么

- `Bit[w]` 是固定宽度、二态的 integral 字段；`Logic[w]` / `Reg[w]` 用于需要 X/Z 的四态值。
- `Enum[Bit[w]]` 给底层 bit 宽度加上命名语义；使用真实 enum member 赋值，而不是散落的 magic number。
- `Array[T, n]` 是定长布局；`DynArray[T]`、`Queue[T]` 与 `AssocArray[K, V]` 则具有各自的长度/键语义。
- `rand=False` 只影响 constrained-random 的默认参与，不改变序列化。所有声明字段依然进字节布局。

`to_bytes()` 是 `pack()` 的方便入口；`unpack(payload)` 返回 `(decoded_object, consumed_bytes)`。始终检查
`consumed_bytes == len(payload)`：这既检测截断，也检测调用端混入了额外帧数据。

**跟读代码：** 先看 `Header` 中五行 descriptor 声明，理解“class 属性是字段模板”；再看 `main()` 的
`.value` 赋值，理解“实例字段承载运行时值”；最后看 `encoded` / `decoded` 与三个断言，理解“编码后的新对象
不依赖原对象仍能恢复同一值”。示例内已在这三处标出原因。

**练习：** 将 `payload` 改为 `Array[Bit[16], 2]`，观察长度和 bytes 的变化。然后将 `opcode` 改成
`Logic[2]`，尝试以 `LogicValue.from_string("1x")` 赋值；这会引出第 5 章中四态覆盖的特别规则。

## 2. 容器、handle 与对象图

运行 [`02_collections_and_graphs.py`](../examples/cookbook/02_collections_and_graphs.py)。这章的 `Node.next`
是 `Object["Node"]` handle，而不是嵌套值。`Message.path` 故意在 queue 中重复引用第一个节点。

### 关键判断：值还是引用？

- 对象作为字段值内嵌时，父对象拥有它的内容；对象会随父对象一同编码。
- `Object["Type"]` 代表可为空的 handle；图编码保留共享身份和环。
- 对象 handle 的类型必须属于同一 `ObjectRegistry` / `Package`。教程使用 `@svobj(registry=MODEL)` 显式
  注册，避免前向引用或多模块加载时产生不明确的类型归属。
- `CodecSession` 用于隔离对象编号与 registry 生命周期；服务端、测试并发或长寿命 channel 不应依赖
  隐式全局对象表。

**跟读代码：** `MODEL.clear()` 说明教程为何可重复执行；两个 `@svobj` 说明 handle 类型为什么必须注册；
`[first, second, first]` 与 `is` 断言则刻意区分了“值相等”和“同一个对象”。把最后一个 `first` 改成
`Node()` 后，重新运行并观察身份断言为何改变。

**练习：** 删除 `second.next = first`，确认重复引用仍保留；再把 `path` 改为 `DynArray[Object["Node"](...)]`
并思考何时应使用 queue 语义、何时应该使用动态数组语义。

## 3. 受约束随机：先写可读的合法空间

运行 [`03_constrained_random.py`](../examples/cookbook/03_constrained_random.py)。`@constraint` 方法是
**source-only DSL**：框架解析其源码、构建约束 IR、交给 Python 求解器并生成同形 SV；它不是供用户在
运行时直接调用的 Python 函数。

本章依次演示：

```python
self.address % 16 == 0             # 普通硬约束
self.opcode @ dist[0 @ 4, 1 @ 2]   # 权重分布（完整语句）
unique(self.tags)                  # 数组所有元素互异
self.payload.size() == self.length # 动态数组长度
for index in range(self.payload.size()):
    self.payload[index] == index    # SV 同形 foreach 约束
soft(self.opcode == 0)              # 可被硬约束推翻的偏好
```

用 `RandomContext(seed=...)` 让例子、单测和 bug 报告可复现。正常流程只需要 `obj.randomize()`，其返回值为
`bool`；`False` 表示当前启用约束不可满足，值保持该次调用前的状态。可使用 `field.rand_mode(0)` 或
`obj.constraint_name.constraint_mode(0)` 暂时移出一个随机变量/约束，随后务必恢复。

**跟读代码：** 先只阅读 `legal()`：每条语句都是对可行解空间的一条说明；再看 `RandomContext`，它并不改变
约束，只使求解选择可复现；最后逐项看断言如何把“地址对齐、长度、payload、互异 tag”映射回声明。尝试删除
`self.payload.size() == self.length`，体会 `unique` 和元素循环不会替你决定动态长度。

### 何时使用这些技巧

- 用 `dist` 表达 stimulus 频率，不要通过多套重复 constraint 硬编码概率。
- `soft` 表示默认偏好，而不是“可能被无声违反的硬约束”。
- `unique` 适合 tag、ID、lane 分配；它不会自动决定动态容器长度。
- `randc=True` 适合小型二态 scalar 的无重复循环，不能和 `dist` 合用。
- `solve_before(before, after)` 只控制选择顺序，不增加可满足性；只有确实需要可重复的选择偏向时才引入。

完整语法、限制和 Python/SV 对齐细节见
[高级随机化规格（中文）](SVTYPES_1_4_ADVANCED_SCALAR_RANDOMIZATION_ZH.md)及
[动态容器随机化规格](SVTYPES_1_5_DYNAMIC_COLLECTION_RANDOMIZATION.md)。

## 4. 分优先级随机：把决策过程显式化

运行 [`04_layered_random.py`](../examples/cookbook/04_layered_random.py)。它先在 priority `100` 选择 opcode，
再在 `10` 选择依赖该 opcode 的 transfer。未列入显式 layer 的成员属于隐式 `builtin` priority `0`，所以
本例 hook 观察到 `100 → 10 → 0`。

```python
@rand_layer(100)
def choose_opcode(self):
    self.opcode
    self.opcode_legal
```

同 priority 的所有 alias 合并成一次随机化。更高 priority 成功后的值，在更低 priority 的求解中是 state；
失败不会回退已经完成的批次。方法退出时，进入前的 mode 状态一定恢复。

在 `pre_randomize` / `post_randomize` 中使用
`svtypes_layered_randomize_active()` 和 `svtypes_layered_randomize_priority()` 区分当前批次。不要重定义
`randomize()`、`randomize_with()` 或 `layered_randomize()`；这些入口属于框架。继承类要扩展父 layer 时，只能
使用同名、同 priority 的 `super().layer_name()`。

**跟读代码：** `choose_opcode` 和 `choose_transfer` 不是普通方法调用；其函数体只是 layer 成员清单。先看
两个 layer 各自列出的字段/constraint，再看 `pre_randomize` 如何记录框架实际执行顺序，最后将 priority `10`
改为 `200`，预测并验证输出顺序和随机结果会怎样改变。

完整约束、动态容器 mode 与跨优先级引用诊断见
[分优先级随机化规格](SVTYPES_LAYERED_RANDOMIZATION_SPEC.md)。

## 5. 功能覆盖率：声明、实例化、采样、检查

运行 [`05_functional_coverage.py`](../examples/cookbook/05_functional_coverage.py)。在 `@covergroup` 内部，
`CovPoint` 定义观测源和 bins，`Cross` 定义成员组合：

```python
class opcode_cp(CovPoint, source=self.opcode):
    read = bins[0]
    write = bins[1]

class opcode_mode(Cross, members=(opcode_cp, mode_cp)):
    write_active = bins[opcode_cp.write, mode_cp.active]
```

覆盖组是声明模板；每个对象需在构造期间 `self.cg.instantiate()`，之后才可以 `sample()`。`sample_count`
统计公开 sample 调用，`instance.snapshot()` 给出具名 bins 的 hit count，`get_inst_coverage()` 给出实例百分比。

继续实践时依次加入：

- `iff=lambda: self.valid == 1`：guard 为假时不采样该 point/cross；
- `ignore_bins`、`illegal_bins` 与 `default_bins`：它们有不同的分母和诊断意义；
- `transition_bins[0, 1]` 与 `repeat(term, min, max)`：覆盖随样本历史变化的序列；
- `CovPointArray`：固定数组每个槽位是具名 coverpoint；容器整体 value-domain point 不能作为 cross 成员；
- `CoverInput[T]` / `CoverRef[T]`：前者用于 covergroup 实例化时的布局参数，后者绑定采样来源。

使用 `CoverageDatabase` 汇总已冻结声明的记录；使用 UCIS 时只通过显式 frozen binding 导入/导出。它是互通投影，
不是内部数据库的替代。功能覆盖率的完整语义、资源边界和当前 target capability gate 见
[2.0 覆盖率路线图](SVTYPES_2_0_COVERAGE_ROADMAP.md)。

**跟读代码：** 三个嵌套 class 分别展示值 bins、历史 bins 和组合 bins；`instantiate()` 解释模板何时变成
有 hit count 的实例；sample 循环的 `0 → 1` 顺序是 transition 能命中的必要条件；最后的 snapshot 断言说明
怎样测试具名 bin，而不是只看一个百分比。

## 6. 发布生成物：让构建系统消费声明

运行 [`06_generate_artifacts.py`](../examples/cookbook/06_generate_artifacts.py)。它先创建一个独立 `Package`，
显式注册类型，再生成：

```python
first = generate(package, output)
check = generate(package, output, mode="check")
```

默认产物包括：SystemVerilog package、C++ header、schema、coverage manifest 和顶层 manifest。生成器按稳定
依赖顺序发布，并记录受管理文件；`mode="check"` 不写文件，只报告缺失或已变更产物，适合 CI。

实际工程建议：

1. 将模型定义放在稳定 Python module，并用一个显式 `Package` 控制发布边界。
2. 在构建步骤调用 `generate(...)`，不要把 `.to_sv_obj()` 的字符串拼接散落到多个脚本。
3. 在 CI 运行 `generate(..., mode="check")`，防止 Python 声明与已提交生成物漂移。
4. 让下游 SVX/仿真构建通过 `sv_runtime_file()`、`cpp_include_dir()` 等公开 helper 查找已安装 runtime，
   不要猜测源码树路径。

**跟读代码：** 先看 `Package` 与 `@svobj(registry=package)` 的配对关系，再比较首次 `generate()` 与随后的
`mode="check"`：前者是发布，后者是 CI 漂移检查。不要在生产代码中把 `TemporaryDirectory` 原样照搬；它只为
教程保证零残留。

## 7. Schema、远程引用与运行时兼容性

运行 [`07_schema_and_runtime_contract.py`](../examples/cookbook/07_schema_and_runtime_contract.py)。这一章不再
关心某个样本的值，而是确认双方是否能安全地交换该值：

- `schema_descriptor(Type)` 给出 unified type name、schema fingerprint 和 encoding fingerprint。前者反映
  声明语义，后者反映二进制编码契约；把它们记录在发布物或握手中，而不是只比较 Python 类名。
- `RemoteRef["domain.Type"]` 是跨边界对象身份，不是本地对象 handle。它只携带目标类型域与对象编号，
  不会递归序列化远端对象。
- `runtime_capabilities()` 和 `require_runtime_compatible(...)` 用于运行时/生成物启动时的能力协商。
  对方多报未知能力可以前向兼容；缺少你真正依赖的能力或格式版本不一致必须在传输前失败。
- 解码外部或不可信输入时，在 session 的 unpack context 上使用 `DecodeLimits` 限制输入字节、动态长度、
  引用数与嵌套深度。不要把默认资源上限当作网络边界防护策略。

**跟读代码：** `RemoteRef[...]` 的字符串是稳定类型域而非 Python class；`RuntimeCapabilities` 中故意加入
未知 future capability，演示前向兼容；`require_runtime_compatible(...)` 则证明“多报可以、缺少所需能力或
版本不等不可以”。

## 8. 从主线进入专项能力

以下能力没有塞进主线脚本，以免第一次学习时同时面对太多概念；它们仍属于 SvTypes 的正式能力。
先完成前七章后，按需求选择一条支线。

### 参数化、类型规格与记录 schema

需要一个模型在不同宽度、元素类型或协议版本间复用时，使用 `Parameter`、`ParamRef` 和
`TypeSpec` / `Signed[...]`，而不是通过运行时修改 class field。参数化声明仍是静态、可生成、可 fingerprint
的类型契约。外部 schema 驱动的记录可使用 `RecordSchema` 生成对应的数据类；适合你已有一份权威 schema、
但仍需要同一套 pack/unpack 与生成基础设施的场景。

开始前阅读 [二进制格式（中文）](svtypes_binary_format_ZH.md) 的参数与 descriptor 约束，并把每一个 specialization
加入第 6 章的 package generation check。

### 外部字段存储

`ExternalFieldStorage` / `MemoryExternalFieldStorage` 将字段值托管给外部拥有者，同时保留 SvTypes 的
声明与编码规则。这适用于模拟器镜像、共享内存或生命周期由框架控制的对象。它不是绕过 descriptor 的捷径：
先建立 storage 生命周期、关闭行为和 ownership 的测试，再调用 `bind_external_value`。具体契约见
[外部字段存储要求](SVTYPES_EXTERNAL_FIELD_STORAGE_REQUIREMENTS.md)。

### 设计期 coverage 工具

覆盖率 GUI 面向**声明设计**：扫描模型、展示 covergroup/point/cross/bins，并辅助编辑 proposal；它不是
运行时 coverage database 或 HTML report 浏览器。完成第 5 章后，运行：

```sh
.venv/bin/python examples/post_2_0_coverage_tools/run_gui.py
```

教程示例与 GUI 工作流见
[coverage tools walkthrough](../examples/post_2_0_coverage_tools/README.md)。编辑器的值域/transition
交互规范见 [coverage GUI domain editor](SVTYPES_COVERAGE_GUI_DOMAIN_EDITOR.md)。

### Python/SV 一致性

当一个需求具有既定 SystemVerilog 语义时，先以 SV 语义设计声明，再分别验证 Python 运行时与生成 SV。
远程 conformance target 是项目测试基础设施的一部分；其配置与私有适配器绝不应进入应用代码、生成物或
公开文档。若当前 target 不能接受或观测某项 LRM 允许的能力，SvTypes 保留 Python 语义并明确标为
capability gate，不以静默 fallback 假装完成 target parity。

## 9. 把它接入真实项目：实用检查表

在将 cookbook 模型扩展为项目模型前，逐项确认：

- [ ] 字段类型、宽度、signedness、enum 底层类型和集合边界是明确的。
- [ ] 每个 `pack` / `unpack` 边界检查已消费字节数，恶意或截断输入使用合适的 `DecodeLimits`。
- [ ] 需要共享身份的字段使用 `Object[...]`，并在显式 registry/session 下测试环与重复引用。
- [ ] 约束失败、关闭 mode、`randc` 循环、动态容器 resize 都有 Python 回归。
- [ ] 覆盖组在对象构造期实例化；coverage 的声明、采样与数据库汇总测试分开。
- [ ] 构建只消费 generator 的稳定产物；CI 既检查 source 行为，也检查发布产物未漂移。
- [ ] 若需要 SystemVerilog parity，使用项目配置的远程 conformance target；能力 gate 不能用 Python
  fallback 偷偷掩盖。

下一步请回到 [API 参考（中文）](reference_ZH.md) 查询精确签名，并把 cookbook 中的单一模型逐步替换为你的协议。
当需求与 SystemVerilog 已定义的语义相交时，以对应 SV 语义为准，再用 SvTypes 的 Python 与生成路径同时验证。

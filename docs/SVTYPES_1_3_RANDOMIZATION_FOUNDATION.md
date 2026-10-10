# SvTypes 1.3 Randomization Foundation

**状态：随机语义契约已实现；性能与回归证据见 `SVTYPES_RANDOMIZATION_BENCHMARKS.md`。**
**范围：从 1.2.0 推进至 2.0.0 的 Python 受约束随机阶段。**

## 1. 目标与边界

SvTypes 2.0.0 是 Python 受约束随机和 Python 功能覆盖率的功能终版。
本文件规定先行随机阶段的语义契约；coverage 的语法、语义、数据库和 UCIS
映射已由 `SVTYPES_2_0_COVERAGE_ROADMAP.md` 统一规定。

2.0.0 不以完整复刻 SystemVerilog 验证语言为目标。随机阶段不包括 SVA、
时序覆盖、SVX/UVM/DPI、C++ randomize 或 coverage runtime。生成 SV 的
既有约束能力必须保持兼容；Python-only 的后续构造若尚未支持生成，必须在
SV 生成期清晰失败，不能静默改变语义。

## 2. 版本路线

| 版本 | 交付 | 完成条件 |
|---|---|---|
| 1.3.0 | 随机语义设计和求解边界 | 冻结本文件的随机域、handle 规则、诊断和内部 `SolveRequest` / `SolveResult` 边界。 |
| 1.4.0 | 高级标量随机 | `randc`、`dist`、`soft`、`solve before`、`unique`、范围/集合语义与稳定分布策略。 |
| 1.5.0 | 容器随机 | dynamic array、queue、associative array 的 size/value、容量和迭代约束。 |
| 1.6.0 | handle 与随机验证闭合 | 已构造非空 handle 的递归联合求解、共享引用/环去重、随机全量回归、分布和性能基准。 |
| 1.7.0+ | coverage 阶段 | 随机完成后，联合设计 coverage 语法、运行时、数据库和 UCIS 映射。 |
| 2.0.0 | 功能终版 | 所有承诺随机与 coverage 能力完成并冻结公开契约。 |

## 3. 随机域

2.0 的 Python 随机域包括当前可随机的整型、枚举、`Bit`、`Logic` 的
二态投影、固定数组和 `SvStruct` leaf；随后扩展到 dynamic array、queue
和 associative array。`Logic` 成功写回时清除 X/Z；作为 frozen state
参与约束的 `Logic` 含 X/Z 时，返回 `False` 并报告 `state_xz`。

`Real`、`ShortReal`、`String`、`RemoteRef` 不进入随机求解域。它们可继续
作为普通建模与序列化字段，但不能因 2.0 随机实现而获得隐式随机语义。

## 4. Handle 规则

SvTypes 对齐 SystemVerilog `rand` class handle 的关键规则：

- 随机化不修改 handle 的身份、null 状态或对象引用拓扑；
- 随机化不分配对象；null handle 不参与求解；
- 已构造且非空的 `rand` handle 所指对象，其随机变量和约束与拥有者的
  随机变量、约束进入同一次联合求解；
- handle 不支持 `randc`；
- 共享引用和环只影响遍历：同一对象在一次求解中只收集一次，不改变图。

该能力不是“对象图拓扑随机化”。

## 5. 求解边界

类级 `ConstraintIR` 继续是声明的稳定表示；运行时 modes、对象 state、
候选采样和策略不得进入 `ir_digest` 或 source schema。

每一次普通 `randomize()` 在执行 hooks 后构造一个运行时 `SolveRequest`：

```text
enabled ConstraintIRs + active random paths + frozen state + variable index
                                    │
                                    ▼
                          candidate sampler / solver backend
                                    │
                                    ▼
                           SolveResult(bit assignments | UNSAT)
```

1.3 保持 1.2 的行为：先进行固定次数的确定性候选抽样，未命中时由 SMT
backend 产生确定性的最小模型。后续版本可以在此边界上实现 distribution、
soft priority 和 solve ordering，而无需改变公开 `randomize()` API。

## 6. 兼容性与验证

`randomize()`、`randomize_with()`、`layered_randomize()`、mode、hooks、
继承和现有 `RandomContext` 的公开语义在 1.3 保持不变。每个后续随机
能力必须同时具有：成功、UNSAT、mode/hook 交互、确定 seed、继承和
layered-randomize 回归；分布特性另需统计回归和可配置资源上限。

## 7. 当前求解策略与资源预算（2026-10-10 确认并实现）

本节的抽样策略和公开预算接口已获确认并实现。第 5 节保留 1.3 的历史
实现事实；当前运行时按本节执行。该专项的验收不等于 2.0 发布冻结完成。

### 7.1 语义底线

以 IEEE 1800-2017 的 18.5.4、18.5.10 为依据：普通随机应对合法值组合
等概率；`dist` 的零权重必须排除对应值。没有其他约束时，权重决定比例；
其他约束使指定分布无法满足时，标准对权重有放宽，但不能把实现的资源
耗尽当成已经证明分布无法满足。多个相互关联分布的具体组合策略不能
仅凭模型数乘积推定为 LRM 唯一算法，应保留已有约定并用可观测用例验证。

因此不建议把“每个 bit 随机偏好一个可行分支”直接作为普通随机的终版
策略。两个可行分支可能包含不同数量的完整解，等概率选分支不意味着
等概率选解。仅设置后端 random seed 也不能证明满足分布要求。

### 7.2 抽样路径

- SMT 负责可行性、soft 冲突判定及域收紧；不再把确定性最小模型直接作为
  抽样失败后的公开随机结果。内部证明/枚举可以继续使用确定性 witness。
- 小空间完整枚举后等概率抽取；保留精确有理数加权与任意位宽整数抽取。
- 大空间先分解独立约束分量，使用能证明等概率的候选生成器。例如相同
  有限值域中的 `unique` 元素，可以均匀无放回生成，排列情形使用均匀洗牌；
  其他硬约束通过拒绝抽样筛选。识别不到结构时保留均匀域候选的拒绝抽样，
  不用有偏 witness 冒充完成随机。各 fast path 都需证明其提案概率及映射
  重数，不能只证明生成值满足约束。
- `solve_before` 的同一 before 层按联合可行投影抽取，层间依次进行，末层
  按条件可行空间完成；不把同一层拆成逐字段条件抽样，也不提前固定末层
  的 `dist` 结果。已可求值的 before 分布在对应投影阶段应用，后续不重复
  应用。randc 先于普通变量选择；完成解数量、完成解的权重拒绝不能让已选
  投影重新抽取。循环、失败不提交历史和 mode 语义不变。
- `dist` 超过当前 4,096 枚举阈值时继续尝试可验证的分布抽样、分量分解或
  精确枚举/计数路径。阈值是策略切换点，不是语义边界，也不授权忽略权重。
  如预算内无法完成所需抽样，明确报告资源失败；不会把这类失败视作能力
  验收完成，更不能以简单的报错替代后续规模与统计验收。

每次成功抽样必须同时满足有效硬约束、可满足 soft 优先级和适用的分布
规则。策略调整允许有约束输出序列变化；保留基础无约束 bit-stream 向量，
同一实现版本、后端、预算配置与 seed 的成功抽样可复现。墙钟超时受负载
影响，不承诺在不同机器上得到相同的超时位置或成功/失败序列。

### 7.3 公开预算接口

在 `RandomContext` 增加两个仅影响 Python 求解的关键字参数：

```python
with RandomContext(seed=7, solve_timeout_ms=30_000, solve_check_limit=100_000):
    ok = packet.randomize()
```

示例值也是当前缺省值。参数接受非负整数或 `None`，`0` 表示立即耗尽
相应预算，布尔值及其他类型无效；单项 `None` 显式关闭该项保护。
`randomize()`、`randomize_with()`、`layered_randomize()`
的签名不变；这些运行时参数不写入源 schema、约束摘要或生成的 SV。

预算共享于一次普通 `randomize` 的整个求解过程，包括候选、soft 试探、
动态 size/元素、图子对象、randc 重新开周期以及分布枚举，不能在进入一个
helper/新建 solver 后重置。每个 backend check 消耗一次检查预算，并使用
剩余墙钟预算限制该次检查；扩展/枚举/抽样循环也周期性检查截止时间。
hooks、用户代码和失败清理不计入求解预算，不承诺入口方法严格实时返回。
`layered_randomize` 内每次普通随机共享该次调用的预算，不把多个层强行
合并为一次 SV randomize；状态仍指出失败层。

### 7.4 结果与恢复

保留公开布尔返回及现有状态字段，增加可选的 `failure_phase` 和
`backend_reason` 诊断字段（成功时为 `None`），并区分：

- `unsat`：确实证明当前有效约束无解；
- `timeout`：求解截止时间耗尽；
- `resource_limit`：检查预算或内部安全资源上限耗尽；
- `unknown`：后端未能判定且不能归因于上述已知预算；
- `sat`：已完成符合适用抽样语义的赋值，而不只是找到了任意可行 witness。

后三类失败返回 `False`，但不能改名为 UNSAT。内部 `SolveResult` 需保留
SAT/UNSAT/UNKNOWN 的区别并携带原因；UNKNOWN 不能当作 soft 冲突而丢掉
约束，也不能当作证明另一 bit 分支可行、枚举完成或 randc 周期耗尽。
不发布试探值、不消耗 randc 历史、不调用成功 post hook；恢复规则延用
既定普通/分层随机语义，用户 hooks 的显式副作用不新增事务回滚。

动态 size 使用完整投影枚举或均匀 size 提案，不再优先发布最小 size。
如果仅尝试了有限 size 提案，元素求解失败不能证明所有 shape 无解，
此时报告 `resource_limit`，而不是 `unsat`。内部枚举/提案阈值用于策略
切换或安全终止，不是支持值域边界；不会通过无权重或最小解回退绕过它们。

### 7.5 验收要求

结构化排列至少覆盖 4/8/16/32 元素的多样性、seed 重放和规模性能；对小
可穷举域将实际频率与完整解空间比较，包含不对称分支、soft、solve_before、
randc、动态图对象和分布组合。超过 4,096 的用例必须完成有意义的成功抽样
和统计验证，不能只验证拒绝或超时。资源测试同时覆盖低检查预算、明确
timeout、独立 UNKNOWN、真实 UNSAT，以及模式、值和循环历史恢复。
不以墙钟敏感的测试替代可控 backend 注入测试；当前版本还需完整本地与
适用的远程验收。性能整改目标只有在这些实作与验收完成后才能关闭。

### 7.6 当前验收结果

本节已完成专项实现与验收。新增测试覆盖 4/8/16/32 元素排列与 seed
重放、非对称空间频率、8,192 模型的成功加权抽样、before 同层联合
概率、before 自身权重与条件完成权重、randc 投影及失败历史、确定性
timeout/UNKNOWN 注入、低预算、动态值恢复和分层模式恢复。
远程目标实际执行有符号边界与 32 元素 unique；极大预算也不会使后端
超时选项回绕或发生浮点溢出。最终全量回归为 **782 passed，264.09 秒**，
包含配置好的全部远程测试，无 skip/xfail。

后续修复了 `randomize_with()` 内联 IR 缓存的函数生命周期错误：不再按
可复用的 `id(fn)` 查找，而按活函数弱键及接收类缓存。新增五项回归覆盖
缓存命中、接收类隔离、函数回收和 SAT/UNSAT 不混用；修复后的最新完整
回归为 **787 passed，327.57 秒**，包括配置远程测试，无 skip/xfail。
该改动不改变约束声明、随机分布策略、公开 API 或生成 SV。

规模、多样性、长期内存与先前 coverage 优化的实测记录统一保存在
`SVTYPES_RANDOMIZATION_BENCHMARKS.md`。本次关闭的是已识别不足的
整改目标，不替代 2.0 RC 的完整发布冻结；未提交、未推送。

依据：[IEEE 1800-2017（18.5.4、18.5.10）](https://fpga.mit.edu/6205/_static/F25/documentation/1800-2017.pdf)、
[Z3 Python Solver API](https://z3prover.github.io/api/html/classz3py_1_1_solver.html)、
[Z3 check 的资源保护实现](https://github.com/Z3Prover/z3/blob/master/src/api/api_solver.cpp)。

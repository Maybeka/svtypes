# SvTypes 1.3 Randomization Foundation

**状态：Working design**
**范围：从 1.2.0 推进至 2.0.0 的 Python 受约束随机阶段。**

## 1. 目标与边界

SvTypes 2.0.0 是 Python 受约束随机和 Python 功能覆盖率的功能终版。
本文件只规定先行的随机阶段；coverage 的语法、语义、数据库和 UCIS
映射将在随机阶段完成后共同设计。

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

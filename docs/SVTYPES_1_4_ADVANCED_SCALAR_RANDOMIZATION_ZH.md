# SvTypes 1.4 高级标量随机化设计

> **中文正本。** 对应英文镜像：[SVTYPES_1_4_ADVANCED_SCALAR_RANDOMIZATION.md](SVTYPES_1_4_ADVANCED_SCALAR_RANDOMIZATION.md)。
> 两个版本必须在同一变更中同步更新；同步校验记录见
> [translation_manifest.json](translation_manifest.json)。

## 范围

1.4 增加了除普通 Boolean predicate 外还需要 solver-policy 支持的 SystemVerilog scalar constrained-random
构造：`dist`、`randc`、`soft`、`solve before`、`unique`。它刻意与 container randomization（1.5）及 non-null
object-handle participation（1.6）分开；后续能力复用本阶段的同一 IR。

本文记录已接受的 scalar interface 与 semantic contract。

## `dist` 源码接口

导入 source-only marker，并把 distribution 写成一个完整 constraint statement：

```python
from svtypes import constraint, dist

@constraint
def legal(self):
    (self.opcode + self.offset) @ dist[
        MODE_A @ self.weight_a,
        (self.min_addr, self.max_addr - 1) @ 2,
        (self.limit - 1, self.limit) / self.range_weight,
        MODE_IDLE,
    ]
```

它生成：

```systemverilog
(opcode + offset) dist {
    MODE_A := weight_a,
    [min_addr : max_addr - 1] := 2,
    [limit - 1 : limit] :/ range_weight,
    MODE_IDLE := 1
};
```

外层 `@` 只属于 DSL 语法；constraint method 被解析而非执行。`dist` 被导出以便显式 import 与 type checker
可见，但没有通用 runtime operation。

Python `@` 具有与 multiplication 相同的 precedence，因此 compound left expression 必须加括号。left expression、
item value、range bound、weight 都只接受受支持的 integral constraint expression subset。left expression 至少要含一个
randomized solver leaf；普通 type resolution 会拒绝 non-integral expression。

`value @ weight` 表示 SV `value := weight`；`(lo, hi) @ weight` 表示 `[lo:hi] := weight`；
`(lo, hi) / total` 表示 `[lo:hi] :/ total`。未加 weight 的 singleton 视为 `:= 1`。zero weight 排除该 item；
statically negative weight 是 declaration error，dynamically negative result 是 invalid solve candidate，不能被选中。

`dist` 始终是完整 constraint statement，与 SV `expression_or_dist` grammar 一致。它可以置于普通 constraint `if`
body 中，以控制 distribution 是否 active；与 Python `and` / `or` 组合会被拒绝，不能赋予偶然的 weighting rule。

## IR 与执行语义

typed IR 以 immutable item sequence 表示 Boolean `dist` expression。每个 item 记录 `low`、可选 `high`、`weight` 与
weight 是 per value（`:=`）还是 range total（`:/`）。它进入 stable constraint digest、直接生成 SV、被 Python evaluator
作为 hard membership constraint 求值，并以同一 membership restriction 编入 SMT backend。

Python randomization 中，satisfying candidate 按 active distribution weight 的乘积接受。range-total weight 精确除以其
inclusive range 的 value count；实现使用 rational arithmetic 与 64-bit rejection draw，不用 floating point。因此 `:=`
与 `:/` 可观测地不同，同时保持 seeded execution 的确定性。

对于可由当前 state 解析的 direct distribution，Python 不会仅因超过 4096-combination enumerator 就把 large declared
range 退化为 minimum SMT model。低于此限制时，所有 support combination 都证明 SAT，并按 exact declared weight product
选择 satisfying combination；超过限制时，对每个 direct `:=` / `:/` item 按 exact total mass 抽样、在选中 range 内 uniform
抽 value，再用 SAT 为该 combination 做 rejection sampling。这覆盖 singleton、finite/wide range、`:/` total-range weight，
以及互相约束但无需 materialize 每个 range member 的多个 direct distribution。若 bounded rejection 仍找不到 feasible
combination，general bounded model policy 仍是 resource boundary。

conditional distribution，及依赖其他 random leaf 的 bound/weight，使用第二种 exact policy：Python 在同一 4096-model limit
内枚举 complete constrained model，应用每个 active branch 的 selected/total weight，并按 resulting rational weight 选择 model。
只有超出该 bounded model space 时，general SMT fallback 才作为 hard-satisfiability fallback；这些 large shape 不得宣称具有
exact Python frequency contract。

## 限制与后续集成

- 与 SV 一致，`randc` target 上的 `dist` 被拒绝。
- 源码形式面向 scalar；runtime expansion 后，current dynamic-collection element 或已分配 rand-handle leaf 上的 distribution
  使用同一 IR 与 solver path。
- dynamic expression 留在 IR 中并生成 SV，不在 Python 预求值。state-resolvable direct support range 用 exact weighted draw，
  没有 support-materialization limit；依赖其他 random leaf 的 conditional distribution/bound/weight 使用 bounded complete-model
  enumeration（4096 model）。
- `soft`、`solve before`、`unique`、`randc` 共享 runtime solve path；各自的 declared priority/order behavior 独立于
  weighted distribution selection 验证。

## `randc` 语义设计

field-policy 写法是 `randc=True`，与显式 `rand=` policy 互斥；它生成 SV `randc` declaration，而非 `rand`。

无论写法如何，semantic contract 固定为：

- 只适用于 2-state scalar integral leaf（`Bit`、`Int`、`LongInt`、Enum）；four-state `Logic`、container、object handle
  都拒绝。
- cycle state 属于 object instance 与 resolved scalar leaf path；它不是 schema identity，也不序列化。
- 成功 `randomize()` 时，先选择 `randc` value，再选择普通 `rand` value；在 cycle 中的每个当前 legal value 都出现前，
  不会重复值。
- failed randomization 不消耗 cyclic value；`rand_mode(0)` 暂停 cycle，重新启用后继续。
- 改变适用于 cyclic variable 的 enabled constraint set 会启动新 cycle。若当前 cycle 没有满足 current constraint 的剩余值、
  但 full domain 仍 SAT，也启动新 cycle；这等价于 SV 的 constraint-change / exhausted-remaining-value reset 行为。
- left side 含 `randc` leaf 的 `dist` 是 declaration error；`solve before` 也必须拒绝 cyclic variable 作为 ordering operand。

Python verification 以 small finite domain 证明无重复、reset、constraint exhaustion、failure non-consumption、mode
pause/resume。target verification 生成同样的 `randc` declaration 并断言同样的 observable cycle invariant；不会要求两侧
random-number stream 完全相同。

## `unique` 标量接口

把 `unique(...)` 写作完整 constraint statement：

```python
from svtypes import constraint, unique

@constraint
def legal(self):
    unique(self.source, self.destination, self.reply)
```

它生成 `unique {source, destination, reply};`。它是 hard constraint：只含 scalar 的形式至少需两个 argument，且任何
satisfying solve 都不能含 duplicate value。unpacked `Array`、`DynArray`、`Queue` argument 属于同一 construct：
`unique(self.words)` 与 `unique(self.tag, self.data)` 分别生成 native `unique {words};` / `unique {tag, data};`。
packed `Bit` / `Logic` 仍是一个 integral value。`unique` 不约束 collection size；empty/singleton collection vacuously true。
associative array、object handle、unpacked struct、nested dynamic collection 会在 declaration 时拒绝。size 已知后 Python flatten
current integral leaf，并采用与 SMT encoding 相同的 pairwise SV equality sizing rule，包括 mixed-width operand。

## `soft` 接口与优先级

把 `soft(expression)` 写成完整 constraint statement：

```python
from svtypes import constraint, soft

@constraint
def legal(self):
    self.opcode < 3
    soft(self.opcode == 2)
```

它生成 `soft (opcode == 32'd2);`。soft expression 永不使 solve failure：仅当它与全部 hard constraint 及先前、更高优先级
soft expression 都一致时才保留。Python 与 SMT backend 按以下顺序 greedy 地应用 soft expression：

1. inline `randomize_with` soft constraint；
2. derived-class constraint block 先于 base-class block；
3. 同一 block 内 later soft statement 先于 earlier statement。

该顺序经 configured target 验证。constraint `if` 内的 `soft` 只在 selected branch active。Python candidate sampling 在
soft clause 未满足时不会提交一个仅 best-effort 的 candidate；它用 incremental SMT policy 区分真实 conflict 与不走运 sample。

## `solve_before` 接口

把 `solve_before(before, after)` 写成完整 constraint statement。每个 argument 可以是一项 scalar random field，或
scalar random field 的 tuple/list：

```python
from svtypes import constraint, solve_before

@constraint
def legal(self):
    solve_before((self.kind, self.length), self.payload_kind)
    self.kind < self.payload_kind
```

它生成 `solve kind, length before payload_kind;`。两个 group 不能为空也不能重叠；成员必须是 scalar `rand` leaf；
`randc` member 或 conditional placement 会被拒绝。cyclic ordering edge 在 randomization time 报告。

Python backend 将它视为 selection-order policy，绝非 hard constraint。对于 packed domain 不超过 4096 value 的 ordered
variable，它枚举 feasible value、从 deterministic random stream 选择一个、固定后继续下一个 variable；更宽 domain 则以同一
stream 一次固定一个 satisfiable bit，再继续下一个 variable。因此 wide ordered field 不会退化为 deterministic minimum model。
large-domain path 不声明 uniform model-counting；`solve before` 控制 selection order，不是 probability contract。

## 验证

`tests/python/test_constraint_dist.py` 覆盖 source parsing、expression position、stable hard membership、`:=` 与 `:/`
sampling 及 invalid source。`tests/python/test_constraint_sv.py::test_remote_sv_dist_expression_simulation` 生成同一 IR 到 SV，
在 target 上编译并检查 repeated target-language randomization 的 resulting support set。

`tests/python/test_constraint_randc.py` 覆盖 Python cycle uniqueness、mode pause/resume、constrained-cycle reset、failed-call
non-consumption、generated declaration 与 invalid combination；匹配 target regression 是
`tests/python/test_constraint_sv.py::test_remote_sv_randc_cycle_simulation`。

`tests/python/test_constraint_unique.py` 覆盖 scalar/mixed-width Python semantics 与 unpacked array/queue flattening；
`test_remote_sv_unique_scalar_simulation`、`test_remote_sv_unique_collection_simulation` 在 target 上验证 scalar、fixed-array、
dynamic-array、queue constraint。

`tests/python/test_constraint_soft.py` 覆盖 hard-over-soft、same-block/inheritance priority、conditional activation；target
soft regression 验证生成 SystemVerilog 的同样 ordering case。

`tests/python/test_constraint_solve_before.py` 覆盖 group、rendering、invalid operand/cycle；匹配 target regression 编译并执行
生成的 `solve ... before ...` declaration。

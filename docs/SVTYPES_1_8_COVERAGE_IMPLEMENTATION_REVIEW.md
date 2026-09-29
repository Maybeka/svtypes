# SvTypes 功能覆盖率实现审阅（1.8–1.10）

## 当前结论（1.4.1 基线）

路线图在当前配置 target 上可观测的 1.8 / 1.9 / 1.10 要求已经落地。三个里程碑仍受已记录的 capability gate 约束，因此不得表述为无门的 target parity 已完成。

下表是本文件唯一的当前状态判断；证据见第 18 节。第 1–17 节保留为历史审阅依据，
其中的“缺口”“未完成”等结论均已被第 18 节复查取代，不得作为当前待办引用。

| 里程碑 | 当前实现 | 能否宣布完成 | 剩余约束 |
|---|---|---|---|
| 1.8 Python core | 声明、采样、内存库、比较域、transition 与 target 微型对照均已交付。 | **Python 语义完成** | 显式 4-state value bin 的 target 具名 hit 有 capability gate。 |
| 1.9 cross 与 SV parity | 远程 10⁴ 次 codec-sync、实例覆盖率、cross 规则、`CoverInput`/`CoverRef` 与 `repeat` 对照均已交付。 | **受支持 target 子集完成** | illegal 非零 hit 中止仿真；`CrossQueueType`、`get_inst_coverage=1` 为生成期 gate。 |
| 1.10 UCIS bridge | 二进制库、UCIS 子集导入/导出、外部样本诊断和 target-run 计数导回均已交付。 | **受支持 interchange 子集完成** | 原生 UCIS dump 不一定可用；adapter 可投影同一 run 的 target 计数。 |

相对上次审阅已关闭的条目：§5.12 target 微型对照并写回路线图；`per_instance=1` 远程实例对拍；外部 UCIS 样本与不兼容诊断；target 计数的 UCIS 导入；transition 与 value bin 同 point 分类；§5.9 / §5.13 / §10 所列剩余 target 微型夹具；§5.13 无损扩展条文。其余见第 18 节。

不得表述为已完成：`CrossQueueType` target parity、非缺省 `get_inst_coverage` 发射、illegal 非零 hit 的 target 对拍、4-state value bin 的 target 具名 hit、原生 UCIS dump（在 gate 解除前）。

---

## 历史审阅依据（非当前待办）

以下正文不改写，用于追溯当前结论的来源；其中的阶段性缺口已按第 18 节完成复查。

- 第 1–7 节：1.8，提交 `407c613`
- 第 8–17 节：1.9 / 1.10，提交 `437fe1c`
- 第 18 节：对当前工作树的复查证据

## 1. 审阅范围

1.8 实现大致对应 `bdde461`（声明 IR 基础）到 `407c613`（Python core 文档）的 coverage 提交（约 40 个 commit）。模块边界与 `__init__.py` 的说明一致：冻结 IR、source-only DSL、embedded runtime、求值器、内存数据库；SystemVerilog 生成与仿真对拍留在 1.9。

对照的契约以当前路线图为准，主要是：

- §8 里程碑「1.8 Python core」的交付与完成门槛
- 已冻结且 1.8 必须实现的 §5.11、§5.13、§5.14 中与 Python core 相关的部分
- §5.12（明确标为 1.8 更新项 / target 微型对照）
- §6 内存数据库、有限来源、确定性 JSON snapshot（1.8–1.9 不承诺持久化二进制库）

`Cross` 运行时、`CrossQueueType`、observation manifest、编解码双侧采样按路线图属于 **1.9**，下文只记录 1.8 树中已提前露出的接口，不按 1.9 门槛判完成。

## 2. 与 §8 完成门槛的对照

| 门槛 | 实现 | 测试 | 判定 |
|---|---|---|---|
| CoverageIR、声明语义摘要、覆盖组类型 ID | `CoverageIR` 冻结 dataclass；`covergroup_type_id` 不随 bins 变化；provenance 不进 digest | `test_coverage_ir.py` | 通过 |
| source-only `@covergroup` 编译 | `frontend.compile_declaration` 读源码 AST，不执行声明函数 | `test_freeze_compiles_source_only_...` | 通过 |
| embedded `.instantiate(...)` 仅宿主 `__init__`、至多一次 | `BoundCoverGroup.instantiate` 检查 construction depth 与重复构造 | `test_coverage_declaration.py` | 通过 |
| 未实例化成员方法失败 | 未 `instantiate` 时 `sample`/`instance` 报错 | 同上 | 通过 |
| `CoverInput` 不改变声明语义摘要；不同 actual 改变实例布局 | materialize 只作用于实例 IR；digest 比较有测试 | `test_cover_input_materializes_...` | 通过 |
| `CoverRef` 每次 sample 读宿主当前值 | 采样时 `getattr(host, name)` | `test_coverref_reads_...` | 通过 |
| 覆盖组内置方法 `start`/`stop`/`set_inst_name`/`get_coverage` | `CoverGroupInstance` 转发 | `test_coverage_instance_methods_...` | 通过 |
| 命中 / 未命中 / ignore / illegal / overlapping normal / default | 分类顺序：iff → ignore → illegal → 全部 normal → default | `test_python_runtime_classifies_...` | 通过 |
| automatic bins：enum / 整除 / 余数 | enum 每成员一 bin；整型按 `auto_bin_max` 切分，余数进最后一 bin | frontend + auto + 对应测试 | 通过 |
| automatic bins：XZ 不进 2-state bin | `_matches_selector` 对带 `x_mask`/`z_mask` 的值一律不匹配 | `test_default_auto_coverage_tracks_handle_nullness_and_excludes_xz_...` | 部分，见 §4 |
| array bins `split()` / `split(count)` / empty bin | 展开、压缩、空 bin 不进分母 | `test_array_bins_split_...` | 通过 |
| 定长 `CovPointArray` 槽跳过 | `IndexError` 时该槽不加 sample/hit | `test_array_points_freeze_to_fixed_slots_...` | 通过 |
| 容器值域 point | `container_value_domain` 对当前元素逐个分类 | `test_dynamic_container_value_domain_...` | 语义偏差，见 §4 |
| 数组式 / 值域声明带 transition 失败 | freeze 报错 | `test_transition_bins_are_rejected_...` | 通过 |
| 定长 transition 与 `repeat`/`[*m:n]` | 历史后缀匹配；首次 sample 不命中长度 ≥2 | `test_transition_bin_matches_...`、`test_transition_repeat_...` | Python 有；target 无，见 §4 |
| 有限来源 N 默认 3、额满仍加 hits、可配置 | `source_limit`；`set_coverage_case_name` 进程级 | frontend + database 来源测试 | 通过 |
| 声明语义摘要相同可 merge；布局摘要不同拒绝 | `CoverageDatabase.merge` / `record` | `test_coverage_database.py` | 通过 |
| `per_instance=1` 时 illegal 按逻辑实例键分开；缺 key / 重复注册失败 | 记录与绑定检查 | `test_per_instance_illegal_...`、`test_per_instance_database_record_...` | 通过 |
| 加权类型汇总；`merge_instances` 0/1 | `type_summary` | `test_database_type_summary_...` | 通过 |
| 确定性 JSON snapshot | `snapshot_json` `sort_keys` | `test_coverage_snapshot_json_...` | 通过 |
| 可选 sample 日志与预算 | 内存 JSON 列表 + `max_records`/`max_bytes` | `test_optional_sample_log_...` | 通过（1.8 未要求分帧二进制） |
| 自动 `cov` → `svtypes_auto_cov` | `auto_coverage_ir`；标量 / 固定槽 / 值域 / `cov_slots` / 关联数组 | `test_auto_coverage_ir.py` | 通过 |
| §5.12 target 微型对照并回写路线图 | 1.8 树中无 coverage 远程 / target fixture | — | **未完成** |

静态 cross 限额函数 `validate_cross_normal_bin_count`（65,536 / 1,048,576）已存在且有边界测试，但 **DSL freeze 不编译 `Cross`，求值器也不采样 cross**。这与「完整 cross 属 1.9」一致；1.7 的限额只以可调用校验的形式交付，尚未接到声明编译。

公开 API 已导出 `Cross` / `CrossOption` / `CoverageCrossIR`。在 1.8 声明体里写 `class ...(Cross, ...)` 会走 point 编译路径并报「必须继承 CovPoint 或 CovPointArray」。这不是 1.8 门槛失败，但接口比运行时能力提前露出。

## 3. 与已冻结语义对齐较好的部分

**身份分层。** `covergroup_type_id`（`sample_type::declaration_name`）与 `declaration_semantic_digest` 分离；`CoverageProvenance` 独立。point/bin 声明名冲突、未知 cross 成员、重复 option 在 IR 构造期失败。

**模板与实例绑定。** `CoverInput` 出现在 bin 选择器中时，声明摘要不变，`instance_layout_digest` 随 actual 变化；同一逻辑实例键、不同布局的 merge 被拒绝。`CoverRef` 不是构造参数，每次 sample 读宿主字段。

**生命周期。** embedded covergroup 必须在宿主 `__init__` 中 `instantiate` 一次；自动 `cov` 组在 `bind_covergroups` 中直接构造。`option.per_instance` 缺省 0；`per_instance=1` 写入数据库时要求 `logical_instance_key`。逻辑实例键必须在首次 sample 前绑定，且与 `option.name` / `set_inst_name()` 分开保存。

**分类优先级。** ignore 丢弃、illegal 另计且可多个同时记录、重叠 normal 全部加 hit、default 吃残余。`at_least` / `goal` / `weight` 进入 `get_coverage` 与类型汇总。空 array bin 不进分母。

**数组式覆盖。** `CovPointArray` freeze 成 `name[0]`…`name[length-1]`；动态长度不足的槽 `samples == 0`。值域 point 对 `DynArray`/`Queue`/`AssocArray` 自动加 `container_value_domain`。

**数据库。** 内存记录 + 严格 merge（声明摘要、definition snapshot、实例布局）。`merge_instances=0` 做实例覆盖率加权平均，不伪造统一 type-bin 表；`=1` 按 bin 名合并后再计分。`record` 对同一 live instance 是刷新而非累加。JSON snapshot 与内部记录隔离。

## 4. 对照最新路线图的缺口

下列各项按「是否挡住宣称 1.8 完成」排序。均未改代码，只记录现状。

### 4.1 §5.11 选择 A：`sample_count`（挡住 1.8 语义关闭）

路线图：一次用户 `sample()` 使 group/instance `sample_count` +1；每个有效元素/槽只按命中增加 bin hit。

实现：没有 group/instance 级 `sample_count`。`PointCounters.samples` 在 `_classify_value` 入口加一。因此：

- 值域 point 一次用户 `sample()`、三个元素时，`samples == 3`（`test_dynamic_container_value_domain_...` 即如此断言）。
- 槽位数组每个有效槽的 point 各 +1，跳过槽为 0；这接近「该槽是否被采到」，但不是用户调用次数。

1.9 对拍约定用测试专用「用户 sample 调用数」，不把循环 `cg.sample()` 当公开计数。1.8 的公开 snapshot 仍把值域多元素计成多次 sample，与已冻结的选择 A 不一致。

### 4.2 §5.12 transition 历史（1.8 更新项，未关闭）

路线图要求：按 LRM 已定义部分实现；与 bin 级 `iff` 的空白用 **target 微型对照** 补全并写回本文；`iff` / default / ignore / illegal / X/Z 均须有对照；完成前不得作为已完成的公开 transition 承诺。

Python 侧已有：有界序列、`repeat(term, m, n)`、首次 sample 不命中长度 ≥2、数组式/值域禁止 transition。历史缓冲区截断为 16（与硬上限一致）。

缺口：

- 1.8 树中 **没有** coverage 的 target 对照，路线图也未因实测更新 §5.12。
- 某 point 一旦存在 `transition` bin，求值器匹配后 **直接 return**，同 point 的 ignore / illegal / default / 普通值 bin 不再走分类。
- freeze **不检查** 展开后长度相对默认 K=8 或硬上限 16。`repeat` 只检查 `0 <= minimum <= maximum`。超过 16 的序列在运行时因历史截断而永不命中，而不是声明期失败。
- digest 未单独记录「各 transition bin 的实际展开序列」；序列在 bin selector 里，未引用的全局默认 K 确实未进摘要（与 §5.10 方向一致），但超长声明缺少失败路径。

### 4.3 §5.13 比较域与 X/Z（1.8 应实现的已冻结规则）

已对齐：2-state 自动 bin 不吃 X/Z；range 为闭区间；重叠 normal 全部命中。

未对齐或未测：

- `_matches_selector` 对任何带 X/Z 的 sample **一律返回假**。路线图允许「显式含 X/Z 的 singleton/set 按 4-state 相等命中；range 仍不匹配含 X/Z 的 sample」。显式 X/Z bin 目前无法命中。
- freeze 未从 point 静态结果类型得到唯一比较域，也未拒绝混宽、异符号、会截断的 literal。bin 选择器按 Python `int` / 运行时相等处理。
- `Color.R` 与同型积分 `0` 规范化为同一底层位模式、两个具名 bin 重叠全命中：无对应测试；实现依赖 `.value` 与 Python `==`，没有声明期规范化。
- 每 point 多个 `default` 时只命中 `defaults[0]`，freeze 未限制「至多一个 default」。

§10 把 §5.13 的 Python oracle 列为后续能力交付前的不可替代证据。1.8 已交付 point runtime，但这组 fixture 仍缺。

### 4.4 其它实现偏差（不单独构成 1.8 未开工，但应在关闭前处理）

- **`get_inst_coverage` 与 `get_coverage` 相同。** 未按 `get_inst_coverage` / `per_instance` 区分实例覆盖率与类型覆盖率。类型结果在 `CoverageDatabase.type_summary`；单实例方法总是返回该实例的 point 加权覆盖率。
- **illegal 使报告失败。** §4 要求 illegal 保留计数并使报告明确失败。`get_coverage` / `type_summary` 只看 normal/default 的 `at_least`，illegal 不影响覆盖率数值，也没有单独的失败标志。
- **`detect_overlap` 等 option** 可声明并冻结，求值器不使用。
- **无稳定逻辑实例键的跨库 type merge。** `per_instance=0` 且未给 key 时，内部用 `__ephemeral__{id(instance)}` 分条。单 run 的 `type_summary` 仍可加权。两个数据库 `merge` 时这些记录不会按类型对齐相加。路线图允许无稳定 key 的实例只参加单 run type coverage、不参加跨库 per-instance merge；若期望 `per_instance=0` 的跨库 **类型** 计数相加，当前 key 模型做不到。
- **master-projection merge、waiver。** 未出现在 1.8 完成门槛中，1.8 树也未实现。
- **sample 日志** 是带预算的内存 JSON，不是路线图后文的独立分帧压缩二进制。与 1.8 门槛「可选 sample 日志」相符，不要当成 1.10 持久化格式。

## 5. 测试画像

1.8 测试文件：

- `tests/python/test_coverage_ir.py`
- `tests/python/test_coverage_identity_limits.py`
- `tests/python/test_coverage_declaration.py`
- `tests/python/test_coverage_frontend.py`
- `tests/python/test_coverage_database.py`
- `tests/python/test_auto_coverage_ir.py`

覆盖声明失败路径、embedded 生命周期、point 分类、automatic/array bins、槽跳过、值域、CoverInput/CoverRef、来源表、merge/layout、`merge_instances`、自动 `cov`。这与「1.8 用 Python 单元测试关门」的策略一致。

明显未覆盖、且属于 1.8 契约的：显式 X/Z bin、enum/同型整数重叠、混宽拒绝、transition 超 K 声明失败、transition 与 ignore/illegal/iff 同 point、group 级用户 `sample_count`、非法命中对报告失败的影响。

## 6. 建议的 1.8 收口顺序（不在本次修改）

1. 把用户 `sample()` 次数与 point 元素分类次数分开，使值域/槽位符合 §5.11 A；snapshot 中写明字段含义。
2. freeze：transition 展开长度相对 K=8/16；每 point 至多一个 default；比较域与溢出 literal（§5.13）。
3. 求值：显式 4-state singleton/set 可命中 X/Z；transition 与 ignore/illegal/default 的同 point 规则按 §5.12 在有 target 证据后再冻结。
4. 补 §5.12 要求的 target 微型对照，并把结论写回路线图；在此之前不要把 transition 写成已完成公开承诺。
5. 1.9 再接 `Cross` DSL、限额校验与 renderer；避免 1.8 公开文档把 `Cross` 写成可采样。

## 7. 总评

1.8 作为「Python 声明编译 + embedded 采样 + 内存库」已经可工作，本地 61 项覆盖率测试通过，§8 表格里大多数具名门槛都有对应实现和测试。它还不是对照最新路线图的关闭点：选择 A 的 `sample_count`、§5.12 的 target 证据、以及 §5.13 的比较域/显式 X/Z 仍缺。1.9 开发可以继续，但不宜把当前 Python core 表述成「1.8 完成门槛已全部满足」。

---

# 1.9 / 1.10 审阅（`437fe1c`）

## 8. 审阅范围

权威提交为 **`437fe1c`**。1.9 与 1.10 在该提交中一并交付，因此对照 §8 的两行门槛分别判定，不把「parity and interoperability bridge」这个提交标题当成关闭证明。

对照条款：

- §8 里程碑「1.9 cross 与 SV parity」与「1.10 UCIS bridge」
- §5.9 已冻结的 cross 成员/限额/`cross_retain_auto_bins`/`CrossQueueType`/计分顺序
- §5.14 覆盖率公式与 option 发射
- §5.15 observation JSON / manifest / 编解码双侧采样
- §7 UCIS 适配层优先级（内存库 + JSON 测试通道；公开二进制库与 UCIS 在 1.10）
- §9 验证策略中 1.9 双侧采样必须覆盖的构造集合

**不纳入：** 改代码、改路线图、宣称 2.0 RC（性能基准不在 1.10）。适配层启动与仿真器私有 UCDB 按规格不属于本仓库。

**验证证据：** 当前工作树全量本地 pytest 为 **391 passed, 19 skipped**。配置的远程 SystemVerilog conformance target 上 `test_remote_coverage.py` 当前为 **2 passed**，其中包含 10⁴ 个编解码同步样本，并通过私有 adapter 将 target 的具名 bin 结果按 manifest 转为中立 observation JSON 后与 Python counters 精确比较。该 fixture 仍只覆盖静态 cross + ignore 的窄构造集合，不改变下文对 1.9 全构造证据面不足的判定。

## 9. 1.9 与 §8 完成门槛对照

| 门槛 | 实现 | 测试 | 判定 |
|---|---|---|---|
| cross 声明编译进 CoverageIR，限额在 freeze 前检查 | `frontend._cross_class` 编译 `Cross`；成员上限 8；`validate_cross_normal_bin_count` 接到 freeze | `test_static_cross_compiles_...`；限额函数边界在 `test_coverage_identity_limits.py` | 通过（超限 DSL freeze 缺直接 fixture，但校验已接线） |
| `cross_retain_auto_bins` 0/1 与显式 tuple | 缺省 0 时只保留具名 normal；`=1` 补自动 tuple；renderer 在目标不接受该 option 时用显式 `ignore_bins __svtypes_unselected_*` 压住未选 tuple | frontend + `test_renderer_suppresses_unselected_...` | 通过 |
| `CrossQueueType` 受限解释器；实例化后 concrete queue 进布局摘要 | freeze 保存函数 AST；`.instantiate` 求值；`instance_layout_digest` 随 queue 变化 | `test_cross_queue_function_materializes_...` | Python 语义通过 |
| `CrossQueueType` 与生成 SV 的 queue/bin 名/coverage 相等 | `_queue_function_lines` 能发出同形 SV；`to_sv_obj()` 在当前 target 上以 `SVT-COV-SV-BACKEND` 拒绝 | `test_renderer_rejects_cross_queue_when_configured_target_lacks_type_support` | **Python 可交付；target parity 未完成**（路线图已记 capability gate，判定与规格一致） |
| 求值器按 §5.9 采样 cross | `_sample_cross`：成员 iff/ignore/illegal/transition 使 `classified` 为空则跳过；cross `iff`；ignore 优先；重叠 normal 全命中；queue 按值 tuple | 静态 cross 与 queue 分类测试；**无** cross `iff`、成员 illegal 使 cross 跳过的专门用例 | 部分 |
| SV renderer 从冻结 IR 生成覆盖组，不从 AST 手写 | `object.to_sv_obj` 调用 `render_type_coverage`；嵌套 collector、`sample(item)`、值域 `foreach` | `test_coverage_sv_renderer.py` | 通过（option 发射见缺口） |
| observation manifest 与生成同产物 | `observation_manifest`；`generator` 在生成 `.sv` 时写出 `.coverage-manifest.json` | renderer 测试；generator 接线 | 通过 |
| 固定向量 / 编解码双侧采样：manifest 指导的具名 hit/illegal 精确对拍 | `parse_observation` / `compare_manifest_hits`；远程夹具 pack → hex → SV `unpack` + `cov.sample(packet)`，Python 同序列 `sample()` | 本地 `test_cross_conformance_fixture_...` 只断言夹具文件；`test_remote_generated_cross_covergroup` 含 10⁴ 样本与 hit 比较 | **仅一条静态 cross+ignore 路径**；见 §11 |
| coverage 百分比按 §5.14 交叉校验 | observation JSON **不含** 百分比；比较器也不读百分比。远程 TB 在 SV 侧 `$fatal` `get_coverage() != 100.0`，不是 Python 重算后的固定小数位对拍 | 远程夹具 | **未完成** |
| 按 §5.15 选择 type / instance 覆盖率；`per_instance=1` 绑同一逻辑实例键 | manifest 支持 `instances` + 强制 `target_labels`；远程夹具未设 `per_instance=1`、未绑定 `logical_instance_key` | `test_observation_manifest_binds_instance_keys_...` 只测清单形状 | 清单协议有；双侧采样用例无 |
| ignore 只验证未进 named hit/分母 | 协议故意不含 ignore hit；远程 ignore `masked` | 已在远程 target 执行 | 协议对齐；证据面窄 |
| manifest 缺项或 target 多出未映射具名 bin 失败 | `compare_manifest_hits`：observation 键必须 ⊆ manifest labels；`expected` 中缺失则失败 | `test_observation_protocol_compares_only_manifest_named_items` | **部分**：只强制 `expected` 里的项，不强制 manifest 中每一个可比对项都出现在 observation |
| 生成期拒绝不支持语义 | CrossQueueType → `SVT-COV-SV-BACKEND`；不发射退化 SV | renderer 拒绝测试 | 通过 |
| LRM 允许而当前 target 不能生成/观测：路线图逐项记 capability gate | 路线图 §5.5 已写明当前 target 不识别 `CrossQueueType` | — | 该条 gate 已记录。其它 LRM/target 差（option 发射、§5.12 微型对照）未同样逐项关门 |
| §9 至少覆盖：标量 point、定长槽位（`i >= size()` 跳过）、容器值域、有界 transition、`iff`、ignore/illegal/`default`、cross；建议每构造 ≥10⁴ sample | 远程夹具只有标量 `Bit(1)` × 2 + 静态 cross + ignore，10⁴ 次 | `test_remote_coverage.py` 两个测试 | **未完成** |

## 10. 1.9 已落地且与规格对齐较好的部分

**Cross 进入声明编译。** 1.8 树上写 `class ...(Cross, ...)` 会误走 point 路径。HEAD 上 freeze 编译 `members`/`iff`、显式 bins、`ignore_bins`、`CrossOption`，并把 65,536 / 1,048,576 限额接到 freeze。缺省 `cross_retain_auto_bins = 0` 与冻结条款一致。

**计分顺序（Python）。** 成员 point 先分类；`None`（iff / ignore / illegal / transition）或空 tuple（未覆盖值）使 cross 整次跳过，符合「illegal 成员不另造 cross-illegal bin」。存活后再评 cross `iff`、queue/静态 ignore、再给 normal（含 queue 值 tuple）加 hit。`cross_coverage()` 分母只含 kind=`normal` 的 bin。

**`CrossQueueType`。** 不是 Python callback：源码 AST 白名单解释；`CoverInput` 在 instantiate 时求值；concrete queue 进实例布局、不进声明语义摘要。当前 target 不能识别该 LRM 构造时，生成失败而不是发出无效 SV。这与路线图「Python 可交付、不得宣称 target parity」一致。

**Renderer 与 manifest。** 覆盖组从冻结 IR 嵌进宿主 `to_sv_obj()`；自动 `cov` 与用户组走同一渲染。observation label 形如 `covergroup_type_id__category__name`，多 covergroup 同名 point 可区分。per-instance 清单要求 harness 显式 `target_label`，禁止从 handle / 对象编号 / 报告名推导。忽略 bins 列入 manifest 的 `kind`，但不进入 observation 的 hit 字段。

**编解码同步夹具形状。** `test_remote_coverage.py` 用 `pack` 写出定长 `samples.hex`，SV testbench `unpack` 后 `cov.sample(packet)`，Python 对同一 `(opcode, mode)` 序列采样。这是 §9 要求的「一份刺激、一次同步」，不是两份手写向量。规模 10⁴ 满足该条夹具的建议下限。adapter 被明确放在仓库外，本仓库只校验规范化 JSON。

## 11. 1.9 缺口

下列各项按是否挡住宣称 1.9 完成排序。

### 11.1 双侧采样证据面（挡住 1.9 关闭）

§9 要求至少覆盖标量 point、定长槽位数组（含 `i >= size()` 跳过）、容器值域、有界 transition、`iff`、ignore/illegal/`default`、以及 cross。HEAD 上的远程夹具只覆盖 **两个 1-bit 标量 point + 一个静态 cross + 一条 ignore**。没有槽位跳过、值域 `foreach`、transition 历史、point/cross `iff`、illegal、`default` 的 codec-sync 对拍，也没有 `per_instance=1` 的实例覆盖率对拍。

本地 `test_cross_conformance_fixture_uses_codec_synchronized_vectors` 不启动 target，只检查生成了 hex/manifest/`unpack`+`sample` 文本。远程夹具虽已通过，也只能关闭这一条 cross 构造，不能关闭 1.9。

### 11.2 §5.14 百分比与 §5.15 `sample_count` 观测通道

冻结协议：hit/illegal 精确相等是主证据；百分比由 Python 按 §5.14 重算后与 target 做固定小数位交叉校验；生成 SV 必须另记测试专用「用户 sample 调用数」并纳入观测输出。

实现：`parse_observation` 只接受 `{"items": {label: {"hits", "illegal_hits"}}}`。没有 coverage 百分比字段，也没有用户 `sample_count`。`compare_manifest_hits` 不比较百分比。远程 TB 用 `cov.cg.get_coverage() != 100.0` 做 SV 单侧断言，没有把该值送进 observation、也没有与 Python `get_coverage()` 对拍。

1.8 已指出的 snapshot `samples`（值域一次用户 `sample()` 计成元素个数）在 HEAD 仍在，见第 16 节；它不能冒充 §5.15 的测试专用计数器。

### 11.3 `CrossQueueType` 不得宣称 target parity

路线图已正确记录 capability gate。Python 分类与实例布局测试充分。生成路径在当前 target 上必须失败，因此「实例化后 concrete tuple queue、bin 名和 coverage 必须与生成 SV 相等」这一完成门槛 **尚未有 target 证据**，也不能在解除 gate 前写成已完成。`_queue_function_lines` 的单测只证明文本形状，不证明仿真。

### 11.4 Renderer 未显式发射覆盖率 option

§5.14：Python、SV 和 UCIS export 须区分类型/实例百分比；**renderer 显式发射相关 option，不依赖仿真器缺省**。`render_type_coverage` 发射 coverpoint/cross/`iff`/bins，**不**发射 `option.per_instance`、`option.weight`/`goal`/`at_least`、`type_option.merge_instances` 等。缺省 `per_instance=0` 时，这把类型 vs 实例行为交给仿真器缺省，与冻结句不一致。`cross_retain_auto_bins` 用显式 ignore 展开弥补了「目标不接受该 option」——这是对的——但其它 option 没有对应策略。

### 11.5 Manifest 比较器与 §5.9 Python 微型缺口

- `compare_manifest_hits` 对 observation 中多出的 **item** 相对 manifest 失败，但对「manifest 有、expected 未列入」的项不要求 observation 出现。§5.15 写的是 manifest 可比对项缺失即失败。远程测试用 snapshot 填满 expected，掩盖了比较器偏松。
- Python 侧缺少：cross 自身 `iff`、成员 illegal 使 cross 跳过、两个重叠 cross normal bin、成员 `default` 进入笛卡尔积。求值器注释与代码方向正确，但 §5.9 要求的 target fixture 最小集合在 Python 里也不完整。
- 限额：`validate_cross_normal_bin_count` 的边界测试仍调用独立函数，没有一个 freeze 一个超限 `Cross` 的 DSL 用例。

### 11.6 仍由 1.8 挡住、1.9 对拍会踩到的语义

§5.11 `sample_count`、§5.12 无 target 微型对照、§5.13 显式 X/Z 与比较域、`get_inst_coverage` 与 `get_coverage` 相同。双侧采样若把值域/transition/X/Z 加进夹具，会直接暴露这些偏差。它们不是 1.9 新引入的，但 1.9 关闭条件依赖它们。

## 12. 1.10 与 §8 完成门槛对照

| 门槛 | 实现 | 测试 | 判定 |
|---|---|---|---|
| 公开二进制 coverage database：chunk / schema / version | `SVTCOVDB` + `u16` version=1 + flags=0；chunk `META`/`RECS`，各带 SHA-256；payload 为 canonical JSON | `test_database_binary_round_trip_...`、`test_database_binary_rejects_corruption_and_unknown_version` | 通过 |
| 版本兼容策略 | 未知 version/flags、损坏 checksum、重复/缺失 chunk 一律拒绝；不按 v1 静默解释 | 同上 | 通过 |
| UCIS XML export 子集 | UCIS 1.0 层次；整数常量/闭区间 point bin；仅引用已导出 point 的静态 cross；独立 `svtypes.ucis.loss-report` | `test_ucis_export_*` | 通过（子集边界明确） |
| UCIS XML import 子集 | `import_ucis` 读 `cgInstance` 的 name/points/crosses/options；不猜测 `covergroup_type_id` / digest | 导出后再 import 的计数断言 | **部分**：得到外部记录，不是 SvTypes `CoverageDatabase` |
| loss report | 无法无损投影的 default/transition/动态 selector/queue cross 省略并记录；无 coverpoint 则整组省略，不造非法空节点 | `test_ucis_export_omits_unrepresentable_default_bin_...` | 通过 |
| UCIS round-trip | 简单整数 bin：export XML → import dict 可恢复 count；defaults 经 JSON 配置补齐并记损失 | `test_ucis_export_contains_functional_coverage_and_loss_report` | **不是** DB→XML→DB 身份还原 |
| 跨 run merge | 二进制 `read`/`write` + 既有严格 `CoverageDatabase.merge` / `apply_baseline`（声明摘要、definition、布局、source limit） | `test_database_binary_round_trip_merge_and_baseline` | 通过（走 SvTypes 二进制库，不走 UCIS merge） |
| target 导出集成验证 | 无读取仿真器 UCIS/coverage export 的测试 | — | **未完成** |
| 外部 UCIS 样本导入和不兼容诊断 | 非 1.0 / 非 `UCIS` 根、畸形 XML 报错；import defaults 记损失 | 单元测试覆盖自产 XML；**无**外部样本文件 | 诊断路径有；外部样本无 |

## 13. 1.10 已落地且与规格对齐较好的部分

**持久化容器与运行时分离。** `docs/svtypes_binary_format.md` 与 `persistence.py` 一致：文件不是 pickle，不恢复 covergroup 实例、constructor actual、transition 历史、sample log 或 sampling 使能位。`apply_baseline` 只灌累计计数，历史清空。这与「累计统计起点、不是挂起的仿真」一致。

**格式自校验。** MAGIC、version、flags、chunk 布局、SHA-256、canonical JSON 缺一拒绝。`CoverageDatabase.to_bytes` / `from_bytes` / `write` / `read` 公开。`COVERAGE_DATABASE_FORMAT_VERSION` 已导出。

**UCIS 是投影不是第二份权威库。** 导出只发射能无损表示的 functional coverage；loss report 与 XML 分开，不把省略项伪装成 UCIS 数据。`cgId` 携带 `covergroup_type_id`。import 文档写明调用者必须再映射到冻结声明，才能作为运行时基线——避免用 UCIS 名猜测 SvTypes identity。

**空库仍给出合法骨架。** 没有任何可导出 covergroup 时仍写一个语义为空的 `instanceCoverages`，以满足 UCIS 至少一节点的约束，而不是虚构 coverpoint。

## 14. 1.10 缺口

### 14.1 「UCIS → SvTypes database」未闭合

§7 第 3 步与 §8「UCIS round-trip」按字面是受控导入进 **SvTypes database**。实现停在 `list[dict]`（`name` / `points` / `crosses` / `options`）。没有 `logical_instance_key`、`declaration_semantic_digest`、`instance_layout_digest`，也不能 `apply_baseline`。这与「不伪造 identity」的保守选择一致，但完成门槛不能写成已关闭：还缺一层调用者必须提供的声明绑定，以及该绑定失败时的不兼容诊断（digest/layout 不匹配应走现有 `CoverageError`，目前 import 根本走不到那里）。

### 14.2 无 target 导出集成

§7 第 4 步：外部 SystemVerilog target 的 UCIS/coverage export 可作为集成验证输入。仓库内没有夹具去编译、导出、再 `import_ucis`。1.9 的 observation JSON 按规格 **不得** 代替 1.10 的这一条。

### 14.3 `cgInstance` 名称混用报告名与逻辑实例键

导出用 `instance_name or logical_instance_key or covergroup_type_id` 作为 UCIS `cgInstance@name`。§5.4：`option.name` / `set_inst_name()` 是报告名称，`logical_instance_key` 是跨 run 对齐键，二者不得互相覆盖。把二者轮换填进同一 XML 属性，会让外部工具把报告名当成 merge 键，或反过来。`key` 目前只是导出序号。这不一定破坏自产 XML 的 round-trip 测试，但与已冻结的身份分层不一致。

### 14.4 子集边界（记录，不单独构成未开工）

无法导出 default / transition / 动态 selector / `CrossQueueType` bins；依赖未导出 point 的 cross 整段省略。这与「子集 + loss report」设计相符。解除 1.9 的 CrossQueueType capability gate 之后，UCIS 子集仍不会自动覆盖 queue bins，需要在 1.10 契约里继续当作损失，而不是静默丢 count。

## 15. 1.9 / 1.10 测试画像

相对 1.8 新增：

- `tests/python/test_coverage_sv_renderer.py`：IR→SV、manifest、observation 协议、target 缺 `CrossQueueType` 时生成失败
- `tests/python/test_remote_coverage.py`：codec-sync 夹具；远程对拍（`remote_sv`）
- `tests/python/test_coverage_frontend.py` 中的静态 cross / `CrossQueueType` 分类
- `tests/python/test_coverage_database.py` 中的二进制 round-trip、损坏/未知版本、UCIS export/import、default bin 省略

仍缺、且属于 1.9/1.10 门槛的：§9 全构造双侧采样；observation 中的百分比与用户 `sample_count`；`per_instance=1` 远程对拍；仿真器 UCIS 导入；UCIS → `CoverageDatabase` 绑定；cross `iff` / 成员 illegal 的 Python 与 target 微型对照。

## 16. 1.8 缺口在 `437fe1c` 上的延续

第 4 节所列偏差在 HEAD 抽查仍然成立，1.9/1.10 没有顺便修掉：

- 值域 point 一次用户 `sample()`、三元素时 `samples == 3`（`test_dynamic_container_value_domain_...` 仍如此断言）
- `get_inst_coverage` 直接返回 `get_coverage()`
- `_matches_selector` 对任何带 X/Z 的 sample 一律不匹配
- freeze 仍不拒绝超 K 的 transition，也不限制每 point 至多一个 default
- illegal 不影响覆盖率数值、没有报告失败标志
- §5.12 仍无 target 微型对照

因此：即使把 1.9 的 cross/renderer 和 1.10 的文件格式都算「代码已有」，1.8 语义关闭条件也还没满足。后续对拍夹具不应建立在当前 `samples` 字段等于「用户 `sample()` 次数」这一假设上。

## 17. 1.7–1.10 总评

| 里程碑 | 代码是否大体落地 | 对照 §8 能否宣称关闭 |
|---|---|---|
| 1.7 设计冻结 | 规格 + IR 形状 | 规格层可维持冻结；Python/target fixture 仍按后续门交付 |
| 1.8 Python core | 是 | **否**（`sample_count`、§5.12 target、§5.13） |
| 1.9 cross 与 SV parity | Python cross、renderer、manifest、一条 codec-sync 夹具 | **否**（§9 构造集合、百分比/`sample_count` 观测、CrossQueueType target parity、option 发射） |
| 1.10 UCIS bridge | 版本化二进制库 + UCIS 子集 export/import + loss report | **否**（UCIS→SvTypes DB、target 导出集成、外部样本） |

`437fe1c` 把 1.9/1.10 的**模块边界**搭齐了：cross 不再停在限额函数，生成与观测协议进了本仓库，二进制库和 UCIS 投影可本地 round-trip。它还不是路线图意义上的 1.9 或 1.10 关闭点。在补齐 §9 全构造双侧采样、§5.15 观测字段、以及 UCIS 导入到带 digest 的 `CoverageDatabase`（外加至少一次 target UCIS 集成）之前，不宜把当前树表述成 coverage parity 或 interoperability 已完成。

建议的收口顺序（不在本次修改）：先修 1.8 的 `sample_count` 与比较域，再按 §9 列表拆远程夹具（每构造独立、≥10⁴），把百分比与用户 sample 计数写入 observation；CrossQueueType 维持现有 capability gate，直到配置支持该 LRM 构造的 target；1.10 增加「外部记录 + 调用者提供的冻结声明 → `apply_baseline`」的显式绑定 API/测试，并用一次仿真器 export 证明 import 诊断，而不是用 observation JSON 代替。

## 18. 当前基线复查证据

本节核对当前未提交 diff 是否关闭第 11 / 14 / 16 节所列条目。结论见文首；本节仅列证据。

本地：coverage 相关 pytest（不含远程）**111 passed**。远程 `tests/python/test_remote_coverage.py` **15 passed**（其中 8 项为 `remote_sv`）。

### 已核对为关闭

- **用户采样次数：** covergroup 的 `sample_count` 每次公开 `sample()` 加 1。容器三个元素时，该值为 1；point 自己的 `samples` 仍是 3（分类次数）。merge / baseline 会带上 `sample_count`。
- **freeze：** 同一个 coverpoint 不能有两个 `default`。transition 展开长度必须在 2…16。溢出、四态进二态、含 X/Z 的 range endpoint、不同 enum 类型会失败。无类型 `int` 按 source 比较域转换；SV/typed literal 先按自身宽度、signedness 和四态规则规范化，再仅以是否可无损转换到 source 域判定，不因 signedness 不同而单独失败（例如 `8'shff` 先解释为 `-1`）。无法推导 source 比较域的 literal bin 在 freeze 失败。`Color.R` 与同型 `0` 会一起命中。§5.13 条文已按该无损扩展规则改写。
- **X/Z：** Python 中 `bins["1x"]` 按四态相等命中；range 仍不匹配含 X/Z 的 sample。生成文本为 `2'b1x`。远程夹具证明 2-state `0 => 1` 不能经由 X/Z sample 完成。
- **illegal：** 有 `has_illegal_hits()`。覆盖率数字仍按 normal/default/transition 计算。transition-only coverpoint 在命中前是 0%，命中后是 100%。
- **§5.12：** `test_remote_transition_history_iff_ignore_default_and_xz` 用 10⁴ 次 codec-sync 对照 coverpoint `iff`、同 point ignore/`default`/illegal 声明、以及 X/Z 打断 2-state transition。规则已写回路线图 §5.12。求值器在匹配 transition 之后仍按当前值分类 ignore/illegal/normal/default。
- **生成 option：** 受支持的静态 option 会写进 SV。`type_option.merge_instances=1` 已有生成与 remote codec-sync 对照；`get_inst_coverage=1` 仍在生成期失败。动态槽位带 `iff (path.size() > i)`；transition 写成 `(0 => 1)`。`CoverInput` 作为 covergroup 构造形参发射，`CoverRef` 采样路径为 `item.<field>`，`repeat(term, m, n)` 发射为 `term[*m:n]`。
- **observation：** 单实例为 `{"items","summary"}`；`per_instance=1` 为 `{"instances": {logical_key: {items, summary}}}`。
- **UCIS：** 绑定导入；子集 `default` 为 0 hit。`tests/python/data/ucis/` 提供外部 1.0 样本（含命名空间）以及版本/未知 bin 失败。`test_remote_target_ucis_export_imports_into_coverage_database` 把一次 target run 的计数经 UCIS 1.0 XML 导回 `CoverageDatabase`。
- **§9 / per_instance：** 原 cross 夹具仍在。`test_remote_per_instance_covergroups_compare_instance_coverage` 用两个 collector、逻辑实例键和 `get_inst_coverage()` 对拍实例覆盖率。
- **§5.9 target 微型夹具：** `test_remote_cross_iff_member_skip_and_overlapping_normal_bins` 对照 cross 级 `iff`、成员 `iff` 为假时跳过 cross、以及同一 sample 命中两个重叠 cross normal bin。
- **§5.13 target 微型夹具：** `test_remote_enum_and_range_set_overlapping_normal_bins` 对照 enum 与同型 integral 重叠、以及 range 与 set 重叠。
- **§10 target 微型夹具：** `test_remote_cover_input_and_cover_ref_sample_bindings` 对照 `CoverInput` 构造绑定与 `CoverRef` 当前值；`test_remote_transition_repeat_matches_finite_repetition_lengths` 对照 `repeat[*m:n]`。

### 已实现但仍不得表述为门槛关闭

- illegal 非零 hit 因当前 target 命中即中止而不能作为双侧采样证据。
- 显式 4-state value bin 的具名 hit 不出现在当前 target report 中。
- 原生 UCIS dump 不一定可用；集成测试接受 adapter 把同一 run 的 target 计数投影为 UCIS 1.0。

### 仍为 capability gate（不是缺子系统）

- `CrossQueueType` 的配置 target 对拍（当前 target 不能生成）。
- 非缺省 `get_inst_coverage=1` 的 option 发射。
- illegal 非零 hit 的完整 target 对拍。
- 4-state value bin 的 target 具名 hit。
- 原生 UCIS dump（在配置恢复该能力之前）。

# SvTypes 后 2.0 扩展架构设计

## 1. 目的与结论

本设计定义两项 **2.0 之后、与主线解耦** 的能力：

1. 覆盖率设计与辅助配置 GUI（以下简称“GUI”）：扫描一个 SvTypes 设计，交互展示 covergroup、CovPoint、Cross、bin 及其定义；帮助用户提出可审阅的 coverage 配置/源码修改建议。
2. 自定义代码输出后端（以下简称“后端 SDK”）：使外部包能够基于 SvTypes 的稳定声明信息生成其他风格或其他目标的代码，而不修改 SvTypes 的 IR、类型系统、随机化、覆盖率或既有渲染器。

两项能力均采取“**主线只定义语义，扩展只消费冻结数据**”的方式。它们不得成为 `svtypes` 的运行时依赖，也不得反向改变设计的语义、标识、随机结果、覆盖率数据库或既有 SV/C++ 生成结果。

本文件是后 2.0 的架构与详细设计，而非当前主线的实现计划。2.0 的随机化和覆盖率承诺面、公开 API、数据库格式与测试基线保持独立。

## 2. 不可破坏的边界

### 2.1 依赖方向

```text
                    +-------------------------+
                    |        svtypes          |
                    |  语义、IR、数据库、生成  |
                    +------------+------------+
                                 ^
                                 | 只读、版本化快照
             +-------------------+-------------------+
             |                                       |
+------------+-------------+             +-----------+------------+
| svtypes-backend-sdk      |             | svtypes-coverage-tools  |
| 后端协议、清单桥接、CLI   |             | 扫描器、目录、浏览器 UI |
+------------+-------------+             +-----------+------------+
             ^                                       ^
             |                                       |
      第三方输出后端                         IDE/网页/桌面前端
```

`svtypes` 不导入、发现或执行任何第三方后端或 GUI 包。扩展包可以依赖 `svtypes`，但不得依赖其私有模块、私有数据结构或生成器内部控制流。主线不添加 GUI 框架、浏览器运行时、插件管理器或第三方目标的依赖。

### 2.2 语义所有权

- SvTypes 是类型、约束、随机、coverage IR、semantic digest、覆盖率计算和覆盖率合并语义的唯一所有者。
- 扩展只能读取“设计交换清单”（Design Manifest）；不能取得可变 IR、`SvObject`、covergroup 实例、Python callable 或对象地址。
- 扩展选项、输出风格、显示状态和建议算法不参与 schema fingerprint、encoding fingerprint、declaration semantic digest、instance layout digest 或覆盖率合并判断。
- provenance 仅用于诊断和源码导航；它不改变声明身份，也不可被用作跨运行匹配的唯一依据。
- 扩展失败只能使该扩展任务失败，不能改变主线生成、Python 随机化、覆盖率采样或数据库写入的结果。

### 2.3 非目标

本设计不引入：

- 从 GUI 直接修改 live 对象、自动执行 `sample()`、`randomize()` 或任意用户函数；
- 由第三方替换 SvTypes 的随机求解器、coverage evaluator、数据库合并器或标准 SV 语义；
- 以 monkey patch 替换 `to_sv_obj()`、`to_cpp_obj()` 或在导入时隐式注册后端；
- 把任意 Python 对象图、任意动态 callable 或宿主进程内存布局作为后端输入；
- 为第三方目标在主线中增加特殊字段、特殊语义或不可验证的兼容分支。

## 3. 共同的数据契约：Design Manifest

### 3.1 定位

Design Manifest 是跨进程、只读、确定性的 JSON 文档。它表示“已经完成声明解析后的设计”，而不是 Python 对象序列化，也不是源码 AST。它是后端 SDK 和浏览器唯一的设计输入。

它应由一个独立的清单桥接包生成；在 2.0 稳定前，该桥接包作为实验性外部消费者，仅读取已经公开的 schema descriptor、冻结 coverage IR 和生成观察清单。这样两项支线可以独立推进，而不要求在 2.0 主线上增加扩展 API。

若实践证明需要长期承诺，后续再以单独设计评审，将一个纯函数式的 `svtypes.export_design_manifest(...)` 纳入主线。该函数只能导出同一份数据，不能触发新语义或改变既有生成接口。

### 3.2 顶层结构

清单格式标识为 `svtypes.design-manifest/v1`，并至少包含：

```json
{
  "format": "svtypes.design-manifest/v1",
  "producer": {"svtypes_version": "…", "bridge_version": "…"},
  "design": {"name": "…", "manifest_digest": "…"},
  "types": [],
  "coverage": {"covergroups": []},
  "provenance": [],
  "diagnostics": []
}
```

- `types` 使用公开 schema descriptor 的规范化数据，保留类型/字段、编码与 schema 指纹。
- `coverage.covergroups` 使用 `CoverageIR.definition_snapshot()` 的完整语义定义，并附带 `covergroup_type_id`、`declaration_semantic_digest` 与各 point/cross/bin 的稳定语义 ID。
- `provenance` 保存可选的文件、行、列、限定名和源码锚点，独立于语义定义。清单消费者必须能在 provenance 缺失时工作。
- `diagnostics` 是结构化的扫描诊断；一个有诊断的清单仍可含可用的已成功声明。
- `manifest_digest` 是对省略 provenance 与 diagnostics 后规范化内容的摘要。它只用于防止拿错输入，不替代各声明的语义摘要。

清单不得包含对象句柄、`id()`、绝对临时路径、闭包、可执行 Python 代码、环境变量、随机种子或任何运行时采样值。

### 3.3 版本与兼容

- 格式主版本不兼容时递增；消费者必须显式声明支持的格式主版本。
- 新字段必须是可选的，并放入明确命名的对象；消费者不得因为未知可选字段而改变既有语义。
- 消费者应以 capability 表示所需构造，例如 `coverage.cross.function_bins`、`constraint.dist`。输入含未支持构造时，必须产生明确错误或降级为“不可生成”；不得静默遗漏。
- 清单的排序、JSON 编码和 artifact manifest 均必须确定性。相同声明输入得到字节稳定的清单。

## 4. 自定义代码输出后端 SDK

### 4.1 包与发现模型

建议建立独立包 `svtypes-backend-sdk`，并将具体后端放在各自独立包或仓库中。SDK 提供 CLI、协议类型、清单读取、校验、artifact 写入和测试夹具；它不是 `svtypes` 的依赖。

后端由 SDK 显式发现，例如命令行传入 Python entry-point 名称或包路径。`svtypes` 的 `generate()` 不发现插件、不接受后端对象，也不改变已有 `sv`、`cpp`、`schema` target 的含义。

```text
svtypes-design-export <输入> --out design.json
svtypes-backend build --backend example.target --input design.json --out generated/
```

每次 build 在独立进程中运行。后端不能依赖导入顺序或修改宿主进程状态。

### 4.2 协议

概念协议如下；正式 SDK 可采用 `Protocol` 与 JSON Schema 同时约束：

```python
class Backend(Protocol):
    metadata: BackendMetadata

    def validate(self, design: DesignManifest) -> tuple[Diagnostic, ...]: ...

    def render(
        self, design: DesignManifest, options: Mapping[str, JsonValue]
    ) -> ArtifactSet: ...
```

`BackendMetadata` 至少含唯一 `backend_id`、后端版本、支持的 Design Manifest 版本、capability 列表和可接受选项的 schema。`ArtifactSet` 至少含 artifact 的相对路径、字节内容、媒体类型、用途和摘要。

SDK 强制：

- 输入对象为深度不可变视图；后端对其赋值或修改嵌套容器必须失败。
- artifact 路径必须是干净的相对路径，不能越出输出目录，不能冲突，不能覆盖不属于本次 artifact set 的文件。
- 后端选项仅属于该后端。它们写入 artifact manifest 以便可复现，但不得写入 Design Manifest 或覆盖率数据库。
- 诊断含稳定代码、严重级别、消息和可选 provenance；不得只返回未解释的异常。
- 未支持输入构造是验证失败，不是“跳过该节点”。

### 4.3 与现有渲染器的关系

既有 `generate()`、`to_sv_obj()`、`to_cpp_obj()` 保持其公开行为和测试基线。最初可提供一个“参考 SV 后端适配器”，用于以清单重现可验证的声明片段；它是 parity 参照，不是将现有渲染器替换为插件架构的前提。

后端不得把生成后的 SV/C++ 文本再次解析为输入，也不得依赖 private renderer helper。它应从类型 schema、约束/随机声明和 coverage IR 直接生成自己的目标表示。这样不同代码风格、目录布局、命名策略或语言目标可以演进，而不将它们耦合到主线渲染器。

### 4.4 失败与安全模型

- 清单导出失败：不产生“看似完整”的清单；可选择输出仅含诊断的失败报告。
- 后端验证失败：零 artifact 或明确标为不完整的 artifact set，退出为失败。
- 后端渲染失败：保留上一次成功输出；临时目录完成写入与摘要校验后才原子替换本次目录。
- 后端不得访问网络、私有仿真环境或未声明的设计外文件作为隐式输入。若某后端确需外部资源，必须将其声明为显式、可校验的输入。

### 4.5 后端验收测试

SDK 应提供可复用 conformance suite，覆盖：确定性、输入不可变、未知 capability、稳定诊断、路径逃逸、冲突 artifact、错误退出不污染输出，以及用同一 manifest 重复生成的摘要一致性。具体后端另须以固定 fixture 验证其目标代码的语义，不得以修改 SvTypes 核心测试来迁就后端。

## 5. 覆盖率设计 GUI 与辅助配置

### 5.1 组件边界

建议建立独立包 `svtypes-coverage-tools`，包含：

- **scanner**：受控地导入用户指定设计，提取声明并导出 Design Manifest；
- **catalog**：把清单规范化为可查询的覆盖率目录；
- **GUI service**：本地交互应用，维护当前扫描会话、catalog 与选择状态；
- **UI**：连接 GUI service 的本地网页或桌面前端；
- **planner**：生成可审阅、带前置条件的 coverage change proposal。

GUI 必须是可启动、持续运行的本地交互应用，而不是一次性导出的 HTML 报告。UI 框架不属于本设计承诺；应优先选择本地运行、可离线使用的实现，并将 GUI service 与 scanner 进程分开，使 GUI 进程不需要导入用户设计。

### 5.2 扫描流程

扫描的输入必须明确：模块入口（导入后发现模块内全部 `SvObject` 子类）或已导出的 Design Manifest。默认扫描在隔离子进程中进行，因为导入 Python 模块本身可能有顶层副作用。

扫描只收集声明信息：字段、类型、约束、covergroup、CovPoint、Cross、bin、option、type_option、稳定 ID 与 provenance。它还读取受限 `@coverage_init` 方法的签名及其 `instantiate(...)` 映射，作为可参数化实例布局预览的声明数据；它不自动构造业务对象、不调用 `instantiate()`、不采样、不随机化，也不执行用户提供的初始化、bin/cross 函数。

扫描问题记录为结构化诊断，不能用“猜测的声明”替代解析失败的节点。GUI 不创建业务实例、不采样、不随机化，也不读取或写入 coverage database；运行结果的可视化不属于本阶段能力。

### 5.3 目录与结果关联

目录层次为：设计 → 模块内发现的类型（并列） → 静态字段引用 → covergroup 类型 → point/cross。bin 在所属
point/cross 的详情面板中显示，不作为侧边树节点。模块内每个用户类型都是顶层兄弟节点；字段引用只作为
所属类型下的边，不把被引用类型从顶层移除。类型层次仅表示声明中的字段类型引用；它不构造
对象、不表示运行时实例，也不推导对象地址或运行时路径。显示名称来自声明，稳定关联键使用：

| 层级 | 关联依据 |
| --- | --- |
| covergroup 类型 | `covergroup_type_id` + `declaration_semantic_digest` |
| point/cross/bin | 覆盖率 IR 的稳定语义 ID |

Manifest 可附带从源码 AST 提取的 `display_order`，用于保持 covergroup、point/cross 与显式
bin 的声明顺序；它是纯展示元数据，不参与声明摘要、稳定 ID 或 merge。

摘要不匹配时，proposal CLI 必须拒绝应用，而非按名称或源码位置猜测匹配。

GUI 至少应支持：

- 打开/刷新一个扫描会话，并展示 scanner 诊断；
- 左侧声明树：模块内类型并列 → 静态字段引用 → covergroup → point/cross；
- 详情页：source/`iff`、成员、selector、normal/ignore/illegal/default/transition 分类、option/type_option、稳定 ID 和摘要。若 cross 含同名局部 `CovPoint`，它必须作为该 cross 详情中的**有效成员定义**内嵌显示，展示其完整 source、`iff`、options 与 bins，并明确外层同名 point 不参与该 cross；它不得出现在左侧公开 point 树、公开 point 数量、数据库项或独立详情路由中；
- 对含 `@coverage_init` 的覆盖组提供 layout-preview：用户只填写该初始化函数中流向 `CoverInput` 的具名参数，GUI 以与 core 相同的纯 materialization 规则展示该 actual 下的实例 bins 与实例布局；预览不创建业务对象、不会采样，也不能替代真实初始化；
- `CoverRef` 显示为声明绑定的宿主静态字段（如 `mode → self.mode`），不出现在 GUI 的参数表，GUI 不接受普通值作为 ref actual；
- 按名称、bin 分类和来源路径筛选；
- 源码位置跳转信息；

### 5.4 辅助配置的边界

独立 proposal CLI 可以产生变更建议，但不能擅自改变 coverage 语义。其输出是独立的 `svtypes.coverage-proposal/v1` 文档，例如：

```json
{
  "format": "svtypes.coverage-proposal/v1",
  "preconditions": {
    "covergroup_type_id": "Packet::cg",
    "declaration_semantic_digest": "…",
    "source_anchor": {"file": "…", "line": 42}
  },
  "operations": [
    {"kind": "add_bin", "point": "opcode", "name": "reserved", "selector": "…"}
  ],
  "rationale": "…"
}
```

可能的 operation 是新增/修改明确的 bin、option、ignore/illegal selector 或说明性注释；其精确定义应只覆盖已有 SvTypes DSL 能表达的构造。proposal 必须带声明摘要前置条件，且只由用户在编辑器/CLI 中明确确认后应用。应用前须重新扫描并检查前置条件；任何不匹配均拒绝自动应用并展示差异。

建议算法必须可解释：展示其使用的静态值域、命中数据、规则与不确定性。它不得自动把未命中 bin 标为 ignore、推断 unreachable、修改 `at_least`/`goal`、改变 cross 成员，或把建议当作覆盖率结果的一部分。

### 5.5 GUI 会话与服务接口

GUI service 在 loopback 地址上启动，只接受本机 UI 请求。它维护会话的输入入口、最新 Manifest、catalog、诊断和当前选择；会话状态不进入 SvTypes 数据库或语义摘要。

服务至少提供以下操作：启动扫描、刷新扫描、读取声明树、读取选中节点详情、按条件筛选与实例布局预览。每次扫描均由独立 worker 完成；service 自身不导入用户设计。proposal 格式与显式应用适配器保留为独立 CLI 能力，不属于 GUI service。

GUI 不读取 coverage database、UCIS 或采样结果。本阶段只解决“coverage 定义是什么、包含哪些 bin、给定合法 `CoverInput` actual 时将 materialize 为何种实例布局”的设计期问题；运行结果查看或 GUI 内变更编辑若未来需要，必须另行立项，不能反向改变 GUI 的基本输入与职责。

## 6. 交付、隔离与实施顺序

### 6.1 仓库与分支

推荐在 2.0 发布并冻结承诺语义面后，分别从该标签创建独立工作树或独立仓库：

- `post2.0/backend-sdk`：清单桥接、SDK、参考后端与 conformance suite；
- `post2.0/coverage-tools`：scanner、catalog、设计 GUI 与 proposal 格式。

优先使用独立发布包。若暂时放在同一源代码仓库，也必须是顶层独立包、独立依赖锁定、独立 CI 任务和独立发布节奏；不得在 `python/svtypes/` 中引入 GUI/插件依赖。合并回主线只接受必要、纯只读且独立评审的公开清单导出契约。

### 6.2 分阶段里程碑

| 阶段 | 交付物 | 对主线的要求 |
| --- | --- | --- |
| E0 | 设计清单 JSON Schema、fixture、bridge 原型 | 仅消费已公开冻结数据 |
| E1 | backend SDK、artifact writer、conformance suite、一个参考后端 | 无主线依赖反转 |
| E2 | scanner 与静态 coverage catalog CLI | 独立子进程；不创建业务实例 |
| E3 | 持续运行的本地设计 GUI：扫描会话、声明树、详情、筛选和源码导航 | 不读取数据库；GUI service 不导入用户设计 |
| E4 | proposal 格式、预览与显式应用适配器 | 不自动改源码；摘要前置条件 |
| E5 | 评估是否需要主线 `export_design_manifest()` | 单独 API/兼容性评审 |

每阶段都可单独停止或发布；E1 不依赖 E2，E2/E3 也不依赖任何第三方输出后端。

### 6.3 进入条件与验收标准

开始 E0 前应满足：2.0 的 coverage IR、身份字段、数据库快照和公开 DSL 语义已冻结并有回归测试。任一尚未冻结的字段只能以实验性 bridge 数据暴露，不能被外部工具当作长期协议。

每个扩展版本发布前必须证明：

- 不增加 `svtypes` 的运行时依赖，也不改变既有 Python/SV/C++ 生成输出；
- 对相同输入可重现清单、artifact 和 catalog；
- 对不兼容摘要、未支持构造、缺失 provenance、扫描异常和 proposal 前置条件不匹配均给出可行动诊断；
- GUI 浏览与建议过程不改变采样、随机化或源码，除非用户显式执行经过前置条件检查的应用步骤；
- 所有公开源、文档、测试、生成物和 Git 记录保持仿真器中立。

## 7. 待后续评审的事项

本设计刻意不预先决定以下实现选择：UI 技术栈、清单桥接的最终包名、entry-point 的具体命名、proposal 的源码编辑器适配器，以及第一批第三方目标后端。它们不改变上述依赖方向和语义边界。

若某目标需要当前 Design Manifest 无法无歧义表达的信息，应先提出“主线已有哪项稳定语义缺少导出”的最小补充，而不是要求 GUI/后端读取私有状态或为目标特化核心 IR。

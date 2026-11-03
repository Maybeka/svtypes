# SvTypes 1.0.0 运行时能力契约

本文件定义启动阶段的兼容性判断。它不定义 SVX 的调用名称、调度、接收者、
构造或异常协议。

## 描述内容与比较规则

每个运行时描述均包含下列无符号版本值；`1.0.0` 对每一项均要求完全相等。

| 内容 | `1.0.0` 值 | 比较规则 |
|---|---:|---|
| 包主版本 | 1 | 完全相等 |
| 模式格式 | 1 | 完全相等 |
| 二进制格式 | 1 | 完全相等 |
| 对象包络格式 | 2 | 完全相等 |
| 生成运行时接口版本 | 1 | 完全相等 |

描述还包含按字典序排序、无重复的“可提供能力”名称。消费者另外给出“必需能力”
名称集合。比较时：

- 所有版本值必须通过上表规则；
- 每个必需能力必须出现在对端的可提供集合中；
- 对端额外提供的未知名称可忽略；
- 未知或不可提供的必需名称导致初始化失败；
- 失败诊断必须说明不匹配的版本项或缺失名称。

## `1.0.0` 稳定能力名称

| 名称 | 含义 |
|---|---|
| `svtypes.checked-encoding-descriptor.v1` | 传输前可比较公开线路描述并拒绝不兼容值。 |
| `svtypes.codec-context.v1` | 打包和解包操作拥有可嵌套、相互隔离的上下文。 |
| `svtypes.record-schema.v1` | 可由外部提供记录名称及有序字段描述，生成普通对象记录。 |
| `svtypes.remote-reference.v1` | 支持固定八字节的可空外来对象引用值。 |

## `1.1.0` 能力名称

`1.1.0` 在 `1.0.0` 集合上增加：

| 名称 | 含义 |
|---|---|
| `svtypes.constraint-ir.v1` | 类级 Typed Constraint IR 与源 schema `constraints` / `ir_digest` / `rand_layers`。 |
| `svtypes.constraint-sample.v1` | Python `randomize()` 使用冻结的位级抽样算法。 |

Python 使用 `RuntimeCapabilities`、`runtime_capabilities()` 和
`require_runtime_compatible()`。SystemVerilog 与 C++ 运行时提供同等描述和
比较入口；生成类型也提供转发至其运行时的静态入口。
`1.1.0` 运行时必须宣称上述新增能力名称。

## 结构体导出与运行数据能力

`svtypes.struct-kinds.v1` 表示支持默认 unpacked 的 `SvStruct` 与显式 packed 的
`SvPackedStruct`，包含必需的 `packed` identity/schema、逐成员 codec、生成类型与
packer、方法参数/结果记录所需的值、局部字段访问、按值复制、容器与引用身份，
以及相应的兼容性校验。Python、SV 和 C++ 同步声明此能力。

## 可选诊断与共享字段适配能力

| 名称 | 含义 |
| --- | --- |
| `svtypes.optional-codec-diagnostics.v1` | 普通对象解码默认 off；操作上下文可显式选择 strict，对嵌套解码传播，保留必要协议解析与身份检查。显式 checked 描述比较不被关闭。 |
| `svtypes.generated-field-visitors.v1` | 提供生成器共享字段清单所需的运行时字段适配及 C++ 内联标注支持；不表示完整手改或性能目标已验收。 |
| `svtypes.generated-field-operations.v1` | 支持生成的 SV 数值、枚举、字符串、实数、对象句柄、远程引用、受支持的结构体及容器共用字段清单进行编解码、计数、显示和适用的参数处理；旧运行时缺少此能力时不能使用相应新产物。 |
| `svtypes.generated-wire-helpers.v1` | 支持当前生成代码使用的编码辅助契约：SV 字段操作在编码时计数，C++ 支持编译期指纹字节及相应对象头接口。缺少此能力的旧原生运行时不能用于这些新产物。 |

三侧均声明这些能力。多文件生成 manifest 的 `required_runtime_capabilities` 列出代码目标的
需求，schema-only 输出为空列表。部署/集成工具可显式使用现有兼容性查询拒绝旧能力集合；
生成应用不在每次运行时读取 manifest，不增加隐式启动校验。旧运行时对摘要的强制比较不受新
发送端控制，依赖旧严格默认的调用应在新运行时显式选择 strict。

对象头、二进制和 schema 格式不因可选比较而改变；既有能力不重新解释。缺失新 helper 的旧
原生运行时不能编译当前生成代码，应使用匹配运行时或明确保留旧生成物。

该名称不承诺消费者的调用调度协议，也不表示随机求解后端的所有目标能力门已解除。
完整边界与迁移规则见[结构体契约](SVTYPES_STRUCT_CONTRACT.md)。

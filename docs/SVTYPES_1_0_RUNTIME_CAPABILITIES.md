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

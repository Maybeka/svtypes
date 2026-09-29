# SvTypes 二进制格式

> **中文正本。** 对应英文镜像：[svtypes_binary_format.md](svtypes_binary_format.md)。
> 两个版本必须在同一变更中同步更新；同步校验记录见
> [translation_manifest.json](translation_manifest.json)。

本文定义 Python SvTypes、生成的 SystemVerilog SvTypes 与 SVX typed payload channel 共用的稳定字节格式。

## 通用规则

- 所有多字节标量均为 little-endian。
- 动态长度以 4-byte unsigned little-endian integer 编码。
- 对象值先写 presence marker；非空时随后写 object envelope。
- object envelope 内字段严格按确定的声明顺序编码；基类字段先于派生类字段。
- `pack_bytes=False` 的字段不进入字节流。
- 裸 scalar、struct、collection 只包含值字节。非空 `SvObject` envelope 还包含 unified type name 与
  encoding fingerprint；传输层 payload kind 不属于 SvTypes 格式。

## 覆盖率数据库容器（1.10）

`CoverageDatabase` 持久文件是独立于对象编解码的跨 run 累计统计容器。它不是 Python 对象快照，不能恢复
covergroup instance、constructor actual、transition history、sample log 或当前 sampling state。导入后，一条
兼容记录可以成为新 instance 的**统计基线**，之后仍由该 instance 继续采样。

文件头依次为 `SVTCOVDB`（8 bytes）、little-endian `u16 format_version`（当前 `1`）及 little-endian
`u16 flags`（当前必须为 `0`）。之后必须且只能有 `META`、`RECS` 两个 chunk。每个 chunk 为 4-byte ASCII tag、
little-endian `u32 payload_length`、payload 的 32-byte SHA-256 以及 UTF-8 canonical JSON payload。重复、缺失、
截断、校验错误、未知 version 或未知 flags 一律拒绝；不得将未来版本静默解释成版本 1。

`META` 固定为 `{"format": "svtypes.coverage.database", "version": 1}`；`RECS` 保存数据库记录。记录含 declaration
semantic digest、definition、instance layout digest、coverage option、bin definition、hit/illegal count、sample count
和有限 case source。跨 run merge 与统计基线导入都必须严格匹配 declaration、definition、layout 与 source limit；
任何不匹配都是明确错误。

## UCIS XML 互通（1.10）

`export_ucis(database)` 导出 UCIS 1.0 XML 及独立的结构化 loss report；`export_ucis_file(path, database)` 写入文件。
UCIS 是用于通用 coverage 浏览、报告和交换的投影，不替代 SvTypes 持久数据库：SvTypes identity、source evidence、
runtime object 及无法精确映射的语义不得伪装成 UCIS 数据。

当前可无损导出的子集是 integer constant / closed range 的 coverpoint bin，以及只引用这些已导出 point bin 的静态
cross bin。不能无损表达的 default/transition bin、dynamic selector、queue-calculated cross bin 及依赖未导出 point 的
cross 会从 XML 省略，并逐项出现在 loss report。若 covergroup 没有可导出 coverpoint，则整个 covergroup 省略并报告
损失；不能生成 schema 不合法的空节点或虚构 range。XML 遵循 UCIS 1.0 的
`UCIS → instanceCoverages → covergroupCoverage → cgInstance → coverpoint/cross` 层次，并含必需的 `cgId` source
identifier。

`import_ucis(xml, defaults=...)` 读取该功能覆盖率子集，返回外部记录，不猜测 SvTypes 的 frozen declaration identity。
UCIS 未携带的 SvTypes 专有设定以调用者提供的全局 `defaults` 补齐，并逐项写入 import loss report。
`import_ucis_file(path, config_path=...)` 支持等价的 UTF-8 XML 文件与 JSON 配置；配置必须是全局 key/value JSON object，
不能为单一 covergroup 设置不同策略。导入结果只有经调用者显式映射到兼容 SvTypes declaration 后，才能作为
runtime 统计基线。

## 基础类型

| 类型 | 编码 |
|---|---|
| `Bit[width]()` | `(width + 7) // 8` 个 little-endian byte。pack 时末 byte 未使用的高位清零。 |
| `Bit[width, Signed]()` | byte 数与 unsigned bit 相同；pack 前按 two's-complement width 归一化。 |
| `Logic[width_or_shape]()` | 连续三组 `(width + 7) // 8` 个 little-endian plane：known value bit、X mask、Z mask。X/Z mask 互斥；X/Z 下的 value bit 为 0；每个 plane 的未使用高位为 0。 |
| `Int` | 32-bit signed，等同 `Bit[32, Signed]()`。 |
| `LongInt` | 64-bit signed，等同 `Bit[64, Signed]()`。 |
| `Enum[Bit[width]]` | 精确 `(width + 7) // 8` 个 little-endian byte；不属于 enum 的解码值必须拒绝。 |
| `String` | 4-byte byte length 后接 UTF-8 byte。生成 SV string 是 byte string，每字符 pack 一个 byte。 |
| `Real` | 与 SystemVerilog `$realtobits` / `$bitstoreal` 一致的 8 个 IEEE-754 byte。 |
| `ShortReal` | 与 `$shortrealtobits` / `$bitstoshortreal` 一致的 4 个 IEEE-754 byte。 |

## 组合类型

| 类型 | 编码 |
|---|---|
| fixed `Array[T, N]()` | 按 index 顺序编码 `N` 个元素；没有 length prefix。 |
| `DynArray[T]()` | 4-byte element count，之后按 index 顺序编码元素。 |
| `Queue[T]()` | 与 `DynArray[T]()` 相同。 |
| `AssocArray[K, V]()` | 4-byte entry count，后接按 key 的稳定编码 byte 字典序排序的 key/value pair。Python、生成 SV、生成 C++ 使用同一顺序；插入顺序和 simulator iteration order 不影响 payload。 |
| `SvObject` | 1-byte presence marker：`0` 为空且无后续 object byte；`1` 表示后接 object envelope。 |
| object envelope | magic `SVXO`、2-byte little-endian format version、2-byte little-endian encoding-field count、8-byte little-endian object number、作为 SvTypes `String` 的 unified type name、原始 32-byte SHA-256 encoding fingerprint，最后是 field byte。冻结的 1.0.0 envelope version 是 `2`；prototype version `1` 必须拒绝。 |
| nested `SvObject` | 在字段位置内联使用同一 `SvObject` 编码。 |
| object array/queue | 元素按 container 顺序作为 `SvObject` 编码；每个非空元素有各自 marker 和 envelope。 |
| 生成 C++ 的 object array/queue | C++ container 使用 object pointer，以保留 identity；同时适用于 `Queue[GraphNode]()` 风格与显式 `Queue[Object["GraphNode"]]()` reference 声明。 |

## 对象引用

冻结 marker space：

```text
0: null object
1: inline object envelope follows
2: reference record follows
```

reference record：

```text
presence = 2
8-byte little-endian __svtypes_object_number
```

reference record 后没有 field byte。prototype version-1 object stream 不能作为冻结 version-2 stream 接受。

若 inline object envelope 的 id 已存在于当前 target registry 且类型兼容，unpack 会原地更新已注册对象。同一图中
多个同 id、兼容的 inline envelope 因而表示对同一 target object 的重复更新；已有 id 的类型不兼容是错误。

## 错误契约

- Python unpack 对截断但原本受支持的 byte stream 抛出 `ValueError`。
- 生成 SV unpack 对同一情形调用 `$fatal`。
- 不支持的 generated-SV type 必须在 generation 阶段以 `NotImplementedError` 失败。
- recursive object graph 使用上述 M4 reference record。
- packed union 不在当前 SVX 范围；加入前需要独立的 active-member/tag policy 和 bit-aliasing contract。

## Decoder 资源限制与事务

- 默认 dynamic string、array、queue、associative array 最多接受 `1_000_000` byte/element。Python codec 可通过
  `String(max_bytes=...)`、`DynArray[T](max_length=...)`、`AssocArray[K, V](max_length=...)` 进一步收紧局部限制。
- Python object-graph decode 使用不可变公开 `DecodeLimits`：默认 64 MiB input、1,000,000 dynamic element、
  100,000 inline object、256 次 nested object codec call。`CodecSession.unpack_context(limits)` 为单次操作选择更严格限制。
- Python graph decode 对 identity、registry entry、field value 做 staging；最外层成功操作一起 commit，任一 exception 都丢弃 staging，
  既有 object 与 registry binding 不变。
- 生成 SV/C++ 在分配 string/dynamic container 前强制 1,000,000 dynamic-length limit。额外的 graph-depth/object-count
  parity 在 operation context 替换当前 global graph state 前仍属于 release qualification。

## 对象身份

每个非空 object envelope 都有保留的 `__svtypes_object_number`：

```text
63              48 47                            0
+----------------+--------------------------------+
| origin_number      | local object counter           |
+----------------+--------------------------------+
```

- `0` 保留给 null/unassigned。
- origin id 标识分配该对象的 backend/session。
- local counter 由该 backend/session 单调分配。
- prototype 缺省 origin id：Python `0x0001`、SystemVerilog `0x0002`、C++ `0x0003`。
- 缺省 lifecycle model 是显式 session/shadow ownership；registry 必须提供 cleanup/reset operation。
- weak-reference registry 是可选 backend capability；当前 target 无可用 `weak_reference` 支持，故 legacy SV flow 不能要求它。

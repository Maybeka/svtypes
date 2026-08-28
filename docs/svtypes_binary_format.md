# SvTypes Binary Format

This file defines the stable byte format used by Python SvTypes,
generated SystemVerilog SvTypes, and SVX typed payload channels.

## General Rules

- All multi-byte scalar values are little-endian.
- Dynamic lengths are encoded as a 4-byte unsigned little-endian integer.
- Object values are serialized as a presence marker followed by an object
  envelope when present.
- Object fields are serialized in deterministic declaration order inside the
  object envelope.
- Base-class fields are serialized before derived-class fields.
- Fields with `pack_bytes=False` are omitted from the byte stream.
- Bare scalar, struct, and collection values contain only value bytes. A
  non-null `SvObject` envelope additionally contains its unified type name and
  encoding fingerprint. Transport-level payload kind remains outside SvTypes.

## Coverage database container (1.10)

`CoverageDatabase` 的持久文件是独立于对象编解码的跨 run 累计统计容器。它不是 Python
对象快照，也不恢复 covergroup 实例、constructor actual、transition history、sample log 或当前
sampling 状态。导入后可将一条兼容记录作为新实例的**统计基线**，随后由新实例继续采样。

文件头为 `SVTCOVDB`（8 bytes）、little-endian `u16 format_version`（当前为 `1`）和 `u16 flags`
（当前必须为 0）。随后必须且只能出现两个 chunk：`META`、`RECS`。每个 chunk 为 4-byte ASCII tag、
little-endian `u32 payload_length`、payload 的 32-byte SHA-256、UTF-8 canonical JSON payload。重复、
缺失、截断、校验失败、未知版本或未知 flags 一律拒绝；未来版本不得静默按版本 1 解释。

`META` 固定为 `{"format": "svtypes.coverage.database", "version": 1}`；`RECS` 是数据库记录。
记录保存 declaration semantic digest、definition、instance layout digest、覆盖率选项、bin 定义、
hit/illegal count、sample count 和有限 case source。跨 run merge 和统计基线导入都必须严格匹配
declaration、definition、layout 与 source limit；不匹配为明确错误。

## UCIS XML interchange (1.10)

`export_ucis(database)` 导出 UCIS 1.0 XML 与独立的结构化 loss report；
`export_ucis_file(path, database)` 将 XML 写入文件。UCIS 是面向通用覆盖率浏览、报告和交换的投影，
不是 SvTypes 持久数据库的替代品：SvTypes identity、source evidence、运行时对象和不能精确映射的语义
不会伪装成 UCIS 数据。

当前可无损导出的子集是整数常量/闭区间的 coverpoint bins，以及仅引用这些已导出 point bins 的
静态 cross bins。无法无损表达的 default/transition bins、动态 selector、队列计算的 cross bins
以及依赖未导出 point 的 cross，会从 XML 中省略，并在 loss report 中逐项说明。若一个 covergroup
没有可导出的 coverpoint，则整个 covergroup 在 UCIS XML 中省略并报告损失；不会生成 schema 不合法的
空节点或虚构 range。导出的 XML 遵循 UCIS 1.0 的 `UCIS → instanceCoverages → covergroupCoverage →
cgInstance → coverpoint/cross` 层次，并包含 required `cgId` source identifiers。

`import_ucis(xml, defaults=...)` 读取这一功能覆盖率子集，返回外部记录而不猜测 SvTypes 的 frozen
declaration identity。UCIS 未携带的 SvTypes 专有设置使用调用者提供的全局 `defaults` 补齐，并对每项
补齐写入 import loss report。`import_ucis_file(path, config_path=...)` 支持等价的 UTF-8 XML 文件和
JSON 配置文件；配置文件必须是一个全局键值 JSON object，不能为单个覆盖率设置不同策略。导入结果必须
由调用者显式映射到兼容的 SvTypes 声明，方可成为运行时统计基线。

## Primitive Types

| Type | Encoding |
|---|---|
| `Bit(width, signed=False)` | `(width + 7) // 8` little-endian bytes. Unused high bits in the final byte are zero on pack. |
| `Bit(width, signed=True)` | Same byte width as unsigned bits. Values are normalized to two's-complement width before packing. |
| `Logic(width_or_shape)` | Three consecutive `(width + 7) // 8` little-endian planes: known value bits, X mask, then Z mask. X and Z masks are disjoint; value bits under X/Z are zero; unused high bits in every plane are zero. |
| `Int` | 32-bit signed value, same encoding as `Bit(32, signed=True)`. |
| `LongInt` | 64-bit signed value, same encoding as `Bit(64, signed=True)`. |
| `Enum(width=8/16/32/64, signed=...)` | Exactly `width / 8` little-endian two's-complement bytes. Width and signedness are mandatory declaration arguments; decoded values not present in the enum are rejected. |
| `String` | 4-byte byte length followed by UTF-8 bytes on Python. Generated SV strings are byte strings and pack one byte per character. |
| `Real` | 8 IEEE-754 bytes matching SystemVerilog `$realtobits` / `$bitstoreal`. |
| `ShortReal` | 4 IEEE-754 bytes matching SystemVerilog `$shortrealtobits` / `$bitstoshortreal`. |

## Composite Types

| Type | Encoding |
|---|---|
| fixed `Array(T, N)` | `N` elements serialized in index order, without a length prefix. |
| `DynArray(T)` | 4-byte element count followed by elements in index order. |
| `Queue(T)` | Same as `DynArray(T)`. |
| `AssocArray(K, V)` | 4-byte entry count followed by key/value pairs sorted lexicographically by each key's stable encoded bytes. Python, generated SV, and generated C++ use the same ordering; insertion and simulator iteration order do not affect the payload. |
| `SvObject` | 1-byte presence marker. `0` means null and no more object bytes follow. `1` means an object envelope follows. |
| object envelope | Magic bytes `SVXO`, 2-byte little-endian format version, 2-byte little-endian encoding-field count, 8-byte little-endian object number, unified type name as a SvTypes `String`, the raw 32-byte SHA-256 encoding fingerprint, then field bytes. The frozen `1.0.0` envelope version is `2`; prototype version `1` is rejected rather than interpreted as version `2`. |
| nested `SvObject` | Same `SvObject` encoding inline at the field position. |
| object arrays/queues | Elements are serialized as `SvObject` values in container order, so each element has its own presence marker and envelope when present. |
| object arrays/queues in generated C++ | SvTypes object elements are represented as object pointers in generated C++ containers so identity is preserved. This applies to both `Queue(GraphNode())`-style object element declarations and explicit `Queue(Object("GraphNode"))` reference declarations. |

## Object References

The frozen marker space is:

```text
0: null object
1: inline object envelope follows
```

2: reference record follows
```

The reference record is:

```text
presence = 2
8-byte little-endian __svtypes_object_number
```

No field bytes follow a reference record. Prototype version-1 object streams
are not accepted as frozen version-2 streams.

If an inline object envelope carries an id that already exists in the current
target registry and the type is compatible, unpack updates the registered
object in place. Multiple compatible inline envelopes with the same id in one
graph therefore describe repeated updates to the same target object. A type
mismatch for an existing id is an error.

## Error Contract

- Python unpack raises `ValueError` for truncated supported byte streams.
- Generated SV unpack calls `$fatal` for truncated supported byte streams.
- Unsupported generated-SV types must fail during generation with
  `NotImplementedError`.
- Recursive object graphs use M4 reference records described above.
- Packed unions are excluded from current SVX scope. They require a separate
  active-member/tag policy and bit-aliasing contract before they can be added.

## Decoder Resource Limits and Transactions

- Dynamic strings, arrays, queues, and associative arrays accept at most
  `1_000_000` bytes/elements by default. Python codecs can lower the local
  limit with `String(max_bytes=...)`, `DynArray(..., max_length=...)`, or
  `AssocArray(..., max_length=...)`.
- Python object-graph decoding uses immutable public `DecodeLimits`. Defaults
  are 64 MiB input, 1,000,000 dynamic elements, 100,000 inline objects, and
  256 nested object codec calls. `CodecSession.unpack_context(limits)` selects
  stricter limits for one operation.
- A Python graph decode stages identities, registry entries, and field values.
  The outermost successful operation commits them together; any exception
  discards the staged state and leaves existing objects and registry bindings
  unchanged.
- Generated SV and C++ enforce the 1,000,000 dynamic-length limit before
  allocating a string or dynamic container. Additional graph-depth/object-count
  parity remains a release-qualification item until operation contexts replace
  their current global graph state.

## Object Identity

Each non-null object envelope carries a reserved `__svtypes_object_number`.

```text
63              48 47                            0
+----------------+--------------------------------+
| origin_number      | local object counter           |
+----------------+--------------------------------+
```

- `0` is reserved for null/unassigned.
- The origin id identifies the allocating backend/session.
- The local counter is monotonically allocated by that backend/session.
- The default origin ids used by the prototype are Python `0x0001`,
  SystemVerilog `0x0002`, and C++ `0x0003`.
- The default lifecycle model is explicit session/shadow ownership. Registries
  must provide a cleanup/reset operation.
- Weak-reference registries are optional backend capabilities. The configured target
  testing showed no usable `weak_reference` support, so weak references cannot
  be required for legacy SV flows.

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
- Weak-reference registries are optional backend capabilities. Current SystemVerilog target
  testing showed no usable `weak_reference` support, so weak references cannot
  be required for legacy SV flows.

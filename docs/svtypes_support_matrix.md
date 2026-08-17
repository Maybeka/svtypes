# SvTypes Support Matrix

This matrix tracks the production-readiness status for SvTypes serialization
after M4. Python and C++ coverage runs locally; SystemVerilog typed-channel
coverage is an optional SVX/SystemVerilog target integration check.

Status legend:

- `covered`: covered by the current Python/SystemVerilog target parity examples
- `partial`: some implementation or tests exist, but the feature is not
  complete in the listed language/backend
- `deferred`: deliberately postponed to a later milestone
- `excluded`: deliberately out of current SVX scope

| Type | Python Pack/Unpack | Generated SV Pack/Unpack | C++ Codegen | Optional SVX Channel | Failure Mode |
|---|---|---|---|---|---|
| `Bits(width)` | covered | covered | covered | covered | truncated byte streams raise explicit unpack errors |
| `Bits(shape)` | covered | covered | covered | not yet qualified in SVX | shape is retained in identity/declarations and bytes flatten rightmost-dimension-fastest |
| `LogicBits(width/shape)` | covered | covered | covered | not yet qualified in SVX | three-plane value/X/Z encoding; malformed streams fail |
| `Int` | covered | covered | covered | covered | malformed byte stream must fail during unpack |
| `LongInt` | covered | covered | covered | covered | malformed byte stream must fail during unpack |
| `String` | covered | covered | covered | covered | truncated length/data must fail during unpack |
| `Enum(width=8/16/32/64, signed=...)` | covered | covered | covered | covered | width and signedness are mandatory; aliases/out-of-range members and invalid decoded values fail |
| `Real` | covered | covered | covered | covered | simulator parity instability must be documented or deferred |
| `ShortReal` | covered | covered | covered | covered | simulator parity instability must be documented or deferred |
| `SvObject` | covered | covered | covered | covered | missing generated unpack support must fail clearly |
| `SvStruct` | covered | covered | covered | not yet qualified in SVX | non-null by-value packed composite; dynamic/handle members and inheritance are rejected |
| `RemoteRef` | covered | covered | covered | not yet qualified in SVX | exactly one opaque nullable 64-bit object number; no graph/ownership behavior; resolution requires consumer transport context |
| fixed `Array` | covered | covered | covered | covered | unsupported element type must fail during generation |
| multi-dimensional fixed `Array` | covered | covered | covered | covered | generated SV emits explicit loops for nested fixed arrays |
| `DynArray` | covered | covered | covered | covered | truncated length/data must fail during unpack |
| `Queue` | covered | covered | covered | covered | unsupported element type must fail during generation |
| nested `SvObject` | covered | covered | covered | covered | encoded with object presence marker and envelope |
| object arrays/queues | covered | covered | covered | covered | elements use object presence marker and envelope |
| inheritance | covered | covered | covered | covered | generated C++ verifies inherited base fields and derived fields in one object envelope |
| `AssocArray` | covered | covered | covered | covered | entries are sorted lexicographically by canonical encoded key bytes |
| packed union | excluded | excluded | excluded | excluded | out of current SVX scope; requires a separate active-member/tag policy |
| tree-shaped object number envelope | covered | covered | covered | covered | object numbers are metadata in by-value object envelopes |
| recursive object graph | covered | covered | covered | covered | marker `2` reference records; generated C++ uses object handles for object containers and preserves shared references/self-cycles |
| emitted parameterized class pack/unpack | covered | covered | covered | covered | generated C++ covers template classes, emitted specializations, and member-only direct template specializations |
| member-only parameterized object field | covered | covered | covered | covered | generated SV uses direct `Base#(...)`; generated C++ uses direct `Base<...>` with no intermediate class |
| dependent parameterized field shapes | deferred | deferred | deferred | deferred | field descriptors such as `Bits(WIDTH)` are not modeled yet |

M3 is complete for tree-shaped Python <-> SystemVerilog typed-channel
serialization. Recursive graph synchronization, reference records, and
lifecycle policy are tracked by [SVTYPES_MILESTONE_4.md](SVTYPES_MILESTONE_4.md).

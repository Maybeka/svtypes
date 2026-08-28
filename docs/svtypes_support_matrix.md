# SvTypes Support Matrix

This matrix tracks the production-readiness status for SvTypes serialization
after M4. Python and C++ coverage runs locally; SystemVerilog typed-channel
coverage is an optional remote-target integration check.

Status legend:

- `covered`: covered by the current Python/target parity examples
- `partial`: some implementation or tests exist, but the feature is not
  complete in the listed language/backend
- `deferred`: deliberately postponed to a later milestone
- `excluded`: deliberately out of current SVX scope

| Type | Python Pack/Unpack | Generated SV Pack/Unpack | C++ Codegen | Optional SVX Channel | Failure Mode |
|---|---|---|---|---|---|
| `Bit(width)` | covered | covered | covered | covered | truncated byte streams raise explicit unpack errors |
| `Bit(shape)` | covered | covered | covered | not yet qualified in SVX | shape is retained in identity/declarations and bytes flatten rightmost-dimension-fastest |
| `Logic(width/shape)` | covered | covered | covered | not yet qualified in SVX | three-plane value/X/Z encoding; malformed streams fail |
| `Reg(width/shape)` | covered | covered | covered | not yet qualified in SVX | Python-equivalent to `Logic`; only the generated SV declaration spelling is `reg` |
| `Int` | covered | covered | covered | covered | malformed byte stream must fail during unpack |
| `LongInt` | covered | covered | covered | covered | malformed byte stream must fail during unpack |
| `String` | covered | covered | covered | covered | truncated length/data must fail during unpack |
| `Enum(width=8/16/32/64, signed=...)` | covered | covered | covered | covered | width and signedness are mandatory; aliases/out-of-range members and invalid decoded values fail |
| `Real` | covered | covered | covered | covered | simulator parity instability must be documented or deferred |
| `ShortReal` | covered | covered | covered | covered | simulator parity instability must be documented or deferred |
| `SvObject` | covered | covered | covered | covered | missing generated unpack support must fail clearly |
| `SvStruct` | covered | covered | covered | not yet qualified in SVX | non-null by-value packed composite; dynamic/handle members and inheritance are rejected |
| `RemoteRef` | covered | covered | covered | not yet qualified in SVX | exactly one opaque nullable 64-bit object number; no graph/ownership behavior; resolution requires consumer transport context |
| fixed `Array` | covered | covered | covered | covered | public runtime class; unsupported element type must fail during generation |
| multi-dimensional fixed `Array` | covered | covered | covered | covered | tuple shapes recursively construct public nested `Array` instances; generated SV emits explicit loops |
| `DynArray` | covered | covered | covered | covered | truncated length/data must fail during unpack |
| `Queue` | covered | covered | covered | covered | unsupported element type must fail during generation |
| nested `SvObject` | covered | covered | covered | covered | encoded with object presence marker and envelope |
| object arrays/queues | covered | covered | covered | covered | elements use object presence marker and envelope |
| inheritance | covered | covered | covered | covered | generated C++ verifies inherited base fields and derived fields in one object envelope |
| `AssocArray` | covered | covered | covered | covered | entries are sorted lexicographically by stable encoded key bytes |
| packed union | excluded | excluded | excluded | excluded | out of current SVX scope; requires a separate active-member/tag policy |
| tree-shaped object number envelope | covered | covered | covered | covered | object numbers are metadata in by-value object envelopes |
| recursive object graph | covered | covered | covered | covered | marker `2` reference records; generated C++ uses object handles for object containers and preserves shared references/self-cycles |
| emitted parameterized class pack/unpack | covered | covered | covered | covered | generated C++ covers template classes and member-only direct template specializations; `specialize()` products are Python-side bindings that do not emit classes |
| member-only parameterized object field | covered | covered | covered | covered | generated SV uses direct `Base#(...)`; generated C++ uses direct `Base<...>` with no intermediate class |
| unbound parameter template | covered | covered | covered | covered | value parameters declare svtypes scalar types (`Parameter(Int)` etc.), type parameters use `Parameter(type)`; template definition generated as SV `class X #(parameter int W)` / C++ `template <int32_t W> struct X`; `specialize()` is a Python-side binding and never emits a generated SV/C++ class — target-language uses are `X#(.W(4))` / `X<4>`; subclass parameter forwarding via `Base.specialize(A=ParamRef())` flattens to the original template; unbound templates and ParamRef intermediate classes cannot be instantiated or used as base/field types; `str`/`float` parameters are rejected for generated C++ (not legal non-type template params) |
| template instance cross-language encoding | covered | covered | covered | covered | int/longint value-parameter instances encode with the same `$sformatf`/`std::to_string` type-name expression as Python (`Tpl[W:int=4]`); bare type-parameter instances have no stable cross-language name (C++ `typeid().name()` is mangled, SV `$typename` is qualified) — their cross-language unpack is not supported; `specialize()` products never emit generated classes |
| symbolic `range()` loop in constraints | covered (constant bounds) | covered (SV `foreach` + bounds) | excluded (no C++ randomize) | deferred | constant bounds unroll for Python SMT/randomize; unbound bounds render as `foreach (arr[i]) if (i >= start && i < stop)`, since SV constraints have no `for` statement |
| dependent parameterized field shapes | deferred | deferred | deferred | deferred | field descriptors such as `Bit(WIDTH)` are not modeled yet |
| constrained-random (`@constraint`, `randomize()`, `@rand_layer`, `layered_randomize()`) | covered | covered (generated `constraint {}` and `layered_randomize()`) | excluded (no C++ `randomize()`) | deferred | Python SAT/UNSAT/`state_xz` and layered status; SV simulator sampling need not match Python bit-for-bit. `DynArray`/`Queue` support `size()` plus `foreach` and may join a `rand_layer` as a whole: the generated helper snapshots and controls current elements individually, never queries a non-singular container mode. |
| `Logic.signed` | covered | covered | covered | not yet qualified in SVX | default unsigned identity string includes `signed=false`; three-plane bytes unchanged |

M3 is complete for tree-shaped Python <-> SystemVerilog typed-channel
serialization. Recursive graph synchronization, reference records, and
lifecycle policy are tracked by [SVTYPES_MILESTONE_4.md](SVTYPES_MILESTONE_4.md).

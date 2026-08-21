# SvTypes Milestone 3 Parity Example

This example contains the generated SystemVerilog model for M3 tree-shaped
SvTypes serialization. Its declarations and code generator run locally; the
`tb*.sv` channel benches additionally require SVX and a supported simulator.

`generated_types.sv` is produced from the Python SvTypes declarations in
`tests/types.py` by running:

```sh
PYTHONPATH=python:. python3 examples/milestone_3_svtypes_parity/generate_sv.py > examples/milestone_3_svtypes_parity/generated_types.sv
```

For standalone verification, run the local Python and C++ parity tests from
the repository root:

```sh
.venv/bin/python -m pytest -q tests/python/test_cpp_manytypes.py \
  tests/python/test_cpp_inheritance.py
```

See the [local milestone guide](../../docs/local_milestones.md) for setup and
the SVX/SystemVerilog target integration boundary.

The transaction covers:

- `Bit(width)`
- `Int`
- `LongInt`
- `String`
- `Enum`
- `Real`
- `ShortReal`
- fixed `Array`
- `DynArray`
- `Queue`
- nested `SvObject`
- inheritance
- emitted parameterized SvTypes classes
- member-only direct parameterized object fields

The test sends the transaction in both directions through the M2 typed channel
layer. Payload data remains binary. Recursive object graphs and reference
records are M4 scope.

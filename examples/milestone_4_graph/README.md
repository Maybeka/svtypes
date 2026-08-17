# SvTypes Milestone 4 Graph Parity Example

This example contains the generated SystemVerilog model for M4 object-graph
serialization. Its declarations and code generator run locally; the `tb*.sv`
channel benches additionally require SVX and a supported simulator.

`generated_types.sv` is produced from the Python declarations in
`tests/types.py`:

```sh
PYTHONPATH=python:. python3 examples/milestone_4_graph/generate_sv.py > examples/milestone_4_graph/generated_types.sv
```

For standalone verification, run the local Python and C++ graph-parity tests
from the repository root:

```sh
.venv/bin/python -m pytest -q tests/python/test_m4_graph.py \
  tests/python/test_cpp_graph.py
```

See the [local milestone guide](../../docs/local_milestones.md) for setup and
the SVX/SystemVerilog target integration boundary.

The test covers:

- marker `2` object reference records
- shared child identity preservation
- self-cycle identity preservation
- repeated object references in queues
- object queues generated from both `Queue(GraphNode())` and
  `Queue(Object("GraphNode"))`
- Python -> SV typed-channel graph payloads
- SV -> Python typed-channel graph payloads
- malformed SV reference records for unresolved ids, type mismatches, and id `0`

C++ graph parity is covered for explicit `Object("Type")` reference fields and
object queues. Forward references and weak-reference lifecycle cleanup are not
covered by this example.

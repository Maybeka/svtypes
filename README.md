# SvTypes

SvTypes is the independent data-modeling and serialization package used by
SVX. It provides a Python DSL for SystemVerilog-like types, binary
pack/unpack, and generated SystemVerilog/C++ support code.

SvTypes does not depend on SVX.

当前稳定版本为 `1.2.0`。支持范围、明确排除项与兼容性承诺见
[支持矩阵](docs/svtypes_support_matrix.md)和
[二进制格式](docs/svtypes_binary_format.md)。

## Documentation

公开接口、格式和运行时能力说明位于 [docs](docs/README.md)。

## Layout

```text
python/svtypes/      Python SvTypes package
svtypes_runtime/     SystemVerilog and C++ support files
docs/                SvTypes format and support documentation
tests/               Python regression tests
examples/            SvTypes codegen/parity examples
```

## Quick Start

```sh
python3 -m venv .venv
.venv/bin/python -m pip install pytest z3-solver
.venv/bin/python -m pytest -q
```

The command above permits remote SystemVerilog conformance tests to skip when no target host is
available. Release validation must require that host instead:

```sh
.venv/bin/python -m pytest -q --require-remote-sv
```

Set `SVTYPES_REMOTE_SV_HOST` to use a host other than the project default.
The target host must provide `svtypes_remote_sv_runner`: its `compile` command
accepts the generated source files in the current directory, and its `run`
command executes the compiled fixture. Coverage fixtures additionally write
the manifest-normalized `coverage-observation.json`; adapter implementation and
target-private database handling stay outside this repository.

SVX should depend on a versioned SvTypes release and discover support files
through the public `svtypes` package helpers.

## License

SvTypes is released under the [Apache License, Version 2.0](LICENSE).

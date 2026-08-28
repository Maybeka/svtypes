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

The command above permits remote SystemVerilog target tests to skip when no SystemVerilog target host is
available. Release validation must require that host instead:

```sh
.venv/bin/python -m pytest -q --require-target
```

Set `SVTYPES_target_HOST` to use a host other than the project default.

SVX should depend on a versioned SvTypes release and discover support files
through the public `svtypes` package helpers.

## License

SvTypes is released under the [Apache License, Version 2.0](LICENSE).

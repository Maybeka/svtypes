# SvTypes 实操教程示例

本目录是 [SvTypes Cookbook](../../docs/cookbook.md) 的可运行部分。每个文件都可以独立执行；按编号顺序运行，
它们共同构成一个从 Python 数据模型到 SystemVerilog 生成与验证的完整路径。

先在仓库根目录准备环境：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install pytest z3-solver
```

随后逐章运行：

```sh
for lesson in examples/cookbook/[0-9][0-9]_*.py; do
  PYTHONPATH=python:. .venv/bin/python "$lesson"
done
```

| 章节 | 文件 | 你会亲手完成 |
|---:|---|---|
| 1 | `01_define_and_serialize.py` | 定义定宽字段、枚举和固定数组，并完成二进制往返。 |
| 2 | `02_collections_and_graphs.py` | 使用动态数组、对象句柄、队列和有环对象图。 |
| 3 | `03_constrained_random.py` | 编写约束、分布、soft、unique 与动态数组约束。 |
| 4 | `04_layered_random.py` | 将随机问题按优先级拆成多次普通随机化，并观察 hook。 |
| 5 | `05_functional_coverage.py` | 声明并采样 coverpoint、transition 和 cross。 |
| 6 | `06_generate_artifacts.py` | 将一个 package 发布为 SV、C++、schema 与 coverage manifest。 |
| 7 | `07_schema_and_runtime_contract.py` | 检查 schema/encoding identity、RemoteRef 与跨进程 runtime capability。 |

示例刻意不依赖远程 SystemVerilog conformance target，也不会在仓库里写入生成物。第 6 章使用临时目录；
若要把产物交给构建系统，请把其中的临时目录替换为受版本管理的输出目录，并在 CI 中执行 generator 的
`mode="check"`。

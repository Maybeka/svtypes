# Coverage tools end-to-end walkthrough

启动实际的本地交互 GUI：

```sh
.venv/bin/python examples/post_2_0_coverage_tools/run_gui.py
```

默认地址为 `http://127.0.0.1:8765/`；可通过 `--port` 覆盖，`--port 0` 请求临时端口。
扫描入口是模块名 `packet`（不是逐个 `module:Type`）。工具发现该模块内全部
`SvObject` 子类，并在「覆盖率结构」中并列展示，例如：

```
Monitor
Packet
Top
  |- packet
```

其中 `Top.packet` 只是字段引用边；`Packet` 仍作为顶层兄弟出现，不会被挪进 `Top` 下。

`Packet` 保留一个主 covergroup，并以两个较小的 covergroup 展示输入和 Parameter 布局。
其中涵盖 `iff`、normal/ignore/illegal/default bins、enum bins、固定数量 array bins、16-bit
地址域上的大范围 coverpoint、transition bins、普通显式 cross，以及带局部 CovPoint 的 cross。
打开后可以浏览 covergroup、point 与 cross；
array bin 成员可在所属 point/cross 页面内展开，bin 的 Stable ID 会在悬停名称时显示。该层次仅表示
类型声明和字段引用，不构造对象，也不表示运行时实例；GUI 不创建 proposal。
coverpoint 值域编辑器的当前行为见
[docs/SVTYPES_COVERAGE_GUI_DOMAIN_EDITOR.md](../../docs/SVTYPES_COVERAGE_GUI_DOMAIN_EDITOR.md)。

- `flag_cp`：只声明 `bins[0]` 的 `Bit(1)` coverpoint，用来看完整比较域（0–1）而不是被声明 bin 收成一个点。
- `addr_cp`：`Bit(16)` 地址域（0–65535），用来查看宽轴上的区间编辑。
- `opcode_mode`：沿用公开 coverpoint 的显式/自动 cross。
- `opcode_compact`：对 `opcode_cp` 做 cross 局部重设（`compact` / `control` / `reserved`）。
- `Monitor.activity`：独立并列类型上的简单 covergroup。

`explicit_window` 展示由 covergroup `CoverInput` 签名直接驱动的实例布局预览：

- 选择 `explicit_window`，在“CoverInput 配置”填写 `first=0`、`last=3`，再应用配置。
- `parameter_window` 使用类 `Parameter` 的 `reserved_first=4` 与 `reserved_last=7` 声明一个
  范围 bin。目录与 Manifest 保留这两个参数的符号引用；Python 实例布局使用绑定值，生成 SV
  仍使用参数名。

下列命令则演示非交互的清单与 proposal 产物，不会修改 `packet.py`：

```sh
.venv/bin/python examples/post_2_0_coverage_tools/run_demo.py --out .tmp/coverage-tools-demo
```

The output directory contains:

| File | Meaning |
| --- | --- |
| `packet.design.json` | Isolated scanner’s read-only Design Manifest |
| `packet.catalog.json` | Static covergroup/point/bin tree |
| `add-opcode-two.proposal.json` | CLI proposal example: add an opcode-two bin |
| `add-opcode-two.review.txt` | CLI proposal preview |

The proposal is intentionally **not** applied and is not editable in the GUI.
To apply it in a real design,
rescan the changed design, rebuild its catalog, inspect the review text, then
use `svtypes-coverage proposal apply … --confirm`. The adapter verifies the
covergroup digest and exact source text before it writes anything.

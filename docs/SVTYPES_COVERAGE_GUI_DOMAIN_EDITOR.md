# 覆盖率 GUI：coverpoint 值域编辑器（当前实现）

本文记录设计期 loopback GUI 里 **coverpoint 值域编辑器** 的当前行为。它是 E3 浏览 GUI 上的页内原型，不是写回源码的编辑器，也不改变 CoverageIR / 采样 / 数据库语义。

对照：[后 2.0 扩展架构](SVTYPES_POST_2_0_EXTENSION_ARCHITECTURE.md) §5、[实施记录](SVTYPES_POST_2_0_EXTENSION_IMPLEMENTATION.md)。语义以 IEEE 1800 coverpoint bins 为准（闭区间整数、normal 可重叠、`default` 吃剩余、无显式 coverage bins 时才生成 automatic bins）。

## 1. 边界

已落地：

- 只出现在 **coverpoint** 详情页的「值域」卡片中。
- 编辑结果留在当前浏览器页的内存草稿里。静态 point 按 semantic id 分键；经 `CoverInput` / `Parameter` materialize 的 point 还会纳入该次实例 bins 布局，因此草稿不会套用到另一组 actual。刷新页面或点「放弃所有修改」后回到 catalog 声明。
- 不写源码、不生成 proposal、不读 coverage database / UCIS / 采样结果。
- GUI service 仍只在 loopback 上服务；scanner 仍在子进程导入设计。

未做、也不在本原型范围内：

- 把草稿写回 Python DSL 或走 proposal apply。
- cross bins、带 `CoverInput` / 非整数常量选择器的普通值域 bins。
- 四态 X/Z 值、通配、开区间、运行覆盖率命中。
- 在值域卡片里直接改 `option.auto_bin_max`（只读取 catalog 里已有选项）。

打不开值域编辑器时，卡片显示「这个 coverpoint 不是小整数闭区间，暂不提供值域编辑。」典型原因：选择器不是整数常量/闭区间/`values`/`array_split`，或没有可推断的比较域。含 transition 时则改用 transition 序列编辑器。

## 2. 代码与入口

| 位置 | 作用 |
| --- | --- |
| `extensions/svtypes_coverage_tools/src/svtypes_coverage_tools/gui.py` | 工作台 HTML/CSS、coverpoint 详情布局、`/bin-canvas.js` |
| `extensions/svtypes_coverage_tools/src/svtypes_coverage_tools/gui_bin_canvas.js` | 值域模型、绘制、交互 |
| `extensions/svtypes_coverage_tools/src/svtypes_coverage_tools/catalog.py` | 节点带 `enum_labels`（字符串键）和 `comparison_domain` |
| `examples/post_2_0_coverage_tools/run_gui.py` | 示例：扫描模块 `packet`，默认 `http://127.0.0.1:8765/` |
| `tests/python/test_coverage_tools.py` | HTML/脚本字符串回归 |

脚本以 `gui_bin_canvas.js?v=` 缓存戳提供；改 JS/CSS 后需刷新页面。当前戳为 `v=79`。

## 3. 页面结构

coverpoint 详情自上而下：

1. **hero**：名称、采样表达式、`iff`。
2. **值域**卡片：标题栏 + 轴 + bins 编辑区。
3. **Bins** 卡片：默认折叠，列出当前草稿的 bin 名称、分类和 selector；它随着值域编辑器的新增、删除、重排和范围修改即时刷新。
4. **Options | 声明标识** 两列。声明标识中的路径即 semantic id。含 `CoverInput` / `Parameter` 时，hero 下另有布局预览面板（与值域编辑器独立）。

值域标题栏：

- 标题「值域」。从/到和「共 N 个 bins · M 个值未覆盖」作为同一组值域概要显示；后两项随当前草稿即时更新。
- 标题栏右侧提供会话内撤销 `↶`、重做 `↷` 和「放弃所有修改」。撤销／重做也支持 Cmd/Ctrl+Z 和 Cmd/Ctrl+Shift+Z；每个值域编辑器维护独立历史，不写入源码。撤销／重做紧靠「放弃所有修改」左侧；`ⓘ` 位于最右端，可悬停或点击显示值域编辑的操作说明；不再在下方编辑区重复显示该说明。
- 非枚举：可编辑「从 / 到」，范围必须落在 **完整比较域** 内（由 `options.comparison_domain` 或 `enum_labels` 得到）。缩到比较域内部可以，超出则钳位。
- 枚举：不提供标题栏从/到，始终显示全部枚举元。
- 「放弃所有修改」：恢复 catalog 声明，**保留当前显示域**。

轴与编辑区之间是通栏分隔线。编辑区标题下另有一条两端留白的短线。

## 4. 轴与格子

- 显示域宽度 ≤ 32 个离散值：下方为数值/枚举格子（紧凑模式）。32 是编辑器绘制预算，不是公开语义契约。
- 更宽（例如 `Bit(16)`）：刻度轴。
- 轴与每行滑块对齐：左侧留出 bin 名列（约 7.5em + 间隙），格子/刻度与虚线框同宽。
- 空白格子：斜线底；双击后创建显式 bin，并可在第二次点击保持左键时立即拖出范围。
- `default` 格子：条纹底，同样可双击新建显式 bin；新 bin 或新片段加入后，`default` 自动缩小为剩余值域。
- 选中 bin 覆盖的格子用该 bin 的颜色铺满；其它 bin 占用的格子降饱和，不得盖住选中范围。
- 仅 **选中 bins** 才会高亮数值范围。点击格子/刻度不会选中已有 bin。

声明顺序：每条 bin 一行，顺序与 catalog 声明一致（含 array 折叠后的基名）。

## 5. Bin 行（值域内）

每条 bin：

- **名称**在整条滑块左侧，固定宽度，超出省略号，悬停显示全名。可编辑 bin 悬停时出现左侧拖拽把手；名称与轨道之间出现删除按钮。删除按钮隐藏时不参与布局，名称保有完整显示宽度；显示时才由名称列让出所需空间。
- 数值格或刻度所在行的左侧提供两个图标按钮：`＋` 新建普通 bin，`◇` 新建 `default`。它们与数值格处于同一行，不额外占用 bin 行；按钮以悬停提示说明用途。已有 `default` 时，后者保留在原位但禁用、灰显。
- 右侧虚线框是该 bin 的滑块区。一个 bin 可有多段闭区间；各段画成多个滑块。段与段重叠时，虚线框内拆成多行轨道。
- 未选中：不可拖动，不显示左右手柄。点击 bin 行中除拖拽把手、删除按钮和轨道外的任意区域，或点击滑块，都会选中该 bin。整行悬停会以明显底色标示；数值/枚举格本身不显示 bin 名称提示。
- 选中且可编辑：显示手柄；拖动手柄改该段的一端。直接拖动片选本体进入删除拖动：仍在值域卡片内时显示禁止移动标志且不改变范围；移出卡片时，片选副本跟随鼠标并显示「松开删除」；仅在卡片外松开左键才删除该片段。移出后再回到卡片内不会删除；若鼠标移出整个页面，本次拖动取消且不改变任何片段。
- `default` 与 IEEE **automatic bins** 不可拖、不可改段。

## 6. 下方编辑区

标题统计：`共 N 个 bins`；显示域不超过 4096 个值且存在未覆盖值时，追加 `· M 个值未覆盖`。计数规则：

- 普通具名 bin（即使多段 `values`）计 1。
- 数组 bin 按元素个数计（数字 `count`，或 `auto` 时按当前段数）。
- automatic bins 按自动生成的段数计。
- `default` 计 1。

选中显式 bin 后：

- 名称输入框（合法 SystemVerilog 标识符，页内不与其它 bin 重名）；宽度随内容增长。
- `选择器` 与名称、数组切分、分类置于同一编辑行，以圆点分隔；下方不再放置整 bin 的删除按钮，删除通过名称与轨道之间的行级删除按钮完成。
- 编辑行宽度不足时，圆点、`选择器` 标签和 selector 作为一组在标签前换行，不拆散标签与 selector。
- **数组 bins**使用与分类相同的三段滑槽：`单个`、`自动分段`、`分为 x 段`。第三段未选中时整体显示为 `分为 x 段` 并可直接选中；选中后才在原位置显示内嵌数字输入框 `x`。`自动分段`对应 array split 的 `auto`，不是 IEEE automatic bins。
- 首次激活 `分为 x 段` 时，初值按当前 selector 推断：单值为 1；连续范围默认 2；已有多个非连续片选时取片选数，并始终不超过 selector 覆盖的离散值数。
- `normal` / `ignore` / `illegal` 三档滑槽。
- 删除整个 bin。
- 片选以 SV selector 形式显示，例如 `{1, [4:5], 9}`，非编辑状态与普通文本一致，只在每个数值或枚举两侧保留少量点击空间。双击其中一个数值、枚举值或范围后，仅该片段原位变成数值输入或枚举选择；范围仍以 `[从:到]` 形式编辑。
- 通过轨道滑块、手柄或数值输入改变范围时，正在显示的 selector 文本与折叠的 **Bins** 卡片立即反映同一份草稿。
- 编辑片段在焦点移出该片段时自动恢复 selector 文本；按 `Escape` 可立即取消编辑状态。
- 下方明确提示：在上方该 bin 的虚线轨道中双击；第二次点击保持左键并拖动，即新增一个范围。新片段可与其它 bin 或 `default` 重叠；若与 `default` 重叠，`default` 自动缩小。它不依赖数值格或刻度是否显示。
- 在既有 bin 的虚线轨道内执行该操作时，始终为该 bin 新增片选；不会新建另一条 bin。
- 普通左键拖动片选只在值域轨道内移动它。按住 `Shift` 再以左键拖动则进入删除模式：片选在值域卡片内保持原位并显示禁止移动标志；拖出卡片后，跟随鼠标显示「松开删除」预览。仅当左键最终在卡片外、仍在页面内松开时删除；拖出后再移回卡片不会删除，移出整个页面则取消本次拖动。选中片选后按 `Delete` 键也可删除该片选。
- 滚轮改数字框时立即改对应滑块；只动被滚的那一端，两端相等后不再让滚轮把两端对调。
- 值域上下界、片选数值和数组分段数均使用同一条内嵌下划线输入样式，且可用滚轮以 1 为步长调整。bin 名称是文本而非数值，不响应滚轮。

`default`：说明「覆盖所有尚未被其它 bins 覆盖的值」，可整条删除，不能改范围。剩余片段随其它 bins 变化自动重算。

IEEE **automatic bins**（声明名 `auto[…]`，折叠显示为 `auto`）：

- 只在没有显式 `normal` / `default` / `transition` 时存在（与 freeze 规则一致；单独的 ignore/illegal 不抑制 auto）。
- 按 `option.auto_bin_max`（缺省 64）和 **当前显示域** 切分：枚举则每枚举元一段；整数则均匀切分，余数进最后一段。
- 名称固定为 `auto`，无数组开关、无分类滑槽、无删除、无范围编辑。
- 有显式 coverage bins 或 default 后自动消失；全部显式 coverage bins 删光后会再生成。
- 点在仅被 auto 占用的格子上，会新建一条显式 bin，随后 auto 按上条规则消失。

## 7. 选择器与分组

从 catalog 读入时：

- `constant` / `enum_literal` / 闭 `range` / `values` / `array_split` 转为一段或多段闭区间。
- `values` **不合并**相邻段，以便非连续集合分滑块显示。
- 非连续片选在 selector 与 Bins 卡片中按起始值、结束值的数值顺序显示；其编辑标识仍对应原片选。
- 其它不支持的选择器使 point 不进入编辑器；transition 则进入独立的序列编辑器，不建立数值轨道。
- 名称形如 `name[n]`、`name[lo:hi]` 的连续成员折成一行数组 bin；`auto[…]` 折成 automatic 行且段保持分开（相邻也不合成一条）。
- transition bin 与普通 bin 同列于值域组件。它默认不显示数值片选轨道，而是在自己的 bin 行内显示有序 selector 节点；选中一个节点后，selector 下方显示该集合对应的临时轨道，值域数值格同时按该集合高亮。节点可以表示单值、范围或离散集合。草稿仅在页面内存中，不会写回源码或 CoverageIR。

页内新建的 bin 目前是单点 `constant`；非连续片段通过在当前 bin 虚线轨道中双击并拖出。这些草稿只用于显示，没有对应的源码 AST。

## 8. 示例点（`examples/post_2_0_coverage_tools/packet.py`）

| 节点 | 用来看什么 |
| --- | --- |
| `Packet::cg::opcode_cp` | 显式 bins + `default`；可在 default 格子中新增 bin 或为已选 bin 放置非连续片段，default 会自动缩小 |
| `Packet::cg::flag_cp` | 比较域 0–1，仅 `bins[0]`，格子 1 为空白可新建 |
| `Packet::cg::sparse_opcode_cp` | `bins[1, 4:5, 9]` 的同一 bin 三段非连续值域；可逐段选择、拖动、修改或删除 |
| `Packet::cg::addr_cp` | `Bit(16)` 宽轴 |
| `Packet::cg::kind_cp` | 枚举，标题栏不能改从/到 |
| `Packet::cg::length_cp` | `length_band` 固定个数数组 |
| `Packet::svtypes_auto_cov::opcode` | 只读 automatic bins |

`explicit_window` 的 bins 由 `CoverInput` 驱动，选择器不是整数常量，值域编辑器不打开；用 CoverInput 布局预览。

## 9. 验证

```sh
PYTHONPATH=python:. .venv/bin/python -m pytest -q tests/python/test_coverage_tools.py tests/python/test_coverage_tools_example.py
.venv/bin/python examples/post_2_0_coverage_tools/run_gui.py --port 8765
```

GUI 回归目前以 HTML/JS 字符串断言为主（缓存戳、分隔线、`canCreateAt`、`syncAutomatic`、数组分段滑槽等），不是浏览器端到端。

## 10. 与架构文档的关系

[扩展架构 §5.5](SVTYPES_POST_2_0_EXTENSION_ARCHITECTURE.md) 把「GUI 内变更编辑」标为需另行立项。当前值域编辑器是该方向的 **页内原型**：可以在浏览器里改声明的可视化形状，但会话草稿不进入 Manifest、catalog digest 或 proposal。在接上写回之前，E3 仍是只读扫描 + 本页草稿。

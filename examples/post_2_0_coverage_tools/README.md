# Coverage tools end-to-end walkthrough

启动实际的本地交互 GUI：

```sh
.venv/bin/python examples/post_2_0_coverage_tools/run_gui.py
```

默认地址为 `http://127.0.0.1:8765/`；可通过 `--port` 覆盖，`--port 0` 请求临时端口。

也可以在 **pywebview 桌面窗口**中运行同一套网页，不移植或替换网页组件：

```sh
.venv/bin/python -m pip install pywebview
.venv/bin/python examples/post_2_0_coverage_tools/run_webview_gui.py
```

这是可选桌面壳试验，不加入 SvTypes 主线依赖；默认使用独立回环临时端口，
关闭窗口时关闭其服务，不影响已经打开的网页。未暴露 Python JS
bridge，未启用下载或 `file://` 访问；不修改声明源文件。尚未完成正式分发打包验收。
Windows 强制使用 WebView2（需要安装其 Runtime），不降级到旧 IE 内核；
macOS 使用系统 WKWebView；Linux 的后端和依赖需要另行验证。

`--probe` 会输出 Web API/CSS 能力探测及示例页面 DOM 冒烟结果，随后保留窗口；
`--probe-only` 测试后关闭测试窗口，通过返回 0，失败返回非 0。
`--debug` 启用调试能力但不自动弹出开发工具。

本机 macOS、pywebview 6.2.1 的试验中，18 项能力探测通过，包含 Grid、Flex
gap 实际布局、CSS 变量、`:has`、SVG、Canvas、Pointer Events、ResizeObserver、
BigInt 和 Fetch。普通 / 数组 / 枚举 / 非连续 / transition point、宽值域及两种
cross 的页面与 DSL 卡片均完成 DOM 渲染；transition 双击进入编辑、聚焦、临时
轨道与 Esc 退出通过合成 Pointer/Keyboard 事件检查，没有捕获到脚本错误。
这不是完整 Web 标准符合性测试，也不替代真实鼠标拖动、IME、快捷键的人工
验收；Windows、Linux 尚未实测。UA 中的 WebKit 兼容字符串不是系统引擎版本。
性能、内存、打包体积、跨平台视觉一致性和正式发布安全边界仍需单独评估。

2026-10-11 本地 macOS arm64 体积试验使用 PyInstaller 6.22.3、Python 3.14.8
和 pywebview 6.2.1，保留示例源码、扫描 worker 及 DSL 源码读取所需文件，
排除 Qt 和其他平台的 WebView 后端。带 Z3 原生库的包 `.app` 磁盘占用
51.50 MiB；ZIP 文件 21.40 MiB，UDZO DMG 24.80 MiB，tar.xz 15.58 MiB。
未带 Z3 原生库的对照包为 26.02 MiB、ZIP 11.97 MiB、tar.xz 9.99 MiB；
这个对照不代表拥有可用的随机求解器。对照包使用 `--strip` 后仅节省
约 0.13 MiB。压缩文件大小不等于安装占用。系统 WKWebView 不随包分发。
早期试验的构建 spec、worker 启动适配和产物在忽略的 `.tmp/webview-package-20261011/`
中保存，不是冻结的公开打包接口。遵照用户要求，**没有启动任何打包后的
应用**；仅做文件组成、压缩及 ad-hoc 签名静态检查，不宣称运行通过，
也未测 Windows/Linux、跨系统版本兼容性或正式签名/公证分发。

#### GUI 桌面打包流程

`build_webview_gui.py` 和 `webview_gui.spec` 是当前示例的可复现打包流程，
不是完整 SvTypes 运行时分发，也不表示已完成发行版运行验收。
保留类型/约束/覆盖率声明解析、Manifest/catalog、参数化布局预览、原始用户
DSL 和网页编辑组件，排除 Z3、随机求解执行模块、Qt/CEF 及非目标平台的
WebView 后端。不携带 SV/C++ 仿真运行时资产，不内嵌 WebView2 浏览器内核。
主线 Python 入口及布局预览仍共享一些轻量运行时模块（例如表达式求值和
覆盖组实例布局构建）；保留这些真实依赖，不通过缺功能的桩替代，也不承诺
包内每个 Python 类都只有设计时方法。排除 Python 的 `ssl`、`_ssl`、
OpenSSL 哈希后端 `_hashlib`，保留 Python 自带哈希实现。因此包内 Python
不提供 HTTPS/TLS，不影响 GUI 的本机普通 HTTP；系统 WebKit/WebView2 自身
的网络能力不属于这次裁剪范围。

构建脚本校验固定的 pywebview/PyInstaller/hooks 版本，自动复制 pywebview 到
工作目录，将 SSL 导入延迟到 HTTPS 分支；原始依赖、SvTypes 主库不被修改。
依赖源码形状变化时构建报错，禁止静默套用补丁。每次重新分析依赖，并检查
排除的模块/原生库没有重新进入包中；HTTPS 分支缺少 SSL 时明确报错。

源码只保留需被 source-only DSL 解析的 `packet.py`，不重复携带内部包的全部
`.py` 文件；内部实际依赖由 PyInstaller 分析并收集字节码。冻结入口显式
分发扫描 worker，避免把 `-m` 子进程误当作再次启动 GUI；资源路径解析处理
macOS `.app` 内部符号链接，确保原始 DSL 源码锚点仍位于扫描根内。

在**目标操作系统**的 Python 环境中只执行构建：

```sh
python -m pip install -r examples/post_2_0_coverage_tools/requirements-webview-build.txt
python examples/post_2_0_coverage_tools/build_webview_gui.py
```

建议使用独立虚拟环境；构建环境必须能安装此平台对应的 pywebview 后端依赖。
当前已验证 Python 3.14.8/macOS arm64；上述固定依赖不是其他 Python/OS 组合
已经通过验证的承诺。命令从任何当前目录调用都以仓库根为构建根，不需要安装
完整 SvTypes 运行时、Z3 或 Qt。可通过 `--distpath`/`--workpath` 指定输出目录。
构建会替换同名产物，不应把用户数据放入构建输出目录。不要直接调用 spec，
它需要构建脚本准备隔离依赖。命令不会启动生成的应用。

默认产物：

- macOS：`.tmp/coverage-gui-package/dist/SvTypesCoverageGui.app`。
- Windows：`.tmp/coverage-gui-package/dist/SvTypesCoverageGui/`，分发整个目录，
  不能只复制其中的 EXE。

macOS 构建还会保留一个目录式副本；它与 `.app` 是替代产物，不要把两者的
体积相加作为安装占用。应用和构建用隔离依赖均保存在忽略的 `.tmp` 中。
Windows 使用 WinForms/WebView2 后端，WebView2 Runtime 由系统提供；当前 Windows
分支仅准备了构建配方，**未进行 Windows 构建、体积或运行验证**。未启用 UPX。
当前配方只内置 Packet 示例，还不是选择任意外部工程/解释器的正式分发工具。

2026-10-11 裁剪后的 macOS arm64 `.app` 磁盘占用 **24.44 MiB**，相较此前
不带 Z3 原生库的 26.02 MiB 对照包减少约 1.58 MiB。包静态检查确认 Z3、
随机求解执行模块和 Qt 不在字节码归档/原生依赖中；ad-hoc 签名静态校验通过。
通过阻止源码环境及扫描子进程导入已排除的求解模块，21 项工具/示例/桌面壳
源码测试通过，涵盖扫描、参数化预览、DSL 源码与冻结入口分发；这些测试不
启动窗口，也不是打包后运行验证。最终本地产物位于
`.tmp/gui-trim-20261011/final/dist/SvTypesCoverageGui.app`，按要求没有启动。

同日进一步进行无 TLS 裁剪，并纳入上述打包流程。重新构建的 macOS arm64
包占用 **18.70 MiB**，比 24.44 MiB 减少
约 **5.75 MiB（23.5%）**，原生依赖中已无 OpenSSL。此配置不提供 Python
HTTPS/TLS 能力；系统 WebKit 的网络实现不属于这次裁剪范围。

源码环境同时禁止上述模块及求解模块导入，21 项工具/示例/桌面壳测试通过，
SHA-256 回退实现的已知向量通过。源码版 WKWebView 的 18 项能力检查、8 个
point/cross 页面及 transition 编辑入口、Escape 和本机 HTTP 接口探针通过。
原始网页探针曾等待动画帧超时，复测探针为动画帧等待增加 100 ms 定时
兜底后通过，此兜底已纳入桌面壳探针；未修改 GUI 业务代码。交互检查使用合成 DOM 事件，不是完整
人工交互验证。只启动并自动关闭了源码测试窗口，**没有运行打包应用**。
打包流程的 5 项准备/版本拒绝/仅构建/TLS 错误测试通过；对自动生成的隔离依赖复测，
工具/示例/桌面壳与构建测试合计 26 项通过。包的 ad-hoc 签名静态校验通过，
这不等于 Developer ID 签名、公证或运行验收。正式流程产物没有启动，Windows
尚未验证。

桌面壳测试（模拟生命周期，不需要启动窗口）：

```sh
.venv/bin/python -m pytest -q examples/post_2_0_coverage_tools/test_webview_gui.py \
  examples/post_2_0_coverage_tools/test_webview_build.py
```

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

- `flag_cp`：只声明 `bins[0]` 的 `Bit[1]()` coverpoint，用来看完整比较域（0–1）而不是被声明 bin 收成一个点。
- `addr_cp`：`Bit[16]()` 地址域（0–65535），用来查看宽轴上的区间编辑。
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

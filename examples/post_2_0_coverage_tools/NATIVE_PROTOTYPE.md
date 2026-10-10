# 原生桌面方向原型

这是一版 **PySide6 Qt Widgets** 桌面示例，没有 WebEngine、HTTP 服务或网页组件。
它不替换网页 GUI、不改主线，不代表 E3 原生实现已经验收。

在仓库环境中运行：

```sh
.venv/bin/python -m pip install PySide6-Essentials
.venv/bin/python examples/post_2_0_coverage_tools/run_native_gui.py
```

左侧选择 `Packet → cg` 下的 `opcode_cp`、`sparse_opcode_cp`、
`mode_sequence` 和 `opcode_compact`，分别看普通 bins、非连续集合、
transition 节点共用轨道，以及 cross 的成员 bin 引用。

原型提供：

- 原生树、bins 表格、文本输入、自绘轨道、DSL 语法高亮。
- 常量整数 selector 的实时草稿预览；Enter 确认、Esc 放弃本次 selector 编辑。
- 移动片选、拖动两端修改范围、双击轨道新增片选。
- transition 的节点选择与普通 selector **共用同一个输入组件及轨道**。
- 每个 point 独立的撤销/重做与放弃所有修改；不删除原始 transition。
- 原始声明源码和选中 bin 的 DSL 草稿分开展示，不冒充完整的双向源码编辑。

尚未移植：数组 bins 分段、枚举标签编辑、transition 节点结构增删/重复项/
分类设置、普通 bins 增删/重排、cross 编辑、输入参数布局配置、持久化及源码写回。
这些不是新的主线语义限制。窗口中的草稿不改变 catalog、CoverageIR 或数据库。
现有 cross 示例显示具名成员组合，不宣称通用 cross selector 的完整渲染。

扫描沿用 `GuiSession` 的隔离子进程，使用启动示例的 Python 环境。
示例可用 `--module`、`--source-root`、`--python-path` 指向其他设计；
尚未提供独立打包应用选择外部项目解释器的界面。

目前不打包，也不以 Qt 安装 wheel 的大小冒充最终应用大小。
后续若选择此方向，再评估依赖裁剪、平台发布和完整交互验收。

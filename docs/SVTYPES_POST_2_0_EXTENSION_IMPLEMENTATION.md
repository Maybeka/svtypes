# 后 2.0 扩展架构实施记录

本记录对应 [扩展架构设计](SVTYPES_POST_2_0_EXTENSION_ARCHITECTURE.md)，列出已完成的 E0–E5 交付物及其边界。三个扩展均是独立子项目，位于 `extensions/`；`python/svtypes/` 不导入它们。

| 阶段 | 交付物 | 验证 |
| --- | --- | --- |
| E0 | `svtypes-design-manifest`：v1 JSON Schema、fixture、确定性 bridge、显式类型 CLI | 确定性、摘要、provenance、篡改、CLI 测试 |
| E1 | `svtypes-backend-sdk`：显式发现、冻结输入、capability 检查、安全原子 artifact writer、参考 JSON 后端 | 输入不可变、路径逃逸、重复/缺 capability、原输出保留、CLI 测试 |
| E2 | `svtypes-coverage-tools` scanner 与 catalog：独立子进程导入、部分扫描诊断、静态类型/字段引用层次与 coverage tree | 子进程 PID、错误不覆盖旧清单、稳定 ID/catalog 摘要、CLI 与层次关系测试 |
| E3 | 持续运行的 loopback 设计 GUI：扫描会话、静态类型/字段引用层次、声明详情、筛选与源码信息 | GUI service 不导入用户设计；scanner child PID、节点 API、层次节点与 loopback 限制测试 |
| E4 | `coverage-proposal/v1`、review 文本、严格显式 source apply adapter | proposal 摘要、相对源码锚点、fresh catalog digest、原文匹配、`confirm=True` 测试 |
| E5 | [主线导出 API 评审](SVTYPES_POST_2_0_E5_EXPORT_API_REVIEW.md) | 结论：保持独立 bridge，等待 2.0 承诺面冻结后再作最小 API 评审 |

## 使用边界

- Design Manifest、catalog、proposal 和 artifact manifest 均为版本化 JSON 文档；输入和摘要不兼容时显式报错，不以名称或源码位置猜测匹配。
- scanner 只在 child process 导入用户设计，且不创建业务实例；GUI 不读取 coverage database 或运行结果。类型层次只表示声明中的字段引用，不表示对象实例或运行时路径。
- GUI 只维护设计期扫描会话和当前选择，并提供只读的实例布局预览。proposal CLI 先生成和审阅，再由用户提供重新扫描后的 catalog 和 `--confirm` 执行精确的文本替换；GUI 不创建、审阅或应用 proposal。coverpoint 详情里的[值域编辑器](SVTYPES_COVERAGE_GUI_DOMAIN_EDITOR.md)是页内草稿原型：可改显示形状，不写源码、不进入 catalog digest。
- 参考后端只证明 SDK 协议；既有 SvTypes SV/C++ generator 不被替换、不发现插件，也不依赖任何扩展包。

## 延后事项

UI 技术栈、源码编辑器集成、第一批第三方代码目标、以及是否将 bridge 的纯导出函数提升为主线 API，仍按架构文档的独立评审边界演进；这些事项不会改变当前主线语义。

# 变更记录

## 1.6.0 — 2026-11-14

本版新增显式辅助诊断策略、非拥有型会话及覆盖数据库主库合并，并修复已承诺功能的边界问题。
升级时注意：普通导入不再自动启用受检项目加载器；需要该行为时使用 `checked_project(root)`
或 `run_checked()`。生成器的 schema/coverage 元数据改为显式选择。覆盖数据库写出 v2，
继续读取 v1/v2；读取新文件的工具需要支持 v2。对象 wire 格式保持不变。

- 安装包公共入口改用显式重导出，避免类型工具将公开 API 误判为私有；不增加运行时检查。
- 辅助检查默认关闭：普通导入不安装项目检查器，显式 `checked_project(root)` 或受检入口
  才启用；外部绑定新增 `diagnostics="off"|"strict"`，句柄身份解析不再强制指纹诊断。
- 参数化 SV 类的形状诊断改为显式开启；分层随机框架实现移入辅助区。
- 编码元数据按需计算并缓存，声明/注册变化仅使相关缓存失效；普通 rand 不计算 randc 签名。
- `generate()` 默认仅生成 SV/C++ 与产物归属清单；完整元数据需显式加入 `schema`、`coverage`
  目标。需要保留原完整产物集合的调用应传 `targets={"sv", "cpp", "schema", "coverage"}`。

- 新增可选 `CodecSession(retain_objects=False)`：登记表不延长 Python 对象寿命，
  默认仍强持有；对象存活期间的身份、共享/循环图与线格式保持不变。显式 PackContext
  在自身存活期间保持已编码对象，避免 Python 身份复用导致错误引用。
- 保持导入 SvTypes 后普通 Python 的调用者作用域：`globals`、`locals`、`vars`、`eval`、
  `exec`、`super` 及其别名不再因声明观察器而在错误的栈帧中执行。
- 深度冻结覆盖率 IR 的嵌套数据，隔离输入容器和可变 JSON 快照，避免声明摘要与实例布局分离。
- 修复跨 run 匿名覆盖率记录碰撞和严格合并的部分写入；严格检查同类型的声明兼容性。
  新增显式 `merge(..., mode="master")`、依赖冲突报告与 `reject_conflicts=True` 原子失败策略。
- 覆盖数据库格式升级至 v2，继续读取 v1；保存具体实例定义、投影报告和 UCIS 导入损失。
  修复 transition 数据库计分及 cross 局部 illegal 证据的合并和基线恢复。
- UCIS 绑定导入检查可表示 bin 的种类/范围、cross 成员顺序/索引和计分选项，拒绝缺失或冲突定义；
  参数化数组 bins 使用实例展开结果，缺失的子集外计数显式记录转换损失。
- 后端 SDK 按产物归属发布，保护无关文件与手改文件；增加显式覆盖选项和跨平台路径检查。

## 1.5.0 — 2026-11-14

本版包含 Python 接口的破坏性变更：Object 句柄槽和非打包结构体的使用方式需要
按下述说明迁移。新增的受检调用和存储协议详见中英文参考文档。

- `SvStruct` 默认采用非打包结构体语义；需要 packed 布局时使用 `SvPackedStruct`。
  原有 packed 声明需要改用新类型并重新生成目标代码；对应类型身份、编码、嵌套容器和
  生成声明均区分两种结构体。
- 优化生成代码的可维护性：默认使用真实类型声明，`type(...)` 为可选模式并以 `var`
  声明变量；字段 wire 形状从声明推导，辅助诊断与核心代码分离。
- 改善约束采样和内联缓存生命周期，优化覆盖率索引、数据库序列化，并增加性能基准。
- 覆盖率桌面入口采用 pywebview，提供 GUI 专用依赖裁剪及 Windows 单文件打包流程。

- 公开 `slot_identity()` / `relocated_slot()`：同槽 Ref 共享作用域内的外部存储，
  身份不延长来源寿命；退出恢复最新值，失败按阶段报告并保留函数体及清理异常。
- 公开借用用途查询、Ref 绑定校验和可选值参数地址写回协议；普通后端行为保持不变，
  框架无需依赖实验模块或私有上下文标志。

- 修复外部容器 `.value` 路径复制句柄、回放旧字段值，以及对象整体复制丢失外部句柄的问题。
  结构体视图保留存储权限，句柄读取统一使用绑定会话；整体复制的新对象使用目标会话。

- `run_checked()` 改为黑名单检查：默认保留 Python 行为，只拒绝明确的载体使用违规。
  普通注解、类注解、dataclass、调用者作用域和无源码回调不再被误拒绝；Input 在
  实参求值完毕后的调用入口拷入，已求值的数据不重新求值。

- Python 的 `Object[T]` 成员现在返回固定的可空槽。将 `owner.child = child`
  迁移为 `owner.child.value = child`，并使用 `owner.child.value` 读取所指对象。
  需要对象时请显式构造。handle 容器元素同样使用 `container[i].value` 或
  `container[key].value` 取值。生成的 SystemVerilog 和 C++ 保留各自的 handle 访问语法。

- 非打包结构体的 `.value` 读取独立的字段映射，也支持赋入完整映射。若成员名为
  `value`，将 `record.value.value` 改为 `field(record, "value").value`。

- 新增 `run_checked()` 和 `Input`、`Output`、`Inout`、`Ref` 参数方向。
  `Output` 和 `Inout` 在正常返回时先准备所有写回，再按形参声明顺序发布。
- 新增 `borrow_element()` 稳定借用。借用在容器移位或删除后仍指向所选元素；
  外部存储可通过 `ExternalBorrowStorage` 提供这一能力。

## 1.4.2 — 2026-10-08

- 补齐集成方所需的公开 codec 接口：`AssocArray.key_codec` / `value_codec`、`sv_codegen_context()`、声明初始化值查询、`SvObject.codec_template()` 和带会话上下文的 `checked_unpack()`。codec prototype、解码和复制不执行用户构造器；导入的同源对象编号不会被重新分配。
- 新增预实例化类装配后的 `refresh_declarations()`；显式 RecordSchema 的下划线字段保留正常字段行为；修正 enum 类型身份中的 JSON 编码。
- 修正嵌套集合的 SV typedef 与 packer 生成、RemoteRef 集合解包时的记录别名，以及生成的成员打印引用。字段初始化使用声明默认值，保留四态、字符串转义和嵌套 struct / 定长数组默认值。
- 外部嵌套容器的索引、迭代与切片取得的子容器保留路径绑定；切片赋值使用局部操作。复制及随机化快照只复制值，不复制后端或不透明 key；结果发布支持空、共享、循环和容器内 handle，保留已有对象身份。
- 外部随机化在原对象上执行 pre/post hook，成功后同步 randc 历史，失败不消耗循环值；分层 hook 保留当前层级上下文。用户 hook 的显式写入不回滚，多个后端写入仍不承诺事务性。
- 自动覆盖率递归采样嵌套容器的叶值；多维定长数组保留完整槽位索引，动态子容器跳过缺失槽位与空值域。生成 SV 保留宽整数 bin 边界的位宽和符号；嵌套分层随机恢复逐元素 mode。
- 为外部存储快照、handle 发布、constructor-free codec 及嵌套切片补入专项回归，并同步中英文参考文档。

## 1.4.1 — 2026-09-28

- 稳定 SVX 类型规格 API：新增公开边界 `is_type_spec`、`is_materializable_type`、`materialize_type_spec`、`type_spec_identity`，使集成方无需持有工厂对象即可比较与重建类型身份；`tests/typing/svx_type_specs.py` 提供静态类型基线。

## 1.4.0 — 2026-09-28

- 类型声明迁移为括号式类型规格（**破坏性变更**）：`Bit[width]`、`Logic[width_or_shape]`、`Reg[...]`、`Enum[Bit[w]]`、`Array[T, N]`、`Queue[T]`、`AssocArray[K, V]`、`RemoteRef["..."]` 取代旧的调用式宽度写法；`Bit(8)` 等旧写法明确报错并提示新形式。新增 `Signed` / `Unsigned` / `SignedFamily` 与 `BitSigned` / `LogicSigned` / `RegSigned`。
- 受约束随机推进到 1.3–1.6 里程碑：新增 `dist`、`soft`、`solve_before`、`unique` 约束构造与 `randc` 循环随机字段，覆盖表达式、稀疏、有限、条件与加权分布；动态数组与队列可声明 `rand=True` 并约束 `size()`，元素 mode 与生成 SV 对齐；非空 `rand` handle 与其所指对象参与同一次联合求解，支持容器内 handle 约束。
- 分层随机增强：priority 专属 `pre_randomize` / `post_randomize` hook 交接、分层随机上下文暴露、动态集合元素按层随机化。
- 功能覆盖率（2.0 前置能力）：`@covergroup`、`CovPoint` / `CovPointArray`、`bins` / `illegal_bins` / `ignore_bins` / `default_bins`、`iff` 条件采样、有界 transition、`Cross` / `CrossQueueType`、内存 `CoverageDatabase`、`CoverInput` / `CoverRef` / `@coverage_init` 与实例 bin 布局、`cov=True` 自动默认覆盖组、按类型与实例的加权汇总、有限用例来源与受控 merge，以及 UCIS XML 导入导出。新增 `CoverageError` / `CoverageDeclarationError`。
- 新增 owner-scoped 外部字段存储：`ExternalFieldStorage` / `MemoryExternalFieldStorage`、字段路径与身份描述（`FieldPath` / `FieldIdentity` / `FieldKey` / `FieldIndex` / `FieldMember` / `FieldOperation`）、`bind_external_value`；运行时宣称 `svtypes.external-field-storage.v1`。新增 `ExternalStorageError` / `ExternalStorageClosedError`。
- 依赖参数的字段形状（如 `Bit[WIDTH]()` 与 lambda 布局表达式）在 `specialize()` 时物化；未绑定布局只保留目标语言符号。
- 远程 SystemVerilog 一致性回归升级为必需门槛：未配置或不可达的远程目标使 `remote_sv` 测试失败而非跳过；覆盖生成类型的 pack/unpack 字节对等、对象图身份、字段策略、plusarg、覆盖率采样与截断流失败。
- 文档：新增 cookbook、`reference_ZH` / `svtypes_binary_format_ZH` 中文对照、2.0 功能覆盖率路线图、1.3–1.6 随机设计文档、外部字段存储需求、扩展架构与实现说明；支持矩阵新增功能覆盖率表与括号类型规格条目，并逐项标注 capability gate。

## 1.2.0 — 2026-08-22

- 增加 `@rand_layer` 与 `layered_randomize()`：按 priority 从高到低分批调用普通 `randomize()`，支持 `builtin` 默认组、固定 unpacked-array 元素 target、同名 `super()` 合并、进入前 mode 忽略与退出恢复、`pre_randomize` / `post_randomize` 生命周期，以及源 schema `rand_layers`（不进入 `ir_digest` 或编码布局）。生成的 SV 方法为 `virtual function int layered_randomize()`。
- 增加 AST 约束 DSL、Typed IR、`randomize()` / `RandomContext` 与 `svtypes.constraint-sample.v1` 抽样。Python SMT 后端以 `z3-solver` 为可选依赖。
- `Logic` 增加正式 `signed` 属性；统一类型名称与 C++ `LogicValue<Width, Signed>` 含符号性，三平面字节布局不变。
- 两态 packed 类型正式命名为 `Bit`，四态 packed 类型正式命名为 `Logic`；不保留旧名称别名。
- 新增 `Reg`：Python 数据类型与 `Logic` 完全等价，仅在生成的 SystemVerilog 中保留历史关键字 `reg`。
- `Array` 从工厂函数改为公开运行时类；元组 shape 继续递归构造嵌套数组，并提供多维静态类型推断。
- 源 schema 为实际类增加 `constraints` / `ir_digest`；编码描述与生成的编码 API 不含该键。
- 运行时宣称 `svtypes.constraint-ir.v1` 与 `svtypes.constraint-sample.v1`。
- 增加不依赖集成层的远程 SystemVerilog conformance：生成类型的 pack/unpack 字节对等、对象图身份、字段策略、plusarg、覆盖率采样，以及截断流失败。
- 参数化模板：`Parameter` 支持类型声明——值参数用 svtypes 标量类型（`Parameter(Int)` / `Parameter(LongInt)` / `Parameter(String)` / `Parameter(Real)` / `Parameter(ShortReal)`），类型参数用 `Parameter(type)`；未绑定即类型已知、值未定，`Parameter()` 绑定值（如 `Parameter()(5)`）仍从值推断。含未绑定 `Parameter` 的类视为参数化模板，不可实例化；其参数化定义可直接生成（SV `class X #(parameter int W)`、C++ `template <int32_t W> struct X`；类型参数为 `parameter type T` / `template <typename T>`）。`specialize()` 只做 Python 侧绑定（实例化、schema、`randomize` 折叠 IR），**不生成目标语言类**——目标语言一律用参数类实例 `X#(.W(4))` / `X<4>`，`specialize()` 产物调用 `to_sv_obj()`/`to_cpp_obj()` 报错。子类继承参数类用 `Base.specialize(A=ParamRef())` / `ParamRef("W")` 转发本类参数并压平到原始模板（`class Child #(...) extends Base#(.A(A))`）；未特化模板与 ParamRef 中间类不可作父类/不可实例化；`specialize()` 产物不可再 `specialize()`。`str`/`float` 参数不是合法的 C++ 非类型模板参数，生成 C++ 时明确报错（SV 侧支持）。
- 约束语法：`range()` 循环上下界为常量时在 Python 侧展开供 `randomize()` 求解；上下界为未绑定参数（符号循环）时保留循环，SV 端以 `foreach` + 边界条件渲染（SV 约束无 `for` 语句）。约束块只生成在声明它的参数类上，子类靠 `extends Tpl#(...)` 继承模板约束（不再有包装类）。

## 1.0.0 — 2026-08-15

- 冻结 Python、SystemVerilog 与 C++ 三端的数据类型、编码、对象图和能力协商契约。
- 提供多文件生成、清单校验、动态记录、外来对象引用、会话范围的对象登记与显式清理。
- 固化字段策略：随机化、命令行参数覆盖、打印、覆盖率与字节打包排除；`intelli` 保留为不产生行为的延后元数据。
- 将对象登记公开接口统一为 SvTypes 名称，不再暴露带 SVX 前缀的名称。
- 将发行版本提升至 `1.0.0`。

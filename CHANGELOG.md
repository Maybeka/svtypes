# 变更记录

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

# 变更记录

## 1.2.0 — 2026-08-22

- 增加 `@rand_layer` 与 `layered_randomize()`：按 priority 从高到低分批调用普通 `randomize()`，支持 `builtin` 默认组、固定 unpacked-array 元素 target、同名 `super()` 合并、进入前 mode 忽略与退出恢复、`pre_randomize` / `post_randomize` 生命周期，以及源 schema `rand_layers`（不进入 `ir_digest` 或编码布局）。生成的 SV 方法为 `virtual function int layered_randomize()`。
- 增加 AST 约束 DSL、Typed IR、`randomize()` / `RandomContext` 与 `svtypes.constraint-sample.v1` 抽样。Python SMT 后端以 `z3-solver` 为可选依赖。
- `Logic` 增加正式 `signed` 属性；统一类型名称与 C++ `LogicValue<Width, Signed>` 含符号性，三平面字节布局不变。
- 两态 packed 类型正式命名为 `Bit`，四态 packed 类型正式命名为 `Logic`；不保留旧名称别名。
- 新增 `Reg`：Python 数据类型与 `Logic` 完全等价，仅在生成的 SystemVerilog 中保留历史关键字 `reg`。
- `Array` 从工厂函数改为公开运行时类；元组 shape 继续递归构造嵌套数组，并提供多维静态类型推断。
- 源 schema 为实际类增加 `constraints` / `ir_digest`；编码描述与生成的编码 API 不含该键。
- 运行时宣称 `svtypes.constraint-ir.v1` 与 `svtypes.constraint-sample.v1`。
- 增加不依赖 SVX 的远程 SystemVerilog target 全量仿真：生成类型的 pack/unpack 字节对等、对象图身份、字段策略、plusarg、覆盖率采样，以及截断流失败。
- 参数化模板：`Parameter` 支持类型声明——值参数用 svtypes 标量类型（`Parameter(Int)` / `Parameter(LongInt)` / `Parameter(String)` / `Parameter(Real)` / `Parameter(ShortReal)`），类型参数用 `Parameter(type)`；未绑定即类型已知、值未定，`Parameter()` 绑定值（如 `Parameter()(5)`）仍从值推断。含未绑定 `Parameter` 的类视为参数化模板，不可实例化；其参数化定义可直接生成（SV `class X #(parameter int W)`、C++ `template <int32_t W> struct X`；类型参数为 `parameter type T` / `template <typename T>`）。`specialize()` 只做 Python 侧绑定（实例化、schema、`randomize` 折叠 IR），**不生成目标语言类**——目标语言一律用参数类实例 `X#(.W(4))` / `X<4>`，`specialize()` 产物调用 `to_sv_obj()`/`to_cpp_obj()` 报错。子类继承参数类用 `Base.specialize(A=ParamRef())` / `ParamRef("W")` 转发本类参数并压平到原始模板（`class Child #(...) extends Base#(.A(A))`）；未特化模板与 ParamRef 中间类不可作父类/不可实例化；`specialize()` 产物不可再 `specialize()`。`str`/`float` 参数不是合法的 C++ 非类型模板参数，生成 C++ 时明确报错（SV 侧支持）。
- 约束语法：`range()` 循环上下界为常量时在 Python 侧展开供 `randomize()` 求解；上下界为未绑定参数（符号循环）时保留循环，SV 端以 `foreach` + 边界条件渲染（SV 约束无 `for` 语句）。约束块只生成在声明它的参数类上，子类靠 `extends Tpl#(...)` 继承模板约束（不再有包装类）。

## 1.0.0 — 2026-08-15

- 冻结 Python、SystemVerilog 与 C++ 三端的数据类型、编码、对象图和能力协商契约。
- 提供多文件生成、清单校验、动态记录、外来对象引用、会话范围的对象登记与显式清理。
- 固化字段策略：随机化、命令行参数覆盖、打印、覆盖率与字节打包排除；`intelli` 保留为不产生行为的延后元数据。
- 将对象登记公开接口统一为 SvTypes 名称，不再暴露带 SVX 前缀的名称。
- 将发行版本提升至 `1.0.0`。

# SvTypes 方括号类型规格 API：SVX 对接意见

## 结论

`Bit[8]`、`Array[Bit[8], 16]`、`RemoteRef["domain.Type"]` 等方括号
类型规格 API 的运行时模型符合 SVX 的需求。SVX 应以它作为跨语言方法和
manifest 的唯一类型声明来源，逐步移除用户可见的
`inheritance_type(...)`。

`TypeSpec` 应保持为不可变、可缓存的类型规格；调用规格对象（例如 `Bit[8]()`）
才产生原有的 codec/字段描述符。不要以动态 Python 子类实现规格，否则会破坏既有的
序列化、`deepcopy` 及 `Reg`/`Logic` 运行时类型语义。

## 目标用法

```python
from svtypes import Bit, Int, Queue, RemoteRef
import svx


class Driver(...):
    @svx.inheritance_method
    def transfer(
        self,
        opcode: Bit[8],
        payload: Queue[Bit[8]],
        peer: RemoteRef["sv://tb_pkg/BaseDriver"],
    ) -> svx.Function[Int]:
        ...
```

SVX 在内部物化类型规格，并提取 schema、encoding、SV type 与 packer；用户不应
手工提供 Python factory、SV 类型名或 packer。

需要稳定支持的规格包括：

- `Bit[8]`、`Bit[8, Signed]`、`Logic[32]`、`Reg[8]`；
- `Array[Bit[8], 16]`、`DynArray[Bit[8]]`、`Queue[Bit[8]]`、
  `AssocArray[Bit[8], Logic[4]]`；
- `Object[Packet]`、`Object["Packet"]`、
  `RemoteRef["sv://tb_pkg/BaseDriver"]`；
- `Int`、`LongInt`、`Real`、`ShortReal`、`RealTime`、`String`，以及通过
  `class Op(Enum[Bit[8]]): ...` 定义的具体 enum 类；
- 已具体化的参数化类型。

## 所需的公开契约

SVX 不应依赖 `TypeSpec` 的内部字段。SvTypes 应将以下能力以稳定公开 API 提供，
名字可不同但语义需要等价：

```python
svtypes.is_type_spec(annotation) -> bool
svtypes.is_materializable_type(annotation) -> bool
svtypes.materialize_type_spec(annotation, *, location: str) -> codec/字段描述符
svtypes.type_spec_identity(annotation) -> JSON-compatible value
```

`is_type_spec()` 仅判断对象是否为方括号规格；
`is_materializable_type()` 判断完整的跨语言边界类型集合。该契约需接受
`TypeSpec`、无参数内建类型、`SvObject`/`SvStruct`、具体 enum 类、
`Object[...]`、`RemoteRef[...]` 和已具体化参数类型；不适合作为跨语言边界类型的
输入必须给出明确错误。

当前的 `materialize_type()` 可作为基础，但需要明确成为公开稳定 API，并定义它对
普通类型、`TypeSpec` 与符号参数类型的行为。未绑定参数或符号布局不是跨语言边界类型，
必须在 manifest 生成前拒绝。

`type_spec_identity()` 的结果必须是递归、JSON 可序列化且跨进程稳定的结构；不得包含
构造器闭包、Python `repr`、对象地址或缓存键。`Reg[...]` 与 `Logic[...]` 的 SV 声明
样式同样是 identity 的语义部分。

## 静态类型支持缺口

运行时 API 已具备，但尚不能证明 Pyright/Pylance 能可靠接受它们作为注解：

- `py.typed` 已存在，但 `Bit`、`Logic`、`RemoteRef`、`Object`、`TypeSpec` 等没有
  完整静态声明；
- `collection.pyi` 仍描述旧构造器签名，和 `Array[...]()`、`Queue[...]()` 等新 API
  不一致；
- 尚未发现 Pyright/Pylance 回归测试。

建议补齐 inline typing 或 `.pyi`，为所有 bracket 类型声明
`__class_getitem__` 的静态签名，并用严格 Pyright 测试至少验证：

```python
value: Bit[8]
payload: Queue[Bit[8]]
peer: RemoteRef["sv://pkg/Class"]
```

目标是 LSP 不报告“非法类型表达式”并能展示规格；不要求进行位宽算术或 Python
整数范围推导。静态声明也必须移除已废弃的旧构造器接口。

## Handle 边界

`RemoteRef["canonical.class.id"]` 是 SVX 跨语言类 handle 的底层 SvTypes codec。
它保持为无所有权、可空的 64-bit object ID 传输类型即可。对象注册、动态镜像恢复、
生命周期和跨语言继承兼容性均属于 SVX，不应进入 SvTypes。

# SvTypes 外部字段存储需求

## 状态与目的

本文提出 SvTypes 的通用外部字段存储能力，用于让一个正常的 SvTypes
字段实例将其值存放在任意外部系统中，同时保持 SvTypes 作为类型、归一化、
编码、容器和嵌套对象语义的唯一来源。

该能力的首个消费者是 SVX：跨语言继承中的 Python 镜像字段需要读写绑定的
SystemVerilog 对象成员。但 API 必须是 SvTypes 自身的通用能力，不能导入、
引用或假设 SVX、VPI、DPI、仿真器、对象 ID 或特定传输机制。

## 用户模型

现有 SvTypes 字段声明不变：

```python
class B(SvObject):
    retries = Int()
    words = Array(Bit(16), 8)
    tags = AssocArray(String(), Int())
```

绑定外部存储后，`b.retries` 仍是 `Int`，`b.words` 仍是 `Array`，
`b.tags` 仍是 `AssocArray`。用户不应得到一个替代的 facade 类型，也不应
学习第二套字段 DSL。字段的 `.value`、索引、嵌套成员、pack/unpack、约束、
随机化和覆盖率仍遵循 SvTypes 的公共语义。

## 最小公共 API

SvTypes 应公开以下稳定类型和方法。名称可在实现审阅时微调，但职责和语义
不可省略。

```python
class ExternalFieldStorage(Protocol):
    def read(
        self,
        key: object,
        descriptor: FieldDescriptor,
        path: FieldPath,
    ) -> bytes: ...

    def write(
        self,
        key: object,
        descriptor: FieldDescriptor,
        path: FieldPath,
        operation: FieldOperation,
        payload: bytes | None,
    ) -> bytes | None: ...

class TypeBase:
    def bind_external_storage(
        self,
        storage: ExternalFieldStorage,
        key: object,
        path: FieldPath = (),
    ) -> None: ...

    def unbind_external_storage(self) -> None: ...
```

`key` 是后端私有的不透明值。SvTypes 只能保存和原样传入它，不能比较、序列化、
记录或解释其内容。

`FieldDescriptor` 是公开且稳定的字段描述：至少包括统一类型名、编码描述符、
字段初始化/声明语义和可用的结构信息。它必须足以让后端验证目标与字段类型匹配，
但不得暴露或要求调用者访问 SvTypes 私有属性。

`FieldPath` 是从已绑定字段根到叶节点的不可变路径。它至少支持：

- 固定/动态数组和队列的整数索引；
- 关联数组的类型化键；
- 嵌套 SvObject/SvStruct 的成员名；
- 任意合法的组合路径。

关联数组键必须以其 SvTypes 键类型的规范表示携带，不能以不受约束的 Python
对象或语言特定字符串猜测键类型。

`FieldOperation` 至少包括 `set`、`insert`、`delete`、`append`、`pop`、
`resize`。后端必须拒绝与字段描述或路径不相容的操作。`set` 可用于标量、
嵌套叶节点和完整容器；空路径 `()` 的 `set` 表示整体字段赋值。

## 读写语义

外部绑定不改变类型语义：

```text
field.value 读取
  -> storage.read(key, descriptor, path)
  -> field 的正常 SvTypes unpack/校验

field.value 写入
  -> field 的正常 SvTypes normalize/pack
  -> storage.write(key, descriptor, path, set, payload)
```

字段自身而非后端负责归一化、宽度、signedness、四态、枚举验证、对象引用、
资源限额和二进制编码。后端只存取已经由 SvTypes 描述的字节与路径操作。

`read()` 返回值必须是该路径叶节点描述所要求的完整编码字节；截断、尾随字节、
错误描述符和不相容版本由 SvTypes 的正常解码流程拒绝。

绑定字段默认不维护会掩盖外部更新的隐式可写缓存。每个逻辑读取从后端取得当前值；
未来若引入显式事务或快照缓存，必须是额外、可见、版本化的 API。

## 容器与嵌套对象

容器必须支持整体和局部两种操作，且由用户实际表达式决定粒度：

```python
b.words.value = [1] * 8       # 一次空路径整体 set
b.words[3].value = 7          # path=(index 3) 的局部 set
b.words.value[3] = 7          # 同样是局部 set
b.tags.value["mode"] = 2      # path=(key "mode") 的局部 set
b.queue.value.append(9)       # append
```

对于已绑定集合，`.value` 不能返回脱离外部存储的普通可变 `list`/`dict` 副本。
它应返回满足相应 Python collection 协议的实时 sequence/mapping view，使索引
赋值、切片、插入、删除和键写入形成所对应的路径操作。`field[index]` 返回的
仍是正常 SvTypes 元素字段，只是绑定到派生路径。

`Array`、`DynArray`、`Queue`、`AssocArray`、`ObjectDescriptor`、嵌套
`SvObject` 和 `SvStruct` 必须递归传播绑定。复制、deepcopy、解包到新对象和
字段重建不得意外复制外部绑定；新对象默认未绑定，除非公共 API 明确重新绑定。

## 生命周期与错误

- 只能绑定实例拥有的字段对象，不能绑定类级声明模板；否则必须失败。
- `bind_external_storage()` 必须是原子的：失败时原字段与全部子字段保持原状。
- `unbind_external_storage()` 递归解除所有派生路径绑定；之后字段恢复正常本地
  SvTypes 行为，或由宿主在访问前使对象失效。
- 后端读写失败必须以公开、可区分的 SvTypes 外部存储异常上报，并保留原始异常
  作为原因；不能静默回退到过期本地值。
- 所有会读写字段的公共 SvTypes API，包括 `.value`、容器操作、`from_bytes()`、
  随机化、约束求值和覆盖率采样，必须有已记录的外部绑定行为；不得绕过后端直接
  写私有 `_value` 或容器内部元素。

## 兼容性与能力协商

该能力需要一个新的 `runtime_capabilities()` 名称，例如
`external_field_storage`。依赖它的消费者必须明确声明该能力；旧 SvTypes
运行时应在初始化期失败，而不是在首次字段访问时出现属性错误。

协议、字段描述、路径与操作的版本属于 SvTypes 公共 ABI。未知操作、未知路径
段、描述符不匹配和不支持的容器能力必须明确失败。SvTypes 可以在后续版本扩展
操作集合，但不能改变已有操作的编码或语义。

## 非目标

- 不在 SvTypes 中实现 SVX、VPI、DPI、仿真器访问或跨语言对象表。
- 不把外部存储后端变成新的数据类型/序列化格式。
- 不要求所有字段总是远程；未绑定字段保留当前本地行为。
- 不引入隐式缓存或隐式整容器写回。

## 验收标准

SvTypes 应提供独立于 SVX 的内存 mock backend，并至少覆盖：

1. `Bit`、`Logic`、`Int`、`LongInt`、`Real`、`String` 和 enum 的读写、
   归一化、编码和错误传播。
2. 定长数组、动态数组、队列、关联数组的整体读写、元素/切片/键局部操作与
   非法操作拒绝。
3. 嵌套 SvObject/SvStruct、RemoteRef、组合路径与路径类型验证。
4. 绑定原子性、解绑、复制/deepcopy、对象销毁和后端错误后不回退旧缓存。
5. 已绑定字段上的 pack/unpack、随机化、约束和覆盖率的明确行为。
6. capability 缺失、版本不匹配、截断/尾随编码、描述符不匹配和未知操作的诊断。

完成后，SVX 可以作为该协议的一个后端实现，而不会复制 SvTypes 字段语义。

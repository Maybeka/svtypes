# SvTypes 1.5：动态数组与队列的约束随机

## 范围

本阶段将 SystemVerilog 动态数组和队列的核心随机规则带到 Python：容器声明为
`rand=True` 后，可在 `@constraint` 中使用同名的 `size()` 方法，并用
`range(collection.size())` 写逐元素约束。Python 与生成的 SystemVerilog 使用
相同的源约束。

```python
class Packet(SvObject):
    length = Bit(4)
    data = DynArray(Bit(8), rand=True, max_length=64)

    @constraint
    def legal(self):
        self.data.size() == self.length
        self.length <= 16
        for i in range(self.data.size()):
            self.data[i] == i + 1
```

生成的约束使用 `data.size()` 与 `foreach (data[i])`，不会生成 Python 专有的
`len(data)` 调用。

## 随机化语义

- 仅当动态数组或队列声明为随机变量，且其 `size()` 出现在普通约束谓词或条件中时，
  随机化会求解并调整其长度。
- 尺寸先于 `foreach` 元素约束求解；调整后才对当前元素集合求解约束。
- 未受尺寸约束时，长度保持进入 `randomize()` 时的值，随后随机化现有元素。
- 动态数组缩短时丢弃尾部元素；扩展时保留原元素并在尾部加入新元素。队列也在右侧
  增删。这与 SV 的可观察 resize 方向一致。
- Python 的 `max_length` 仍是资源上限。约束必须给出可满足的、未超过该上限的尺寸；
  它不是自动写入生成 SV 的隐式约束。
- 当所有参与随机尺寸的容器容量组合不超过 4096 时，Python 会枚举尺寸候选，并将每个
  候选交给完整的元素约束求解器；因此不会因先选到一个元素不可满足的尺寸而错误失败。
  更大的组合空间采用有界探测，仍受 Python 的资源上限约束。
- 失败时，Python 恢复调用前的标量值和容器元素/长度。

`for i in range(data.size())` 中的 `size()` 仅表示迭代边界，不算作尺寸约束，因而
不会令原本未约束的空动态数组自行增长。

动态数组和队列也可使用非负常量索引，例如 `self.data[0]`；它会原样生成对应的
SV 下标表达式。调用者必须以尺寸约束或已有元素保证该位置存在。可变索引仍只接受
`range(collection.size())` 的循环变量，保证 Python 与 SV 的 `foreach` 语义一致。

## unique 展开

`unique(self.data)` 与 `unique(self.tag, self.data)` 对 unpacked `Array`、
`DynArray` 和 `Queue` 合法。生成 SV 保持原生 `unique {data}`，不展开成两两
`foreach` 比较。Python 在尺寸确定之后，把当前积分叶子填入同一个 `unique` 节点。
`unique` 不约束 size；空容器或长度为 1 时恒真。关联数组、handle、unpacked struct
以及嵌套动态容器在声明期拒绝。

## 关联数组

`AssocArray` 也提供运行时 `.size()` 查询，与 SV 的容器 API 一致。但其键集合不是
随机变量：不能在随机约束中使用关联数组 `size()`，也不会创建、删除或随机化键。

对已有条目的 value 可声明 `rand=True` 并使用 Python 字典风格的键迭代：

```python
class Lookup(SvObject):
    table = AssocArray(Bit(8), Bit(16), rand=True)

    @constraint
    def legal(self):
        for key in self.table:
            self.table[key] <= 1024
```

它生成 `foreach (table[key])`。整数和枚举键可同时出现在数值表达式中；字符串键只能作为
`self.table[key]` 的索引。三类键的既有条目都可随机化 value。

## rand_mode 与分层随机

目标端确认动态数组和队列支持设置形式的 `rand_mode(0)` / `rand_mode(1)`；但它们是
non-singular 容器，不能使用无参查询形式 `rand_mode()`。现存的标量元素可以查询和设置，
例如 `data[0].rand_mode()`。

动态数组或队列可作为一个整体列入 `@rand_layer`，但不能列出具体下标。这里的“整体”只
是成员归属声明，不是对容器调用 `rand_mode()`，也不控制其尺寸：

```python
class LayeredPacket(SvObject):
    data = DynArray(Bit(8), rand=True, max_length=64)

    @rand_layer(10)
    def payload(self):
        self.data
```

进入 `layered_randomize()` 时，运行时会逐个保存当时存在的元素 mode，并关闭这些元素。
轮到 `payload` 优先级时，仅原来开启的元素开启；原来关闭的元素始终保持关闭。每轮结束后，
进入时已有的元素再次关闭。若随机过程改变了长度，新出现的元素没有进入时快照，保持开启。
退出时，仍存在的原始编号恢复到原 mode；新编号保持开启。容器自身的 mode 和长度不属于
这个分层算法。入口只读取各个奇异元素 `data[i].rand_mode()` 的返回值，不追踪元素为何
关闭；这个逐元素查询结果是唯一依据。随后分层入口开启容器整体 mode 以允许尺寸随机，并按
逐元素快照恢复。容器整体 mode 本身不恢复。

这同样适用于 `DynArray(Object(..., rand=True))` 与 `Queue(Object(..., rand=True))`。
此时元素 mode 控制的是数组槽位中的 handle：关闭的非空 handle 不会把 referent 的随机
变量、约束或 hooks 带入该批次；新增槽位为 active 的 null handle，因而不会分配对象。

同一实现同时用于 Python 与生成的 SV；生成代码只对 `data[i]` 发出合法的
`rand_mode` 调用。

队列的逐元素 mode 以**当前数值下标**为单位，而不随 value 在 `pop_front()` 后左移：若
`queue[1].rand_mode(0)` 后弹出 `queue[0]`，新的 `queue[0]` 仍是默认开启；以后再次填充
`queue[1]` 时，该下标的显式关闭 mode 仍生效。该规则已经与目标端对照验证。

## 验证

- Python：尺寸约束、动态数组和队列的 `foreach`、未约束尺寸保持、失败恢复、关联数组
  拒绝路径、unpacked `unique` 展开、父对象 `randomize()` 对子对象动态数组/队列 `unique`
  的图随机化，以及标量与 handle 元素在分层随机下的逐元素 mode 保存/恢复。
- target：同一组 `size()` + `foreach` 源约束在动态数组与队列上各随机 32 次，并检查尺寸
  与每个元素；`unique {array}`、`unique {tag, data}` 与 `unique {queue}` 检查互异；分层随机
  回归还检查动态数组元素关闭、层内随机和恢复后的 mode。

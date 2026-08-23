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
- 失败时，Python 恢复调用前的标量值和容器元素/长度。

`for i in range(data.size())` 中的 `size()` 仅表示迭代边界，不算作尺寸约束，因而
不会令原本未约束的空动态数组自行增长。

## 关联数组

`AssocArray` 也提供运行时 `.size()` 查询，与 SV 的容器 API 一致。但其键集合不是
随机变量：当前阶段禁止在随机约束中使用关联数组 `size()`，也不会创建或随机化键。
后续若支持关联数组随机化，必须单独定义键域、已有键以及 `foreach` 键变量的语义。

## rand_mode 与分层随机

SystemVerilog target 确认 `dyn_array.rand_mode()` 和 `queue.rand_mode()` 非法，因为这两者是
non-singular 容器；只能对现存元素调用 `rand_mode()`。为避免生成非法 SV，自动生成的
`layered_randomize()` 不会对动态数组或队列直接发出该调用。

因此本阶段不支持将动态数组或队列作为 `rand_layer` 的可控成员。普通 `randomize()`
的动态集合语义已完成；动态集合的分层 mode 规则需要在后续版本基于“尺寸”和“现存/新增
元素”的 SV 行为单独设计，并同时补充 Python/SystemVerilog target 对照。

## 验证

- Python：尺寸约束、动态数组和队列的 `foreach`、未约束尺寸保持、失败恢复、关联数组
  拒绝路径。
- SystemVerilog target：同一组 `size()` + `foreach` 源约束在动态数组与队列上各随机 32 次，并检查尺寸
  与每个元素。

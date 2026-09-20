# SvTypes 1.6：非空 handle 的联合约束随机

## 目标

1.6 将已构造、非空的 **`rand` `ObjectDescriptor` handle** 所指对象纳入一次
`randomize()` 的联合求解。它对齐 SystemVerilog 的关键边界：随机化改变叶子值，
不改变 handle 身份、null 状态、引用拓扑，也不分配对象。普通（非 `rand`）handle
只是状态引用，不会因父对象随机化而递归求解。

这不是对每个 child 依次调用 `child.randomize()`；那种做法无法满足跨对象约束。

## 图遍历

- 图根是调用 `randomize()` 的对象。
- 沿实例中已存在的直接 **`rand` handle** 缓存，或 `Array`、`DynArray`、`Queue`、
  `AssocArray` 中已有的 `rand Object` 元素遍历；未缓存的 `ObjectDescriptor` 和 null
  容器元素都视为 null。遍历不得调用
  `__get__()`，以免隐式构造对象。
- 每个对象按 Python identity 去重；共享引用和环不会产生重复叶子或重复约束。
- 每个对象第一次遇到时获得 canonical path。其他边只是 alias，指向相同的运行时变量。

## Python 接口与生成

```python
class Parent(SvObject):
    child = Object("Child", rand=True)
```

- `rand` 默认为 `False`，因此既有 `Object("Child")` 的序列化、访问和普通状态语义不变。
- 生成 SystemVerilog 时，上例为 `rand Child child;`；默认形式为 `Child child;`。
- `rand=True` 只表示沿非空、已分配的 handle 递归随机化，绝不选择、替换或分配 handle。

`DynArray(Object("Child", rand=True))` 与 `Queue(Object("Child", rand=True))` 是随机
handle 容器，能够在 `size()` 约束下调整长度。调整时保留原编号处的 handle；扩容新增的
槽位是 `None`（SystemVerilog 的 `null`），不会构造 `Child`。保留的非空对象继续与父对象
联合求解；新增 null 槽位无需随机化。若未约束尺寸，容器长度保持不变。

## 约束与路径

每个可达对象的已启用约束都加入同一个 `SolveRequest`。对象自己的 IR 以 canonical
path 为前缀；父对象约束中对 handle 成员的引用也解析到同一个 canonical path。

若约束引用某个 null handle 的成员，Python `randomize()` 返回 `False` 并报告
`null_handle`；不通过访问属性来创建该对象。生成 SV 保留原生 handle 约束表达式。

## modes、hooks 与回滚

- 每个对象自身叶子的 `rand_mode()` 与约束的 `constraint_mode()` 在图内独立生效。
- 对 `DynArray` / `Queue` 设定形式的 `rand_mode(0)` 会停用当前各 handle 变量：保留
  handle 的 referent 不参加该次联合求解，其自身约束与 hooks 也不会执行；再次启用后，
  它们恢复为普通的已分配 `rand` handle。动态容器新增的 handle 槽位仍按默认 active
  mode 初始化，但其值是 null，故没有 referent 可随机化。
- 根对象及每个可达、已启用的 `rand` child 都各执行一次 `pre_randomize()`；求解成功后，
  同一集合各执行一次 `post_randomize()`。这是 SystemVerilog 对随机对象成员的钩子范围。
  跨对象钩子的相对顺序不作为跨模拟器兼容承诺；Python 固定使用按首次可达顺序的 pre-order
  和其逆序的 post-order，以保持可测试的确定性。
- 任一联合约束 UNSAT 或状态含 X/Z 时，恢复所有参与对象的随机叶子；成功时统一写回。

## 不在范围内

- 不随机 handle/null/拓扑，不自动 new 对象；`Object(..., rand=True)` 只表示递归随机化，
  不表示随机选择或分配 handle。
- 容器遍历只跟随已有的非空 handle；动态数组和队列仅可由 `size()` 约束调整长度，且不会
  创建对象；关联数组不会创建或删除键。
- 不支持 `randc` handle。
- `Real`、`String`、`RemoteRef` 等既有非随机域仍不进入联合求解。

## 验证

Python 与目标端必须覆盖：父子交叉约束、child 自身约束、null handle、共享 child、
动态数组/队列 resize 后的 handle 保留与新增 null 槽位、容器 mode 关闭时 child 的约束与
hooks 排除、环、约束/mode 关闭、失败恢复和 hooks。测试比较约束满足性与对象身份，不比较
随机序列。

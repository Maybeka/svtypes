"""Cookbook 第 3 章：用 source-only 约束描述合法 stimulus。"""

from svtypes import Array, Bit, DynArray, RandomContext, SvObject, constraint, dist, soft, unique


class Traffic(SvObject):
    # 未显式设置 rand=False 的 integral 字段默认参与 constrained random。
    opcode = Bit[2]()
    address = Bit[12]()
    length = Bit[3]()
    tags = Array[Bit[3], 3]()
    payload = DynArray[Bit[8]](max_length=8)

    @constraint
    def legal(self):
        # @constraint 的函数体由 SvTypes 解析，不是在 randomize 时直接执行的 Python 代码。
        self.address % 16 == 0
        self.length <= 4
        # dist 是完整约束语句：@ 表示 DSL 的权重拼写，不是 Python 运算结果。
        self.opcode @ dist[0 @ 4, 1 @ 2, 2 @ 1]
        # unique 展开固定数组元素；它不负责决定动态数组长度。
        unique(self.tags)
        # 先约束长度，再用 SV 同形循环约束每个现存元素。
        self.payload.size() == self.length
        for index in range(self.payload.size()):
            self.payload[index] == index
        # 没有硬冲突时优先 opcode=0；硬约束可合法推翻 soft。
        soft(self.opcode == 0)


def main() -> None:
    traffic = Traffic()
    # 固定 seed 使教程输出、单测和问题复现可重复。
    with RandomContext(seed=20260930):
        for _ in range(3):
            assert traffic.randomize()
            assert traffic.address.value % 16 == 0
            assert traffic.length.value <= 4
            assert traffic.payload.value == list(range(traffic.length.value))
            assert len(set(traffic.tags.value)) == len(traffic.tags.value)
            print(
                f"opcode={traffic.opcode.value}, address=0x{traffic.address.value:03x}, "
                f"payload={traffic.payload.value}"
            )


if __name__ == "__main__":
    main()

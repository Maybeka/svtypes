"""Cookbook 第 4 章：先做高优先级选择，再随机依赖字段。"""

from svtypes import Bit, RandomContext, SvObject, constraint, rand_layer


class Request(SvObject):
    opcode = Bit[2]()
    address = Bit[12]()
    length = Bit[4]()
    # 普通 Python 属性可用于教程观测；它不是 SvTypes 序列化字段。
    observed_priorities: list[int] = []

    @constraint
    def opcode_legal(self):
        # 第一阶段只决定 opcode 的合法集合。
        self.opcode <= 2

    @constraint
    def transfer_legal(self):
        # 第二阶段读取已固定的 opcode，并决定 transfer 的其余字段。
        self.address % 64 == 0
        if self.opcode == 0:
            self.length == 1
        else:
            self.length >= 2

    @rand_layer(100)
    def choose_opcode(self):
        # priority 数值越大越先执行；同 priority 的 layer 会合并为一次 randomize。
        self.opcode
        self.opcode_legal

    @rand_layer(10)
    def choose_transfer(self):
        self.address
        self.length
        self.transfer_legal

    def pre_randomize(self) -> None:
        # layered_randomize 内部仍调用正常 randomize，因此 hook 每个批次都会执行。
        if self.svtypes_layered_randomize_active():
            self.observed_priorities.append(self.svtypes_layered_randomize_priority())


def main() -> None:
    request = Request()
    request.observed_priorities = []  # class 属性改为本对象自己的观测列表。
    with RandomContext(seed=41):
        assert request.layered_randomize()

    # 未分层成员/约束仍会进入隐式 builtin priority 0。
    assert request.observed_priorities == [100, 10, 0]
    assert request.address.value % 64 == 0
    if request.opcode.value == 0:
        assert request.length.value == 1
    else:
        assert request.length.value >= 2
    print(f"优先级批次：{request.observed_priorities}；opcode={request.opcode.value}，length={request.length.value}")


if __name__ == "__main__":
    main()

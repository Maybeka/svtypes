"""Cookbook 第 5 章：采样 coverpoint、transition 和 cross。"""

from svtypes import Bit, CovPoint, Cross, CrossOption, SvObject, bins, covergroup, transition_bins


class Packet(SvObject):
    # cov=False 防止自动 coverage 干扰本章只声明的 bins。
    opcode = Bit[2](cov=False)
    mode = Bit[1](cov=False)

    @covergroup
    def cg(self):
        # 具名 bins 是报告、数据库和 cross 引用的稳定接口。
        class opcode_cp(CovPoint, source=self.opcode):
            read = bins[0]
            write = bins[1]

        class mode_cp(CovPoint, source=self.mode):
            idle = bins[0]
            active = bins[1]

        class opcode_transition(CovPoint, source=self.opcode):
            # transition 依赖前一次 sample；首次 sample 不会命中这个 bin。
            read_to_write = transition_bins[0, 1]

        class opcode_mode(Cross, members=(opcode_cp, mode_cp)):
            class option(CrossOption):
                # 有意关闭未显式声明的自动 cross bin，便于聚焦目标组合。
                cross_retain_auto_bins = 0

            write_active = bins[opcode_cp.write, mode_cp.active]

    def __init__(self) -> None:
        super().__init__()
        # covergroup 是模板；每个对象必须显式实例化才有独立 hit count。
        self.cg.instantiate()


def main() -> None:
    packet = Packet()
    # 顺序特意包含 0 -> 1，从而命中 transition 和 write_active cross bin。
    for opcode, mode in ((0, 0), (1, 1), (0, 1)):
        packet.opcode.value, packet.mode.value = opcode, mode
        packet.cg.sample()

    snapshot = packet.cg.instance.snapshot()
    assert snapshot["opcode_cp"]["hits"] == {"read": 2, "write": 1}
    assert snapshot["opcode_transition"]["hits"] == {"read_to_write": 1}
    assert snapshot["opcode_mode"]["hits"] == {"write_active": 1}
    print(f"sample 次数={packet.cg.sample_count}，实例覆盖率={packet.cg.get_inst_coverage():.1f}%")


if __name__ == "__main__":
    main()

"""A representative packet declaration for the coverage-tools walkthrough."""

from svtypes import (
    Bit,
    CoverInput,
    CovPoint,
    Cross,
    CrossOption,
    Enum,
    Object,
    ObjectRegistry,
    Parameter,
    SvObject,
    bins,
    coverage_init,
    covergroup,
    default_bins,
    ignore_bins,
    illegal_bins,
    transition_bins,
)


DESIGN_REGISTRY = ObjectRegistry()


class PacketKind(Enum, width=8, signed=False):
    """Packet operation category used by the enum coverage example."""

    request = 0
    response = 1
    error = 2


class Packet(SvObject):
    opcode = Bit(4)
    address = Bit(16)
    mode = Bit(2)
    kind = PacketKind()
    length = Bit(4)
    valid = Bit(1)
    reserved_first = Parameter()(4)
    reserved_last = Parameter()(7)

    @covergroup
    def cg(self):
        class opcode_cp(CovPoint, source=self.opcode, iff=lambda: self.valid == 1):
            zero = bins[0]
            one = bins[1]
            data = bins[2:7]
            control = bins[8:11]
            reserved = illegal_bins[15]
            other = default_bins

        class addr_cp(CovPoint, source=self.address, iff=lambda: self.valid == 1):
            header = bins[0:255]
            page = bins[256:4095]
            window = bins[4096:32767]
            high = bins[32768:65534]
            reserved = illegal_bins[65535]

        class mode_cp(CovPoint, source=self.mode, iff=lambda: self.valid == 1):
            idle = bins[0]
            read = bins[1]
            write = bins[2]
            flush = bins[3]

        class kind_cp(CovPoint, source=self.kind, iff=lambda: self.valid == 1):
            request = bins[PacketKind.request]
            response = bins[PacketKind.response]
            error = illegal_bins[PacketKind.error]

        class length_cp(CovPoint, source=self.length, iff=lambda: self.valid == 1):
            length_band = bins[0:14].split(4)
            invalid = illegal_bins[15]

        class flag_cp(CovPoint, source=self.valid):
            off = bins[0]

        class sparse_opcode_cp(CovPoint, source=self.opcode, iff=lambda: self.valid == 1):
            sparse = bins[1, 4:5, 9]

        class mode_sequence(CovPoint, source=self.mode, iff=lambda: self.valid == 1):
            begin_read = transition_bins[0, 1]
            read_then_write = transition_bins[1, 2]

        class opcode_mode(Cross, members=(opcode_cp, mode_cp), iff=lambda: self.valid == 1):
            class option(CrossOption):
                cross_retain_auto_bins = 1

            control_read = bins[opcode_cp.control, mode_cp.read]
            ignored_flush = ignore_bins[opcode_cp.one, mode_cp.flush]

        class opcode_compact(Cross, members=(opcode_cp, mode_cp), iff=lambda: self.valid == 1):
            class opcode_cp(CovPoint, source=self.opcode, iff=lambda: self.valid == 1):
                compact = bins[0:3]
                control = bins[8:11]
                reserved = illegal_bins[15]

            selected = bins[opcode_cp.control, mode_cp.read]

    @covergroup
    def explicit_window(self, first: CoverInput[int], last: CoverInput[int]):
        class opcode_cp(CovPoint, source=self.opcode):
            window = bins[first:last].split(max_bins=None)

    @covergroup
    def parameter_window(self):
        class opcode_cp(CovPoint, source=self.opcode):
            reserved = bins[self.reserved_first:self.reserved_last].split(3)

    @coverage_init
    def configure_coverage(self, first: int, last: int):
        self.cg.instantiate()
        self.explicit_window.instantiate(first, last)
        self.parameter_window.instantiate()

    def __init__(self) -> None:
        super().__init__()
        self.configure_coverage(0, 3)


class Top(SvObject):
    """Static top-level composition used by the GUI hierarchy example."""

    packet = Object("Packet", registry=DESIGN_REGISTRY)
    enabled = Bit(1)


class Monitor(SvObject):
    """Standalone sibling type: discovered beside Top/Packet from the same module."""

    observed = Bit(2)

    @covergroup
    def activity(self):
        class observed_cp(CovPoint, source=self.observed):
            idle = bins[0]
            busy = bins[1:3]

    @coverage_init
    def configure_coverage(self):
        self.activity.instantiate()

    def __init__(self) -> None:
        super().__init__()
        self.configure_coverage()


DESIGN_REGISTRY.register(Packet)
DESIGN_REGISTRY.register(Top)
DESIGN_REGISTRY.register(Monitor)

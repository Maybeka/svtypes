"""Index acceleration must agree with the original linear candidate search."""

from svtypes.coverage.evaluator import CoverageRuntime
from svtypes.coverage.ir import CoverageBinIR, CoverageCrossIR, CoverageIR, CoveragePointIR, CrossMemberViewIR


class LinearRuntime(CoverageRuntime):
    def _point_candidates(self, point, value):
        return list(point.bins)

    def _cross_candidates(self, cross, classified):
        return list(cross.bins)


def constant(value):
    return {"kind": "constant", "value": value}


def reference(*items):
    return {"kind": "cross_bin_refs", "items": [{"point": point, "bin": bin_} for point, bin_ in items]}


def interval(low, high):
    return {"kind": "range", "lower": constant(low), "upper": constant(high)}


def test_range_indexes_match_linear_overlaps_unions_and_noninteger_fallback():
    from svtypes.logic import LogicValue

    point = CoveragePointIR("p", {"kind": "name", "name": "p"}, bins=(
        CoverageBinIR("wide", "normal", interval(-20, 20)),
        CoverageBinIR("left", "normal", interval(-10, 0)),
        CoverageBinIR("right", "normal", interval(0, 10)),
        CoverageBinIR("union", "normal", {"kind": "values", "items": [
            interval(-2, 3), interval(1, 5), constant(2), interval(9, 11)]}),
        CoverageBinIR("dynamic", "normal", {"kind": "range", "lower": constant(0),
                                              "upper": {"kind": "name", "name": "limit"}}),
        CoverageBinIR("ignore", "ignore", interval(12, 14)),
        CoverageBinIR("illegal", "illegal", interval(15, 17)),
        CoverageBinIR("default", "default"),
    ))
    ir = CoverageIR("Packet", "cg", points=(point,))
    indexed, linear = CoverageRuntime(ir), LinearRuntime(ir)
    values = [*range(-25, 26), 0.5, True, LogicValue(8, 2, 1)]
    for limit in (1, 9):
        for value in values:
            context = {"p": value, "limit": limit}
            indexed.sample(context, case_id="range-test")
            linear.sample(context, case_id="range-test")
            assert indexed.snapshot() == linear.snapshot()
            assert indexed.coverage() == linear.coverage()


def test_large_range_point_index_avoids_full_scan_and_domain_enumeration(monkeypatch):
    from svtypes.coverage import evaluator

    point = CoveragePointIR("p", {"kind": "name", "name": "p"}, bins=(
        *(CoverageBinIR(f"part{i}", "normal", interval(i * 100, i * 100 + 20)) for i in range(4096)),
        CoverageBinIR("common", "normal", interval(-10, 2**256)),
    ))
    ir = CoverageIR("Packet", "cg", points=(point,))
    indexed, linear = CoverageRuntime(ir), LinearRuntime(ir)
    calls = 0
    original = evaluator._matches_selector

    def counted(*args):
        nonlocal calls
        calls += 1
        return original(*args)

    monkeypatch.setattr(evaluator, "_matches_selector", counted)
    for value in (-10, 0, 120, 20050, 409500, 2**256, 2**256 + 1):
        before = calls
        indexed.sample({"p": value})
        assert calls - before <= 2
        linear.sample({"p": value})
        assert indexed.snapshot() == linear.snapshot()
    singles, _, fallback = indexed.point_indexes[id(point)]
    assert singles == {} and fallback == []


def test_indexed_sampling_matches_linear_overlaps_suppression_and_private_views():
    point = CoveragePointIR("a", {"kind": "name", "name": "a"}, bins=(
        CoverageBinIR("single", "normal", constant(1)),
        CoverageBinIR("overlap", "normal", {"kind": "values", "items": [constant(1), constant(1), constant(2)]}),
        CoverageBinIR("range", "normal", {"kind": "range", "lower": constant(0), "upper": constant(3)}),
        CoverageBinIR("dynamic", "normal", {"kind": "name", "name": "target"}),
        CoverageBinIR("ignore", "ignore", constant(3)),
        CoverageBinIR("illegal", "illegal", constant(4)),
        CoverageBinIR("default", "default"),
        CoverageBinIR("transition", "transition", {"kind": "values", "items": [constant(0), constant(1)]}),
    ))
    second = CoveragePointIR("b", {"kind": "name", "name": "b"},
                             bins=(CoverageBinIR("one", "normal", constant(1)),),
                             iff={"kind": "name", "name": "valid"})
    cross = CoverageCrossIR("ab", ("a", "b"), bins=(
        CoverageBinIR("exact", "normal", reference(("a", "single"), ("b", "one"))),
        CoverageBinIR("also_exact", "normal", reference(("b", "one"), ("a", "single"))),
        CoverageBinIR("partial", "normal", reference(("a", "overlap"))),
        CoverageBinIR("queue", "normal", {"kind": "cross_queue_values", "items": [[2, 1]]}),
        CoverageBinIR("ignore", "ignore", reference(("a", "dynamic"), ("b", "one"))),
    ))
    local = CoveragePointIR("a", point.expression, bins=(
        CoverageBinIR("private", "normal", constant(2)),
        CoverageBinIR("illegal", "illegal", constant(4)),
    ))
    private_cross = CoverageCrossIR("local", ("a", "b"), bins=(
        CoverageBinIR("private", "normal", reference(("a", "private"), ("b", "one"))),
    ), member_views=(CrossMemberViewIR("a", local),))
    ir = CoverageIR("Packet", "cg", points=(point, second), crosses=(cross, private_cross))
    indexed, linear = CoverageRuntime(ir), LinearRuntime(ir)
    for valid in (True, False):
        for target in (0, 1, 2, 4):
            for a in (0, 1, 2, 3, 4, 5, 0, 1):
                context = {"a": a, "b": 1, "target": target, "valid": valid}
                indexed.sample(context, case_id=f"case-{a}")
                linear.sample(context, case_id=f"case-{a}")
                assert indexed.snapshot() == linear.snapshot()
                assert indexed.histories == linear.histories
                assert indexed.cross_local_illegal_snapshot() == linear.cross_local_illegal_snapshot()
                assert indexed.coverage() == linear.coverage()


def test_large_cross_sampling_matches_linear_without_full_scan(monkeypatch):
    from benchmarks.coverage import LargeCrossPacket
    from svtypes.coverage import evaluator

    packet = LargeCrossPacket()
    runtime = packet.cg.instance.runtime
    linear = LinearRuntime(runtime.ir)
    calls = 0
    original = evaluator._matches_cross_selector

    def counted(*args):
        nonlocal calls
        calls += 1
        return original(*args)

    monkeypatch.setattr(evaluator, "_matches_cross_selector", counted)
    for value in (0, 1, 127, 255):
        packet.opcode.value = value
        packet.mode.value = 255 - value
        before = calls
        runtime.sample({"item": packet, "self": packet})
        assert calls - before < 10
        linear.sample({"item": packet, "self": packet})
        assert runtime.snapshot() == linear.snapshot()

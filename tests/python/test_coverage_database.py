from copy import deepcopy

import pytest

from svtypes import Bit, CoverageDatabase, CoverageError, CoverGroupOption, CoverGroupTypeOption, CoverInput, SvObject, covergroup
from svtypes.coverage import CovPoint, bins


class DatabasePacket(SvObject):
    code = Bit(2)

    @covergroup
    def cg(self):
        class code_cp(CovPoint, source=self.code):
            low = bins[0]
            high = bins[1]

    def __init__(self):
        super().__init__()
        self.cg.instantiate()


def _sample(value: int) -> DatabasePacket:
    packet = DatabasePacket()
    packet.code.value = value
    packet.cg.sample()
    return packet


def test_database_merge_accumulates_matching_logical_instance() -> None:
    left = CoverageDatabase()
    right = CoverageDatabase()
    left.record(_sample(0).cg.instance, logical_instance_key="dut.pkt")
    right.record(_sample(1).cg.instance, logical_instance_key="dut.pkt")

    left.merge(right)

    record = left.snapshot_document()["records"][0]
    assert record["logical_instance_key"] == "dut.pkt"
    assert record["points"]["code_cp"]["hits"] == {"high": 1, "low": 1}


def test_database_rejects_different_declaration_for_same_key() -> None:
    class ImportedSnapshot:
        def __init__(self, document):
            self._document = document

        def snapshot_document(self):
            return self._document

    database = CoverageDatabase()
    instance = _sample(0).cg.instance
    database.record(instance, logical_instance_key="dut.pkt")
    incompatible = deepcopy(instance.snapshot_document())
    incompatible["declaration_semantic_digest"] = "incompatible"
    with pytest.raises(CoverageError, match="declaration mismatch"):
        database.record(ImportedSnapshot(incompatible), logical_instance_key="dut.pkt")


def test_database_record_refresh_does_not_double_count() -> None:
    database = CoverageDatabase()
    packet = _sample(0)
    database.record(packet.cg.instance, logical_instance_key="dut.pkt")
    database.record(packet.cg.instance, logical_instance_key="dut.pkt")
    assert database.snapshot_document()["records"][0]["points"]["code_cp"]["hits"] == {"low": 1}


def test_database_snapshot_is_not_a_mutable_view_of_internal_records() -> None:
    database = CoverageDatabase()
    database.record(_sample(0).cg.instance)
    snapshot = database.snapshot_document()
    snapshot["records"][0]["points"]["code_cp"]["hits"]["low"] = 99
    assert database.snapshot_document()["records"][0]["points"]["code_cp"]["hits"] == {"low": 1}


def test_per_instance_database_record_requires_bound_logical_key_and_checks_layout() -> None:
    class Packet(SvObject):
        code = Bit(2)

        @covergroup
        def cg(self, limit: CoverInput[int]):
            class option(CoverGroupOption):
                per_instance = 1

            class code_cp(CovPoint, source=self.code):
                low = bins[0]

        def __init__(self, limit: int):
            super().__init__()
            self.cg.instantiate(limit)

    database = CoverageDatabase()
    one = Packet(1)
    with pytest.raises(CoverageError, match="requires a logical instance key"):
        database.record(one.cg.instance)
    one.cg.bind_logical_instance("dut.packet")
    database.record(one.cg.instance)
    two = Packet(2)
    two.cg.bind_logical_instance("dut.packet")
    other_database = CoverageDatabase()
    other_database.record(two.cg.instance)
    with pytest.raises(CoverageError, match="instance layout mismatch"):
        database.merge(other_database)

    same_layout = Packet(1)
    same_layout.cg.bind_logical_instance("dut.packet")
    with pytest.raises(CoverageError, match="already registered"):
        database.record(same_layout.cg.instance)


def test_database_type_summary_supports_independent_and_merged_instance_scoring() -> None:
    class Independent(SvObject):
        code = Bit(1)

        @covergroup
        def cg(self):
            class type_option(CoverGroupTypeOption):
                merge_instances = 0

            class code_cp(CovPoint, source=self.code):
                zero = bins[0]
                one = bins[1]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    database = CoverageDatabase()
    for value, key in ((0, "a"), (1, "b")):
        packet = Independent()
        packet.code.value = value
        packet.cg.sample()
        database.record(packet.cg.instance, logical_instance_key=key)
    assert database.type_summary(Independent.cg.freeze().covergroup_type_id) == {"coverage": 50.0, "merge_instances": 0}

    class Merged(SvObject):
        code = Bit(1)

        @covergroup
        def cg(self):
            class type_option(CoverGroupTypeOption):
                merge_instances = 1

            class code_cp(CovPoint, source=self.code):
                zero = bins[0]
                one = bins[1]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    database = CoverageDatabase()
    for value, key in ((0, "a"), (1, "b")):
        packet = Merged()
        packet.code.value = value
        packet.cg.sample()
        database.record(packet.cg.instance, logical_instance_key=key)
    assert database.type_summary(Merged.cg.freeze().covergroup_type_id)["coverage"] == 100.0


def test_database_merge_unions_case_sources_without_changing_counts() -> None:
    left = CoverageDatabase()
    right = CoverageDatabase()
    first = DatabasePacket()
    first.code.value = 0
    first.cg.sample(case_id="first")
    left.record(first.cg.instance, logical_instance_key="dut.packet")
    second = DatabasePacket()
    second.code.value = 0
    second.cg.sample(case_id="second")
    right.record(second.cg.instance, logical_instance_key="dut.packet")
    left.merge(right)
    point = left.snapshot_document()["records"][0]["points"]["code_cp"]
    assert point["hits"] == {"low": 2}
    assert point["source_ids"] == {"low": ["first", "second"]}

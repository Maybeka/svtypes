from copy import deepcopy

import pytest

from svtypes import Bit, CoverageDatabase, CoverageError, SvObject, covergroup
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

from copy import deepcopy
from pathlib import Path

import pytest

from svtypes import Bit, CoverageDatabase, CoverageError, CoverGroupOption, CoverGroupTypeOption, CoverInput, Cross, SvObject, covergroup
from svtypes.coverage import CovPoint, bins, default_bins, illegal_bins, set_coverage_case_name
from svtypes.coverage import export_ucis, export_ucis_file, import_ucis, import_ucis_file
from svtypes.coverage.context import _reset_coverage_case_name_for_testing


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

    definition_mismatch = deepcopy(instance.snapshot_document())
    definition_mismatch["definition"]["sample_type"] = "other"
    with pytest.raises(CoverageError, match="definition mismatch"):
        database.record(ImportedSnapshot(definition_mismatch), logical_instance_key="dut.pkt")


def test_database_record_refresh_does_not_double_count() -> None:
    database = CoverageDatabase()
    packet = _sample(0)
    database.record(packet.cg.instance, logical_instance_key="dut.pkt")
    database.record(packet.cg.instance, logical_instance_key="dut.pkt")
    assert database.snapshot_document()["records"][0]["points"]["code_cp"]["hits"] == {"low": 1}


def test_database_baseline_seeds_new_instance_and_continues_sampling() -> None:
    database = CoverageDatabase()
    prior = _sample(0)
    database.record(prior.cg.instance, logical_instance_key="dut.pkt")

    resumed = DatabasePacket()
    resumed.cg.bind_logical_instance("dut.pkt")
    database.apply_baseline(resumed.cg.instance)
    resumed.code.value = 1
    resumed.cg.sample()

    assert resumed.cg.instance.snapshot()["code_cp"]["hits"] == {"high": 1, "low": 1}


def test_database_baseline_rejects_incompatible_instance() -> None:
    database = CoverageDatabase()
    database.record(_sample(0).cg.instance, logical_instance_key="dut.pkt")
    other = DatabasePacket()
    other.cg.bind_logical_instance("dut.pkt")
    document = other.cg.instance.snapshot_document()
    document["declaration_semantic_digest"] = "other"
    class Incompatible:
        logical_instance_key = "dut.pkt"
        def snapshot_document(self):
            return document
    with pytest.raises(CoverageError, match="declaration mismatch"):
        database.apply_baseline(Incompatible())


def test_database_binary_round_trip_merge_and_baseline(tmp_path) -> None:
    first = CoverageDatabase()
    first.record(_sample(0).cg.instance, logical_instance_key="dut.pkt")
    path = tmp_path / "first.svtcov"
    first.write(str(path))
    restored = CoverageDatabase.read(str(path))
    assert restored.snapshot_document() == first.snapshot_document()

    resumed = DatabasePacket()
    resumed.cg.bind_logical_instance("dut.pkt")
    restored.apply_baseline(resumed.cg.instance)
    resumed.code.value = 1
    resumed.cg.sample()
    next_run = CoverageDatabase()
    next_run.record(resumed.cg.instance)
    assert next_run.snapshot_document()["records"][0]["points"]["code_cp"]["hits"] == {"high": 1, "low": 1}


def test_database_binary_rejects_corruption_and_unknown_version() -> None:
    database = CoverageDatabase()
    database.record(_sample(0).cg.instance)
    data = database.to_bytes()
    with pytest.raises(CoverageError, match="checksum"):
        CoverageDatabase.from_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    incompatible = bytearray(data)
    incompatible[8:10] = (2).to_bytes(2, "little")
    with pytest.raises(CoverageError, match="format version"):
        CoverageDatabase.from_bytes(bytes(incompatible))


def test_ucis_export_contains_functional_coverage_and_loss_report() -> None:
    database = CoverageDatabase()
    database.record(_sample(0).cg.instance, logical_instance_key="dut.pkt")
    xml, report = export_ucis(database)
    assert '<UCIS ' in xml
    assert '<covergroupCoverage' in xml
    assert 'coverpointBin' in xml
    assert 'coverageCount="1"' in xml
    assert report["format"] == "svtypes.ucis.loss-report"
    assert report["losses"] == []
    imported, import_report = import_ucis(xml, defaults={"svtypes_default": "on"})
    assert imported[0]["points"]["code_cp"]["low"] == 1
    assert imported[0]["options"]["svtypes_default"] == "on"
    assert import_report["losses"]


def test_ucis_export_has_required_covergroup_structure_and_file_import_defaults(tmp_path) -> None:
    database = CoverageDatabase()
    database.record(_sample(0).cg.instance, logical_instance_key="dut.pkt")
    path = tmp_path / "coverage.ucis.xml"
    assert export_ucis_file(path, database)["losses"] == []
    root = __import__("xml.etree.ElementTree", fromlist=["ElementTree"]).fromstring(path.read_text())
    cg = root.find(".//cgInstance")
    assert cg is not None
    assert cg.find("cgId/cginstSourceId") is not None
    assert cg.find("cgId/cgSourceId") is not None
    assert all(bin_.find("range/contents") is not None for bin_ in cg.findall(".//coverpointBin"))
    config = tmp_path / "ucis-defaults.json"
    config.write_text('{"cross_retain_auto_bins": 0}', encoding="utf-8")
    imported, report = import_ucis_file(path, config_path=config)
    assert imported[0]["options"]["cross_retain_auto_bins"] == 0
    assert report["losses"]


def test_ucis_import_requires_explicit_frozen_instance_binding() -> None:
    source = CoverageDatabase()
    source.record(_sample(0).cg.instance, logical_instance_key="dut.pkt")
    xml, report = export_ucis(source)
    assert report["losses"] == []

    target = DatabasePacket()
    restored = CoverageDatabase()
    assert restored.import_ucis(xml, bindings={"dut.pkt": target.cg.instance})["losses"] == []
    record = restored.snapshot_document()["records"][0]
    assert record["logical_instance_key"] == "dut.pkt"
    assert record["points"]["code_cp"]["hits"] == {"high": 0, "low": 1}

    with pytest.raises(CoverageError, match="no declared instance binding"):
        CoverageDatabase().import_ucis(xml, bindings={})


def test_ucis_export_omits_unrepresentable_default_bin_without_invalid_xml() -> None:
    class Packet(SvObject):
        code = Bit(1)

        @covergroup
        def cg(self):
            class code_cp(CovPoint, source=self.code):
                fallback = default_bins

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    packet = Packet()
    packet.code.value = 0
    packet.cg.sample()
    database = CoverageDatabase()
    database.record(packet.cg.instance)
    xml, report = export_ucis(database)
    root = __import__("xml.etree.ElementTree", fromlist=["ElementTree"]).fromstring(xml)
    assert root.findall(".//cgInstance") == []
    assert report["losses"] == [{
        "record": "1", "item": Packet.cg.freeze().covergroup_type_id,
        "reason": "covergroup omitted: UCIS requires an exported coverpoint",
    }]


def test_ucis_import_accepts_exported_subset_when_default_bins_are_omitted() -> None:
    class Packet(SvObject):
        code = Bit(1)

        @covergroup
        def cg(self):
            class code_cp(CovPoint, source=self.code):
                low = bins[0]
                fallback = default_bins

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    packet = Packet()
    packet.code.value = 0
    packet.cg.sample()
    source = CoverageDatabase()
    source.record(packet.cg.instance, logical_instance_key="dut.pkt")
    xml, report = export_ucis(source)
    assert any("cannot be losslessly projected" in item["reason"] for item in report["losses"])

    restored = CoverageDatabase()
    restored.import_ucis(xml, bindings={"dut.pkt": Packet().cg.instance})
    record = restored.snapshot_document()["records"][0]
    assert record["points"]["code_cp"]["hits"]["low"] == 1
    assert record["points"]["code_cp"]["hits"]["fallback"] == 0


_UCIS_DATA = Path(__file__).resolve().parent / "data" / "ucis"


def _external_ucis(name: str, type_id: str) -> str:
    return (_UCIS_DATA / name).read_text(encoding="utf-8").replace("__COVERGROUP_TYPE_ID__", type_id)


def test_ucis_import_accepts_namespaced_external_sample_and_rejects_incompatible_documents() -> None:
    type_id = DatabasePacket.cg.freeze().covergroup_type_id
    xml = _external_ucis("external_covergroup.xml", type_id)
    records, report = import_ucis(xml)
    assert records[0]["logical_instance_key"] == "dut.pkt"
    assert records[0]["points"]["code_cp"] == {"low": 4, "high": 0}
    assert report["losses"] == []

    restored = CoverageDatabase()
    restored.import_ucis(xml, bindings={"dut.pkt": DatabasePacket().cg.instance})
    assert restored.snapshot_document()["records"][0]["points"]["code_cp"]["hits"] == {"high": 0, "low": 4}

    with pytest.raises(CoverageError, match="expected UCIS 1.0"):
        import_ucis((_UCIS_DATA / "incompatible_version.xml").read_text(encoding="utf-8"))
    with pytest.raises(CoverageError, match="malformed"):
        import_ucis("<not-xml")
    with pytest.raises(CoverageError, match="expected UCIS 1.0"):
        import_ucis("<coverage/>")
    unknown = _external_ucis("unknown_bin.xml", type_id)
    with pytest.raises(CoverageError, match="unknown bins"):
        CoverageDatabase().import_ucis(unknown, bindings={"dut.pkt": DatabasePacket().cg.instance})
    with pytest.raises(CoverageError, match="covergroup type mismatch"):
        CoverageDatabase().import_ucis(
            xml.replace(type_id, "other.Packet::cg"),
            bindings={"dut.pkt": DatabasePacket().cg.instance},
        )


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
                low = bins[0:limit]

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


def test_non_per_instance_records_without_logical_keys_remain_separate_type_contributors() -> None:
    database = CoverageDatabase()
    first = _sample(0)
    second = _sample(1)
    database.record(first.cg.instance)
    database.record(second.cg.instance)
    records = database.snapshot_document()["records"]
    assert [record["logical_instance_key"] for record in records] == [None, None]
    assert database.type_summary(DatabasePacket.cg.freeze().covergroup_type_id)["coverage"] == 50.0


def test_database_merge_unions_case_sources_without_changing_counts() -> None:
    databases = []
    for case_name in ("first", "second", "third", "overflow"):
        database = CoverageDatabase()
        packet = DatabasePacket()
        packet.code.value = 0
        _reset_coverage_case_name_for_testing()
        set_coverage_case_name(case_name)
        packet.cg.sample()
        database.record(packet.cg.instance, logical_instance_key="dut.packet")
        databases.append(database)
    left = databases[0]
    for right in databases[1:]:
        left.merge(right)
    point = left.snapshot_document()["records"][0]["points"]["code_cp"]
    assert point["hits"] == {"low": 4}
    assert point["source_ids"] == {"low": ["first", "second", "third"]}
    _reset_coverage_case_name_for_testing()


def test_per_instance_illegal_hits_remain_separate_by_logical_key() -> None:
    class Packet(SvObject):
        code = Bit(2)

        @covergroup
        def cg(self):
            class option(CoverGroupOption):
                per_instance = 1

            class code_cp(CovPoint, source=self.code):
                reserved = illegal_bins[3]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    database = CoverageDatabase()
    for key, samples in (("dut.a", 1), ("dut.b", 2)):
        packet = Packet()
        packet.cg.bind_logical_instance(key)
        packet.code.value = 3
        for _ in range(samples):
            packet.cg.sample()
        database.record(packet.cg.instance)
    records = {record["logical_instance_key"]: record for record in database.snapshot_document()["records"]}
    assert records["dut.a"]["points"]["code_cp"]["illegal_hits"] == {"reserved": 1}
    assert records["dut.b"]["points"]["code_cp"]["illegal_hits"] == {"reserved": 2}


def test_database_aggregate_coverage_honors_covergroup_weight() -> None:
    class WeightedLow(SvObject):
        code = Bit(1)

        @covergroup
        def cg(self):
            class option(CoverGroupOption):
                weight = 3

            class code_cp(CovPoint, source=self.code):
                zero = bins[0]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    class WeightedHigh(SvObject):
        code = Bit(1)

        @covergroup
        def cg(self):
            class option(CoverGroupOption):
                weight = 1

            class code_cp(CovPoint, source=self.code):
                zero = bins[0]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    database = CoverageDatabase()
    database.record(WeightedLow().cg.instance)
    high = WeightedHigh()
    high.cg.sample()
    database.record(high.cg.instance)
    assert database.coverage() == 25.0


def test_database_merges_cross_counters_with_merge_instances() -> None:
    class Packet(SvObject):
        code = Bit(1)
        kind = Bit(1)

        @covergroup
        def cg(self):
            class type_option(CoverGroupTypeOption):
                merge_instances = 1

            class code_cp(CovPoint, source=self.code):
                zero = bins[0]
                one = bins[1]

            class kind_cp(CovPoint, source=self.kind):
                zero = bins[0]
                one = bins[1]

            class combined(Cross, members=(code_cp, kind_cp)):
                low = bins[code_cp.zero, kind_cp.zero]
                high = bins[code_cp.one, kind_cp.one]

        def __init__(self, code: int, kind: int):
            super().__init__()
            self.cg.instantiate()
            self.code.value, self.kind.value = code, kind
            self.cg.sample()

    left, right = CoverageDatabase(), CoverageDatabase()
    left.record(Packet(0, 0).cg.instance)
    right.record(Packet(1, 1).cg.instance)
    left.merge(right)
    summary = left.type_summary(Packet.cg.freeze().covergroup_type_id)
    assert summary["coverage"] == 100.0
    assert summary["points"]["combined"]["hits"] == {"high": 1, "low": 1}

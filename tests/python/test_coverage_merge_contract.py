"""Cross-run identity, transactional merge and explicit master projection."""

from copy import deepcopy
import struct

import pytest

from svtypes import Bit, CoverageDatabase, CoverageError, CoverInput, CovPoint, Cross, SvObject, bins, covergroup, illegal_bins, transition_bins
from svtypes.coverage.canonical import semantic_digest
from svtypes.coverage.persistence import _chunk, MAGIC, encode_database


class MergePacket(SvObject):
    code = Bit[2](cov=False)
    mode = Bit[1](cov=False)

    @covergroup
    def cg(self):
        class code_cp(CovPoint, source=self.code):
            zero = bins[0]
            one = bins[1]

        class mode_cp(CovPoint, source=self.mode):
            zero = bins[0]
            one = bins[1]

        class combined(Cross, members=(code_cp, mode_cp)):
            both = bins[code_cp.one, mode_cp.one]

    def __init__(self, value=0):
        super().__init__()
        self.cg.instantiate()
        self.code.value = self.mode.value = value
        self.cg.sample()


class Snapshot:
    def __init__(self, document):
        self.document = document

    def snapshot_document(self):
        return self.document


def document(value=0):
    return MergePacket(value).cg.instance.snapshot_document()


def changed_document():
    doc = document(1)
    for definition in (doc["definition"], doc["instance_definition"]):
        point = next(item for item in definition["points"] if item["name"] == "code_cp")
        point["bins"][0]["selector"]["value"] = 2
    doc["declaration_semantic_digest"] = semantic_digest(doc["definition"])
    doc["instance_layout_digest"] = semantic_digest({"instance_layout": doc["instance_definition"]})
    return doc


def database(doc, key="packet"):
    result = CoverageDatabase()
    result.record(Snapshot(doc), logical_instance_key=key)
    return result


def test_anonymous_contributors_survive_cross_run_disk_merge():
    sources = [database(document(value), None) for value in (0, 1)]
    live, restored = CoverageDatabase(), CoverageDatabase()
    for source in sources:
        live.merge(source)
        restored.merge(CoverageDatabase.from_bytes(source.to_bytes()))
    assert len(restored.snapshot_document()["records"]) == 2
    assert restored.snapshot_document() == live.snapshot_document()
    type_id = MergePacket.cg.freeze().covergroup_type_id
    assert restored.type_summary(type_id) == live.type_summary(type_id)
    assert restored.type_summary(type_id)["coverage"] < 100
    # An explicit string resembling an old implementation key cannot collide.
    restored.merge(database(document(), "__imported__0"))
    assert len(restored.snapshot_document()["records"]) == 3


def test_strict_merge_checks_type_across_distinct_instance_keys_and_is_atomic():
    left = database(document(), "old")
    with pytest.raises(CoverageError, match="declaration mismatch"):
        left.record(Snapshot(changed_document()), logical_instance_key="other")
    right = database(document(), "first")
    # Insert an unrelated type first, then the incompatible existing type.
    unrelated = deepcopy(document())
    unrelated["covergroup_type_id"] = "Unrelated::cg"
    right = database(unrelated, "first")
    right.record(Snapshot(changed_document()), logical_instance_key="different")
    before = left.to_bytes()
    with pytest.raises(CoverageError, match="declaration mismatch"):
        left.merge(right)
    assert left.to_bytes() == before


def test_master_projection_preserves_bins_reports_dependencies_and_persists():
    master = database(document())
    incoming = database(changed_document())
    before = master.snapshot_document()["records"][0]
    report = master.merge(incoming, mode="master")
    after = master.snapshot_document()["records"][0]
    assert after["definition"] == before["definition"]
    assert after["instance_definition"] == before["instance_definition"]
    assert after["points"]["code_cp"] == before["points"]["code_cp"]
    assert after["points"]["combined"] == before["points"]["combined"]
    assert after["points"]["mode_cp"]["hits"] == {"one": 1, "zero": 1}
    conflicts = {item["item"]: item for item in report["conflicts"]}
    assert set(conflicts) == {"code_cp", "combined"}
    assert conflicts["combined"]["dependencies"] == ["code_cp"]
    assert conflicts["code_cp"]["master"] != conflicts["code_cp"]["incoming"]
    assert CoverageDatabase.from_bytes(master.to_bytes()).merge_reports == (report,)
    report["conflicts"].clear()
    assert master.merge_reports[0]["conflicts"]


def test_master_conflict_rejection_is_atomic_and_anonymous_inputs_stay_independent():
    master = database(document())
    before = master.to_bytes()
    with pytest.raises(CoverageError, match="conflict"):
        master.merge(database(changed_document()), mode="master", reject_conflicts=True)
    assert master.to_bytes() == before
    unknown = master.merge(database(document(), "unknown"), mode="master")
    assert unknown["conflicts"][0]["reason"] == "instance absent from master"
    anonymous = database(document(), None)
    anonymous.merge(database(changed_document(), None), mode="master")
    assert len(anonymous.snapshot_document()["records"]) == 2
    assert anonymous.snapshot_document()["records"][1]["points"]["code_cp"]["hits"] == {}


def test_database_reader_rejects_duplicates_and_reads_v1():
    import json
    doc = database(document()).snapshot_document()
    duplicate = deepcopy(doc)
    duplicate["records"].append(deepcopy(duplicate["records"][0]))
    with pytest.raises(CoverageError, match="duplicate logical instance"):
        CoverageDatabase.from_bytes(encode_database(duplicate))
    doc["records"][0].pop("instance_definition")
    meta = json.dumps({"format": "svtypes.coverage.database", "version": 1}).encode()
    legacy = struct.pack("<8sHH", MAGIC, 1, 0) + _chunk(b"META", meta) + _chunk(b"RECS", json.dumps(doc).encode())
    assert CoverageDatabase.from_bytes(legacy).snapshot_document() == doc
    restored = CoverageDatabase.from_bytes(legacy)
    assert not restored.merge(database(document(1)), mode="master")["conflicts"]


def test_master_projection_rejects_group_semantic_changes():
    changed = changed_document()
    changed["definition"]["options"]["goal"] = 50
    changed["declaration_semantic_digest"] = semantic_digest(changed["definition"])
    master = database(document())
    before = master.snapshot_document()["records"]
    report = master.merge(database(changed), mode="master")
    assert report["conflicts"][0]["reason"] == "covergroup semantics differ"
    assert master.snapshot_document()["records"] == before


def test_same_declaration_different_parameter_layout_never_merges_by_names():
    class Packet(SvObject):
        code = Bit[3](cov=False)

        @covergroup
        def cg(self, limit: CoverInput[int]):
            class cp(CovPoint, source=self.code):
                values = bins[0:limit]

        def __init__(self, limit):
            super().__init__()
            self.cg.instantiate(limit)

    first, second = (Packet(limit).cg.instance.snapshot_document() for limit in (2, 4))
    assert first["declaration_semantic_digest"] == second["declaration_semantic_digest"]
    master = database(first)
    before = master.to_bytes()
    with pytest.raises(CoverageError, match="instance layout mismatch"):
        master.merge(database(second))
    assert master.to_bytes() == before
    report = master.merge(database(second), mode="master")
    assert report["conflicts"][0]["reason"] == "instance layout mismatch"
    # Different named instances of one template may have distinct input layouts.
    master.merge(database(second, "other"))
    assert len(master.snapshot_document()["records"]) == 2


def test_cross_local_illegal_evidence_survives_disk_merge_and_baseline():
    class Packet(SvObject):
        code = Bit[1](cov=False)

        @covergroup
        def cg(self):
            class cp(CovPoint, source=self.code):
                valid = bins[0:1]

            class combined(Cross, members=(cp,)):
                class cp(CovPoint, source=self.code):
                    valid = bins[0]
                    bad = illegal_bins[1]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    packet = Packet()
    packet.code.value = 1
    packet.cg.sample()
    database_ = database(packet.cg.instance.snapshot_document())
    database_.merge(CoverageDatabase.from_bytes(database_.to_bytes()))
    restored = CoverageDatabase.from_bytes(database_.to_bytes())
    resumed = Packet()
    restored.apply_baseline(resumed.cg.instance, logical_instance_key="packet")
    assert restored.has_illegal_hits()
    assert resumed.cg.cross_local_illegal_snapshot() == {
        "combined": {"cp": {"bad": {"hits": 2, "source_ids": []}}},
    }


def test_transition_scoring_agrees_with_database_before_and_after_hit():
    class Packet(SvObject):
        code = Bit[1](cov=False)

        @covergroup
        def cg(self):
            class cp(CovPoint, source=self.code):
                rise = transition_bins[0, 1]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    packet = Packet()
    result = CoverageDatabase()
    for value, expected in ((0, 0), (1, 100)):
        packet.code.value = value
        packet.cg.sample()
        result.record(packet.cg.instance)
        assert packet.cg.get_inst_coverage() == expected
        assert result.coverage() == expected
        assert CoverageDatabase.from_bytes(result.to_bytes()).coverage() == expected

"""UCIS projection validates semantics, not only names and hit counts."""

from copy import deepcopy
from xml.etree import ElementTree as ET

import pytest

from svtypes import Bit, CoverageDatabase, CoverageError, CoverInput, CovPoint, Cross, SvObject, bins, covergroup, default_bins
from svtypes.coverage import export_ucis
from test_coverage_merge_contract import MergePacket


def exported():
    packet = MergePacket(1)
    database = CoverageDatabase()
    database.record(packet.cg.instance, logical_instance_key="packet")
    xml, report = export_ucis(database)
    assert not report["losses"]
    return ET.fromstring(xml)


@pytest.mark.parametrize("mutation,match", [
    ("range", "definition differs"),
    ("missing", "missing supported bin"),
    ("kind", "definition differs"),
    ("negative", "nonnegative"),
    ("duplicate", "duplicate item"),
    ("option", "option"),
    ("cross", "definition differs"),
    ("cross_order", "member order"),
])
def test_ucis_rejects_same_names_with_incompatible_semantics(mutation, match):
    root = exported()
    point = root.find(".//coverpoint[@name='code_cp']")
    bin_ = point.find("coverpointBin[@name='one']")
    if mutation == "range":
        bin_.find("range").set("from", "2")
        bin_.find("range").set("to", "2")
    elif mutation == "missing":
        point.remove(bin_)
    elif mutation == "kind":
        bin_.set("type", "illegal")
    elif mutation == "negative":
        bin_.find("range/contents").set("coverageCount", "-1")
    elif mutation == "duplicate":
        point.append(deepcopy(bin_))
    elif mutation == "option":
        point.find("options").set("at_least", "2")
    elif mutation == "cross":
        root.find(".//crossBin/index").text = "1"
    else:
        root.find(".//cross/crossExpr").text = "mode_cp"
    database = CoverageDatabase()
    before = database.to_bytes()
    with pytest.raises(CoverageError, match=match):
        database.import_ucis(ET.tostring(root, encoding="unicode"), bindings={"packet": MergePacket().cg.instance})
    assert database.to_bytes() == before


def test_ucis_two_record_import_is_atomic():
    root = exported()
    original = root.find(".//cgInstance")
    second = deepcopy(original)
    second.set("key", "second")
    second.find("coverpoint/coverpointBin/range").set("from", "-1")
    root.find(".//covergroupCoverage").append(second)
    database = CoverageDatabase()
    with pytest.raises(CoverageError, match="definition differs"):
        database.import_ucis(ET.tostring(root, encoding="unicode"), bindings={
            "packet": MergePacket().cg.instance, "second": MergePacket().cg.instance,
        })
    assert database.snapshot_document() == {"records": []}


def test_ucis_roundtrip_uses_concrete_parameter_bins_and_retains_loss_provenance():
    class Packet(SvObject):
        code = Bit[3](cov=False)

        @covergroup
        def cg(self, limit: CoverInput[int]):
            class cp(CovPoint, source=self.code):
                low = bins[0:limit]
                other = default_bins

        def __init__(self, limit):
            super().__init__()
            self.cg.instantiate(limit)

    packet = Packet(3)
    packet.code.value = 2
    packet.cg.sample()
    database = CoverageDatabase()
    database.record(packet.cg.instance, logical_instance_key="packet")
    xml, report = export_ucis(database)
    assert 'to="3"' in xml
    assert report["losses"]
    restored = CoverageDatabase()
    report = restored.import_ucis(xml, bindings={"packet": Packet(3).cg.instance})
    assert report["losses"]
    persisted = CoverageDatabase.from_bytes(restored.to_bytes())
    record = persisted.snapshot_document()["records"][0]
    assert record["points"]["cp"]["hits"]["low"] == 1
    assert record["import_provenance"][0]["losses"] == report["losses"]
    with pytest.raises(CoverageError, match="definition differs"):
        CoverageDatabase().import_ucis(xml, bindings={"packet": Packet(4).cg.instance})


def test_ucis_parameter_array_bins_use_materialized_names():
    class Packet(SvObject):
        code = Bit[3](cov=False)

        @covergroup
        def cg(self, lower: CoverInput[int], upper: CoverInput[int]):
            class cp(CovPoint, source=self.code):
                values = bins[lower:upper].split(max_bins=None)

        def __init__(self):
            super().__init__()
            self.cg.instantiate(2, 4)

    packet = Packet()
    packet.code.value = 3
    packet.cg.sample()
    database = CoverageDatabase()
    database.record(packet.cg.instance, logical_instance_key="packet")
    xml, report = export_ucis(database)
    assert not report["losses"]
    restored = CoverageDatabase()
    assert not restored.import_ucis(xml, bindings={"packet": Packet().cg.instance})["losses"]
    assert restored.snapshot_document()["records"][0]["points"]["cp"]["hits"] == {
        "values[2]": 0, "values[3]": 1, "values[4]": 0,
    }


def test_ucis_defaulted_import_settings_remain_in_persisted_provenance():
    database = CoverageDatabase()
    report = database.import_ucis(ET.tostring(exported(), encoding="unicode"),
                                 bindings={"packet": MergePacket().cg.instance},
                                 defaults={"source_limit": 3})
    assert report["losses"]
    restored = CoverageDatabase.from_bytes(database.to_bytes())
    assert restored.snapshot_document()["records"][0]["import_provenance"][0]["losses"] == report["losses"]


def test_ucis_never_substitutes_public_bins_for_cross_local_definitions():
    class Packet(SvObject):
        code = Bit[2](cov=False)

        @covergroup
        def cg(self):
            class cp(CovPoint, source=self.code):
                selected = bins[0:3]

            class combined(Cross, members=(cp,)):
                class cp(CovPoint, source=self.code):
                    selected = bins[0]

                selected = bins[cp.selected]

        def __init__(self):
            super().__init__()
            self.cg.instantiate()

    packet = Packet()
    packet.cg.sample()
    database = CoverageDatabase()
    database.record(packet.cg.instance, logical_instance_key="packet")
    xml, report = export_ucis(database)
    assert report["losses"]
    root = ET.fromstring(xml)
    assert root.find(".//crossBin") is None
    restored = CoverageDatabase()
    report = restored.import_ucis(xml, bindings={"packet": Packet().cg.instance})
    assert any(loss["item"] == "combined" for loss in report["losses"])
    cross = root.find(".//cross")
    bin_ = ET.SubElement(cross, "crossBin", name="selected", type="default")
    ET.SubElement(bin_, "index").text = "0"
    ET.SubElement(bin_, "contents", coverageCount="1")
    with pytest.raises(CoverageError, match="cannot be proven equivalent"):
        CoverageDatabase().import_ucis(ET.tostring(root, encoding="unicode"), bindings={"packet": Packet().cg.instance})

"""E2 regressions for isolated scans and static coverage catalogs."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest


_ROOT = Path(__file__).resolve().parents[2]
_BRIDGE_SOURCE = _ROOT / "extensions" / "svtypes_design_manifest" / "src"
_TOOLS_SOURCE = _ROOT / "extensions" / "svtypes_coverage_tools" / "src"
for source in (_BRIDGE_SOURCE, _TOOLS_SOURCE):
    sys.path.insert(0, str(source))

from svtypes import Bit, CovPoint, Cross, Object, ObjectRegistry, SvObject, bins, covergroup  # noqa: E402
from svtypes_design_manifest import canonical_json_bytes, export_design_manifest  # noqa: E402
from svtypes_coverage_tools import CatalogError, GuiError, GuiSession, ProposalError, ScanError, apply_proposal, build_catalog, create_gui_server, create_proposal, load_catalog, load_proposal, preview_coverage_layout, render_review, scan_design, start_gui_server, write_catalog, write_proposal  # noqa: E402
from svtypes_coverage_tools.gui import _app_html  # noqa: E402
from svtypes_coverage_tools.__main__ import main as coverage_main  # noqa: E402


class _Packet(SvObject):
    opcode = Bit(2)

    @covergroup
    def cg(self):
        class opcode_cp(CovPoint, source=self.opcode):
            read = bins[0]
            write = bins[1]

    def __init__(self):
        super().__init__()
        self.cg.instantiate()


_HIERARCHY_REGISTRY = ObjectRegistry()


class _HierarchyPacket(SvObject):
    opcode = Bit(1)

    @covergroup
    def cg(self):
        class opcode_cp(CovPoint, source=self.opcode):
            zero = bins[0]


class _HierarchyTop(SvObject):
    packet = Object("_HierarchyPacket", registry=_HIERARCHY_REGISTRY)
    enabled = Bit(1)


_HIERARCHY_REGISTRY.register(_HierarchyPacket)
_HIERARCHY_REGISTRY.register(_HierarchyTop)


def test_catalog_exposes_declaration_tree_and_stable_bin_ids(tmp_path: Path):
    manifest = export_design_manifest([_Packet], design_name="catalog-test")
    catalog = build_catalog(manifest)
    group = catalog["groups"][0]
    point = group["points"][0]

    assert point["semantic_id"].endswith("::point::opcode_cp")
    assert point["bins"][0]["semantic_id"].endswith("::point::opcode_cp::bin::read")
    output = write_catalog(tmp_path / "catalog.json", catalog)
    assert load_catalog(output) == catalog

    tampered = copy.deepcopy(catalog)
    tampered["groups"][0]["owner_type"] = "unexpected"
    with pytest.raises(CatalogError, match="digest"):
        write_catalog(tmp_path / "tampered.json", tampered)


def test_catalog_embeds_cross_local_covpoint_without_creating_public_point():
    class Packet(SvObject):
        opcode = Bit(2)
        mode = Bit(1)

        @covergroup
        def cg(self):
            class opcode_cp(CovPoint, source=self.opcode):
                all_values = bins[0:3]

            class mode_cp(CovPoint, source=self.mode):
                read = bins[0]

            class opcode_mode(Cross, members=(opcode_cp, mode_cp)):
                class opcode_cp(CovPoint, source=self.opcode):
                    compact = bins[0:1]

                selected = bins[opcode_cp.compact, mode_cp.read]

    catalog = build_catalog(export_design_manifest([Packet]))
    group = catalog["groups"][0]
    assert [point["name"] for point in group["points"]] == ["mode_cp", "opcode_cp"]
    cross = group["crosses"][0]
    assert len(cross["member_views"]) == 1
    view = cross["member_views"][0]
    assert view["member"] == "opcode_cp"
    assert view["point"]["name"] == "opcode_cp"
    assert [bin_["name"] for bin_ in view["point"]["bins"]] == ["compact"]
    assert "semantic_id" not in view["point"]
    assert "semantic_id" not in view["point"]["bins"][0]
    page = _app_html().decode("utf-8")
    assert "有效成员定义" in page
    assert "外层定义不参与此 cross" in page


def test_catalog_builds_a_static_type_field_hierarchy():
    manifest = export_design_manifest([_HierarchyTop, _HierarchyPacket], design_name="hierarchy-test")
    catalog = build_catalog(manifest)
    hierarchy = catalog["type_hierarchy"]
    top_name = f"{__name__}._HierarchyTop"
    packet_name = f"{__name__}._HierarchyPacket"
    nodes = {item["type_name"]: item for item in hierarchy["nodes"]}

    assert hierarchy["roots"] == sorted([top_name, packet_name])
    assert nodes[top_name]["references"] == [
        {"field": "packet", "kind": "object_ref", "target_type": packet_name}
    ]
    assert any(item.endswith("::cg") for item in nodes[packet_name]["covergroups"])


def test_catalog_cli_writes_static_catalog(tmp_path: Path):
    from svtypes_design_manifest import write_design_manifest

    manifest_path = write_design_manifest(tmp_path / "design.json", export_design_manifest([_Packet]))
    catalog_path = tmp_path / "catalog.json"
    assert coverage_main(["catalog", "--input", str(manifest_path), "--out", str(catalog_path)]) == 0
    assert load_catalog(catalog_path)["groups"][0]["covergroup_type_id"].endswith("::cg")


def test_scanner_imports_design_only_in_child_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    marker = tmp_path / "imported-pid.txt"
    module = tmp_path / "isolated_design.py"
    module.write_text(
        "import os\n"
        "from pathlib import Path\n"
        "Path(os.environ['SVT_TEST_SCAN_MARKER']).write_text(str(os.getpid()), encoding='utf-8')\n"
        "from svtypes import Bit, CovPoint, SvObject, bins, covergroup\n"
        "class Packet(SvObject):\n"
        "    opcode = Bit(1)\n"
        "    @covergroup\n"
        "    def cg(self):\n"
        "        class opcode_cp(CovPoint, source=self.opcode):\n"
        "            zero = bins[0]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SVT_TEST_SCAN_MARKER", str(marker))
    result = scan_design(
        ["isolated_design"],
        output=tmp_path / "design.json",
        source_root=tmp_path,
        python_paths=(tmp_path, _BRIDGE_SOURCE, _TOOLS_SOURCE),
    )

    assert int(marker.read_text(encoding="utf-8")) != os.getpid()
    assert result["report"]["child_pid"] != os.getpid()
    assert result["manifest"]["design"]["name"] == "design"
    assert result["manifest"]["provenance"][0]["file"] == "isolated_design.py"


def test_scanner_failure_does_not_replace_existing_manifest(tmp_path: Path):
    output = tmp_path / "existing.json"
    output.write_text("prior", encoding="utf-8")

    with pytest.raises(ScanError, match="SVT-SCAN-IMPORT"):
        scan_design(
            ["missing_design"],
            output=output,
            python_paths=(_BRIDGE_SOURCE, _TOOLS_SOURCE),
        )
    assert output.read_text(encoding="utf-8") == "prior"


def test_scanner_retains_successful_declarations_with_import_diagnostics(tmp_path: Path):
    module = tmp_path / "partial_design.py"
    module.write_text(
        "from svtypes import Bit, SvObject\n"
        "class Packet(SvObject):\n    code = Bit(1)\n",
        encoding="utf-8",
    )
    result = scan_design(
        ["partial_design", "missing_design"], output=tmp_path / "partial.json",
        python_paths=(tmp_path, _BRIDGE_SOURCE, _TOOLS_SOURCE),
    )
    assert len(result["manifest"]["types"]) == 1
    assert result["manifest"]["diagnostics"][0]["code"] == "SVT-SCAN-IMPORT"


def test_design_gui_scans_and_browses_declarations_without_database(tmp_path: Path):
    module = tmp_path / "gui_design.py"
    module.write_text(
        "from svtypes import Bit, CovPoint, SvObject, bins, covergroup\n"
        "class Packet(SvObject):\n"
        "    opcode = Bit(1)\n"
        "    @covergroup\n"
        "    def cg(self):\n"
        "        class opcode_cp(CovPoint, source=self.opcode):\n"
        "            zero = bins[0]\n",
        encoding="utf-8",
    )
    session = GuiSession(tmp_path, (tmp_path, _BRIDGE_SOURCE, _TOOLS_SOURCE))
    with pytest.raises(GuiError, match="loopback"):
        create_gui_server(session, host="0.0.0.0")
    state = session.scan(["gui_design"])
    group = state["catalog"]["groups"][0]
    node = session.node(group["points"][0]["bins"][0]["semantic_id"])
    assert node["kind"] == "bin"
    assert node["node"]["name"] == "zero"


def test_gui_serves_a_coverage_workbench_not_a_raw_json_debug_page(tmp_path: Path):
    del tmp_path
    page = _app_html().decode("utf-8")

    assert "覆盖率工作台" in page
    assert "按覆盖意图浏览" in page
    assert "Bin 方案" in page
    assert "JSON.stringify(x.node" not in page
    assert "for(const b of p.bins)add(b.name" not in page
    assert "for(const b of c.bins)add(b.name" not in page
    assert "function binDetail" not in page
    assert "function wireBinDetails" not in page
    assert "function wireArrayBins" in page
    assert "title='${esc(bin.semantic_id)}'" in page
    assert ".array-bin-member td:first-child{padding-left:31px" in page
    assert ".array-bin-member td:first-child::before" in page
    assert "function arrayBinIndex" in page
    assert "group.sort((left,right)=>arrayBinIndex(left.name)-arrayBinIndex(right.name))" in page
    assert "function binOrder" not in page
    assert "localeCompare(b.name,undefined,{numeric:true" not in page
    assert "bin.kind==='transition'&&bin.selector?.kind==='values'" in page
    assert "bin.selector.items.map(expr).join(' → ')" in page
    assert "静态设计类型" in page
    assert "静态字段引用" in page
    assert "document.createElement('details')" in page
    assert "tree-branch" in page
    assert "position:fixed;top:72px;bottom:0;left:0;width:var(--sidebar-width,310px)" in page
    assert ".tree .node{align-items:center;display:grid;gap:6px;grid-template-columns:16px minmax(0,1fr) auto}" in page
    assert ".tree .node.type .kind-icon{background:transparent;border:2px solid #3b82f6" in page
    assert ".tree .node.group .kind-icon{background:transparent;border:2px solid #7c3aed" in page
    assert ".tree .node.point .kind-icon{background:#059669" in page
    assert ".tree .node.cross .kind-icon{background:#d97706" in page
    assert ".tree .node.ref .kind-icon::before{content:'→'}" in page
    assert "e.target.closest('.kind-icon')" in page
    assert "sidebar-chrome" in page
    assert "tree-scroll" in page
    assert "展开 / 折叠' aria-label='展开 / 折叠'>▸" not in page
    assert ".tree .node{padding-left:calc(9px + var(--tree-indent, 0px))}" in page
    assert "n.style.setProperty('--tree-indent',`${depth*18}px`)" in page
    assert ".tree .node-label{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}" in page
    assert "⛓" in page
    assert "sidebar-resizer" in page
    assert "setupSidebarResize()" in page
    assert "function logicText" in page
    assert "async function api(" in page
    assert "if(k==='subscript')" in page
    assert "if(k==='field'||k==='container_values'||k==='assoc_values')" in page
    assert "if(k==='enum_literal')" in page
    assert "zh.delete" not in page
    assert "q('#binName')" not in page
    assert "bin-canvas-host" in page
    assert "/bin-canvas.js" in page
    assert "wireBinCanvas(" in page
    assert "有效成员定义" in page
    assert "外层定义不参与此 cross" in page
    assert "<h3>值域</h3>" in page
    assert "放弃所有修改" in page
    assert "bin-domain-min" in page
    assert "bin-add-default" in page
    assert "bin-reset" in page
    assert "html.bin-dragging" in page
    assert "is-selected-range" in page
    assert "${ids(p)}" in page
    assert "${options(p.options)}" in page
    assert "function optionText" in page
    assert ".bin-kind-slot,.bin-array-slot{" in page
    assert "bin-nudge" not in page
    assert "<h3>采样规则</h3>" not in page
    assert "悬停滚轮" not in page
    assert "/bin-canvas.js?v=88" in page
    assert "transition-domain" in page
    assert "bin-undo" in page
    assert "bin-redo" in page
    assert "bin-help-popover" in page
    assert ".bin-cell.is-empty,.bin-cell.is-automatic,.bin-cell.is-addable{cursor:pointer}" in page
    assert ".bin-canvas-panel .panel-head{gap:8px;padding:8px 12px;justify-content:flex-start;flex-wrap:wrap}" in page
    assert ".bin-array-slot{" in page
    assert ".bin-array-mode.is-selected" in page
    assert ".bin-row-name{" in page
    assert ".bin-fragment-token{" in page
    assert ".bin-editor{border-top:1px solid var(--line)" in page
    assert ".bin-editor-rule{" in page
    assert "bins-panel" in page
    assert "bin-domain-overview" in page
    assert "bin-row-handle" in page
    assert "bin-row-delete" in page


def test_gui_serves_coverpoint_domain_canvas_script(tmp_path: Path):
    session = GuiSession(tmp_path)
    server, _thread, url = start_gui_server(session)
    try:
        from urllib.request import urlopen
        body = urlopen(url + "bin-canvas.js", timeout=5).read().decode("utf-8")
    finally:
        server.shutdown()
    assert "function mountBinCanvas" in body
    assert "function wireBinCanvas" in body
    assert "function transitionSelectorText" in body
    assert "function renderTransitionEditor" in body
    assert "transition-repeat-toggle" in body
    assert "transition-step-chip" in body
    assert "::layout::${JSON.stringify(bins)}" in body
    assert "新建 bin" in body
    assert 'class="secondary bin-delete">删除' not in body
    assert 'class="bin-name-input"' in body
    assert 'class="bin-lane"' in body
    assert "function renameBin" in body
    assert "event.shiftKey" in body
    assert 'classList.add("bin-dragging")' in body
    assert "is-selected-range" in body
    assert "function kindSlot" in body
    assert "function rangeRows" in body
    assert "function packTracks" in body
    assert "function firstOpenValue" in body
    assert "function selectorSyntax" in body
    assert "model.editingFragment = fragment" in body
    assert 'class="bin-fragment-token' in body
    assert "event.detail >= 2" in body
    assert 'class="bin-enum-lo"' in body
    assert 'class="bin-enum-hi"' in body
    assert "function startDeleteDrag" in body
    assert "松开删除" in body
    assert "bin-delete-ghost" in body
    assert 'class="bin-row-name"' in body
    assert 'class="bin-enum-value"' in body
    assert 'class="bin-value"' in body
    assert 'event.key === "Delete"' in body
    assert "bin-nudge" not in body
    assert "function sizeName" in body
    assert "function setEdge" in body
    assert "if (!setEdge(item, edge, current + delta, model, at))" in body
    assert 'input.addEventListener("wheel"' in body
    assert "function groupArrayItems" in body
    assert "function arrayControls" in body
    assert "function suggestedArrayCount" in body
    assert "function orderedFragments" in body
    assert 'class="bin-array-slot"' in body
    assert 'class="bin-array-mode' in body
    assert 'class="bin-array-count"' in body
    assert "/^auto$/i.test(text)" in body
    assert "const isSecondPress" in body
    assert "event.timeStamp - previousPress.time <= 500" in body
    assert "function syncAutomatic" in body
    assert "function canCreateAt" in body
    assert "option.auto_bin_max" in body
    assert "/^-?\\d+:-?\\d+$/" in body
    assert "axis.addEventListener(\"wheel\"" not in body
    assert "model.selected = hits[hits.length - 1].name" not in body
    assert "function axisFromPoint" in body
    assert "function applyDomain" in body
    assert "function axisSpec" in body
    assert "if (model.enum)" in body
    assert "clamp(min, model.nativeMin, model.nativeMax)" in body
    assert "clamp(max, model.nativeMin, model.nativeMax)" in body
    assert 'bound.classList.toggle("hidden", Boolean(model.enum))' in body
    assert "function syncDefault" in body
    assert "function uncoveredFragments" in body
    assert "function addDefault" in body
    assert "覆盖所有尚未被其它 bins 覆盖的值" in body


def test_gui_previews_cover_input_layout_in_the_isolated_scanner(tmp_path: Path):
    module = tmp_path / "gui_layout_design.py"
    module.write_text(
        "from svtypes import Bit, CoverInput, CovPoint, SvObject, bins, coverage_init, covergroup\n"
        "class Packet(SvObject):\n"
        "    code = Bit(3)\n"
        "    @covergroup\n"
        "    def cg(self, first: CoverInput[int], last: CoverInput[int]):\n"
        "        class code_cp(CovPoint, source=self.code):\n"
        "            window = bins[first:last].split(max_bins=None)\n"
        "    @coverage_init\n"
        "    def configure_coverage(self, first: int, last: int):\n"
        "        self.cg.instantiate(first, last)\n",
        encoding="utf-8",
    )
    paths = (tmp_path, _BRIDGE_SOURCE, _TOOLS_SOURCE)
    direct = preview_coverage_layout(
        "gui_layout_design:Packet", "cg", {"first": 0, "last": 1},
        source_root=tmp_path, python_paths=paths,
    )
    assert [item["name"] for item in direct["layout"]["points"][0]["bins"]] == ["window[0]", "window[1]"]

    session = GuiSession(tmp_path, paths)
    state = session.scan(["gui_layout_design"])
    group = state["catalog"]["groups"][0]
    preview = session.preview_layout({
        "covergroup_type_id": group["covergroup_type_id"],
        "arguments": {"first": 2, "last": 3},
    })
    assert [item["name"] for item in preview["layout"]["points"][0]["bins"]] == ["window[2]", "window[3]"]


def test_proposal_is_reviewable_and_applies_only_after_confirmation(tmp_path: Path):
    source = tmp_path / "proposal_design.py"
    source.write_text(
        "from svtypes import Bit, CovPoint, SvObject, bins, covergroup\n"
        "class Packet(SvObject):\n"
        "    opcode = Bit(1)\n"
        "    @covergroup\n"
        "    def cg(self):\n"
        "        class opcode_cp(CovPoint, source=self.opcode):\n"
        "            zero = bins[0]\n",
        encoding="utf-8",
    )
    scan = scan_design(
        ["proposal_design"], output=tmp_path / "design.json", source_root=tmp_path,
        python_paths=(tmp_path, _BRIDGE_SOURCE, _TOOLS_SOURCE),
    )
    catalog = build_catalog(scan["manifest"])
    source_lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
    line = next(index for index, value in enumerate(source_lines, 1) if "zero = bins[0]" in value)
    group = catalog["groups"][0]
    proposal = create_proposal(
        catalog,
        covergroup_type_id=group["covergroup_type_id"],
        operations=({"kind": "add_bin", "point": "opcode_cp", "name": "one", "selector": {"kind": "constant", "value": 1}},),
        rationale="Record the second opcode value.",
        edits=({"file": "proposal_design.py", "start_line": line, "end_line": line, "expected_text": source_lines[line - 1], "replacement": source_lines[line - 1] + "            one = bins[1]\n"},),
    )
    catalog_path = write_catalog(tmp_path / "catalog.json", catalog)
    cli_path = tmp_path / "cli-proposal.json"
    assert coverage_main([
        "proposal", "create", "--catalog", str(catalog_path), "--covergroup", group["covergroup_type_id"],
        "--operation", json.dumps(proposal["operations"][0]), "--edit", json.dumps(proposal["edits"][0]),
        "--rationale", proposal["rationale"], "--out", str(cli_path),
    ]) == 0
    assert load_proposal(cli_path) == proposal
    saved = write_proposal(tmp_path / "proposal.json", proposal)
    assert load_proposal(saved) == proposal
    assert "add_bin" in render_review(proposal)
    with pytest.raises(ProposalError, match="confirm=True"):
        apply_proposal(proposal, catalog, source_root=tmp_path)
    changed = apply_proposal(proposal, catalog, source_root=tmp_path, confirm=True)
    assert changed == (source,)
    assert "one = bins[1]" in source.read_text(encoding="utf-8")


def test_proposal_rejects_stale_source_and_digest(tmp_path: Path):
    source = tmp_path / "design.py"
    source.write_text(
        "from svtypes import Bit, CovPoint, SvObject, bins, covergroup\n"
        "class Packet(SvObject):\n"
        "    code = Bit(1)\n"
        "    @covergroup\n"
        "    def cg(self):\n"
        "        class code_cp(CovPoint, source=self.code):\n"
        "            zero = bins[0]\n",
        encoding="utf-8",
    )
    scan = scan_design(["design"], output=tmp_path / "design.json", source_root=tmp_path, python_paths=(tmp_path, _BRIDGE_SOURCE, _TOOLS_SOURCE))
    catalog = build_catalog(scan["manifest"])
    group = catalog["groups"][0]
    line = next(index for index, value in enumerate(source.read_text(encoding="utf-8").splitlines(keepends=True), 1) if "zero = bins[0]" in value)
    proposal = create_proposal(
        catalog, covergroup_type_id=group["covergroup_type_id"], operations=({"kind": "add_comment"},), rationale="document it",
        edits=({"file": "design.py", "start_line": line, "end_line": line, "expected_text": "            zero = bins[0]\n", "replacement": "changed\n"},),
    )
    source.write_text(source.read_text(encoding="utf-8").replace("zero = bins[0]", "other = bins[0]"), encoding="utf-8")
    with pytest.raises(ProposalError, match="stale"):
        apply_proposal(proposal, catalog, source_root=tmp_path, confirm=True)
    stale_catalog = copy.deepcopy(catalog)
    stale_catalog["groups"][0]["declaration_semantic_digest"] = "c" * 64
    semantic = {
        "design_manifest_digest": stale_catalog["design_manifest_digest"],
        "format": stale_catalog["format"],
        "groups": stale_catalog["groups"],
        "type_hierarchy": stale_catalog["type_hierarchy"],
    }
    stale_catalog["catalog_digest"] = hashlib.sha256(canonical_json_bytes(semantic)).hexdigest()
    with pytest.raises(ProposalError, match="digest"):
        apply_proposal(proposal, stale_catalog, source_root=tmp_path, confirm=True)

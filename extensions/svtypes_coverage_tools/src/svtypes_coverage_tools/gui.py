"""Local design-time GUI service for SvTypes coverage declarations.

The service is intentionally small and dependency-free: it serves a live local
web application, while every design import remains delegated to the isolated
scanner worker.  It does not load coverage databases or sample user objects.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
from threading import Thread
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse

from .catalog import build_catalog, write_catalog
from .scanner import ScanError, preview_coverage_layout, scan_design

_BIN_CANVAS_JS = Path(__file__).with_name("gui_bin_canvas.js")


class GuiError(RuntimeError):
    """A local GUI request is invalid for the current design session."""


def _declaration_source(source_root: Path, provenance: Mapping[str, Any], name: str, kind: str) -> str | None:
    """Read one declared coverage class from its local, scanner-recorded source anchor."""
    relative = provenance.get("file")
    line = provenance.get("line")
    if not isinstance(relative, str) or not isinstance(line, int) or line < 1:
        return None
    try:
        path = (source_root / relative).resolve()
        path.relative_to(source_root)
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
    except (OSError, SyntaxError, ValueError):
        return None
    def start_line(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
        return min([node.lineno, *(item.lineno for item in node.decorator_list)])

    owner = next((node for node in ast.walk(tree)
                  if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and start_line(node) == line), None)
    if owner is None:
        return None
    base = "Cross" if kind == "cross" else "CovPoint"
    candidate = next((node for node in ast.walk(owner)
                      if isinstance(node, ast.ClassDef) and node.name == name
                      and any(isinstance(item, ast.Name) and item.id == base for item in node.bases)), None)
    if candidate is None or not hasattr(candidate, "end_lineno"):
        return None
    return ast.get_source_segment(text, candidate, padded=True)


@dataclass(slots=True)
class GuiSession:
    """Mutable GUI-only state; no value here participates in SvTypes semantics."""

    source_root: Path
    python_paths: tuple[Path, ...] = ()
    work_dir: Path | None = None
    manifest: dict[str, Any] | None = None
    catalog: dict[str, Any] | None = None
    module_references: tuple[str, ...] = ()
    scan_serial: int = 0
    _temporary_work_dir: tempfile.TemporaryDirectory[str] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.source_root = self.source_root.resolve()
        self.python_paths = tuple(path.resolve() for path in self.python_paths)
        if self.work_dir is None:
            self._temporary_work_dir = tempfile.TemporaryDirectory(prefix="svtypes-coverage-gui-")
            self.work_dir = Path(self._temporary_work_dir.name)
        else:
            self.work_dir = self.work_dir.resolve()
            self.work_dir.mkdir(parents=True, exist_ok=True)

    def scan(self, modules: list[str]) -> dict[str, Any]:
        if not modules or any(not isinstance(item, str) or not item or ":" in item for item in modules):
            raise GuiError("GUI scan requires one or more importable module entry points")
        assert self.work_dir is not None
        result = scan_design(
            modules,
            output=self.work_dir / "latest.design.json",
            source_root=self.source_root,
            python_paths=self.python_paths,
        )
        self.manifest = result["manifest"]
        self.catalog = build_catalog(self.manifest)
        write_catalog(self.work_dir / "latest.catalog.json", self.catalog)
        self.module_references = tuple(modules)
        self.scan_serial += 1
        return self.state()

    def state(self) -> dict[str, Any]:
        return {
            "source_root": str(self.source_root),
            "module_references": list(self.module_references),
            "scan_serial": self.scan_serial,
            "catalog": self.catalog,
            "diagnostics": [] if self.manifest is None else self.manifest.get("diagnostics", []),
        }

    def node(self, semantic_id: str) -> dict[str, Any]:
        if self.catalog is None:
            raise GuiError("scan a design before selecting a declaration")
        if semantic_id.startswith("type::"):
            type_name = semantic_id[len("type::"):]
            for item in self.catalog["type_hierarchy"]["nodes"]:
                if item["type_name"] == type_name:
                    return {"kind": "type", "node": item}
        for group in self.catalog["groups"]:
            if group["covergroup_type_id"] == semantic_id:
                return {"kind": "covergroup", "node": group}
            for collection, kind in (("points", "point"), ("crosses", "cross")):
                for item in group[collection]:
                    if item["semantic_id"] == semantic_id:
                        node = dict(item)
                        source = _declaration_source(self.source_root, group.get("provenance", {}), item["name"], kind)
                        if source is not None:
                            node["source"] = source
                        return {"kind": kind, "node": node, "covergroup": group["covergroup_type_id"]}
                    for bin_ in item["bins"]:
                        if bin_["semantic_id"] == semantic_id:
                            return {"kind": "bin", "node": bin_, "parent": item["semantic_id"], "covergroup": group["covergroup_type_id"]}
        raise GuiError(f"unknown coverage declaration {semantic_id!r}")

    def preview_layout(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Preview the bins built by one declared ``@coverage_init`` method."""
        if self.catalog is None or self.manifest is None:
            raise GuiError("scan a design before previewing a coverage layout")
        group_id = payload.get("covergroup_type_id")
        arguments = payload.get("arguments")
        parameter_actuals = payload.get("parameter_actuals", {})
        if not isinstance(group_id, str):
            raise GuiError("layout preview requires covergroup_type_id")
        if not isinstance(arguments, Mapping) or any(not isinstance(key, str) for key in arguments):
            raise GuiError("layout preview arguments must be a JSON object with string keys")
        if not isinstance(parameter_actuals, Mapping) or any(not isinstance(key, str) for key in parameter_actuals):
            raise GuiError("layout preview parameter_actuals must be a JSON object with string keys")
        group = next((item for item in self.catalog["groups"] if item["covergroup_type_id"] == group_id), None)
        if group is None:
            raise GuiError(f"unknown covergroup {group_id!r}")
        owner_type = group.get("owner_type")
        if not isinstance(owner_type, str) or not owner_type:
            raise GuiError("selected covergroup is missing owner_type")
        owner = owner_type.split("[", 1)[0]
        reference = None
        for module in self.module_references:
            if owner == module:
                raise GuiError(f"covergroup owner {owner!r} is a module, not a type")
            prefix = module + "."
            if owner.startswith(prefix):
                reference = f"{module}:{owner[len(prefix):]}"
                break
        if reference is None:
            raise GuiError("selected covergroup owner was not discovered from the scanned modules")
        result = preview_coverage_layout(
            reference,
            group["declaration_name"],
            dict(arguments),
            parameter_actuals=dict(parameter_actuals),
            source_root=self.source_root,
            python_paths=self.python_paths,
        )
        layout = result.get("layout")
        if not isinstance(layout, Mapping):
            raise GuiError(f"covergroup {group['declaration_name']!r} did not materialize")
        return {
            "covergroup_type_id": group_id,
            "arguments": dict(arguments),
            "layout": layout,
        }

def _app_html() -> bytes:
    """Return the live local coverage workbench; all data comes from the loopback API."""
    return """<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>SvTypes 覆盖率工作台</title>
<style>
:root{--ink:#172033;--muted:#6a7487;--paper:#f6f8fc;--card:#fff;--line:#e3e8f1;--accent:#6256e8;--accent-soft:#eeecff;--good:#087f5b;--good-soft:#e5f7ef;--warn:#b45309;--warn-soft:#fff3df;--bad:#c53030;--bad-soft:#ffebeb;--shadow:0 10px 28px rgba(27,39,68,.08)}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.45 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;display:grid;grid-template-columns:310px minmax(0,1fr);grid-template-rows:72px minmax(0,1fr);height:100vh}header{grid-column:1/3;display:flex;align-items:center;justify-content:space-between;padding:0 28px;background:#171a35;color:#fff;box-shadow:0 2px 12px rgba(13,17,42,.16)}.brand{display:flex;gap:13px;align-items:center}.brand h1{font-size:16px;margin:0;letter-spacing:.01em}.brand p{margin:1px 0 0;color:#bfc4e6;font-size:12px}.status{font-size:12px;color:#dfe2ff;background:#2b2e54;padding:7px 11px;border-radius:999px}.header-actions{align-items:center;display:flex;gap:8px}.header-actions input.status{border:0;box-shadow:none;caret-color:#fff;max-width:18em;min-width:6em;outline:0;width:auto}.header-actions input.status::placeholder{color:#8b90b8}.icon-btn{align-items:center;background:#2b2e54;border:0;border-radius:999px;box-shadow:none;color:#dfe2ff;cursor:pointer;display:inline-flex;justify-content:center;padding:7px 11px}.icon-btn:hover{background:#353a68;color:#fff}.icon-btn svg{display:block;height:14px;width:14px}aside{border-right:1px solid var(--line);background:#fff;display:flex;flex-direction:column;overflow:hidden;padding:0}.sidebar-chrome{background:#fff;border-bottom:1px solid var(--line);flex:0 0 auto;padding:14px 14px 10px}.tree-scroll{flex:1 1 auto;min-height:0;overflow:auto;padding:8px 14px 18px}main{overflow:auto;padding:26px 32px 48px;max-width:1500px;width:100%;margin:auto}.section-label{font-size:11px;font-weight:750;color:var(--muted);letter-spacing:.09em;text-transform:uppercase;margin:14px 8px 7px}label{display:block;font-size:12px;font-weight:650;color:#4f596c;margin:8px 0 4px}input,textarea,select,button{font:inherit}input,textarea,select{border:1px solid #dbe1ec;border-radius:8px;padding:8px 9px;background:#fff;color:var(--ink);width:100%}textarea{resize:vertical;min-height:54px}button{border:0;border-radius:8px;padding:9px 12px;background:var(--accent);color:#fff;font-weight:700;cursor:pointer;box-shadow:0 2px 5px rgba(70,58,180,.2)}button:hover{background:#5145d6}button.secondary{background:#fff;color:#4e46bd;border:1px solid #cfcaf9;box-shadow:none}.tree{display:grid;gap:2px}.node{width:100%;border:0;background:transparent;color:#35405a;box-shadow:none;text-align:left;padding:8px 9px;border-radius:7px;font-weight:550;display:flex;align-items:center;gap:8px}.node:hover,.node.active{background:var(--accent-soft);color:#453ab5}.node .icon{width:18px;text-align:center;color:#7771cb}.node .count{margin-left:auto;color:#7d8798;font-size:11px;font-weight:700}.node.type{font-weight:750}.node.point,.node.cross{margin-left:12px}.node.bin{margin-left:28px;font-size:12px;color:#647086}.node.bin .icon{font-size:9px}.diagnostic{padding:9px;border-radius:8px;background:var(--bad-soft);color:var(--bad);font-size:12px;margin:4px 0}.sidebar-head{align-items:center;display:flex;gap:8px;justify-content:space-between;margin:0}.sidebar-head .section-label{margin:0}.sidebar-icon-btn{align-items:center;background:transparent;border:0;border-radius:8px;box-shadow:none;color:var(--muted);cursor:pointer;display:inline-flex;height:28px;justify-content:center;padding:0;width:28px}.sidebar-icon-btn:hover,.sidebar-icon-btn.active{background:var(--accent-soft);color:#453ab5}.sidebar-icon-btn svg{display:block;height:15px;width:15px}.filter-panel{margin:10px 0 0}.filter-panel.hidden{display:none}.diag-btn{position:relative}.diag-btn.hidden{display:none}.diag-btn .badge{background:var(--bad);border-radius:999px;color:#fff;font-size:10px;font-weight:800;line-height:1;min-width:16px;padding:3px 5px;position:absolute;right:-4px;top:-4px}.diag-panel{background:#fff;border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow);display:none;max-height:min(60vh,420px);overflow:auto;padding:12px;position:fixed;right:24px;top:82px;width:min(380px,calc(100vw - 48px));z-index:5}.diag-panel.open{display:block}.diag-panel-head{align-items:center;display:flex;font-size:13px;font-weight:750;justify-content:space-between;margin-bottom:8px}.diag-panel-head button{background:transparent;border:0;box-shadow:none;color:var(--muted);cursor:pointer;font-size:18px;line-height:1;padding:0 4px}.diag-panel-head button:hover{color:var(--ink)}.hero{background:linear-gradient(115deg,#fff 0%,#fafaff 60%,#f1f0ff 100%);border:1px solid var(--line);border-radius:16px;padding:25px 27px;box-shadow:var(--shadow);margin-bottom:18px}.eyebrow{font-size:11px;text-transform:uppercase;letter-spacing:.09em;font-weight:800;color:#665ad7}.hero h2{margin:5px 0 5px;font-size:25px;letter-spacing:-.02em}.hero p{margin:0;color:var(--muted)}.summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:12px;margin:18px 0}.metric{background:var(--card);border:1px solid var(--line);padding:14px 15px;border-radius:11px}.metric strong{display:block;font-size:23px;letter-spacing:-.04em}.metric span{font-size:12px;color:var(--muted)}.panel{background:var(--card);border:1px solid var(--line);border-radius:13px;box-shadow:0 2px 8px rgba(24,35,62,.03);margin:14px 0;overflow:hidden}.panel-head{display:flex;justify-content:space-between;gap:12px;align-items:center;padding:14px 17px;border-bottom:1px solid var(--line)}.panel-head h3{margin:0;font-size:14px}.panel-body{padding:16px 17px}.chips{display:flex;gap:6px;flex-wrap:wrap}.chip,.kind{display:inline-flex;align-items:center;border-radius:999px;padding:3px 8px;font-size:11px;font-weight:750}.chip{background:#eef1f6;color:#566176}.kind.normal{background:var(--good-soft);color:var(--good)}.kind.ignore{background:#edf0f5;color:#687284}.kind.illegal{background:var(--bad-soft);color:var(--bad)}.kind.default{background:var(--warn-soft);color:var(--warn)}.kind.transition{background:var(--accent-soft);color:#5044c2}.definition{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;background:#f8f9fc;border:1px solid #e9edf4;border-radius:8px;padding:10px 12px;color:#313b51;overflow:auto}.muted{color:var(--muted)}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse}th{text-align:left;padding:9px 10px;font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;border-bottom:1px solid var(--line)}td{padding:11px 10px;border-bottom:1px solid #eef1f5;vertical-align:top}tr:last-child td{border-bottom:0}.bin-name{font-weight:750}.bin-row{cursor:pointer}.bin-row:hover{background:#fafaff}.two-col{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(240px,.6fr);gap:14px}.key-value{display:grid;gap:2px;font-size:12px}.key-value dt{color:var(--muted);font-size:11px;margin-top:8px}.key-value dt:first-child{margin-top:0}.key-value dd{margin:0;overflow-wrap:anywhere;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11px;line-height:1.4}.hidden{display:none!important}.error{color:var(--bad);background:var(--bad-soft);padding:10px 12px;border-radius:8px}.proposal{margin-top:28px;border-top:1px solid var(--line);padding-top:20px}.proposal-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.review{white-space:pre-wrap;background:#171a35;color:#e8e9ff;border-radius:9px;padding:13px;max-height:240px;overflow:auto;font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace}.layout-list{display:grid;gap:10px}.layout-point{border:1px solid var(--line);border-radius:9px;padding:10px}.layout-point strong{display:block;margin-bottom:7px}.welcome{max-width:680px;margin:10vh auto;text-align:center}@media(max-width:850px){body{grid-template-columns:1fr;grid-template-rows:72px auto minmax(0,1fr);height:auto;min-height:100vh}header{grid-column:1;padding:0 17px}aside{border-right:0;border-bottom:1px solid var(--line);max-height:42vh}main{padding:18px}.two-col,.proposal-grid{grid-template-columns:1fr}.node.bin{margin-left:20px}}
body{grid-template-columns:var(--sidebar-width,310px) minmax(0,1fr)}header{height:72px}aside{position:fixed;top:72px;bottom:0;left:0;width:var(--sidebar-width,310px);overflow:hidden}main{grid-column:2;grid-row:2;min-height:0}#sidebar-resizer{background:transparent;bottom:0;cursor:col-resize;left:calc(var(--sidebar-width,310px) - 3px);position:fixed;top:72px;width:6px;z-index:2}#sidebar-resizer:hover,#sidebar-resizer.resizing{background:var(--accent)}.tree .node{align-items:center;display:grid;gap:6px;grid-template-columns:16px minmax(0,1fr) auto}.tree-branch>summary{cursor:default;list-style:none}.tree-branch>summary::-webkit-details-marker{display:none}.tree .kind-icon{box-sizing:border-box;display:inline-block;height:10px;justify-self:center;width:10px}.tree-branch>summary>.kind-icon{cursor:pointer}.tree .node.type .kind-icon{background:transparent;border:2px solid #3b82f6;border-radius:2px;transform:rotate(45deg)}.tree-branch.type[open]>summary>.kind-icon{background:#3b82f6}.tree .node.group .kind-icon{background:transparent;border:2px solid #7c3aed;border-radius:2px}.tree-branch.group[open]>summary>.kind-icon{background:#7c3aed}.tree .node.point .kind-icon{background:#059669;border-radius:999px}.tree .node.cross .kind-icon{background:#d97706;border-radius:0;clip-path:polygon(50% 0,100% 50%,50% 100%,0 50%)}.tree .node.ref .kind-icon{background:transparent;border:0;color:#64748b;font-size:12px;font-weight:700;height:auto;line-height:1;transform:none;width:auto}.tree .node.ref .kind-icon::before{content:'→'}.tree .node-label{cursor:pointer}.tree .node-label:hover{color:#453ab5}.tree .count{cursor:pointer}.tree-children{border-left:1px solid var(--line);margin-left:0;padding-left:18px;width:100%}.tree-children>.node,.tree-children>.tree-branch{margin-left:0!important;width:calc(100% + 18px)}@media(max-width:850px){aside{position:static;width:auto}#sidebar-resizer{display:none}main{grid-column:1;grid-row:3}}
.tree-children{border-left:0;margin:0;padding:0;width:100%}.tree-children>.node,.tree-children>.tree-branch{margin-left:0!important;width:100%}.tree .node{padding-left:calc(9px + var(--tree-indent, 0px))}.tree .node-label{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.array-bin-group{cursor:pointer}.array-bin-group:hover{background:#fafaff}.array-bin-member{background:#fafbfe}.array-bin-member td:first-child{padding-left:31px;position:relative}.array-bin-member td:first-child::before{color:var(--muted);content:'↳';left:12px;position:absolute}.member-view{border-top:1px solid var(--line);padding:14px 0}.member-view:first-child{border-top:0;padding-top:0}.member-view:last-child{padding-bottom:0}.member-view p{margin:6px 0}.local-view{background:#fafaff;border:1px solid #e4e1ff;border-radius:9px;margin:10px 0;padding:13px}.local-view:first-child{margin-top:0}.local-view:last-child{margin-bottom:0}.local-view-bins{margin:11px 0}header{left:0;position:fixed;right:0;top:0;z-index:3}.proposal{display:none!important}
.tree-children{border-left:0;margin:0;padding:0;width:100%}.tree-children>.node,.tree-children>.tree-branch{margin-left:0!important;width:100%}.tree .node{padding-left:calc(9px + var(--tree-indent, 0px))}.tree .node-label{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.array-bin-group{cursor:pointer}.array-bin-group:hover{background:#fafaff}.array-bin-member{background:#fafbfe}.array-bin-member td:first-child{padding-left:31px;position:relative}.array-bin-member td:first-child::before{color:var(--muted);content:'↳';left:12px;position:absolute}.input-config>summary{cursor:pointer;list-style:none}.input-config>summary::-webkit-details-marker{display:none}.input-config>summary::before{color:var(--muted);content:'▸';font-size:13px}.input-config[open]>summary::before{content:'▾'}.input-config>summary h3{margin-right:auto}.input-config .preview-layout{background:#eef0f6;box-shadow:none;color:#4e596d;padding:6px 10px}.input-config .preview-layout:hover{background:#e2e6ef}.layout-inputs{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:4px 12px}.layout-inputs label{align-items:center;display:grid;gap:8px;grid-template-columns:max-content minmax(90px,1fr);margin:3px 0;white-space:nowrap}.layout-inputs input{min-width:0}.layout-preview>p{margin:8px 0 0}.layout-preview .layout-result{color:var(--muted)}header{left:0;position:fixed;right:0;top:0;z-index:3}.proposal{display:none!important}

.bin-canvas-panel{margin:10px 0}
.bin-canvas-panel .panel-head{gap:8px;padding:8px 12px;justify-content:flex-start;flex-wrap:wrap}
.bin-canvas-panel .panel-head h3{font-size:14px}
.bin-canvas-panel .panel-body{padding:8px 0 10px}
.bin-canvas-panel .panel-head .bin-domain-bound{align-items:center;display:inline-flex;gap:4px;margin:0;font-size:12px;font-weight:650;white-space:nowrap}
.bin-canvas-panel .panel-head .bin-domain-min,.bin-canvas-panel .panel-head .bin-domain-max{appearance:textfield;background:transparent;border:0;border-bottom:1px solid #94a3b8;border-radius:0;box-shadow:none;color:inherit;font:inherit;font-variant-numeric:tabular-nums;min-width:3.25em;outline:none;padding:0 1px;text-align:center;width:auto}
.bin-canvas-panel .panel-head .bin-domain-min::-webkit-inner-spin-button,.bin-canvas-panel .panel-head .bin-domain-min::-webkit-outer-spin-button,.bin-canvas-panel .panel-head .bin-domain-max::-webkit-inner-spin-button,.bin-canvas-panel .panel-head .bin-domain-max::-webkit-outer-spin-button{appearance:none;margin:0}
.bin-canvas-panel .panel-head .bin-reset{background:#fff;box-shadow:none;font-size:12px;margin-left:auto;padding:3px 8px}
.bin-canvas-panel .panel-head .bin-add-default{background:#fff;box-shadow:none;font-size:12px;padding:3px 8px}
html.bin-dragging,html.bin-dragging *{cursor:var(--bin-drag-cursor,grabbing)!important;user-select:none!important}
.bin-canvas{display:grid;gap:8px;user-select:none}
.bin-axis{background:#f4f7fb;border:1px solid var(--line);border-radius:10px;margin:0 12px;outline:none;overflow:hidden;padding:8px;position:relative;touch-action:none}
.bin-axis:focus{box-shadow:inset 0 0 0 2px #ddd7ff}
.bin-lanes{display:grid;gap:4px}
.bin-row{align-items:stretch;display:grid;gap:8px;grid-template-columns:7.5em minmax(0,1fr)}
.bin-row-name{align-self:start;color:#3d4a60;cursor:pointer;font-size:11px;font-weight:750;min-width:0;overflow:hidden;padding:3px 0;text-overflow:ellipsis;white-space:nowrap}
.bin-lane{background:repeating-linear-gradient(45deg,#cfd8e6 0 5px,#e8eef6 5px 10px);border:1px dashed #9aabc0;border-radius:7px;display:grid;gap:2px;min-height:20px;position:relative}
.bin-track{display:grid;gap:2px;grid-template-columns:repeat(var(--cells),minmax(0,1fr));min-height:18px;position:relative}
.bin-clip{align-items:center;border-radius:6px;color:#fff;cursor:pointer;display:flex;font-size:11px;font-weight:750;height:18px;justify-content:center;min-width:0;position:relative;box-shadow:inset 0 0 0 1px rgba(255,255,255,.18)}
.bin-clip.is-default{background:#b45309}
.bin-clip.is-automatic{opacity:.92}
.bin-clip.is-selected{box-shadow:0 0 0 2px #fff,0 0 0 4px var(--accent);z-index:2}
.bin-clip.is-selected.is-editable{cursor:grab}
.bin-clip-label{overflow:hidden;padding:0 6px;pointer-events:none;text-overflow:ellipsis;white-space:nowrap}
.bin-handle{background:rgba(255,255,255,.85);border-radius:99px;bottom:2px;cursor:ew-resize;display:none;position:absolute;top:2px;width:5px}
.bin-handle-lo{left:3px}
.bin-handle-hi{right:3px}
.bin-handle.is-armed{background:#fff;box-shadow:0 0 0 2px var(--accent);width:8px}
.bin-clip.is-selected.is-editable .bin-handle{display:block}
.bin-delete-ghost{align-items:center;border:2px solid #dc2626;border-radius:6px;box-shadow:0 8px 20px rgba(127,29,29,.28);color:#fff;display:none;font-size:11px;font-weight:800;justify-content:center;opacity:.92;overflow:visible;pointer-events:none;position:fixed;z-index:1000}
.bin-delete-ghost.is-visible{display:flex}
.bin-delete-ghost span{background:#dc2626;border-radius:5px;box-shadow:0 3px 10px rgba(127,29,29,.25);left:50%;padding:4px 7px;position:absolute;top:-31px;transform:translateX(-50%);white-space:nowrap}
.bin-canvas.is-wide .bin-lane{display:grid;height:auto}
.bin-canvas.is-wide .bin-track{display:block;height:18px;position:relative;width:100%}
.bin-canvas.is-wide .bin-clip{min-width:8px;position:absolute;top:1px}
.bin-cells{display:grid;gap:2px;grid-template-columns:repeat(var(--cells),minmax(0,1fr));margin-left:calc(7.5em + 8px);margin-top:6px}
.bin-ticks{color:var(--muted);height:16px;margin-left:calc(7.5em + 8px);margin-top:8px;position:relative}
.bin-ticks span{font-size:11px;position:absolute;transform:translateX(-50%)}
.bin-ticks span:first-child{transform:none}
.bin-ticks span:last-child{transform:translateX(-100%)}
.bin-cell{background:#d5deea;border:0;border-radius:6px;box-shadow:inset 0 0 0 1px #9aabc0;color:#4a5b70;cursor:default;font-size:11px;font-weight:700;min-height:26px;padding:0}
.bin-cell.is-empty,.bin-cell.is-automatic,.bin-cell.is-addable{cursor:pointer}
.bin-cell.is-normal{background:color-mix(in srgb, var(--cell) 16%, white);box-shadow:none;color:#7b8494}
.bin-cell.is-ignore{background:repeating-linear-gradient(135deg,#e6e9ef,#e6e9ef 4px,#d8dce6 4px,#d8dce6 8px);box-shadow:none;color:#8a93a3}
.bin-cell.is-illegal{background:#f8eaea;box-shadow:none;color:#c48b8b}
.bin-cell.is-idle{filter:saturate(.35);opacity:.5}
.bin-cell.is-empty{background:repeating-linear-gradient(45deg,#c5d0de 0 6px,#e7edf5 6px 12px);box-shadow:inset 0 0 0 1px #8b9cb3;color:#3d4f66}
.bin-cell.is-default{background:repeating-linear-gradient(90deg,transparent,transparent 6px,rgba(180,83,9,.16) 6px,rgba(180,83,9,.16) 7px),#ffe3b3;box-shadow:inset 0 0 0 1px #d9a65d;color:#8a4b08}
.bin-cell.is-overlap:not(.is-selected-range){box-shadow:inset 0 0 0 1px rgba(23,32,51,.12)}
.bin-cell.is-selected-range{background:color-mix(in srgb, var(--cell) 42%, white);box-shadow:inset 0 0 0 2px var(--cell);color:var(--cell);filter:none;font-weight:800;opacity:1}
.bin-cell.is-selected-range.is-illegal{background:var(--bad-soft);box-shadow:inset 0 0 0 2px var(--bad);color:var(--bad)}
.bin-cell.is-selected-range.is-ignore{box-shadow:inset 0 0 0 2px #64748b;color:#475569;opacity:1}
.bin-editor{border-top:1px solid var(--line);display:grid;gap:6px;padding:10px 12px 0}
.bin-editor-title{color:var(--muted);font-size:12px;font-weight:650}
.bin-editor-rule{background:var(--line);height:1px;margin:2px 28px 4px}
.bin-editor-main{align-items:center;display:flex;flex-wrap:wrap;gap:6px}
.bin-swatch{border-radius:999px;height:8px;width:8px}
.bin-editor-ranges{display:grid;gap:4px}
.bin-editor-range{align-items:center;display:grid;gap:6px;grid-template-columns:max-content max-content minmax(0,1fr) 4.5em}
.bin-selector-row{line-height:1.8}
.bin-selector-syntax{color:#334155;font-family:inherit;font-size:13px;white-space:normal}
.bin-fragment-token{appearance:none;background:transparent;border:0;color:inherit;cursor:text;font:inherit;line-height:inherit;padding:0 3px}
button.bin-fragment-token:hover,button.bin-fragment-token:focus{background:transparent;box-shadow:none;color:inherit;outline:1px dotted #94a3b8;outline-offset:1px}
.bin-fragment-token.is-editing{color:#4c1d95;font-weight:700;outline:1px solid #c4b5fd;outline-offset:1px}
.bin-fragment-hint{margin:0}
.bin-name-input{appearance:none;background:transparent;border:0;border-bottom:1px solid #94a3b8;border-radius:0;box-shadow:none;color:inherit;font-weight:750;min-width:5em;outline:none;padding:0 1px;width:auto}
.bin-name-text{font-weight:750;padding:4px 2px}
.bin-editor label{align-items:center;display:inline-flex;gap:4px;margin:0}
.bin-editor label.is-armed-edge{color:#453ab5;font-weight:750}
.bin-editor input.bin-lo,.bin-editor input.bin-hi,.bin-editor input.bin-value{appearance:textfield;background:transparent;border:0;border-bottom:1px solid #94a3b8;border-radius:0;box-shadow:none;color:inherit;font:inherit;font-variant-numeric:tabular-nums;min-width:3.25em;outline:none;padding:0 1px;text-align:center;width:3.25em}
.bin-editor input.bin-lo::-webkit-inner-spin-button,.bin-editor input.bin-lo::-webkit-outer-spin-button,.bin-editor input.bin-hi::-webkit-inner-spin-button,.bin-editor input.bin-hi::-webkit-outer-spin-button,.bin-editor input.bin-value::-webkit-inner-spin-button,.bin-editor input.bin-value::-webkit-outer-spin-button{appearance:none;margin:0}
.bin-enum-lo,.bin-enum-hi,.bin-enum-value{min-width:10em;width:auto}
.bin-enum-values{display:flex;flex-wrap:wrap;gap:6px}
.bin-kind-slot,.bin-array-slot{align-items:stretch;background:#e8ebf2;border-radius:999px;display:flex;height:28px;isolation:isolate;padding:2px}
.bin-kind-slot .bin-kind,.bin-array-mode,.bin-array-count-choice{align-items:center;background:transparent;border:0;border-radius:999px;box-shadow:none;color:#334155;display:inline-flex;font-size:11px;font-weight:700;gap:2px;height:24px;min-height:24px;padding:3px 8px;position:relative;white-space:nowrap}
.bin-kind-slot .bin-kind,button.bin-array-mode{cursor:pointer}
.bin-kind-slot .bin-kind:hover,button.bin-array-mode:hover,.bin-array-count-choice:hover{background:#dcd9ff;box-shadow:none;color:#332c86}
.bin-kind-slot .bin-kind[aria-pressed='true'],.bin-array-mode.is-selected,.bin-array-count-choice.is-selected{background:#fff;box-shadow:0 1px 3px rgba(23,32,51,.12);color:#334155}
.bin-kind-slot .bin-kind[data-kind='normal'][aria-pressed='true']{color:var(--good)}
.bin-kind-slot .bin-kind[data-kind='ignore'][aria-pressed='true']{color:#687284}
.bin-kind-slot .bin-kind[data-kind='illegal'][aria-pressed='true']{color:var(--bad)}
.bin-delete{border-color:#f0c7c7;color:var(--bad);font-size:12px;margin-left:auto;padding:4px 8px}
.bin-array-count-choice{padding:0 8px 0 0}
.bin-array-count-choice .bin-array-mode{height:24px;min-height:24px;padding-right:2px}
.bin-array-count-choice .bin-array-mode.is-selected{background:transparent;box-shadow:none;color:inherit}
.bin-array-count{appearance:textfield;background:transparent;border:0;border-bottom:1px solid #94a3b8;border-radius:0;color:inherit;font:inherit;min-width:1.4em;outline:none;padding:0 1px;text-align:center}
.bin-array-count:focus,.bin-name-input:focus,.bin-editor input.bin-lo:focus,.bin-editor input.bin-hi:focus,.bin-editor input.bin-value:focus,.bin-canvas-panel .panel-head .bin-domain-min:focus,.bin-canvas-panel .panel-head .bin-domain-max:focus{border-bottom-color:var(--accent)}
.bin-array-count:disabled{border-bottom-color:transparent;opacity:.55}
.bin-row{grid-template-columns:7.5em minmax(0,1fr);position:relative}.bin-row.has-delete:hover{grid-template-columns:calc(7.5em - 30px) 22px minmax(0,1fr)}.bin-row-name{align-items:center;display:flex;gap:3px}.bin-row-name-text{min-width:0;overflow:hidden;text-overflow:ellipsis}.bin-row-handle,.bin-row-delete{background:transparent;border:0;box-shadow:none;color:#7d8798;cursor:grab;font-size:15px;font-weight:700;line-height:1;padding:1px 2px}.bin-row-handle{display:inline-flex;opacity:0;transition:opacity .12s,color .12s}.bin-row-delete{align-self:start;cursor:pointer;display:none;font-size:17px;justify-self:center;width:20px}.bin-row:hover{background:#e5e1ff;border-radius:7px}.bin-row:hover .bin-lane{border-color:#7a70da;box-shadow:inset 0 0 0 1px #bcb6ff}.bin-row:hover .bin-row-handle{opacity:1}.bin-row.has-delete:hover .bin-row-delete{display:inline-flex}.bin-row-handle:hover,.bin-row-delete:hover{background:transparent;box-shadow:none;color:#453ab5}.bin-row-delete:hover{color:var(--bad)}.bin-row.is-drop-target .bin-lane{box-shadow:0 0 0 2px var(--accent)}.bin-scale-row{align-items:center;display:grid;gap:8px;grid-template-columns:7.5em minmax(0,1fr);margin-top:6px}.bin-scale-row .bin-new-actions{align-items:center;display:flex;gap:4px;grid-column:1;padding-left:23px}.bin-new-icon{align-items:center;background:var(--card);border:1px solid var(--line);border-radius:6px;box-shadow:0 1px 2px rgba(24,35,62,.06);color:#566176;cursor:pointer;display:inline-flex;font-size:15px;font-weight:750;height:24px;justify-content:center;line-height:1;padding:0;width:24px}.bin-new-icon:hover{background:var(--accent-soft);border-color:#cfcaf9;box-shadow:0 1px 3px rgba(70,58,180,.12);color:#453ab5}.bin-new-icon:disabled{background:#f3f5f8;border-color:#e4e8ef;color:#a7afbb;cursor:not-allowed;box-shadow:none}.bin-scale-row .bin-cells,.bin-scale-row .bin-ticks{grid-column:2;margin:0}.bin-domain-overview{align-items:center;display:inline-flex;gap:8px}.bin-domain-overview .bin-domain-stats{margin-right:0}.bin-domain-stats{color:var(--muted);font-size:12px;white-space:nowrap}.bins-panel>summary{align-items:center;cursor:pointer;display:flex;font-size:14px;font-weight:750;gap:8px;list-style:none;padding:12px 16px}.bins-panel>summary::-webkit-details-marker{display:none}.bins-panel>summary::before{border:solid #69758b;border-width:0 2px 2px 0;content:"";display:inline-block;height:8px;transform:rotate(-45deg);transition:transform .14s ease;width:8px}.bins-panel[open]>summary::before{transform:rotate(45deg)}.bins-panel>summary .muted{font-size:11px;font-weight:600}.bins-panel .panel-body{border-top:1px solid var(--line)}body{grid-template-rows:60px minmax(0,1fr)}header{height:60px;padding:0 24px}aside{top:60px}#sidebar-resizer{top:60px}main{padding:18px 22px 32px}.bin-kind-slot,.bin-array-slot{height:28px}
.bin-track{align-items:center;min-height:22px}.bin-canvas.is-wide .bin-track{height:22px}.bin-canvas.is-wide .bin-clip{top:2px}.bin-row{gap:8px}.bin-row.has-delete:hover{column-gap:4px;grid-template-columns:calc(7.5em - 10px) 10px minmax(0,1fr)}.bin-row-name{align-self:center;font-size:12px;gap:2px}.bin-row-handle{align-self:center;font-size:14px;padding:0}.bin-row-delete{align-self:center;font-size:16px;padding:0;width:10px}.bin-scale-row .bin-new-actions{padding-left:10px}.bin-editor-divider{color:#a0a8b6;font-weight:750}.bin-selector-label{color:var(--muted);font-size:11px;font-weight:700}.bin-selector-control{align-items:center;display:inline-flex;flex:0 0 auto;flex-wrap:wrap;gap:6px;max-width:100%}.bin-editor-main .bin-selector-syntax{font-size:12px}.bin-help{position:relative}.bin-help>summary{align-items:center;background:transparent;border:0;border-radius:999px;box-shadow:none;color:var(--muted);cursor:pointer;display:flex;height:22px;justify-content:center;list-style:none;padding:0;width:22px}.bin-help>summary::-webkit-details-marker{display:none}.bin-help>summary:hover,.bin-help[open]>summary{background:var(--accent-soft);color:#453ab5}.bin-help-popover{background:#fff;border:1px solid var(--line);border-radius:8px;box-shadow:var(--shadow);color:#435066;display:none;font-size:12px;font-weight:500;line-height:1.5;padding:9px 11px;position:absolute;right:0;top:27px;width:300px;z-index:6}.bin-help>summary:hover + .bin-help-popover,.bin-help[open] .bin-help-popover{display:block}main{align-self:stretch;margin:0 auto}.bin-header-actions{align-items:center;display:flex;gap:5px;margin-left:auto}.bin-history{align-items:center;background:transparent;border:1px solid transparent;border-radius:6px;box-shadow:none;color:#566176;display:inline-flex;height:25px;justify-content:center;line-height:1;padding:0;width:25px}.bin-history svg{fill:none;height:17px;stroke:currentColor;stroke-linecap:round;stroke-linejoin:round;stroke-width:1.8;width:17px}.bin-history:hover:not(:disabled){background:var(--accent-soft);border-color:#d8d3ff;box-shadow:none;color:#453ab5}.bin-history:disabled{color:#c1c8d3;cursor:not-allowed}.bin-header-actions .bin-reset{margin-left:0}
.transition-domain{display:grid;gap:7px;padding:4px 0}.transition-domain>p{margin:0 0 2px}.transition-selector{align-items:center;background:#f8f9fc;border:1px solid #e6eaf1;border-radius:7px;display:grid;gap:10px;grid-template-columns:minmax(7.5em,max-content) minmax(0,1fr);padding:7px 9px}.transition-selector code{color:#334155;overflow-wrap:anywhere}
.transition-domain{display:grid;gap:9px;padding:4px 0}.transition-domain>p{margin:0 0 2px}.transition-bin{background:#f8f9fc;border:1px solid #e2e6ef;border-radius:9px;padding:9px}.transition-bin-head{align-items:center;display:flex;gap:8px}.transition-bin-name{background:transparent;border:0;border-bottom:1px solid transparent;border-radius:0;font-weight:750;max-width:18em;padding:2px 3px}.transition-bin-name:focus{border-bottom-color:var(--accent);outline:0}.transition-bin-tools{display:flex;gap:3px;margin-left:auto}.transition-bin-tools button,.transition-step-delete,.transition-repeat-toggle{background:transparent;border:0;border-radius:5px;box-shadow:none;color:#69758b;font-size:13px;line-height:1;padding:4px 5px}.transition-bin-tools button:hover:not(:disabled),.transition-step-delete:hover:not(:disabled),.transition-repeat-toggle:hover{background:var(--accent-soft);color:#453ab5}.transition-bin-tools button:disabled,.transition-step-delete:disabled{color:#c4cad5;cursor:not-allowed}.transition-steps{align-items:center;display:flex;flex-wrap:wrap;gap:5px;margin:9px 0}.transition-step{align-items:center;display:inline-flex;gap:4px}.transition-step-text{background:#fff;border:1px solid #dbe1ec;border-radius:6px;min-width:5.5em;padding:4px 6px;width:9em}.transition-repeat{align-items:center;background:#eeecff;border-radius:5px;color:#5145a5;display:inline-flex;font:12px ui-monospace,SFMono-Regular,Menlo,monospace;gap:1px;padding:3px 4px}.transition-repeat input{background:transparent;border:0;border-bottom:1px solid #a9a2e8;border-radius:0;color:inherit;font:inherit;min-width:1.5em;padding:0;text-align:center;width:2.6em}.transition-arrow{color:#7771cb;font-size:17px;margin:0 1px}.transition-step-add,.transition-bin-add{align-self:start;background:#fff;border:1px dashed #bbb7ec;box-shadow:none;color:#5145a5;font-size:12px;padding:5px 8px}.transition-step-add:hover,.transition-bin-add:hover{background:var(--accent-soft);color:#453ab5}.transition-bin-add{align-self:stretch}
.transition-sequence-host{border-top:1px solid var(--line);margin-top:14px;padding-top:13px}.transition-domain{gap:10px}.transition-editor>p::before{background:#eeecff;border-radius:999px;color:#5145a5;content:"TRANSITION";font-size:10px;font-weight:800;letter-spacing:.08em;margin-right:7px;padding:3px 6px}.transition-bin{background:linear-gradient(105deg,#fbfbff,#f5f4ff);border-color:#dbd8fb;box-shadow:0 1px 3px rgba(70,58,180,.04);padding:10px 11px}.transition-bin-head{border-bottom:1px solid #e6e4fb;padding:0 0 8px}.transition-bin-name{font-size:13px}.transition-bin-label{background:#eeecff;border-radius:999px;color:#6256b2;font-size:10px;font-weight:800;letter-spacing:.05em;padding:2px 6px}.transition-steps{gap:7px;margin:12px 1px}.transition-step{gap:5px}.transition-step-chip{background:#fff;border:1px solid #cfcaf9;border-radius:7px;box-shadow:0 1px 2px rgba(70,58,180,.08);color:#383270;font:12px ui-monospace,SFMono-Regular,Menlo,monospace;max-width:18em;overflow:hidden;padding:5px 8px;text-overflow:ellipsis;white-space:nowrap}.transition-step-chip:hover{background:#eeecff;border-color:#9d94ed;color:#453ab5}.transition-step-text{border-color:#8f86e6;box-shadow:0 0 0 2px rgba(98,86,232,.12);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;height:28px}.transition-repeat{box-shadow:inset 0 0 0 1px #d5d0ff}.transition-step-tools{display:inline-flex;gap:1px;opacity:0;transition:opacity .12s}.transition-step:hover .transition-step-tools,.transition-step:focus-within .transition-step-tools{opacity:1}.transition-arrow{color:#8077d1;font-weight:700}.transition-step-add{border-radius:6px}.transition-bin-add{border-radius:8px;padding:7px 9px}.transition-bin-tools{opacity:0;transition:opacity .12s}.transition-bin:hover .transition-bin-tools,.transition-bin:focus-within .transition-bin-tools{opacity:1}
.transition-bin-row .transition-lane{align-items:center;background:#faf9ff;display:flex;flex-wrap:wrap;gap:6px;min-height:34px;padding:5px 8px}.transition-bin-row .transition-step-chip{max-width:13em}.transition-bin-row.is-active .transition-lane,.transition-bin-row.is-selected .transition-lane{border-color:#8f86e6;box-shadow:inset 0 0 0 1px #c9c3ff}.transition-active-editor{margin-bottom:8px}.transition-active-track{background:#f7f6ff;border:1px solid #dcd8fb;border-radius:7px;height:18px;overflow:hidden;position:relative}.transition-active-track span{background:#7c3aed;border-radius:4px;height:100%;position:absolute;top:0}.bin-cell.is-transition{background:#e9ddff;color:#4c1d95}
.transition-bin-row .transition-lane{min-height:22px;padding:0 8px}.transition-step-selector{color:#334155;cursor:text;font:inherit;line-height:22px;padding:0 3px}.transition-step-selector:hover{color:#453ab5}.transition-step-selector .bin-selector-syntax{font-size:13px}.transition-active-track{flex-basis:100%;margin:3px 0 4px}
.bin-editor:empty{display:none}.transition-step-selector input.bin-lo,.transition-step-selector input.bin-hi,.transition-step-selector input.bin-value{appearance:textfield;background:transparent;border:0;border-bottom:1px solid #94a3b8;border-radius:0;box-shadow:none;color:inherit;font:inherit;font-variant-numeric:tabular-nums;min-width:3.25em;outline:none;padding:0 1px;text-align:center;width:3.25em}.transition-step-selector input.bin-lo::-webkit-inner-spin-button,.transition-step-selector input.bin-lo::-webkit-outer-spin-button,.transition-step-selector input.bin-hi::-webkit-inner-spin-button,.transition-step-selector input.bin-hi::-webkit-outer-spin-button,.transition-step-selector input.bin-value::-webkit-inner-spin-button,.transition-step-selector input.bin-value::-webkit-outer-spin-button{appearance:none;margin:0}.transition-step-selector input.bin-lo:focus,.transition-step-selector input.bin-hi:focus,.transition-step-selector input.bin-value:focus{border-bottom-color:var(--accent)}
.transition-bin-row .transition-active-track{background:repeating-linear-gradient(45deg,#cfd8e6 0 5px,#e8eef6 5px 10px);border:1px dashed #9aabc0;border-radius:7px;grid-column:2;height:22px;margin:0;overflow:visible;position:relative}.transition-bin-row .transition-active-track span{background:unset;border-radius:unset;height:auto;position:static;top:auto}.transition-bin-row .transition-active-track .bin-clip{position:relative}.bin-canvas.is-wide .transition-bin-row .transition-active-track .bin-clip{position:absolute}
.transition-bin-row .transition-active-track .bin-handle{background:rgba(255,255,255,.85);border-radius:99px;bottom:2px;cursor:ew-resize;display:none;position:absolute;top:2px;width:5px}.transition-bin-row .transition-active-track .bin-handle-lo{left:3px}.transition-bin-row .transition-active-track .bin-handle-hi{right:3px}.transition-bin-row .transition-active-track .bin-clip.is-selected .bin-handle{display:block}
</style></head><body>
<header><div class='brand'><div><h1>覆盖率工作台</h1><p>SvTypes · 设计期覆盖率浏览器</p></div></div><div class='header-actions'><input id='modules' class='status' placeholder='模块' title='模块入口'><button id='scan' class='icon-btn' title='扫描 / 刷新' aria-label='扫描 / 刷新'><svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round'><path d='M21 12a9 9 0 1 1-2.6-6.3'/><polyline points='21 3 21 9 15 9'/></svg></button><button id='diagnostics-btn' class='icon-btn diag-btn hidden' title='诊断信息' aria-label='诊断信息'><svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round'><path d='M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z'/><line x1='12' y1='9' x2='12' y2='13'/><line x1='12' y1='17' x2='12.01' y2='17'/></svg><span id='diagnostics-count' class='badge'>0</span></button><div id='status' class='status'>尚未扫描设计</div></div></header><div id='diagnostics-panel' class='diag-panel' role='dialog' aria-label='诊断信息'><div class='diag-panel-head'><span>诊断信息</span><button id='close-diagnostics' type='button' aria-label='关闭'>×</button></div><div id='diagnostics'></div></div>
<aside><div class='sidebar-chrome'><div class='sidebar-head'><div class='section-label'>覆盖率结构</div><button id='toggle-filter' class='sidebar-icon-btn' type='button' title='查找覆盖率' aria-label='查找覆盖率' aria-expanded='false'><svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2.2' stroke-linecap='round' stroke-linejoin='round'><circle cx='11' cy='11' r='7'/><line x1='21' y1='21' x2='16.65' y2='16.65'/></svg></button></div><div id='filter-panel' class='filter-panel hidden'><input id='filter' placeholder='Filter groups, points, bins'></div></div><div class='tree-scroll'><div id='tree' class='tree'></div></div></aside><div id='sidebar-resizer' role='separator' aria-label='Resize sidebar' aria-orientation='vertical'></div>
<main><section id='detail'><div class='welcome'><h2>按覆盖意图浏览</h2><p class='muted'>扫描设计后，选择 covergroup、coverpoint 或 cross。工作台直接说明覆盖率结构，而不暴露原始 IR。</p></div></section></main>
<style>.dsl-card summary{cursor:pointer}.dsl-card .definition{background:#eff1f5;color:#4c4f69;margin:0;white-space:pre-wrap}.dsl-keyword{color:#8839ef;font-weight:700}.dsl-class-name,.dsl-type,.dsl-member{color:#df8e1d;font-style:italic}.dsl-argument,.dsl-self{color:#d20f39;font-style:italic}.dsl-bin-family{color:#4c4f69;font-style:italic}.dsl-number{color:#fe640b}.dsl-operator{color:#179299}.dsl-punctuation{color:#7c7f93}.transition-bin-editor{margin-top:2px}</style>
<script src='/bin-canvas.js?v=88'></script>
<script>
let state=null,selected=null,selectedId=null,canvasBins=null;const layoutDrafts=new Map(),layoutResults=new Map();const q=s=>document.querySelector(s);const esc=x=>String(x??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));
async function api(path,method='GET',payload=null){const response=await fetch(path,{method,headers:payload?{'Content-Type':'application/json'}:{},body:payload?JSON.stringify(payload):null});const value=await response.json();if(!response.ok)throw Error(value.error||`HTTP ${response.status}`);return value;}
function logicText(value){const logic=value?.$logic;if(!logic)return null;let bits='';for(let i=logic.width-1;i>=0;i--){const bit=1<<i;bits+=(logic.z&bit)?'z':(logic.x&bit)?'x':(logic.value&bit)?'1':'0';}return `${logic.width}'b${bits}`;}
function expr(v){if(v==null)return '—';if(typeof v!=='object')return String(v);if(Array.isArray(v))return v.map(expr).join(', ');const k=v.kind;if(k==='name'||k==='parameter_ref')return v.name;if(k==='attribute')return expr(v.base)+'.'+v.name;if(k==='subscript')return `${expr(v.base)}[${expr(v.index)}]`;if(k==='field'||k==='container_values'||k==='assoc_values')return v.path;if(k==='slot')return `${v.path}[${v.index}]`;if(k==='is_null')return `${v.path} == null`;if(k==='slot_is_null')return `${v.path}[${v.index}] == null`;if(k==='container_nullness'||k==='assoc_nullness')return `${v.path} 的 nullness`;if(k==='enum_literal')return `${v.type_name}.${v.member}`;if(k==='constant'){const logic=logicText(v.value);return logic??(typeof v.value==='string'?`"${v.value}"`:String(v.value));}if(k==='range')return `[${expr(v.lower)}:${expr(v.upper)}]`;if(k==='values')return v.items.map(expr).join(', ');if(k==='repeat')return `${expr(v.term)}[*${v.minimum}:${v.maximum}]`;if(k==='unary')return v.operator+' '+expr(v.operand);if(k==='binary')return `${expr(v.left)} ${v.operator} ${expr(v.right)}`;if(k==='boolean')return v.values.map(expr).join(` ${v.operator} `);if(k==='compare')return `${expr(v.left)} ${v.operators.map((op,i)=>op+' '+expr(v.comparators[i])).join(' ')}`;if(k==='if')return `${expr(v.then)} if ${expr(v.condition)} else ${expr(v.else)}`;if(k==='array_split')return expr(v.selector);if(k==='cross_bin_refs')return v.items.map(x=>x.point+'.'+x.bin).join(' × ');if(k==='cross_queue_call')return v.function+'('+v.args.map(expr).join(', ')+')';if(k==='cross_queue_values')return v.items.map(expr).join(', ');if(k==='transition')return (v.items||[]).map(expr).join(' → ');return '已定义选择器';}
function selector(bin){if(bin.kind==='default')return '所有未覆盖值';if(bin.display_selector)return bin.display_selector;if(bin.kind==='transition'&&bin.selector?.kind==='values')return bin.selector.items.map(expr).join(' → ');return expr(bin.selector);}
function kind(value){return `<span class='kind ${esc(value)}'>${esc(value)}</span>`;}
function shapeLabel(name,shapes){return shapes?.length?`${name}(${shapes.map(x=>`${x.name}: ${x.type_name}`).join(', ')})`:name;}
function treeShapeLabel(name,shapes,key,groupKey){const values=layoutDrafts.get(key)?.arguments||layoutDrafts.get(groupKey)?.arguments||{};return shapes?.length?`${name}(${shapes.map(x=>x.name in values?`${x.name}=${values[x.name]}`:`${x.name}: ${x.type_name}`).join(', ')})`:name;}
function treeBinCount(group,item){const layout=layoutResults.get(`${group.covergroup_type_id}::${item.semantic_id}`)||layoutResults.get(`${group.covergroup_type_id}::group`),collection=item.members?layout?.crosses:layout?.points,materialized=collection?.find(value=>value.name===item.name);return materialized?.bins?.length??item.bins.length;}
function ids(node){return `<dl class='key-value'><dt>路径</dt><dd>${esc(node.semantic_id||node.covergroup_type_id||'—')}</dd>${node.declaration_semantic_digest?`<dt>摘要</dt><dd>${esc(node.declaration_semantic_digest)}</dd>`:''}</dl>${node.source?`<pre class='dsl-source hidden'>${esc(node.source)}</pre>`:''}`;}
function arrayBinBase(name){return name.match(/^(.*)\\[-?\\d+\\]$/)?.[1]||null;}
function arrayBinIndex(name){return Number(name.match(/\\[(-?\\d+)\\]$/)?.[1]);}
function binsTable(items){if(!items?.length)return `<p class='muted'>没有显式声明的 bins。</p>`;let rows='',i=0;while(i<items.length){const bin=items[i],base=arrayBinBase(bin.name);if(base){const group=[];while(i<items.length&&arrayBinBase(items[i].name)===base)group.push(items[i++]);group.sort((left,right)=>arrayBinIndex(left.name)-arrayBinIndex(right.name));rows+=`<tr class='array-bin-group' data-array-bin-group='${esc(base)}'><td class='bin-name'>${esc(base)} <span class='muted'>[${group.length}]</span></td><td colspan='2'><span class='array-bin-action muted'>点击展开</span></td></tr>`+group.map(b=>`<tr class='array-bin-member hidden' data-array-bin-group='${esc(base)}'><td class='bin-name' title='${esc(b.semantic_id)}'>${esc(b.name)}</td><td>${kind(b.kind)}</td><td><code>${esc(selector(b))}</code></td></tr>`).join('');continue;}rows+=`<tr><td class='bin-name' title='${esc(bin.semantic_id)}'>${esc(bin.name)}</td><td>${kind(bin.kind)}</td><td><code>${esc(selector(bin))}</code></td></tr>`;i++;}return `<div class='table-wrap'><table><thead><tr><th>Bin</th><th>分类</th><th>值 / 选择器</th></tr></thead><tbody>${rows}</tbody></table></div>`;}
function optionText(value){return value&&typeof value==='object'?JSON.stringify(value):String(value);}
function options(options){const rows=Object.entries(options||{});return rows.length?`<div class='chips'>${rows.map(([k,v])=>`<span class='chip'>${esc(k)} = ${esc(optionText(v))}</span>`).join('')}</div>`:`<span class='muted'>默认选项</span>`;}
function leaf(parent,label,id,cls='',icon='•',count='',depth=0){const n=document.createElement('button');n.className='node '+cls;n.dataset.id=id;n.style.setProperty('--tree-indent',`${depth*18}px`);n.innerHTML=`<span class='kind-icon' aria-hidden='true'></span><span class='node-label'>${esc(label)}</span>${count!==''?`<span class='count'>${count}</span>`:''}`;n.onclick=()=>select(id);parent.append(n);}
function branch(parent,label,id,cls,icon,count,depth,children){const detail=document.createElement('details');detail.className='tree-branch '+cls;detail.open=true;const summary=document.createElement('summary');summary.className='node '+cls;summary.dataset.id=id;summary.style.setProperty('--tree-indent',`${depth*18}px`);summary.innerHTML=`<span class='kind-icon' title='展开 / 折叠' aria-hidden='true'></span><span class='node-label'>${esc(label)}</span>${count!==''?`<span class='count'>${count}</span>`:''}`;summary.addEventListener('click',e=>{e.preventDefault();if(e.target.closest('.kind-icon')){detail.open=!detail.open;return;}select(id);});detail.append(summary);const nested=document.createElement('div');nested.className='tree-children';children(nested);detail.append(nested);parent.append(detail);}
function renderDiagnostics(){const items=state?.diagnostics||[],btn=q('#diagnostics-btn'),panel=q('#diagnostics-panel'),list=q('#diagnostics');list.innerHTML='';if(!items.length){btn.classList.add('hidden');panel.classList.remove('open');return;}btn.classList.remove('hidden');q('#diagnostics-count').textContent=String(items.length);for(const d of items){const e=document.createElement('div');e.className='diagnostic';e.textContent=`${d.code}: ${d.message}`;list.append(e);}}function render(){q('#tree').innerHTML='';if(!state?.catalog){renderDiagnostics();return;}q('#status').textContent=`${state.catalog.groups.length} covergroups`;renderDiagnostics();const groups=new Map(state.catalog.groups.map(g=>[g.covergroup_type_id,g]));const types=new Map((state.catalog.type_hierarchy?.nodes||[]).map(t=>[t.type_name,t]));const rootSet=new Set(state.catalog.type_hierarchy?.roots||[]);const renderGroup=(parent,g,depth)=>{const groupKey=`${g.covergroup_type_id}::group`;branch(parent,treeShapeLabel(g.declaration_name||g.covergroup_type_id,g.cover_input_shapes,groupKey,groupKey),g.covergroup_type_id,'group','◈',g.points.length+g.crosses.length,depth,nested=>{for(const item of g.items||[...g.points,...g.crosses])leaf(nested,treeShapeLabel(item.name,item.cover_input_shapes,`${g.covergroup_type_id}::${item.semantic_id}`,groupKey),item.semantic_id,item.members?'cross':'point',item.members?'⛓':'○',treeBinCount(g,item),depth+1);});};const renderType=(parent,name,ancestors,label=null,depth=0)=>{const t=types.get(name);if(!t)return;branch(parent,label||name,'type::'+name,'type','◇',t.covergroups.length,depth,nested=>{for(const id of t.covergroups){const g=groups.get(id);if(g)renderGroup(nested,g,depth+1);}for(const r of t.references){if(ancestors.has(r.target_type))continue;if(rootSet.has(r.target_type)){leaf(nested,r.field,'type::'+r.target_type,'ref','→','',depth+1);continue;}renderType(nested,r.target_type,new Set([...ancestors,name]),`${r.field} → ${r.target_type}`,depth+1);}});};for(const root of state.catalog.type_hierarchy?.roots||[])renderType(q('#tree'),root,new Set());filter();}
function filter(){const term=q('#filter').value.toLowerCase();for(const n of q('#tree').children)n.style.display=n.textContent.toLowerCase().includes(term)?'':'none';}
function groupDetail(g){const items=g.items||[...g.points,...g.crosses],shapes=g.cover_input_shapes||[];return `<div class='hero'><div class='eyebrow'>Covergroup</div><h2>${esc(g.declaration_name||g.covergroup_type_id)}</h2><p>${esc(g.owner_type)}${g.provenance?.file?' · '+esc(g.provenance.file):''}</p>${shapes.length?`<div class='chips'>${shapes.map(x=>`<span class='chip'>${esc(x.name)}: ${esc(x.type_name)}</span>`).join('')}</div>`:''}<div class='summary'><div class='metric'><strong>${g.points.length}</strong><span>coverpoints</span></div><div class='metric'><strong>${g.crosses.length}</strong><span>crosses</span></div><div class='metric'><strong>${g.points.reduce((n,p)=>n+p.bins.length,0)+g.crosses.reduce((n,c)=>n+c.bins.length,0)}</strong><span>declared bins</span></div></div></div><div class='two-col'><div class='panel'><div class='panel-head'><h3>覆盖项</h3></div><div class='panel-body'><div class='table-wrap'><table><thead><tr><th>Item</th><th>Kind</th><th>覆盖来源</th><th>Bins</th></tr></thead><tbody>${items.map(item=>`<tr class='bin-row coverage-row' data-id='${esc(item.semantic_id)}'><td class='bin-name'>${esc(item.name)}</td><td>${item.members?'Cross':'Coverpoint'}</td><td>${item.members?(item.members||[]).map(x=>`<span class='chip'>${esc(x)}</span>`).join(' '):`<code>${esc(expr(item.expression))}</code>`}</td><td>${item.bins.length}</td></tr>`).join('')}</tbody></table></div></div></div><div><div class='panel'><div class='panel-head'><h3>Options</h3></div><div class='panel-body'>${options(g.options)}</div></div><div class='panel'><div class='panel-head'><h3>类型选项</h3></div><div class='panel-body'>${options(g.type_options)}</div></div><div class='panel'><div class='panel-head'><h3>声明标识</h3></div><div class='panel-body'>${ids(g)}</div></div></div></div>`;}
function typeDetail(t){const refs=t.references||[];return `<div class='hero'><div class='eyebrow'>静态设计类型</div><h2>${esc(t.type_name)}</h2><p>${t.exported?'模块内发现的类型。':'被引用但不在扫描模块中定义的类型。'}</p><div class='summary'><div class='metric'><strong>${t.covergroups.length}</strong><span>covergroups</span></div><div class='metric'><strong>${refs.length}</strong><span>字段引用</span></div></div></div><div class='panel'><div class='panel-head'><h3>静态字段引用</h3></div><div class='panel-body'>${refs.length?`<div class='table-wrap'><table><thead><tr><th>字段</th><th>引用类型</th><th>目标类型</th></tr></thead><tbody>${refs.map(r=>`<tr><td>${esc(r.field)}</td><td>${esc(r.kind)}</td><td><code>${esc(r.target_type)}</code></td></tr>`).join('')}</tbody></table></div>`:'<p class="muted">没有引用用户定义类型。</p>'}</div></div>`;}
function pointDetail(p,g){return `<div class='hero'><div class='eyebrow'>coverpoint · ${esc(g)}</div><h2>${esc(p.name)}</h2><p>采样 <code>${esc(expr(p.expression))}</code>${p.iff?` 仅当 <code>${esc(expr(p.iff))}</code>`:''}</p></div><div class='panel bin-canvas-panel'><div class='panel-head'><h3>值域</h3><span class='bin-domain-overview'><label class='bin-domain-bound'>从 <input class='bin-domain-min' type='number'></label><label class='bin-domain-bound'>到 <input class='bin-domain-max' type='number'></label><span class='bin-domain-stats'></span></span><span class='bin-header-actions'><button type='button' class='bin-history bin-undo' title='撤销 (⌘/Ctrl+Z)' aria-label='撤销' disabled><svg viewBox='0 0 24 24' aria-hidden='true'><path d='m9 7-5 5 5 5'/><path d='M5 12h9a6 6 0 0 1 6 6'/></svg></button><button type='button' class='bin-history bin-redo' title='重做 (⌘/Ctrl+Shift+Z)' aria-label='重做' disabled><svg viewBox='0 0 24 24' aria-hidden='true'><path d='m15 7 5 5-5 5'/><path d='M19 12h-9a6 6 0 0 0-6 6'/></svg></button><button type='button' class='secondary bin-reset' title='仅此页有效，不写回源码'>放弃所有修改</button><details class='bin-help'><summary aria-label='编辑操作说明'>ⓘ</summary><div class='bin-help-popover'>选择 bin 后可拖动片选两端来调整范围；双击 selector 中的片选可原位编辑。双击该 bin 的虚线轨道，第二次点击按住并拖动即可新增范围。按住 <kbd>Shift</kbd> 拖动片选，或选中片选后按 <kbd>Delete</kbd>，可删除该片选。</div></details></span></div><div class='panel-body'><div class='bin-canvas-host'></div></div></div><details class='panel bins-panel'><summary><span>Bins</span><span class='muted'>声明的 bins</span></summary><div class='panel-body'>${binsTable(p.bins)}</div></details><div class='two-col'><div class='panel'><div class='panel-head'><h3>Options</h3></div><div class='panel-body'>${options(p.options)}</div></div><div class='panel'><div class='panel-head'><h3>声明标识</h3></div><div class='panel-body'>${ids(p)}</div></div></div>`;}
function crossMemberViews(c){const views=c.member_views||[];if(!views.length)return '';return `<div class='panel'><div class='panel-head'><h3>有效成员定义</h3></div><div class='panel-body'><p class='muted'>以下同名局部 CovPoint 仅在此 cross 内生效，并取代外层同名 coverpoint；外层定义不参与此 cross。</p>${views.map(view=>{const point=view.point,member=view.member,anchor=`local-${esc(c.semantic_id)}-${esc(member)}`;return `<div class='member-view local-view' id='${anchor}' data-cross-member-view='${esc(member)}'><div><strong>${esc(member)}</strong></div><p>采样 <code>${esc(expr(point.expression))}</code>${point.iff?` 仅当 <code>${esc(expr(point.iff))}</code>`:''}</p><div class='local-view-bins'>${binsTable(point.bins)}</div><p><span class='muted'>Options：</span>${options(point.options)}</p></div>`;}).join('')}</div></div>`;}
function crossDetail(c,g){return `<div class='hero'><div class='eyebrow'>Cross · ${esc(g)}</div><h2>${esc(c.name)}</h2><p>Combines the effective member coverage items below${c.iff?` 仅当 <code>${esc(expr(c.iff))}</code>`:''}.</p><div class='chips'>${(c.members||[]).map(m=>`<span class='chip'>${esc(m)}</span>`).join('')}</div></div>${crossMemberViews(c)}<div class='two-col'><div class='panel'><div class='panel-head'><h3>Cross bins</h3></div><div class='panel-body'>${binsTable(c.bins)}</div></div><div><div class='panel'><div class='panel-head'><h3>Options</h3></div><div class='panel-body'>${options(c.options)}</div></div><div class='panel'><div class='panel-head'><h3>声明标识</h3></div><div class='panel-body'>${ids(c)}</div></div></div></div>`;}
function wireArrayBins(){document.querySelectorAll('.array-bin-group').forEach(row=>row.onclick=()=>{const name=row.dataset.arrayBinGroup,expanded=row.dataset.expanded==='true';document.querySelectorAll(`.array-bin-member[data-array-bin-group="${CSS.escape(name)}"]`).forEach(member=>member.classList.toggle('hidden',expanded));row.dataset.expanded=String(!expanded);row.querySelector('.array-bin-action').textContent=expanded?'点击展开':'点击折叠';});}
function wireCoverageRows(){document.querySelectorAll('.coverage-row').forEach(row=>row.onclick=()=>select(row.dataset.id));}
function applyStoredLayout(g,node){const layout=layoutResults.get(`${g.covergroup_type_id}::${node.semantic_id}`)||layoutResults.get(`${g.covergroup_type_id}::group`);if(!layout)return;const collection=node.members?layout.crosses:layout.points,materialized=collection?.find(item=>item.name===node.name),binPanel=[...document.querySelectorAll('.panel')].find(item=>['Bin 方案','Cross bins'].includes(item.querySelector('h3')?.textContent));if(materialized&&binPanel)binPanel.querySelector('.panel-body').innerHTML=binsTable(materialized.bins);if(materialized&&!node.members)canvasBins=materialized.bins;if(materialized?.member_views){for(const view of materialized.member_views){const card=document.querySelector(`.local-view[data-cross-member-view="${CSS.escape(view.name)}"]`);if(card)card.querySelector('.local-view-bins').innerHTML=binsTable(view.point.bins);}}}
function applyStoredGroupLayout(g){const layout=layoutResults.get(`${g.covergroup_type_id}::group`);if(!layout)return;let total=0;for(const item of g.items||[...g.points,...g.crosses]){const collection=item.members?layout.crosses:layout.points,materialized=collection?.find(value=>value.name===item.name);if(!materialized)continue;total+=materialized.bins.length;const row=document.querySelector(`.coverage-row[data-id="${CSS.escape(item.semantic_id)}"]`);if(row)row.cells[3].textContent=materialized.bins.length;}const metric=q('.hero .summary .metric:last-child strong');if(metric){metric.textContent=total;metric.nextElementSibling.textContent='实例化 bins';}}
function layoutPreview(g,node){if(!g.cover_input_shapes?.length&&!g.parameter_shapes?.length)return '';const key=`${g.covergroup_type_id}::${node?.semantic_id||'group'}`;return `<div class='layout-preview' data-layout-key='${esc(key)}' data-group-id='${esc(g.covergroup_type_id)}' data-node-id='${esc(node?.semantic_id||'')}'><div class='layout-inputs'></div><p><span class='layout-result muted'></span></p></div>`;}
function inputPanel(g,node){const content=layoutPreview(g,node);if(!content)return '';const title=!g.cover_input_shapes?.length&&g.parameter_shapes?.length?'Parameter 设置':'CoverInput 配置',head=`<h3>${title}</h3><button class='preview-layout'>应用配置</button>`,body=`<div class='panel-body'>${content}</div>`,expanded=node&&layoutResults.has(`${g.covergroup_type_id}::${node.semantic_id}`)?' open':'';return node?`<details class='panel input-config'${expanded}><summary class='panel-head'>${head}</summary>${body}</details>`:`<div class='panel input-config'><div class='panel-head'>${head}</div>${body}</div>`;}
function scalar(text){if(text==='true')return true;if(text==='false')return false;if(text!==''&&!Number.isNaN(Number(text)))return Number(text);return text;}
function layoutFields(panel,g,node){const key=panel.dataset.layoutKey,draft=layoutDrafts.get(key)||{},arguments=draft.arguments||{},parameters=draft.parameter_actuals||{},inputs=panel.querySelector('.layout-inputs');inputs.innerHTML=(g.cover_input_shapes||[]).map(p=>`<label><span>${esc(p.name)} <span class='muted'>${esc(p.type_name||'')}</span></span><input class='layout-argument' data-name='${esc(p.name)}' data-type='${esc(p.type_name||'')}' value='${esc(arguments[p.name]??'')}'></label>`).join('')+(g.parameter_shapes||[]).map(p=>`<label><span>${esc(p.name)} <span class='muted'>${esc(p.type_name||'')}</span></span><input class='layout-parameter' data-name='${esc(p.name)}' data-type='${esc(p.type_name||'')}' value='${esc(parameters[p.name]??p.value??'')}'></label>`).join('');inputs.querySelectorAll('input').forEach(input=>input.oninput=()=>{const next={arguments:{},parameter_actuals:{}};inputs.querySelectorAll('.layout-argument').forEach(x=>{if(x.value!=='')next.arguments[x.dataset.name]=scalar(x.value);});inputs.querySelectorAll('.layout-parameter').forEach(x=>{if(x.value!=='')next.parameter_actuals[x.dataset.name]=scalar(x.value);});layoutDrafts.set(key,next);});}
function layoutInputError(panel){for(const input of panel.querySelectorAll('.layout-argument')){const value=input.value.trim(),name=input.dataset.name,type=input.dataset.type;if(!value)return `请填写 ${name}。`;if(['int','float'].includes(type)&&!Number.isFinite(Number(value)))return `${name} 必须是 ${type} 数值。`;}return null;}
function wireLayoutPreview(g,node){document.querySelectorAll('.layout-preview').forEach(panel=>{const button=panel.closest('.input-config').querySelector('.preview-layout');layoutFields(panel,g,node);button.onclick=async event=>{event.preventDefault();event.stopPropagation();const result=panel.querySelector('.layout-result'),inputError=layoutInputError(panel);if(inputError){result.textContent=inputError;result.className='layout-result error';return;}const key=panel.dataset.layoutKey,draft=layoutDrafts.get(key)||{arguments:{},parameter_actuals:{}};try{const y=await api('/api/layout-preview','POST',{covergroup_type_id:panel.dataset.groupId,arguments:{...draft.arguments},parameter_actuals:{...draft.parameter_actuals}});layoutResults.set(key,y.layout);render();await select(selectedId);if(!node)applyStoredGroupLayout(g);}catch(e){result.textContent='无法应用配置。请检查输入值是否符合声明的类型和范围。';result.className='layout-result error';}};});}
function renderLayout(layout){const points=layout.points||[];return `<div class='layout-list'>${points.map(p=>`<div class='layout-point'><strong>${esc(p.name)} <span class='muted'>${esc(expr(p.expression))}</span></strong>${binsTable(p.bins)}</div>`).join('')||'<p class="muted">没有实例化的 coverpoint bins。</p>'}</div>`;}
async function ensureParameterDefaults(group){const key=`${group.covergroup_type_id}::group`;if(!group.parameter_shapes?.length||group.cover_input_shapes?.length||layoutResults.has(key))return;const result=await api('/api/layout-preview','POST',{covergroup_type_id:group.covergroup_type_id,arguments:{},parameter_actuals:{}});layoutResults.set(key,result.layout);}
async function loadDefaultParameterLayouts(){for(const group of state?.catalog?.groups||[]){try{await ensureParameterDefaults(group);}catch(error){console.warn('Parameter default layout unavailable',error);}}render();}
async function select(id){try{const x=await api('/api/node?id='+encodeURIComponent(id));selected=x;selectedId=id;canvasBins=null;document.querySelectorAll('.node.active').forEach(n=>n.classList.remove('active'));document.querySelector(`.node[data-id="${CSS.escape(id)}"]`)?.classList.add('active');let group=null;if(x.kind==='type')q('#detail').innerHTML=typeDetail(x.node);else if(x.kind==='covergroup'){group=x.node;await ensureParameterDefaults(group);q('#detail').innerHTML=groupDetail(group);q('.hero h2').textContent=shapeLabel(group.declaration_name||group.covergroup_type_id,group.cover_input_shapes);q('.hero .chips')?.remove();q('#detail').querySelector('.hero').insertAdjacentHTML('afterend',inputPanel(group,null));applyStoredGroupLayout(group);}else if(x.kind==='point'||x.kind==='cross'){group=state.catalog.groups.find(item=>item.covergroup_type_id===x.covergroup);if(group)await ensureParameterDefaults(group);q('#detail').innerHTML=x.kind==='point'?pointDetail(x.node,x.covergroup):crossDetail(x.node,x.covergroup);if(group){q('.hero h2').textContent=shapeLabel(x.node.name,x.node.cover_input_shapes);q('#detail').querySelector('.hero').insertAdjacentHTML('afterend',inputPanel(group,x.node));applyStoredLayout(group,x.node);}}else throw Error('Bins are displayed within their coverpoint or cross.');wireCoverageRows();if(group)wireLayoutPreview(group,x.kind==='covergroup'?null:x.node);wireBinCanvas(x.kind==='point'?x.node:null,canvasBins);wireArrayBins();}catch(e){q('#detail').innerHTML=`<p class='error'>${esc(e.message)}</p>`;}}
function setupSidebarResize(){const handle=q('#sidebar-resizer');let active=false;const resize=e=>{if(!active)return;document.documentElement.style.setProperty('--sidebar-width',`${Math.max(240,Math.min(560,e.clientX))}px`);};handle.onpointerdown=e=>{active=true;handle.classList.add('resizing');handle.setPointerCapture(e.pointerId);};handle.onpointermove=resize;handle.onpointerup=e=>{active=false;handle.classList.remove('resizing');handle.releasePointerCapture(e.pointerId);};handle.onpointercancel=()=>{active=false;handle.classList.remove('resizing');};}
setupSidebarResize();q('#toggle-filter').onclick=()=>{const panel=q('#filter-panel'),btn=q('#toggle-filter'),open=panel.classList.toggle('hidden');btn.classList.toggle('active',!panel.classList.contains('hidden'));btn.setAttribute('aria-expanded',String(!panel.classList.contains('hidden')));if(!panel.classList.contains('hidden'))q('#filter').focus();};q('#diagnostics-btn').onclick=e=>{e.stopPropagation();q('#diagnostics-panel').classList.toggle('open');};q('#close-diagnostics').onclick=()=>q('#diagnostics-panel').classList.remove('open');document.addEventListener('click',e=>{const panel=q('#diagnostics-panel'),btn=q('#diagnostics-btn');if(!panel.classList.contains('open'))return;if(panel.contains(e.target)||btn.contains(e.target))return;panel.classList.remove('open');});q('#scan').onclick=async()=>{try{const modules=q('#modules').value.split(/[\\n,]/).map(x=>x.trim()).filter(Boolean);state=await api('/api/scan','POST',{modules});render();await loadDefaultParameterLayouts();q('#detail').innerHTML=`<div class='welcome'><h2>扫描完成</h2><p class='muted'>从结构图中选择覆盖项以查看其意图和 Bin 方案。</p></div>`;}catch(e){q('#status').textContent=e.message;}};q('#filter').oninput=filter;api('/api/state').then(async x=>{state=x;q('#modules').value=(x.module_references||[]).join('\\n');render();await loadDefaultParameterLayouts();});</script></body></html>""".encode("utf-8")


def _handler(session: GuiSession):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # suppress local request noise
            return

        def _send(self, status: HTTPStatus, value: Any, content_type: str = "application/json") -> None:
            data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> Mapping[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, Mapping):
                raise GuiError("GUI request body must be a JSON object")
            return value

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path)
            try:
                if path.path == "/":
                    self._send(HTTPStatus.OK, _app_html(), "text/html")
                elif path.path == "/bin-canvas.js":
                    self._send(HTTPStatus.OK, _BIN_CANVAS_JS.read_bytes(), "application/javascript")
                elif path.path == "/api/state":
                    self._send(HTTPStatus.OK, session.state())
                elif path.path == "/api/node":
                    identifier = parse_qs(path.query).get("id", [""])[0]
                    self._send(HTTPStatus.OK, session.node(identifier))
                else:
                    self._send(HTTPStatus.NOT_FOUND, {"error": "unknown GUI endpoint"})
            except GuiError as exc:
                self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        def do_POST(self) -> None:  # noqa: N802
            try:
                if self.path == "/api/scan":
                    body = self._body()
                    modules = body.get("modules", body.get("types", []))
                    self._send(HTTPStatus.OK, session.scan(list(modules)))
                elif self.path == "/api/layout-preview":
                    self._send(HTTPStatus.OK, session.preview_layout(self._body()))
                else:
                    self._send(HTTPStatus.NOT_FOUND, {"error": "unknown GUI endpoint"})
            except (GuiError, ScanError) as exc:
                self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except json.JSONDecodeError:
                self._send(HTTPStatus.BAD_REQUEST, {"error": "GUI request body must be JSON"})
    return Handler


class _LoopbackServer(ThreadingHTTPServer):
    """Permit an intentional local GUI restart on the configured port."""

    allow_reuse_address = True


def create_gui_server(session: GuiSession, *, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    """Create a loopback-only GUI server; caller owns ``serve_forever``."""
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise GuiError("GUI service must bind a loopback address")
    return _LoopbackServer((host, port), _handler(session))


def start_gui_server(session: GuiSession, *, host: str = "127.0.0.1", port: int = 0) -> tuple[ThreadingHTTPServer, Thread, str]:
    """Start a GUI server for embedding/tests and return its local URL."""
    server = create_gui_server(session, host=host, port=port)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address, actual_port = server.server_address[:2]
    return server, thread, f"http://{address}:{actual_port}/"

"""GUI-scoped desktop recipe, not a complete SvTypes runtime bundle.

Run PyInstaller on the target OS. Only builds files; never launches the app.
"""

from pathlib import Path
import os
import sys

from PyInstaller.utils.hooks import copy_metadata

example = Path(SPECPATH)
vendor_path = os.environ.get("SVTYPES_GUI_VENDOR_PATH")
if not vendor_path or not (Path(vendor_path) / "webview/http.py").is_file():
    raise RuntimeError("Use build_webview_gui.py to prepare the isolated no-TLS dependency")
root = example.parents[1]
packages = [
    root / "python/svtypes",
    root / "extensions/svtypes_design_manifest/src/svtypes_design_manifest",
    root / "extensions/svtypes_coverage_tools/src/svtypes_coverage_tools",
]
if sys.platform not in {"darwin", "win32"}:
    raise RuntimeError("This experimental recipe has only macOS/Windows backend selection")

excluded = [
    "ssl", "_ssl", "_hashlib",
    "z3", "svtypes.constraint.randomize", "svtypes.constraint.backend.smt",
    "svtypes.constraint.backend.sampling",
    "PySide6", "PySide2", "PyQt6", "PyQt5", "qtpy", "pytest", "tkinter",
    "webview.platforms.qt", "webview.platforms.gtk", "webview.platforms.cef",
    "webview.platforms.android",
]
if sys.platform == "darwin":
    excluded.append("webview.platforms.winforms")
    backend = "webview.platforms.cocoa"
else:
    excluded.append("webview.platforms.cocoa")
    backend = "webview.platforms.winforms"

# Original user DSL is source-only; include the example source, not duplicate
# copies of every internal Python module. Analysis includes imported bytecode.
# webview's hook collects its bridge JS; the existing GUI needs bin-canvas JS.
datas = [
    (str(example / "packet.py"), "."),
    (str(packages[2] / "gui_bin_canvas.js"), "svtypes_coverage_tools"),
    *copy_metadata("pywebview"),
]
# Keep format schemas needed by the existing tool chain.
for package in packages[1:]:
    for path in package.rglob("*.json"):
        datas.append((str(path), str(Path(package.name) / path.relative_to(package).parent)))

a = Analysis(
    [str(example / "webview_frozen_entry.py")],
    pathex=[vendor_path, str(example), *[str(package.parent) for package in packages]],
    datas=datas,
    hiddenimports=["svtypes_coverage_tools.worker", "packet", backend],
    excludes=excluded,
)
# Fail the build if a hook reintroduces an excluded native dependency.
for destination, source, kind in a.binaries:
    name = Path(destination).name.lower()
    if any(token in name for token in ("libssl", "libcrypto", "libz3", "_ssl.", "_hashlib.")):
        raise RuntimeError(f"Unexpected native dependency in GUI package: {name}")
for module, source, kind in a.pure:
    if any(module == name or module.startswith(name + ".") for name in excluded):
        raise RuntimeError(f"Unexpected excluded module in GUI package: {module}")
    if module == "webview.http" and not Path(source).is_relative_to(Path(vendor_path)):
        raise RuntimeError("Dependency analysis did not use the isolated no-TLS adaptation")
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
          name="SvTypesCoverageGui", console=False, strip=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
               name="SvTypesCoverageGui")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="SvTypesCoverageGui.app",
                 bundle_identifier="org.svtypes.coverage.preview")

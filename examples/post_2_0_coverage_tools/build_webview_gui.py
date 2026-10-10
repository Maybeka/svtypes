"""Build the GUI-scoped desktop package; never execute the resulting app."""

from __future__ import annotations

import argparse
import ast
from importlib.metadata import version
from importlib.util import find_spec
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BUILD_VERSIONS = {"pywebview": "6.2.1", "pyinstaller": "6.22.3",
                  "pyinstaller-hooks-contrib": "2026.8"}


def defer_tls_import(source: str) -> str:
    """Mechanically adapt the pinned dependency, rejecting unexpected sources."""
    tree = ast.parse(source)
    imports = [node for node in tree.body if isinstance(node, ast.Import)
               and any(alias.name == "ssl" for alias in node.names)]
    anchor = "    def run(self, handler: WSGIApplication) -> None:  # pragma: no cover\n        import socket\n"
    if (len(imports) != 1 or len(imports[0].names) != 1
            or source.count(anchor) != 1):
        raise RuntimeError("Unsupported pywebview HTTP source; review the no-TLS adaptation")
    lines = source.splitlines(keepends=True)
    del lines[imports[0].lineno - 1:imports[0].end_lineno]
    result = "".join(lines).replace(anchor, anchor.replace(
        "        import socket\n",
        "        try:\n            import ssl\n"
        "        except ImportError as exc:\n"
        "            raise RuntimeError('This GUI package does not provide Python TLS/HTTPS') from exc\n"
        "        import socket\n",
    ))
    compile(result, "webview/http.py", "exec")
    return result


def prepare_vendor(source: Path, work: Path) -> Path:
    """Keep the installed dependency untouched; retain the build-local copy."""
    work.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="webview-no-tls-", dir=work))
    shutil.copytree(source, stage / "webview",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    http = stage / "webview/http.py"
    http.write_text(defer_tls_import(http.read_text(encoding="utf-8")), encoding="utf-8")
    return stage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distpath", type=Path, default=ROOT / ".tmp/coverage-gui-package/dist")
    parser.add_argument("--workpath", type=Path, default=ROOT / ".tmp/coverage-gui-package/build")
    args = parser.parse_args(argv)
    if sys.platform not in {"darwin", "win32"}:
        parser.error("Build on macOS or Windows; cross-compilation is not supported")
    for package, expected in BUILD_VERSIONS.items():
        actual = version(package)
        if actual != expected:
            raise RuntimeError(f"Expected {package} {expected}, found {actual}; install requirements-webview-build.txt")
    spec = find_spec("webview")
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError("Cannot locate the installed pywebview package")
    work = args.workpath.resolve()
    dist = args.distpath.resolve()
    vendor = prepare_vendor(Path(next(iter(spec.submodule_search_locations))), work)
    env = dict(os.environ, SVTYPES_GUI_VENDOR_PATH=str(vendor),
               PYINSTALLER_CONFIG_DIR=str(work / "pyinstaller-cache"))
    # Fresh dependency analysis prevents reuse of bytecode from the TLS build.
    subprocess.run([sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm",
                    "--distpath", str(dist), "--workpath", str(work),
                    str(HERE / "webview_gui.spec")], env=env, check=True, cwd=ROOT)
    print(f"Build finished: {dist}\nThe packaged application was NOT started.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

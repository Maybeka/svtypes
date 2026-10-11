"""Build preparation tests; do not start a desktop window or frozen executable."""

import ast
import runpy
import sys
from types import SimpleNamespace

import pytest

import build_webview_gui as build


HTTP_SOURCE = """import ssl
class SSLWSGIRefServer:
    def run(self, handler: WSGIApplication) -> None:  # pragma: no cover
        import socket
        ssl.SSLContext()
"""


def test_tls_import_is_deferred_and_upgrade_changes_fail_closed():
    result = build.defer_tls_import(HTTP_SOURCE)
    tree = ast.parse(result)
    assert not any(isinstance(node, ast.Import) for node in tree.body)
    assert "import ssl" in result
    assert "does not provide Python TLS/HTTPS" in result
    for unsupported in (HTTP_SOURCE.replace("import ssl", "import ssl, socket"),
                        HTTP_SOURCE.replace("import ssl", "from ssl import SSLContext"),
                        HTTP_SOURCE.replace("import socket", "import os")):
        with pytest.raises(RuntimeError, match="Unsupported"):
            build.defer_tls_import(unsupported)


def test_vendor_preparation_leaves_installed_source_unchanged(tmp_path):
    source = tmp_path / "installed/webview"
    source.mkdir(parents=True)
    (source / "http.py").write_text(HTTP_SOURCE, encoding="utf-8")
    (source / "__pycache__").mkdir()
    (source / "__pycache__/old.pyc").write_bytes(b"old")
    vendor = build.prepare_vendor(source, tmp_path / "build")
    assert (source / "http.py").read_text(encoding="utf-8") == HTTP_SOURCE
    assert (vendor / "webview/http.py").read_text(encoding="utf-8") == build.defer_tls_import(HTTP_SOURCE)
    assert not (vendor / "webview/__pycache__").exists()


def test_build_only_invokes_pyinstaller(monkeypatch, tmp_path):
    monkeypatch.setattr(build.sys, "platform", "darwin")
    monkeypatch.setattr(build, "version", lambda name: build.BUILD_VERSIONS[name])
    monkeypatch.setattr(build, "find_spec", lambda name: SimpleNamespace(submodule_search_locations=["installed"]))
    vendor = tmp_path / "vendor"
    monkeypatch.setattr(build, "prepare_vendor", lambda source, work: vendor)
    calls = []
    monkeypatch.setattr(build.subprocess, "run", lambda command, **kwargs: calls.append((command, kwargs)))
    assert build.main(["--distpath", str(tmp_path / "dist"), "--workpath", str(tmp_path / "build")]) == 0
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[:3] == [build.sys.executable, "-m", "PyInstaller"]
    assert "--clean" in command
    assert kwargs["env"]["SVTYPES_GUI_VENDOR_PATH"] == str(vendor)
    assert kwargs["check"] is True


def test_build_rejects_unreviewed_dependency_version(monkeypatch):
    monkeypatch.setattr(build.sys, "platform", "darwin")
    monkeypatch.setattr(build, "version", lambda name: "unreviewed")
    with pytest.raises(RuntimeError, match="Expected pywebview"):
        build.main([])


def test_tls_branch_reports_explicit_error_without_ssl(monkeypatch):
    namespace = {"WSGIApplication": object}
    exec(build.defer_tls_import(HTTP_SOURCE), namespace)
    monkeypatch.setitem(sys.modules, "ssl", None)
    with pytest.raises(RuntimeError, match="does not provide Python TLS/HTTPS"):
        namespace["SSLWSGIRefServer"]().run(None)


@pytest.mark.parametrize("arguments, expected", [([], "1"), (["--onedir"], "0")])
def test_windows_bundle_selection(monkeypatch, tmp_path, arguments, expected):
    monkeypatch.setattr(build.sys, "platform", "win32")
    monkeypatch.setattr(build, "version", lambda name: build.BUILD_VERSIONS[name])
    monkeypatch.setattr(build, "find_spec", lambda name: SimpleNamespace(submodule_search_locations=["installed"]))
    monkeypatch.setattr(build, "prepare_vendor", lambda source, work: tmp_path)
    calls = []
    monkeypatch.setattr(build.subprocess, "run", lambda command, **kwargs: calls.append(kwargs))
    assert build.main(["--distpath", str(tmp_path / "dist"), "--workpath", str(tmp_path / "build"), *arguments]) == 0
    assert calls[0]["env"]["SVTYPES_GUI_ONEFILE"] == expected


@pytest.mark.parametrize("platform, onefile", [("win32", True), ("win32", False), ("darwin", False)])
def test_spec_packages_binaries_and_resources_in_selected_container(monkeypatch, tmp_path, platform, onefile):
    vendor = tmp_path / "vendor"
    (vendor / "webview").mkdir(parents=True)
    (vendor / "webview/http.py").write_text("# prepared", encoding="utf-8")
    monkeypatch.setenv("SVTYPES_GUI_VENDOR_PATH", str(vendor))
    monkeypatch.setenv("SVTYPES_GUI_ONEFILE", "1" if onefile else "0")
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", SimpleNamespace(copy_metadata=lambda name: []))
    binaries = [("python.dll", "python.dll", "BINARY")]
    datas = [("packet.py", "packet.py", "DATA")]
    calls = {}
    def capture(name):
        def call(*args, **kwargs):
            calls[name] = (args, kwargs)
            return name
        return call
    runpy.run_path(str(build.HERE / "webview_gui.spec"), init_globals={
        "SPECPATH": str(build.HERE),
        "Analysis": lambda *args, **kwargs: SimpleNamespace(pure=[], scripts=[], binaries=binaries, datas=datas),
        "PYZ": capture("PYZ"), "EXE": capture("EXE"),
        "COLLECT": capture("COLLECT"), "BUNDLE": capture("BUNDLE"),
    })
    if onefile:
        assert calls["EXE"][0][2:4] == (binaries, datas)
        assert "exclude_binaries" not in calls["EXE"][1]
        assert "COLLECT" not in calls
    else:
        assert calls["EXE"][1]["exclude_binaries"] is True
        assert calls["COLLECT"][0][1:3] == (binaries, datas)
    assert ("BUNDLE" in calls) == (platform == "darwin")

"""Build preparation tests; do not start a desktop window or frozen executable."""

import ast
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

"""Desktop-shell regressions; mocks do not certify a platform web engine."""

from copy import deepcopy
from types import SimpleNamespace
import sys

import pytest

import run_webview_gui as shell
import webview_frozen_entry as frozen


@pytest.fixture
def report():
    return {"checks": {"css_grid": True}, "page_smoke": {
        "checks": {"point": {"rendered": True, "dsl_card": True, "bin_rows": 0},
                   "same_origin_fetch_json": True}, "errors": []}}


def test_probe_reports_failures(report):
    assert shell.probe_succeeded(report)
    for failed in (
        {**report, "checks": {"css_grid": False}},
        {**report, "page_smoke": {"error": "timeout"}},
        {**report, "page_smoke": {**report["page_smoke"], "errors": ["script error"]}},
        {},
    ):
        assert not shell.probe_succeeded(failed)
    failed = deepcopy(report)
    failed["page_smoke"]["checks"]["point"]["rendered"] = False
    assert not shell.probe_succeeded(failed)


def test_probe_closes_only_test_window(monkeypatch, capsys, report):
    monkeypatch.setattr(shell, "version", lambda name: "test")
    closed = []
    def evaluate(script, callback=None):
        if callback:
            callback(report["page_smoke"])
        elif script == shell.COMPATIBILITY_PROBE:
            return {"checks": report["checks"]}
        else:
            return True
    window = SimpleNamespace(evaluate_js=evaluate, destroy=lambda: closed.append(True))
    assert shell.inspect_compatibility(window)
    assert not closed
    assert shell.inspect_compatibility(window, close=True)
    assert closed == [True]
    assert '"passed": true' in capsys.readouterr().out


@pytest.mark.parametrize("platform, renderer", [("win32", "edgechromium"), ("darwin", None)])
def test_shell_reuses_loopback_server_and_cleans_up(monkeypatch, platform, renderer):
    calls = []
    events = SimpleNamespace(loaded=0)
    window = SimpleNamespace(events=events)
    webview = SimpleNamespace(settings={},
        create_window=lambda title, url, **kwargs: calls.append((title, url, kwargs)) or window,
        start=lambda **kwargs: calls.append(kwargs))
    session = SimpleNamespace(scan=lambda modules: calls.append(modules))
    server = SimpleNamespace(shutdown=lambda: calls.append("shutdown"), server_close=lambda: calls.append("close"))
    thread = SimpleNamespace(join=lambda timeout: calls.append(("join", timeout)))
    monkeypatch.setitem(sys.modules, "webview", webview)
    monkeypatch.setattr(shell.sys, "platform", platform)
    monkeypatch.setattr(shell, "version", lambda name: "test")
    monkeypatch.setattr(shell, "GuiSession", lambda *args: session)
    monkeypatch.setattr(shell, "start_gui_server", lambda *args, **kwargs: (server, thread, "http://127.0.0.1:12345/"))
    assert shell.main([]) == 0
    assert calls[0] == ["packet"]
    assert "js_api" not in calls[1][2]
    assert calls[2] == {"gui": renderer, "debug": False}
    assert webview.settings["ALLOW_FILE_URLS"] is False
    assert calls[-3:] == ["shutdown", "close", ("join", 5)]


def test_frozen_worker_dispatch_does_not_launch_gui(monkeypatch):
    calls = []
    def unexpected_gui(args):
        pytest.fail("worker child must not launch a GUI")
    monkeypatch.setitem(sys.modules, "svtypes_coverage_tools.worker",
                        SimpleNamespace(main=lambda args: calls.append(args) or 7))
    monkeypatch.setitem(sys.modules, "run_webview_gui", SimpleNamespace(main=unexpected_gui))
    assert frozen.main(["-m", "svtypes_coverage_tools.worker", "--out", "manifest.json", "packet"]) == 7
    assert calls == [["--out", "manifest.json", "packet"]]


def test_frozen_gui_dispatch_preserves_arguments(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "run_webview_gui",
                        SimpleNamespace(main=lambda args: calls.append(args) or 0))
    assert frozen.main(["--port", "0"]) == 0
    assert calls == [["--port", "0"]]


def test_frozen_session_uses_resolved_source_resource(monkeypatch, tmp_path):
    resources = tmp_path / "Resources"
    frameworks = tmp_path / "Frameworks"
    resources.mkdir()
    frameworks.mkdir()
    source = resources / "packet.py"
    source.write_text("# example DSL\n", encoding="utf-8")
    (frameworks / "packet.py").symlink_to(source)
    monkeypatch.setattr(shell.sys, "frozen", True, raising=False)
    monkeypatch.setattr(shell.sys, "_MEIPASS", str(frameworks), raising=False)
    monkeypatch.setattr(shell, "GuiSession", lambda *args: args)
    assert shell.create_session() == (resources, (resources,))
    (frameworks / "packet.py").unlink()
    with pytest.raises(RuntimeError, match="missing.*DSL source"):
        shell.create_session()

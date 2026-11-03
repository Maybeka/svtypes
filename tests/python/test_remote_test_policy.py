"""Remote conformance failures must report connection problems accurately."""

from types import SimpleNamespace

import pytest
import conftest


class RemoteItem:
    def get_closest_marker(self, name):
        return name == "remote_sv"


def test_remote_connection_failure_is_reported_and_not_cached(monkeypatch):
    monkeypatch.setenv("SVTYPES_REMOTE_SV_HOST", "test-host")
    monkeypatch.setenv("SVTYPES_REMOTE_SV_ROOT", "/test")
    results = iter((SimpleNamespace(returncode=255, stderr="Network is down\n"),
                    SimpleNamespace(returncode=0, stderr="")))
    monkeypatch.setattr(conftest.subprocess, "run", lambda *args, **kwargs:
                        next(results))
    with pytest.raises(pytest.fail.Exception, match="connection failed: Network is down"):
        conftest.pytest_runtest_setup(RemoteItem())
    conftest.pytest_runtest_setup(RemoteItem())


def test_remote_adapter_probe_reports_missing_adapter(monkeypatch):
    monkeypatch.setenv("SVTYPES_REMOTE_SV_HOST", "test-host")
    monkeypatch.setenv("SVTYPES_REMOTE_SV_ROOT", "/test")
    monkeypatch.setattr(conftest.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=1, stderr=""))
    with pytest.raises(pytest.fail.Exception, match="adapter is not available"):
        conftest.pytest_runtest_setup(RemoteItem())


def test_remote_adapter_probe_checks_python_script_with_spaces(monkeypatch):
    monkeypatch.setenv("SVTYPES_REMOTE_SV_HOST", "test-host")
    monkeypatch.setenv("SVTYPES_REMOTE_SV_ROOT", "/test")
    monkeypatch.setenv("SVTYPES_REMOTE_SV_RUNNER", "python3 '/test/my runner.py'")
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(conftest.subprocess, "run", run)
    conftest.pytest_runtest_setup(RemoteItem())
    assert len(commands) == 1 and "my runner.py" in commands[0][-1]

"""Shared pytest policy for required remote SystemVerilog conformance tests."""

from __future__ import annotations

from functools import lru_cache
import os
import subprocess

import pytest


DEFAULT_REMOTE_SV_RUNNER = "svtypes_remote_sv_runner"


@lru_cache(maxsize=None)
def _remote_sv_reachable(host: str) -> bool:
    result = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host, "true"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Require the configured conformance target before remote tests run."""
    if item.get_closest_marker("remote_sv") is None:
        return
    host = os.environ.get("SVTYPES_REMOTE_SV_HOST")
    root = os.environ.get("SVTYPES_REMOTE_SV_ROOT")
    runner = os.environ.get("SVTYPES_REMOTE_SV_RUNNER", DEFAULT_REMOTE_SV_RUNNER)
    if not host or not root:
        pytest.fail("remote SystemVerilog target host/root is not configured")
    if _remote_sv_reachable(host):
        adapter = subprocess.run(
            ["ssh", host, f"bash -ilc 'command -v {runner}'"],
            capture_output=True,
            text=True,
        )
        if adapter.returncode == 0:
            return
        pytest.fail("remote SystemVerilog target adapter is not available")
    pytest.fail(f"remote SystemVerilog target {host} is not reachable")

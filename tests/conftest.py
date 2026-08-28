"""Shared pytest policy for optional and required remote SV tests."""

from __future__ import annotations

from functools import lru_cache
import os
import subprocess

import pytest


DEFAULT_REMOTE_SV_RUNNER = "svtypes_remote_sv_runner"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--require-remote-sv",
        action="store_true",
        default=False,
        help="fail instead of skip when the configured remote SystemVerilog target is unavailable",
    )


@lru_cache(maxsize=None)
def _remote_sv_reachable(host: str) -> bool:
    result = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host, "true"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Apply one consistent availability policy before any test fixture runs."""
    if item.get_closest_marker("remote_sv") is None:
        return
    host = os.environ.get("SVTYPES_REMOTE_SV_HOST")
    root = os.environ.get("SVTYPES_REMOTE_SV_ROOT")
    runner = os.environ.get("SVTYPES_REMOTE_SV_RUNNER", DEFAULT_REMOTE_SV_RUNNER)
    if not host or not root:
        message = "remote SystemVerilog target host/root is not configured"
        if item.config.getoption("--require-remote-sv"):
            pytest.fail(message)
        pytest.skip(message)
    if _remote_sv_reachable(host):
        adapter = subprocess.run(
            ["ssh", host, f"bash -ilc 'command -v {runner}'"],
            capture_output=True,
            text=True,
        )
        if adapter.returncode == 0:
            return
        message = "remote SystemVerilog target adapter is not available"
        if item.config.getoption("--require-remote-sv"):
            pytest.fail(message)
        pytest.skip(message)
    message = f"remote SystemVerilog target {host} is not reachable"
    if item.config.getoption("--require-remote-sv"):
        pytest.fail(message)
    pytest.skip(message)

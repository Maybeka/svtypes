"""Shared pytest policy for optional and required remote SystemVerilog target tests."""

from __future__ import annotations

from functools import lru_cache
import os
import subprocess

import pytest


DEFAULT_target_HOST = "remote-target"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--require-target",
        action="store_true",
        default=False,
        help="fail instead of skip when the configured remote SystemVerilog target host is unavailable",
    )


@lru_cache(maxsize=None)
def _target_reachable(host: str) -> bool:
    result = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", host, "true"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Apply one consistent availability policy before any test fixture runs."""
    if item.get_closest_marker("remote_target") is None:
        return
    host = os.environ.get("SVTYPES_target_HOST", DEFAULT_target_HOST)
    if _target_reachable(host):
        return
    message = f"remote SystemVerilog target host {host} is not reachable"
    if item.config.getoption("--require-target"):
        pytest.fail(message)
    pytest.skip(message)

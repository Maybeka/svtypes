"""Shared pytest policy for required remote SystemVerilog conformance tests."""

from __future__ import annotations

import os
import shlex
import subprocess

import pytest


DEFAULT_REMOTE_SV_RUNNER = "svtypes_remote_sv_runner"


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Require the configured conformance target before remote tests run."""
    if item.get_closest_marker("remote_sv") is None:
        return
    host = os.environ.get("SVTYPES_REMOTE_SV_HOST")
    root = os.environ.get("SVTYPES_REMOTE_SV_ROOT")
    runner = os.environ.get("SVTYPES_REMOTE_SV_RUNNER", DEFAULT_REMOTE_SV_RUNNER)
    if not host or not root:
        pytest.fail("remote SystemVerilog target host/root is not configured")
    try:
        parts = shlex.split(runner)
    except ValueError as error:
        pytest.fail(f"invalid remote SystemVerilog target adapter command: {error}")
    if not parts:
        pytest.fail("remote SystemVerilog target adapter command is empty")
    probe = f"command -v {shlex.quote(parts[0])} >/dev/null"
    if len(parts) > 1 and parts[1].endswith(".py"):
        probe += f" && test -r {shlex.quote(parts[1])}"
    adapter = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
         host, "bash -ilc " + shlex.quote(probe)],
        capture_output=True,
        text=True,
    )
    if adapter.returncode == 0:
        return
    if adapter.returncode == 255:
        detail = adapter.stderr.strip().splitlines()
        pytest.fail(f"remote SystemVerilog target {host} connection failed: "
                    f"{detail[-1] if detail else 'SSH failed'}")
    pytest.fail("remote SystemVerilog target adapter is not available")

"""Executable smoke tests for the public SvTypes cookbook."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
COOKBOOK_DIR = REPO_ROOT / "examples" / "cookbook"


def test_cookbook_lessons_run_in_order() -> None:
    lessons = sorted(COOKBOOK_DIR.glob("[0-9][0-9]_*.py"))
    assert [lesson.name for lesson in lessons] == [
        "01_define_and_serialize.py",
        "02_collections_and_graphs.py",
        "03_constrained_random.py",
        "04_layered_random.py",
        "05_functional_coverage.py",
        "06_generate_artifacts.py",
        "07_schema_and_runtime_contract.py",
    ]
    environment = os.environ.copy()
    source_root = str(REPO_ROOT / "python")
    environment["PYTHONPATH"] = source_root + os.pathsep + environment.get("PYTHONPATH", "")
    for lesson in lessons:
        result = subprocess.run(
            [sys.executable, str(lesson)],
            cwd=REPO_ROOT,
            env=environment,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr


def test_cookbook_is_linked_from_public_entrypoints() -> None:
    assert "cookbook.md" in (REPO_ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "Cookbook" in (REPO_ROOT / "README.md").read_text(encoding="utf-8")

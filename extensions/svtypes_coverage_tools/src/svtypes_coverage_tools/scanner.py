"""Dedicated-process Design Manifest scanner."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Iterable

from svtypes_design_manifest import load_design_manifest, write_design_manifest


class ScanError(RuntimeError):
    """The isolated scanner could not produce a valid manifest."""


def scan_design(
    modules: Iterable[str],
    *,
    output: str | Path,
    source_root: str | Path | None = None,
    python_paths: Iterable[str | Path] = (),
) -> dict[str, object]:
    """Scan module entry points in a dedicated child interpreter.

    No target design module is imported by this process.  The child discovers
    every ``SvObject`` subclass defined in each module, then writes a manifest.
    """
    references = tuple(modules)
    if not references or any(not isinstance(item, str) or not item or ":" in item for item in references):
        raise ScanError("scanner requires one or more importable module entry points")
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    supplied_paths = [str(Path(path).resolve()) for path in python_paths]
    inherited = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = os.pathsep.join([*supplied_paths, inherited])
    with tempfile.TemporaryDirectory(prefix="svtypes-scan-") as temporary:
        child_output = Path(temporary) / "design-manifest.json"
        report = Path(temporary) / "scan-report.json"
        command = [
            sys.executable,
            "-m",
            "svtypes_coverage_tools.worker",
            "--out",
            str(child_output),
            "--report",
            str(report),
            *references,
        ]
        if source_root is not None:
            command.extend(("--source-root", str(source_root)))
        result = subprocess.run(command, capture_output=True, text=True, env=environment)
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "scanner child failed"
            raise ScanError(message)
        try:
            details = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ScanError(f"scanner child did not write a valid report: {exc}") from exc
        try:
            manifest = load_design_manifest(child_output)
        except Exception as exc:
            raise ScanError(f"scanner child produced an invalid manifest: {exc}") from exc
    write_design_manifest(destination, manifest)
    return {"manifest": manifest, "report": details}


def preview_coverage_layout(
    type_reference: str,
    covergroup: str,
    arguments: dict[str, object],
    *,
    parameter_actuals: dict[str, object] | None = None,
    source_root: str | Path | None = None,
    python_paths: Iterable[str | Path] = (),
) -> dict[str, object]:
    """Materialize one declared covergroup in an isolated process."""
    if not isinstance(type_reference, str) or not type_reference:
        raise ScanError("coverage layout preview requires a module:QualifiedType entry point")
    if not isinstance(covergroup, str) or not covergroup:
        raise ScanError("coverage layout preview requires a covergroup name")
    if not isinstance(arguments, dict) or any(not isinstance(name, str) for name in arguments):
        raise ScanError("coverage layout preview arguments must be a JSON object with string keys")
    if parameter_actuals is not None and (
        not isinstance(parameter_actuals, dict)
        or any(not isinstance(name, str) for name in parameter_actuals)
    ):
        raise ScanError("coverage parameter actuals must be a JSON object with string keys")
    environment = dict(os.environ)
    supplied_paths = [str(Path(path).resolve()) for path in python_paths]
    environment["PYTHONPATH"] = os.pathsep.join([*supplied_paths, environment.get("PYTHONPATH", "")])
    with tempfile.TemporaryDirectory(prefix="svtypes-layout-preview-") as temporary:
        destination = Path(temporary) / "layout.json"
        report = Path(temporary) / "preview-report.json"
        command = [
            sys.executable,
            "-m",
            "svtypes_coverage_tools.worker",
            "--preview-type",
            type_reference,
            "--preview-covergroup",
            covergroup,
            "--preview-arguments",
            json.dumps(arguments, ensure_ascii=False, sort_keys=True),
            "--preview-parameter-actuals",
            json.dumps(parameter_actuals or {}, ensure_ascii=False, sort_keys=True),
            "--out",
            str(destination),
            "--report",
            str(report),
        ]
        if source_root is not None:
            command.extend(("--source-root", str(source_root)))
        result = subprocess.run(command, capture_output=True, text=True, env=environment)
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "layout preview child failed"
            raise ScanError(message)
        try:
            payload = json.loads(destination.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ScanError(f"layout preview child did not write valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("layout"), dict):
        raise ScanError("layout preview child produced an invalid layout")
    return payload

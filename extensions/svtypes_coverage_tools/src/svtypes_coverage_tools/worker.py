"""Child-only importer for :mod:`svtypes_coverage_tools.scanner`."""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
from pathlib import Path
from typing import Any

from svtypes import SvObject
from svtypes.coverage import preview_coverage_layout
from svtypes_design_manifest import DesignManifestError, export_design_manifest, write_design_manifest


def _resolve_type(reference: str) -> type[Any]:
    module_name, separator, qualname = reference.partition(":")
    if not separator or not module_name or not qualname:
        raise DesignManifestError("type reference must have the form module:QualifiedType")
    value: Any = importlib.import_module(module_name)
    for part in qualname.split("."):
        value = getattr(value, part)
    if not isinstance(value, type):
        raise DesignManifestError(f"type reference {reference!r} does not resolve to a type")
    return value


def _discover_module_types(module_name: str) -> list[type[Any]]:
    """Return SvObject subclasses physically defined in *module_name*."""
    if not module_name or ":" in module_name:
        raise DesignManifestError("module entry point must be an importable module name")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise DesignManifestError(f"cannot import module {module_name!r}: {exc}") from exc
    discovered: list[type[Any]] = []
    for _, value in inspect.getmembers(module, inspect.isclass):
        if value is SvObject or not issubclass(value, SvObject):
            continue
        if value.__module__ != module.__name__:
            continue
        discovered.append(value)
    if not discovered:
        raise DesignManifestError(f"module {module_name!r} defines no SvObject subclasses")
    return discovered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SvTypes isolated coverage scanner worker")
    parser.add_argument("modules", nargs="*", metavar="MODULE")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--preview-type")
    parser.add_argument("--preview-arguments")
    parser.add_argument("--preview-covergroup")
    parser.add_argument("--preview-parameter-actuals", default="{}")
    args = parser.parse_args(argv)
    try:
        if args.preview_type is not None:
            if args.modules or not args.preview_covergroup or args.preview_arguments is None:
                raise DesignManifestError(
                    "layout preview requires exactly --preview-type, --preview-covergroup, and --preview-arguments"
                )
            try:
                preview_arguments = json.loads(args.preview_arguments)
                parameter_actuals = json.loads(args.preview_parameter_actuals)
            except json.JSONDecodeError as exc:
                raise DesignManifestError("layout preview arguments must be JSON") from exc
            if not isinstance(preview_arguments, dict) or not isinstance(parameter_actuals, dict):
                raise DesignManifestError("layout preview arguments must be JSON objects")
            layout = preview_coverage_layout(
                _resolve_type(args.preview_type),
                args.preview_covergroup,
                parameter_actuals=parameter_actuals,
                **preview_arguments,
            )
            payload = {
                "covergroup": args.preview_covergroup,
                "layout": layout.definition_snapshot(),
            }
            args.out.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            args.report.write_text(json.dumps({"child_pid": os.getpid(), "diagnostics": []}, sort_keys=True), encoding="utf-8")
            return 0
        if not args.modules:
            raise DesignManifestError("scanner requires at least one module entry point")
        resolved: list[type[Any]] = []
        diagnostics: list[dict[str, str]] = []
        seen: set[str] = set()
        for module_name in args.modules:
            try:
                for cls in _discover_module_types(module_name):
                    key = f"{cls.__module__}.{cls.__qualname__}"
                    if key in seen:
                        continue
                    seen.add(key)
                    resolved.append(cls)
            except (DesignManifestError, ImportError, AttributeError, TypeError) as exc:
                diagnostics.append(
                    {"code": "SVT-SCAN-IMPORT", "severity": "error", "message": f"{module_name}: {exc}"}
                )
        if not resolved:
            raise DesignManifestError(diagnostics[0]["message"] if diagnostics else "no module types resolved")
        document = export_design_manifest(
            resolved,
            source_root=args.source_root,
        )
        document["diagnostics"] = diagnostics
        write_design_manifest(args.out, document)
        args.report.write_text(json.dumps({"child_pid": os.getpid(), "diagnostics": diagnostics}, sort_keys=True), encoding="utf-8")
    except (DesignManifestError, ImportError, AttributeError, TypeError) as exc:
        args.report.write_text(
            json.dumps(
                {
                    "child_pid": os.getpid(),
                    "diagnostics": [{"code": "SVT-SCAN-IMPORT", "severity": "error", "message": str(exc)}],
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        print(f"SVT-SCAN-IMPORT: {exc}", file=__import__("sys").stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

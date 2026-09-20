"""Command line entry point for the E0 Design Manifest bridge."""

from __future__ import annotations

import argparse
import importlib
from pathlib import Path
from typing import Any

from .bridge import DesignManifestError, export_design_manifest, write_design_manifest


def _resolve_type(reference: str) -> type[Any]:
    module_name, separator, qualname = reference.partition(":")
    if not separator or not module_name or not qualname:
        raise DesignManifestError("type entry point must have the form module:QualifiedType")
    try:
        value: Any = importlib.import_module(module_name)
        for part in qualname.split("."):
            value = getattr(value, part)
    except (ImportError, AttributeError) as exc:
        raise DesignManifestError(f"cannot resolve type entry point {reference!r}: {exc}") from exc
    if not isinstance(value, type):
        raise DesignManifestError(f"type entry point {reference!r} does not resolve to a type")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export a read-only SvTypes design manifest")
    parser.add_argument("types", nargs="+", metavar="MODULE:TYPE", help="explicit SvTypes type entry point")
    parser.add_argument("--source-root", type=Path, help="optional root for relative provenance paths")
    parser.add_argument("--out", required=True, type=Path, help="destination manifest path")
    args = parser.parse_args(argv)
    try:
        document = export_design_manifest(
            (_resolve_type(reference) for reference in args.types),
            source_root=args.source_root,
        )
        write_design_manifest(args.out, document)
    except DesignManifestError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())

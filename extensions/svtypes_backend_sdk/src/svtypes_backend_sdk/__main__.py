"""CLI for explicit SvTypes backend builds."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .runner import BackendError, build_from_files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build artifacts from a SvTypes Design Manifest")
    subcommands = parser.add_subparsers(dest="command", required=True)
    build_parser = subcommands.add_parser("build")
    build_parser.add_argument("--backend", required=True, help="explicit module:attribute backend")
    build_parser.add_argument("--input", required=True, type=Path, help="Design Manifest JSON")
    build_parser.add_argument("--out", required=True, type=Path, help="artifact directory")
    build_parser.add_argument("--options", default="{}", help="backend-private JSON object")
    args = parser.parse_args(argv)
    try:
        options = json.loads(args.options)
        result = build_from_files(args.backend, args.input, args.out, options=options)
    except (BackendError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

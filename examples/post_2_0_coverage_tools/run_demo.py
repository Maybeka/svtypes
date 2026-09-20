"""Run the complete read-only coverage-tools workflow for :mod:`packet`."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
for source in (
    ROOT / "python",
    ROOT / "extensions" / "svtypes_design_manifest" / "src",
    ROOT / "extensions" / "svtypes_coverage_tools" / "src",
    HERE,
):
    sys.path.insert(0, str(source))

from svtypes_coverage_tools import (
    build_catalog,
    create_proposal,
    render_review,
    scan_design,
    write_catalog,
    write_proposal,
)


def run(output: Path) -> None:
    """Produce all artifacts without applying the suggested source change."""
    output.mkdir(parents=True, exist_ok=True)

    # 1. Scanner imports packet.py in a separate interpreter, discovers every
    # SvObject defined there, and writes only a declaration manifest.
    scanned = scan_design(
        ["packet"],
        output=output / "packet.design.json",
        source_root=HERE,
        python_paths=(HERE, ROOT / "python", ROOT / "extensions" / "svtypes_design_manifest" / "src", ROOT / "extensions" / "svtypes_coverage_tools" / "src"),
    )
    catalog = build_catalog(scanned["manifest"])
    write_catalog(output / "packet.catalog.json", catalog)

    # 2. Create a proposal to add an opcode-two bin. It contains the exact
    # reviewed text edit but this demo intentionally never calls apply_proposal.
    group = next(
        item for item in catalog["groups"]
        if item["declaration_name"] == "cg" and item["owner_type"].startswith("packet.Packet")
    )
    source_lines = (HERE / "packet.py").read_text(encoding="utf-8").splitlines(keepends=True)
    one_line = next(index for index, value in enumerate(source_lines, 1) if "one = bins[1]" in value)
    proposal = create_proposal(
        catalog,
        covergroup_type_id=group["covergroup_type_id"],
        operations=({"kind": "add_bin", "point": "opcode_cp", "name": "two", "selector": {"kind": "constant", "value": 2}},),
        rationale="Make opcode value 2 visible in the coverage plan.",
        edits=({
            "file": "packet.py",
            "start_line": one_line,
            "end_line": one_line,
            "expected_text": source_lines[one_line - 1],
            "replacement": source_lines[one_line - 1] + "            two = bins[2]\n",
        },),
    )
    write_proposal(output / "add-opcode-two.proposal.json", proposal)
    (output / "add-opcode-two.review.txt").write_text(render_review(proposal), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the SvTypes coverage-tools walkthrough")
    parser.add_argument("--out", type=Path, default=HERE / "out", help="artifact directory")
    args = parser.parse_args(argv)
    run(args.out.resolve())
    print(f"Wrote coverage-tools demo artifacts to {args.out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

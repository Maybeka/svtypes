"""CLI for the independent SvTypes coverage tools."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .catalog import CatalogError, catalog_from_file, write_catalog
from .proposal import ProposalError, apply_proposal, create_proposal, load_proposal, render_review, write_proposal
from .gui import GuiError, GuiSession, create_gui_server
from .scanner import ScanError, scan_design


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SvTypes coverage scanner and catalog tools")
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan")
    scan.add_argument("modules", nargs="+", metavar="MODULE")
    scan.add_argument("--out", required=True, type=Path)
    scan.add_argument("--source-root", type=Path)
    scan.add_argument("--python-path", action="append", default=[], type=Path)
    catalog = commands.add_parser("catalog")
    catalog.add_argument("--input", required=True, type=Path)
    catalog.add_argument("--out", required=True, type=Path)
    gui = commands.add_parser("gui")
    gui.add_argument("--source-root", required=True, type=Path)
    gui.add_argument("--python-path", action="append", default=[], type=Path)
    gui.add_argument("--work-dir", type=Path)
    gui.add_argument("--module", action="append", default=[], dest="modules", metavar="MODULE")
    gui.add_argument("--port", default=8765, type=int)
    proposal = commands.add_parser("proposal")
    proposal_commands = proposal.add_subparsers(dest="proposal_command", required=True)
    create = proposal_commands.add_parser("create")
    create.add_argument("--catalog", required=True, type=Path)
    create.add_argument("--covergroup", required=True)
    create.add_argument("--operation", action="append", required=True, help="one semantic operation as a JSON object")
    create.add_argument("--edit", action="append", required=True, help="one reviewed source edit as a JSON object")
    create.add_argument("--rationale", required=True)
    create.add_argument("--out", required=True, type=Path)
    review = proposal_commands.add_parser("review")
    review.add_argument("--proposal", required=True, type=Path)
    apply = proposal_commands.add_parser("apply")
    apply.add_argument("--proposal", required=True, type=Path)
    apply.add_argument("--catalog", required=True, type=Path)
    apply.add_argument("--source-root", required=True, type=Path)
    apply.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "scan":
            result = scan_design(
                args.modules,
                output=args.out,
                source_root=args.source_root,
                python_paths=args.python_path,
            )
            print(json.dumps(result["report"], ensure_ascii=False, sort_keys=True))
        elif args.command == "catalog":
            result = write_catalog(args.out, catalog_from_file(args.input))
            print(json.dumps({"catalog": str(result)}, ensure_ascii=False, sort_keys=True))
        elif args.command == "gui":
            session = GuiSession(args.source_root, tuple(args.python_path), args.work_dir)
            if args.modules:
                session.scan(args.modules)
            server = create_gui_server(session, port=args.port)
            address, port = server.server_address[:2]
            print(f"SvTypes coverage design GUI: http://{address}:{port}/")
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
        elif args.proposal_command == "create":
            from .catalog import load_catalog
            proposal_document = create_proposal(
                load_catalog(args.catalog), covergroup_type_id=args.covergroup,
                operations=[json.loads(item) for item in args.operation], rationale=args.rationale,
                edits=[json.loads(item) for item in args.edit],
            )
            result = write_proposal(args.out, proposal_document)
            print(json.dumps({"proposal": str(result)}, ensure_ascii=False, sort_keys=True))
        elif args.proposal_command == "review":
            print(render_review(load_proposal(args.proposal)), end="")
        else:
            from .catalog import load_catalog
            changed = apply_proposal(load_proposal(args.proposal), load_catalog(args.catalog), source_root=args.source_root, confirm=args.confirm)
            print(json.dumps({"changed": [str(path) for path in changed]}, ensure_ascii=False, sort_keys=True))
    except (ScanError, CatalogError, ProposalError, GuiError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

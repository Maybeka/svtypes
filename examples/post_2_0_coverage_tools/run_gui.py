"""Launch the design-time coverage GUI for the example Packet declaration."""

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

from svtypes_coverage_tools import GuiSession, create_gui_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Launch the SvTypes coverage design GUI example")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    session = GuiSession(HERE, (HERE, ROOT / "python", ROOT / "extensions" / "svtypes_design_manifest" / "src", ROOT / "extensions" / "svtypes_coverage_tools" / "src"))
    session.scan(["packet"])
    server = create_gui_server(session, port=args.port)
    address, port = server.server_address[:2]
    print(f"Open http://{address}:{port}/ in a browser. Press Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

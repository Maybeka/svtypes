"""Experimental frozen GUI entry; worker children never start a GUI loop."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    # scanner.py re-executes sys.executable with -m. A frozen bootloader does
    # not provide Python's CLI, so explicitly dispatch the one required worker.
    if args[:2] == ["-m", "svtypes_coverage_tools.worker"]:
        from svtypes_coverage_tools.worker import main as worker_main
        return worker_main(args[2:])
    from run_webview_gui import main as gui_main
    return gui_main(args)


if __name__ == "__main__":
    raise SystemExit(main())

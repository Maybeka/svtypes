"""Optional desktop shell for the unchanged coverage web GUI."""

from __future__ import annotations

import argparse
import json
from importlib.metadata import version
from pathlib import Path
import sys
from threading import Event
import time

from run_gui import GuiSession, HERE, ROOT
from svtypes_coverage_tools import start_gui_server


COMPATIBILITY_PROBE = r"""(() => {
    const checks = {};
    const test = (name, fn) => {
        try { checks[name] = Boolean(fn()); }
        catch (error) { checks[name] = {error: String(error)}; }
    };
    test('css_grid', () => CSS.supports('display', 'grid'));
    test('css_flex', () => CSS.supports('display', 'flex'));
    test('css_custom_properties', () => CSS.supports('--probe', '1'));
    test('css_clamp', () => CSS.supports('width', 'clamp(1px, 2px, 3px)'));
    test('css_has', () => CSS.supports('selector(:has(*))'));
    test('css_escape', () => CSS.escape('a:b') === 'a\\:b');
    test('optional_chaining', () => ({}).missing?.value === undefined);
    test('nullish_coalescing', () => (null ?? 1) === 1);
    test('array_at', () => [1, 2].at(-1) === 2);
    test('structured_clone', () => structuredClone({x: [1]}).x[0] === 1);
    test('bigint', () => BigInt('18446744073709551615').toString() === '18446744073709551615');
    test('pointer_events', () => typeof PointerEvent === 'function');
    test('pointer_capture_api', () => typeof Element.prototype.setPointerCapture === 'function');
    test('resize_observer', () => typeof ResizeObserver === 'function');
    test('fetch', () => typeof fetch === 'function');
    test('svg', () => document.createElementNS('http://www.w3.org/2000/svg', 'svg') instanceof SVGElement);
    test('canvas_2d', () => Boolean(document.createElement('canvas').getContext('2d')));
    test('flex_gap_layout', () => {
        const host = document.createElement('div');
        host.style.cssText = 'position:absolute;visibility:hidden;display:flex;gap:7px';
        for (let i = 0; i < 2; i++) {
            const child = document.createElement('div');
            child.style.cssText = 'width:10px;height:10px;flex:none';
            host.append(child);
        }
        document.body.append(host);
        const [a, b] = [...host.children].map(item => item.getBoundingClientRect());
        host.remove();
        return Math.abs(b.left - a.right - 7) < 0.1;
    });
    return {
        user_agent: navigator.userAgent,
        device_pixel_ratio: devicePixelRatio,
        viewport: {width: innerWidth, height: innerHeight},
        secure_context: isSecureContext,
        checks,
        page: {
            tree_nodes: document.querySelectorAll('.node[data-id]').length,
            main_layout: getComputedStyle(document.body).display,
            title: document.title,
        },
    };
})()"""


PAGE_SMOKE_PROBE = r"""(async () => {
    const checks = {};
    const errors = [];
    const onError = event => errors.push(String(event.message || event.reason));
    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onError);
    // Animation frames may be throttled when a desktop window is occluded.
    const frame = () => new Promise(resolve => {
        const fallback = setTimeout(resolve, 100);
        requestAnimationFrame(() => requestAnimationFrame(() => {
            clearTimeout(fallback);
            resolve();
        }));
    });
    const group = state.catalog.groups.find(group => group.declaration_name === 'cg');
    const targets = ['opcode_cp', 'length_cp', 'kind_cp', 'sparse_opcode_cp', 'mode_sequence', 'addr_cp', 'opcode_mode', 'opcode_compact'];
    try {
        for (const name of targets) {
            const item = [...group.points, ...group.crosses].find(item => item.name === name);
            await select(item.semantic_id);
            await frame();
            checks[name] = {
                rendered: document.querySelector('.hero h2')?.textContent === name && !document.querySelector('#detail > .error'),
                dsl_card: Boolean(document.querySelector('.dsl-card')),
                bin_rows: document.querySelectorAll('.bin-row').length,
            };
            if (name === 'mode_sequence') {
                const token = document.querySelector('.transition-step-selector .bin-fragment-token');
                // The web GUI detects a double press through pointerdown,
                // not the browser's separate dblclick event on this node.
                for (let i = 0; i < 2; i++) {
                    token.dispatchEvent(new PointerEvent('pointerdown', {bubbles: true, button: 0, pointerId: 1}));
                }
                await frame();
                checks.transition_doubleclick_focus = Boolean(document.activeElement?.closest('.transition-step-selector'));
                checks.transition_temporary_track = Boolean(document.querySelector('.transition-active-track'));
                document.activeElement.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
                await frame();
                checks.transition_escape_closes_track = !document.querySelector('.transition-active-track');
            }
        }
        const response = await fetch('/api/state');
        checks.same_origin_fetch_json = response.ok && Boolean((await response.json()).catalog);
        await select(group.points.find(item => item.name === 'opcode_cp').semantic_id);
        await frame();
    } catch (error) {
        errors.push(String(error));
    } finally {
        window.removeEventListener('error', onError);
        window.removeEventListener('unhandledrejection', onError);
    }
    return {checks, errors, input_method: 'synthetic DOM events, not physical mouse/keyboard'};
})()"""


def probe_succeeded(report: dict) -> bool:
    """Keep failed/timed-out probes from looking like successful validation."""
    features = report.get("checks", {})
    smoke = report.get("page_smoke", {})
    checks = smoke.get("checks", {})
    return bool(features and checks) and all(value is True for value in features.values()) and not smoke.get("errors") and not smoke.get("error") and all(
        value.get("rendered") is True and value.get("dsl_card") is True
        if isinstance(value, dict) else value is True
        for value in checks.values()
    )


def inspect_compatibility(window, *, close: bool = False) -> bool:
    """Report capability probes; this is not a standards conformance suite."""
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if window.evaluate_js("document.querySelectorAll('.node[data-id]').length > 0"):
                report = window.evaluate_js(COMPATIBILITY_PROBE)
                report["pywebview_version"] = version("pywebview")
                complete = Event()
                def receive(result):
                    report["page_smoke"] = result
                    complete.set()
                window.evaluate_js(PAGE_SMOKE_PROBE, callback=receive)
                if not complete.wait(timeout=20):
                    report["page_smoke"] = {"error": "Timed out after 20 seconds"}
                report["passed"] = probe_succeeded(report)
                print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
                return report["passed"]
            time.sleep(0.1)
        print("Compatibility probe failed: coverage tree did not load in 20 seconds.", flush=True)
    except Exception as exc:
        print(f"Compatibility probe failed: {exc}", flush=True)
    finally:
        if close:
            window.destroy()
    return False


def create_session() -> GuiSession:
    """Resolve real DSL resources, including macOS bundle resource symlinks."""
    if getattr(sys, "frozen", False):
        source = Path(sys._MEIPASS) / "packet.py"
        if not source.is_file():
            raise RuntimeError("The GUI bundle is missing its example DSL source")
        source_root = source.resolve().parent
        return GuiSession(source_root, (source_root,))
    return GuiSession(HERE, (HERE, ROOT / "python", ROOT / "extensions" / "svtypes_design_manifest" / "src", ROOT / "extensions" / "svtypes_coverage_tools" / "src"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--probe", action="store_true", help="Print Web API and layout capability checks")
    parser.add_argument("--probe-only", action="store_true", help="Run probes and close the temporary window")
    parser.add_argument("--debug", action="store_true", help="Enable webview developer tools")
    args = parser.parse_args(argv)
    try:
        import webview
    except ImportError:
        parser.error("Optional dependency missing: install pywebview in this Python environment")
    session = create_session()
    session.scan(["packet"])
    server, thread, url = start_gui_server(session, port=args.port)
    probe_results = []
    try:
        # No Python bridge is exposed. All editing stays in the existing web GUI.
        # Downloads retain pywebview's disabled default; do not allow file:// access.
        webview.settings["ALLOW_FILE_URLS"] = False
        webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = False
        window = webview.create_window(
            "SvTypes · 覆盖率工作台 / pywebview 预览",
            url,
            width=1440,
            height=960,
            min_size=(960, 640),
            text_select=True,
            background_color="#f6f8fc",
        )
        if args.probe or args.probe_only:
            def loaded():
                probe_results.append(inspect_compatibility(window, close=args.probe_only))
            window.events.loaded += loaded
        print(f"pywebview {version('pywebview')} · {url}", flush=True)
        # Never silently fall back to Windows' deprecated IE renderer.
        webview.start(gui="edgechromium" if sys.platform == "win32" else None, debug=args.debug)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return int(args.probe_only and probe_results != [True])


if __name__ == "__main__":
    raise SystemExit(main())

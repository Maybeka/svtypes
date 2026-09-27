#!/usr/bin/env python3
"""Conservatively migrate SvTypes type-spec constructor calls.

This is intentionally an opt-in source transformer, not a runtime
compatibility layer.  It preserves all text outside rewritten call spans and
reports calls whose positional arguments would make an automatic conversion
ambiguous.  Run it with ``--check`` first and use ``--write`` only after
reviewing the report.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Iterable


PACKED = {"Bit", "Logic", "Reg"}
COLLECTIONS = {"Array": 2, "DynArray": 1, "Queue": 1, "AssocArray": 2}
HANDLES = {"Object", "RemoteRef"}


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    message: str


@dataclass(frozen=True)
class Replacement:
    start: int
    end: int
    text: str


def _offsets(source: str) -> list[int]:
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    return offsets


def _span(node: ast.AST, offsets: list[int]) -> tuple[int, int]:
    assert hasattr(node, "lineno") and hasattr(node, "end_lineno")
    return (
        offsets[node.lineno - 1] + node.col_offset,  # type: ignore[attr-defined]
        offsets[node.end_lineno - 1] + node.end_col_offset,  # type: ignore[attr-defined]
    )


def _text(node: ast.AST, source: str, offsets: list[int]) -> str:
    start, end = _span(node, offsets)
    return source[start:end]


def _name(node: ast.AST) -> str | None:
    return node.id if isinstance(node, ast.Name) else None


def _keywords(node: ast.Call, source: str, offsets: list[int]) -> str:
    parts: list[str] = []
    for keyword in node.keywords:
        if keyword.arg is None:
            raise ValueError("**kwargs cannot be migrated automatically")
        parts.append(f"{keyword.arg}={_text(keyword.value, source, offsets)}")
    return ", ".join(parts)


def _bracket(base: str, arguments: Iterable[str]) -> str:
    return f"{base}[{', '.join(arguments)}]"


def _literal_parameter_dtype(value: ast.AST) -> str | None:
    if isinstance(value, ast.Constant):
        if isinstance(value.value, bool) or isinstance(value.value, int):
            return "Int"
        if isinstance(value.value, float):
            return "Real"
        if isinstance(value.value, str):
            return "String"
    return None


def _render_nested_call(node: ast.AST, source: str, offsets: list[int]) -> str:
    """Render a nested descriptor call in its migrated form when unambiguous.

    Parent replacements cover their child spans, so a plain textual replacement
    of ``Array(Bit(8), 2)`` would otherwise leave ``Bit(8)`` behind.  This
    renderer is deliberately small: it is used only for arguments which are
    themselves one of the supported descriptor calls; all other expressions
    retain their original source text.
    """

    if not isinstance(node, ast.Call):
        return _text(node, source, offsets)
    base = _name(node.func)
    if base in PACKED and node.args and len(node.args) == 1 and not node.keywords:
        return _bracket(base, [_text(node.args[0], source, offsets)])
    if base in COLLECTIONS and len(node.args) == COLLECTIONS[base] and not node.keywords:
        values = [_render_nested_call(arg, source, offsets) for arg in node.args]
        return _bracket(base, values)
    if base in HANDLES and len(node.args) == 1 and not node.keywords:
        return _bracket(base, [_text(node.args[0], source, offsets)])
    # Fixed-width scalar/enum/object classes are passed as a type, not as a
    # zero-valued descriptor.  Restrict this to capitalized simple names so a
    # general factory invocation is never silently changed.
    if isinstance(node.func, ast.Name) and not node.args and not node.keywords and base and base[:1].isupper():
        return base
    if base in PACKED and node.args and len(node.args) <= 2:
        keywords = list(node.keywords)
        signed = next((item for item in keywords if item.arg == "signed"), None)
        if signed is not None:
            if not isinstance(signed.value, ast.Constant) or not isinstance(signed.value.value, bool):
                return _text(node, source, offsets)
            keywords.remove(signed)
        try:
            pieces = [_text(node.args[0], source, offsets)]
            if signed is not None and signed.value.value:
                pieces.append("Signed")
            values = [_text(node.args[1], source, offsets)] if len(node.args) == 2 else []
            keyword_text = _keywords(ast.Call(func=node.func, args=[], keywords=keywords), source, offsets)
            if keyword_text:
                values.append(keyword_text)
            return f"{_bracket(base, pieces)}({', '.join(values)})"
        except ValueError:
            return _text(node, source, offsets)
    if base in COLLECTIONS and len(node.args) == COLLECTIONS[base]:
        try:
            values = [_render_nested_call(arg, source, offsets) for arg in node.args]
            keyword_text = _keywords(node, source, offsets)
            return f"{_bracket(base, values)}({keyword_text})"
        except ValueError:
            return _text(node, source, offsets)
    if base in HANDLES and node.args:
        try:
            target = _text(node.args[0], source, offsets)
            values = [_text(arg, source, offsets) for arg in node.args[1:]]
            keyword_text = _keywords(node, source, offsets)
            if keyword_text:
                values.append(keyword_text)
            return f"{_bracket(base, [target])}({', '.join(values)})"
        except ValueError:
            return _text(node, source, offsets)
    return _text(node, source, offsets)


class Transformer(ast.NodeVisitor):
    def __init__(self, path: Path, source: str) -> None:
        self.path = path
        self.source = source
        self.offsets = _offsets(source)
        self.replacements: list[Replacement] = []
        self.findings: list[Finding] = []

    def _replace(self, node: ast.AST, text: str) -> None:
        start, end = _span(node, self.offsets)
        self.replacements.append(Replacement(start, end, text))

    def _report(self, node: ast.AST, message: str) -> None:
        self.findings.append(Finding(self.path, node.lineno, message))  # type: ignore[attr-defined]

    def visit_Call(self, node: ast.Call) -> None:
        # Process an outer ``Parameter(...)(...)`` once as a whole.  Its inner
        # call must not receive a competing replacement.
        if isinstance(node.func, ast.Call) and _name(node.func.func) == "Parameter":
            self._parameter_bound_call(node)
            return
        self.generic_visit(node)
        base = _name(node.func)
        if base is None:
            return
        if base in PACKED:
            self._packed(node, base)
        elif base in COLLECTIONS:
            self._collection(node, base, COLLECTIONS[base])
        elif base in HANDLES:
            self._handle(node, base)
        elif base == "Parameter":
            self._parameter(node)

    def _packed(self, node: ast.Call, base: str) -> None:
        if not node.args:
            self._report(node, f"{base}() has no legacy width; leave unchanged")
            return
        if len(node.args) > 2:
            self._report(node, f"{base} positional signed/radix/policy arguments require manual migration")
            return
        try:
            keywords = list(node.keywords)
            signed = next((item for item in keywords if item.arg == "signed"), None)
            if signed is not None:
                if not isinstance(signed.value, ast.Constant) or not isinstance(signed.value.value, bool):
                    self._report(node, f"{base} signed= expression requires manual migration")
                    return
                keywords.remove(signed)
            args = [_text(node.args[0], self.source, self.offsets)]
            if signed is not None and signed.value.value:
                args.append("Signed")
            spec = _bracket(base, args)
            rest = ([node.args[1]] if len(node.args) == 2 else [])
            pieces = [_text(value, self.source, self.offsets) for value in rest]
            keyword_text = _keywords(ast.Call(func=node.func, args=[], keywords=keywords), self.source, self.offsets)
            if keyword_text:
                pieces.append(keyword_text)
            self._replace(node, f"{spec}({', '.join(pieces)})")
        except ValueError as exc:
            self._report(node, f"{base}: {exc}")

    def _collection(self, node: ast.Call, base: str, arity: int) -> None:
        if len(node.args) != arity:
            self._report(node, f"{base} needs exactly {arity} positional type-spec argument(s) for automatic migration")
            return
        try:
            arguments = [_render_nested_call(arg, self.source, self.offsets) for arg in node.args]
            keyword_text = _keywords(node, self.source, self.offsets)
            self._replace(node, f"{_bracket(base, arguments)}({keyword_text})")
        except ValueError as exc:
            self._report(node, f"{base}: {exc}")

    def _handle(self, node: ast.Call, base: str) -> None:
        if not node.args:
            self._report(node, f"{base}() needs a target type/name")
            return
        try:
            target = _text(node.args[0], self.source, self.offsets)
            rest = [_text(arg, self.source, self.offsets) for arg in node.args[1:]]
            keyword_text = _keywords(node, self.source, self.offsets)
            if keyword_text:
                rest.append(keyword_text)
            self._replace(node, f"{_bracket(base, [target])}({', '.join(rest)})")
        except ValueError as exc:
            self._report(node, f"{base}: {exc}")

    def _parameter(self, node: ast.Call) -> None:
        if len(node.args) != 1 or node.keywords:
            self._report(node, "Parameter construction requires manual migration")
            return
        value = node.args[0]
        if isinstance(value, ast.Name) and value.id in {"Int", "LongInt", "String", "Real", "ShortReal", "type"}:
            self._replace(node, f"Parameter[{_text(value, self.source, self.offsets)}]()")
            return
        dtype = _literal_parameter_dtype(value)
        if dtype is None:
            self._report(node, "Parameter default cannot be typed unambiguously")
            return
        self._replace(node, f"Parameter[{dtype}]({_text(value, self.source, self.offsets)})")

    def _parameter_bound_call(self, node: ast.Call) -> None:
        declaration = node.func
        if declaration.keywords or len(node.args) != 1 or node.keywords:
            self._report(node, "Parameter declaration/binding requires manual migration")
            return
        if not declaration.args:
            dtype = _literal_parameter_dtype(node.args[0])
            if dtype is None:
                self._report(node, "Parameter default cannot be typed unambiguously")
                return
        else:
            if len(declaration.args) != 1:
                self._report(node, "Parameter declaration/binding requires manual migration")
                return
            dtype = _text(declaration.args[0], self.source, self.offsets)
            if dtype not in {"Int", "LongInt", "String", "Real", "ShortReal", "type"}:
                self._report(node, "Parameter dtype requires manual migration")
                return
        self._replace(node, f"Parameter[{dtype}]({_text(node.args[0], self.source, self.offsets)})")


def transform(path: Path) -> tuple[str, list[Finding], int]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    transformer = Transformer(path, source)
    transformer.visit(tree)
    # Keep the outermost replacement when calls nest.  Its renderer already
    # transforms supported child descriptors; applying a child first would
    # otherwise invalidate the parent span.
    replacements: list[Replacement] = []
    covered_end = -1
    for candidate in sorted(transformer.replacements, key=lambda item: item.start):
        if candidate.start < covered_end:
            continue
        replacements.append(candidate)
        covered_end = candidate.end
    rendered = source
    for replacement in reversed(replacements):
        rendered = rendered[:replacement.start] + replacement.text + rendered[replacement.end:]
    return rendered, transformer.findings, len(replacements)


class NestedSpecNormalizer(ast.NodeVisitor):
    """Remove an accidental zero-argument descriptor call inside ``X[...]``.

    This is also useful when upgrading a checkout produced by an early preview
    of the migration tool.  It has intentionally narrower semantics than the
    main transformer: only an already-bracketed collection slice is examined,
    and only zero-argument type calls are stripped.
    """

    def __init__(self, source: str) -> None:
        self.source = source
        self.offsets = _offsets(source)
        self.replacements: list[Replacement] = []
        self._collection_depth = 0

    def visit_Subscript(self, node: ast.Subscript) -> None:
        is_collection = _name(node.value) in COLLECTIONS
        if is_collection:
            self._collection_depth += 1
        self.generic_visit(node)
        if is_collection:
            self._collection_depth -= 1

    def visit_Call(self, node: ast.Call) -> None:
        self.generic_visit(node)
        if not self._collection_depth or node.args or node.keywords:
            return
        base = _name(node.func)
        is_bracketed_type = (
            isinstance(node.func, ast.Subscript)
            and _name(node.func.value) in (PACKED | set(COLLECTIONS) | HANDLES)
        )
        is_simple_type = isinstance(node.func, ast.Name) and bool(base) and base[:1].isupper()
        if is_bracketed_type or is_simple_type:
            self.replacements.append(
                Replacement(
                    *_span(node, self.offsets),
                    _render_normalized_type_expr(node.func, self.source, self.offsets),
                )
            )


def _render_normalized_type_expr(node: ast.AST, source: str, offsets: list[int]) -> str:
    """Render a type expression while stripping nested zero-argument calls."""

    if isinstance(node, ast.Call) and not node.args and not node.keywords:
        base = _name(node.func)
        is_bracketed = isinstance(node.func, ast.Subscript) and _name(node.func.value) in (PACKED | set(COLLECTIONS) | HANDLES)
        if is_bracketed or (base and base[:1].isupper()):
            return _render_normalized_type_expr(node.func, source, offsets)
    if isinstance(node, ast.Subscript):
        return (
            f"{_render_normalized_type_expr(node.value, source, offsets)}"
            f"[{_render_normalized_type_expr(node.slice, source, offsets)}]"
        )
    if isinstance(node, ast.Tuple):
        return ", ".join(_render_normalized_type_expr(item, source, offsets) for item in node.elts)
    return _text(node, source, offsets)


def normalize_nested_specs(path: Path) -> tuple[str, int]:
    source = path.read_text(encoding="utf-8")
    normalizer = NestedSpecNormalizer(source)
    normalizer.visit(ast.parse(source, filename=str(path)))
    replacements: list[Replacement] = []
    covered_end = -1
    for candidate in sorted(normalizer.replacements, key=lambda item: item.start):
        if candidate.start < covered_end:
            continue
        replacements.append(candidate)
        covered_end = candidate.end
    rendered = source
    for replacement in reversed(replacements):
        rendered = rendered[:replacement.start] + replacement.text + rendered[replacement.end:]
    return rendered, len(replacements)


def _source_paths(inputs: Iterable[Path]) -> list[Path]:
    """Expand file and directory operands deterministically.

    The migration command is normally pointed at source roots (for example
    ``python tests examples``), so requiring callers to pre-expand every
    Python file is both surprising and error-prone.
    """

    expanded: set[Path] = set()
    for path in inputs:
        if path.is_dir():
            expanded.update(candidate for candidate in path.rglob("*.py") if candidate.is_file())
        elif path.is_file():
            expanded.add(path)
        else:
            raise FileNotFoundError(path)
    return sorted(expanded)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--summary-only", action="store_true", help="suppress individual manual-migration diagnostics")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="report planned rewrites and unsupported calls")
    mode.add_argument("--write", action="store_true", help="apply unambiguous rewrites in place")
    mode.add_argument("--normalize-nested", action="store_true", help="normalize zero-argument descriptors inside collection specs")
    args = parser.parse_args(argv)
    findings: list[Finding] = []
    changed = 0
    for path in _source_paths(args.paths):
        if args.normalize_nested:
            rendered, count = normalize_nested_specs(path)
            if rendered != path.read_text(encoding="utf-8"):
                path.write_text(rendered, encoding="utf-8")
            changed += count
            continue
        rendered, path_findings, count = transform(path)
        findings.extend(path_findings)
        if args.write and rendered != path.read_text(encoding="utf-8"):
            path.write_text(rendered, encoding="utf-8")
        changed += count
    if not args.summary_only:
        for finding in findings:
            print(f"{finding.path}:{finding.line}: {finding.message}", file=sys.stderr)
    if args.check or args.normalize_nested:
        print(f"{changed} unambiguous rewrite(s); {len(findings)} manual migration(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())

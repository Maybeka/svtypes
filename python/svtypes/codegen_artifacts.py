"""Renderer-owned auxiliary artifacts; never parse generated source text."""

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import re

_artifacts: ContextVar[dict[str, bytes] | None] = ContextVar("svtypes_codegen_artifacts", default=None)


@contextmanager
def collect_support_artifacts():
    artifacts: dict[str, bytes] = {}
    token = _artifacts.set(artifacts)
    try:
        yield artifacts
    finally:
        _artifacts.reset(token)


def sv_support_lines(cls, lines: list[str], indent: str, part: str = "") -> list[str]:
    return _support_lines(cls, lines, indent, "sv", part)


def cpp_support_lines(cls, lines: list[str], indent: str, part: str = "") -> list[str]:
    return _support_lines(cls, lines, indent, "cpp", part)


def _support_lines(cls, lines: list[str], indent: str, language: str, part: str) -> list[str]:
    artifacts = _artifacts.get()
    if artifacts is None:
        return lines
    from .schema import unified_type_name

    identity = unified_type_name(cls)
    stem = re.sub(r"[^A-Za-z0-9_]+", "_", identity).strip("_")
    suffix = hashlib.sha256(identity.encode()).hexdigest()[:10]
    extension = "svh" if language == "sv" else "hpp"
    role = f"_{part}" if part else ""
    path = f"support/{stem}_{suffix}{role}.{extension}"
    content = ("\n".join(lines).rstrip() + "\n").encode()
    if path in artifacts and artifacts[path] != content:
        raise ValueError(f"conflicting generated support artifact: {path}")
    artifacts[path] = content
    directive = "`include" if language == "sv" else "#include"
    return [f'{indent}{directive} "{path}"']

"""One runtime-only resource budget shared by a complete randomize solve."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from math import ceil
from time import monotonic


class SolveFailure(Exception):
    def __init__(self, reason: str, phase: str, backend_reason: str | None = None):
        self.reason = reason
        self.phase = phase
        self.backend_reason = backend_reason
        super().__init__(reason)


@dataclass
class SolveBudget:
    deadline: float | None
    check_limit: int | None
    checks: int = 0

    def poll(self, phase: str = "sampling") -> None:
        if self.deadline is not None and monotonic() >= self.deadline:
            raise SolveFailure("timeout", phase)

    def check(self, solver, z3):
        self.poll("backend_check")
        if self.check_limit is not None and self.checks >= self.check_limit:
            raise SolveFailure("resource_limit", "backend_check", "solve_check_limit exhausted")
        self.checks += 1
        if self.deadline is not None:
            # Backend timeout is an unsigned 32-bit option; keep the Python
            # deadline authoritative instead of wrapping a long user budget.
            remaining_ms = (self.deadline - monotonic()) * 1000
            solver.set(timeout=max(1, ceil(min(remaining_ms, (1 << 32) - 1))))
        result = solver.check()
        self.poll("backend_check")
        if result == z3.unknown:
            detail = solver.reason_unknown()
            reason = "timeout" if "timeout" in detail.lower() else "unknown"
            raise SolveFailure(reason, "backend_check", detail)
        return result


_current: ContextVar[SolveBudget | None] = ContextVar("svtypes_solve_budget", default=None)


@contextmanager
def solving(timeout_ms: int | None, check_limit: int | None):
    try:
        deadline = None if timeout_ms is None else monotonic() + timeout_ms / 1000
    except OverflowError:
        # Such a duration exceeds any representable process lifetime.
        deadline = None
    budget = SolveBudget(deadline, check_limit)
    token = _current.set(budget)
    try:
        yield budget
    finally:
        _current.reset(token)


def poll(phase: str = "sampling") -> None:
    budget = _current.get()
    if budget is not None:
        budget.poll(phase)


def check(solver, z3):
    # Direct internal backend calls retain UNKNOWN semantics even outside a
    # public randomize invocation, without imposing a separate helper budget.
    budget = _current.get() or SolveBudget(None, None)
    return budget.check(solver, z3)

"""Names used by the source-only coverage declaration DSL.

The declaration frontend recognizes these names from AST; it never evaluates
the decorated function.  Their small runtime definitions keep annotations and
language servers useful without turning them into a callback DSL.
"""

from __future__ import annotations

from typing import Any


class CovPoint:
    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__()


class CovPointArray:
    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__()


class Cross:
    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__()


class CoverGroupOption: pass
class CoverGroupTypeOption: pass
class CovPointOption: pass
class CrossOption: pass


class _BinNamespace:
    def __getitem__(self, selector: Any) -> Any:
        return selector


bins = _BinNamespace()
ignore_bins = _BinNamespace()
illegal_bins = _BinNamespace()
transition_bins = _BinNamespace()
default_bins = object()


def repeat(term: Any, minimum: int, maximum: int) -> Any:
    """Declaration-only marker for a bounded transition repetition.

    The frontend consumes the AST directly, so this function is never called
    while compiling a covergroup.
    """
    return term, minimum, maximum

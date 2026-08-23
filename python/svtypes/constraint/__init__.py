from .collect import constraint
from .ir import CONSTRAINT_IR_VERSION
from .layer import rand_layer, set_layered_randomization_reference_policy
from .sample import LayeredRandomizeStatus, RandomContext, RandomizeStatus


class _DistSyntax:
    """Source-only marker for the ``expression @ dist[...]`` constraint DSL."""

    def __class_getitem__(cls, _item):  # pragma: no cover - functions are parsed, not run
        raise TypeError("dist[...] is only valid inside an @constraint method")


dist = _DistSyntax


class _UniqueSyntax:
    """Source-only marker for scalar ``unique(...)`` constraints."""

    def __call__(self, *_args, **_kwargs):  # pragma: no cover - parsed, not run
        raise TypeError("unique(...) is only valid inside an @constraint method")


unique = _UniqueSyntax()


class _SoftSyntax:
    """Source-only marker for ``soft(expression)`` constraints."""

    def __call__(self, *_args, **_kwargs):  # pragma: no cover - parsed, not run
        raise TypeError("soft(...) is only valid inside an @constraint method")


soft = _SoftSyntax()

__all__ = [
    "CONSTRAINT_IR_VERSION",
    "LayeredRandomizeStatus",
    "RandomContext",
    "RandomizeStatus",
    "constraint",
    "dist",
    "unique",
    "soft",
    "rand_layer",
    "set_layered_randomization_reference_policy",
]

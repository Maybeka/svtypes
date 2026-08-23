from .collect import constraint
from .ir import CONSTRAINT_IR_VERSION
from .layer import rand_layer, set_layered_randomization_reference_policy
from .sample import LayeredRandomizeStatus, RandomContext, RandomizeStatus


class _DistSyntax:
    """Source-only marker for the ``expression @ dist[...]`` constraint DSL."""

    def __class_getitem__(cls, _item):  # pragma: no cover - functions are parsed, not run
        raise TypeError("dist[...] is only valid inside an @constraint method")


dist = _DistSyntax

__all__ = [
    "CONSTRAINT_IR_VERSION",
    "LayeredRandomizeStatus",
    "RandomContext",
    "RandomizeStatus",
    "constraint",
    "dist",
    "rand_layer",
    "set_layered_randomization_reference_policy",
]

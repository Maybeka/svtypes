"""Public construction API for generated, schema-defined data classes.

This module deliberately accepts a complete canonical type name from its caller.
Callable naming and invocation semantics remain transport concerns; an
integration such as SVX supplies the resulting identity after applying its own
approved manifest rules.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
import re
from typing import Any, Iterable

from .base import TypeBase
from .errors import DeclarationError
from .object import ObjectDescriptor, SvObject


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class RecordField:
    """One ordered generated-record field."""

    name: str
    descriptor: TypeBase | ObjectDescriptor

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.name):
            raise DeclarationError(f"invalid generated record field name: {self.name!r}")
        if not isinstance(self.descriptor, (TypeBase, ObjectDescriptor)):
            raise DeclarationError(
                f"generated record field {self.name!r} has unsupported descriptor "
                f"{type(self.descriptor).__name__}"
            )


class RecordSchema:
    """Immutable, ordered schema input for a generated :class:`SvObject` type."""

    def __init__(
        self,
        unified_type_name: str,
        fields: Iterable[RecordField | tuple[str, TypeBase | ObjectDescriptor]],
        *,
        class_name: str | None = None,
    ) -> None:
        if not isinstance(unified_type_name, str) or not unified_type_name:
            raise DeclarationError("generated record unified_type_name must be nonempty")
        normalized = tuple(
            item if isinstance(item, RecordField) else RecordField(*item)
            for item in fields
        )
        names = [field.name for field in normalized]
        if len(names) != len(set(names)):
            raise DeclarationError("generated record field names must be unique")
        if class_name is not None and not _IDENTIFIER.fullmatch(class_name):
            raise DeclarationError(f"invalid generated record class name: {class_name!r}")
        self._unified_type_name = unified_type_name
        self._fields = normalized
        self._class_name = class_name

    @property
    def unified_type_name(self) -> str:
        return self._unified_type_name

    @property
    def fields(self) -> tuple[RecordField, ...]:
        return self._fields

    @property
    def is_void(self) -> bool:
        return not self._fields

    def build(self) -> type[SvObject] | None:
        """Create a deterministic, unregistered generated record class.

        Empty records use the public void convention and therefore return
        ``None`` rather than allocating an otherwise meaningless envelope.
        """
        if self.is_void:
            return None
        if self._class_name is not None:
            class_name = self._class_name
        else:
            suffix = re.sub(r"[^A-Za-z0-9_]", "_", self.unified_type_name).strip("_")
            class_name = f"GeneratedRecord_{suffix}"[-200:]
            if not _IDENTIFIER.fullmatch(class_name):  # defensive after truncation
                class_name = "GeneratedRecord"
        namespace: dict[str, Any] = {
            "__module__": "svtypes.generated",
            "_svtypes_unified_type_name": self.unified_type_name,
            "__doc__": f"Generated SvTypes record for {self.unified_type_name}.",
        }
        for field in self.fields:
            namespace[field.name] = copy.deepcopy(field.descriptor)
        return type(class_name, (SvObject,), namespace)

    generated_class = build


# A deliberately descriptive alias for consumers that prefer a factory-style
# spelling over the schema object itself.
GeneratedDataClass = RecordSchema

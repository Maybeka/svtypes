"""Public SvTypes exception hierarchy."""

from dataclasses import dataclass


class SvTypesError(Exception):
    """Base class for errors raised by public SvTypes APIs."""


class DeclarationError(SvTypesError, ValueError):
    """A type or schema declaration is invalid."""


class CoverageError(SvTypesError):
    """Base class for functional-coverage declaration and runtime errors."""


class CoverageDeclarationError(CoverageError, DeclarationError):
    """A functional-coverage declaration is invalid."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class UnsupportedTypeError(SvTypesError, TypeError):
    """A requested type/backend combination is unsupported."""


class EncodeError(SvTypesError, ValueError):
    """A value cannot be encoded by the selected codec."""


class DecodeError(SvTypesError, ValueError):
    """Input bytes cannot be decoded by the selected codec."""


class CompatibilityError(DecodeError):
    """Encoding, schema, format, or runtime metadata is incompatible."""


class RegistryError(SvTypesError, ValueError):
    """A type or object registry operation is invalid."""


class ResourceLimitError(DecodeError):
    """A decoder resource limit was exceeded."""


class ExternalStorageError(SvTypesError):
    """An externally bound field could not be read, written, or validated."""


class ExternalStorageClosedError(ExternalStorageError):
    """A temporary external value binding has already been closed."""


@dataclass(frozen=True, slots=True)
class StorageRelocationFailure:
    """One failed migration stage and its original exception."""

    phase: str
    cause: BaseException


class StorageRelocationError(ExternalStorageError):
    """Migration failed without hiding the body or subsequent cleanup errors."""

    def __init__(self, failures: tuple[StorageRelocationFailure, ...], *,
                 restored: bool, body_error: BaseException | None = None) -> None:
        self.failures = failures
        self.phase = failures[0].phase
        self.restored = restored
        self.body_error = body_error

        def describe(error):
            parts, seen = [], set()
            while error is not None and id(error) not in seen:
                seen.add(id(error))
                parts.append(f'{type(error).__name__}: {error}')
                error = error.__cause__
            return ' <- '.join(parts)

        details = '; '.join(f'{f.phase}: {describe(f.cause)}' for f in failures)
        super().__init__(f'slot relocation failed ({details}); latest value restored: {restored}')


class ConstraintError(SvTypesError):
    """A constrained-random declaration or runtime operation is invalid."""


class ConstraintSyntaxError(ConstraintError, DeclarationError):
    """Constraint source is outside the allowed Python subset."""


class ConstraintNameError(ConstraintError, DeclarationError):
    """A constraint symbol cannot be resolved."""


class ConstraintTypeError(ConstraintError, DeclarationError):
    """A constraint expression violates the Typed IR type rules."""


class ConstraintUnsupportedError(ConstraintError, DeclarationError):
    """A constraint construct is outside the first-phase language."""


class ConstraintBackendError(ConstraintError):
    """The selected constraint backend cannot complete a solve."""


class LayeredRandomizationPriorityWarning(UserWarning):
    """A constraint refers to a random variable of lower layered-randomize priority."""

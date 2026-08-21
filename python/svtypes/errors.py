"""Public SvTypes exception hierarchy."""


class SvTypesError(Exception):
    """Base class for errors raised by public SvTypes APIs."""


class DeclarationError(SvTypesError, ValueError):
    """A type or schema declaration is invalid."""


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

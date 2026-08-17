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

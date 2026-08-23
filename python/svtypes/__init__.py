from .base import FieldOptions, TypeBase, BuiltInType, UserDefinedType

from .bit import Bit
from .logic import Logic, LogicValue, Reg
from .int import Int, LongInt
from .parameter import ParamRef, Parameter
from .real import Real, ShortReal, RealTime
from .string import String
from .collection import Array, DynArray, Queue, AssocArray
from .remote_ref import RemoteRef, RemoteRefValue
from .limits import DecodeLimits
from .record_schema import GeneratedDataClass, RecordField, RecordSchema
from .capabilities import (
    RuntimeCapabilities,
    STABLE_CAPABILITIES,
    require_runtime_compatible,
    runtime_capabilities,
)

from .object import (
    ObjectRegistry,
    CodecSession,
    PackContext,
    UnpackContext,
    SvObject,
    allocate_object_number,
    clear_object_registry,
    get_object,
    register_object,
    reset_object_number_allocator,
    unregister_object,
    svobj,
    Object,
    new,
    SvStruct,
)
from .enum import Enum
from .errors import (
    CompatibilityError,
    ConstraintBackendError,
    ConstraintError,
    ConstraintNameError,
    ConstraintSyntaxError,
    ConstraintTypeError,
    ConstraintUnsupportedError,
    DeclarationError,
    DecodeError,
    EncodeError,
    LayeredRandomizationPriorityWarning,
    RegistryError,
    ResourceLimitError,
    SvTypesError,
    UnsupportedTypeError,
)
from .constraint import (
    CONSTRAINT_IR_VERSION,
    LayeredRandomizeStatus,
    RandomContext,
    RandomizeStatus,
    constraint,
    dist,
    rand_layer,
    set_layered_randomization_reference_policy,
    soft,
    unique,
)
from .randomizable import is_randomizable

from .scope import Scope, Package, Namespace, get_package
from .schema import (
    BINARY_FORMAT_VERSION,
    GENERATOR_RUNTIME_ABI_VERSION,
    OBJECT_ENVELOPE_VERSION,
    SCHEMA_FORMAT_VERSION,
    SchemaDescriptor,
    EncodingDescriptor,
    unified_type_name,
    checked_unpack,
    schema_descriptor,
    encoding_descriptor,
)
from .generator import GenerationResult, generate

from pathlib import Path
import sysconfig

__version__ = "1.2.0"


def package_root() -> Path:
    return Path(__file__).resolve().parent


def runtime_root() -> Path:
    package_dir = package_root()
    candidates = (
        package_dir / "svtypes_runtime",
        package_dir.parent / "svtypes_runtime",
        package_dir.parents[1] / "svtypes_runtime",
        Path(sysconfig.get_path("data")) / "svtypes_runtime",
    )
    for candidate in candidates:
        if (candidate / "sv" / "svtypes_pkg.sv").is_file():
            return candidate
    searched = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"SvTypes runtime assets are not installed; searched: {searched}")


def sv_runtime_file() -> Path:
    return runtime_root() / "sv" / "svtypes_pkg.sv"


def sv_weak_runtime_file() -> Path:
    return runtime_root() / "sv" / "svtypes_pkg_weak.sv"


def cpp_include_dir() -> Path:
    return runtime_root() / "cpp"

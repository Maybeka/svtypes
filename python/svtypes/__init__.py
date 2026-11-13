from .base import FieldOptions, TypeBase, BuiltInType, UserDefinedType

from .bit import Bit
from .logic import Logic, LogicValue, Reg
from .typespec import (
    Signed,
    Unsigned,
    SignedFamily,
    TypeSpec,
    is_type_spec,
    is_materializable_type,
    materialize_type_spec,
    type_spec_from_identity,
    type_spec_identity,
)
BitSigned = SignedFamily(Bit)
LogicSigned = SignedFamily(Logic)
RegSigned = SignedFamily(Logic, declaration_style="reg")
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
    SvPackedStruct,
)
from .enum import Enum
from .errors import (
    CompatibilityError,
    CoverageDeclarationError,
    CoverageError,
    ConstraintBackendError,
    ConstraintError,
    ConstraintNameError,
    ConstraintSyntaxError,
    ConstraintTypeError,
    ConstraintUnsupportedError,
    DeclarationError,
    DecodeError,
    EncodeError,
    ExternalStorageClosedError,
    ExternalStorageError,
    StorageRelocationError,
    StorageRelocationFailure,
    LayeredRandomizationPriorityWarning,
    RegistryError,
    ResourceLimitError,
    SvTypesError,
    UnsupportedTypeError,
)
from .external_storage import (
    ExternalBorrowFieldStorage,
    ExternalBorrowPathStorage,
    ExternalBorrowStorage,
    ExternalFieldStorage,
    ExternalValueBinding,
    FieldDescriptor,
    FieldIdentity,
    FieldIndex,
    FieldKey,
    FieldMember,
    FieldOperation,
    FieldPath,
    MemoryExternalFieldStorage,
    bind_external_value,
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
    solve_before,
    unique,
)
from .randomizable import is_randomizable
from .variable_access import ElementBorrow, borrow_element, field, read_value, write_value
from .storage_location import ExternalStorageLocation, external_storage_location
from .slot_identity import SlotIdentity, slot_identity
from .storage_relocation import StorageFactory, relocated_slot
from .storage_policy import (
    BorrowPurpose, current_borrow_purpose,
    ExternalRefValidationStorage, ExternalValueParameterStorage,
)
from .checked_call import CheckedAccessError, CopyoutError, Inout, Input, Output, Ref
from .checked_source import run_checked
from .checked_imports import observe_module_sources
from ._checked_callbacks import checked_call_gateway, checked_library_sources
from .coverage import (
    CoverageDatabase,
    CovPoint,
    CovPointArray,
    CovPointOption,
    CoverGroupOption,
    CoverGroupTypeOption,
    CoverInput,
    CoverRef,
    CoverageInitCallIR,
    CoverageInitIR,
    Cross,
    CrossQueueType,
    CrossOption,
    bins,
    covergroup,
    coverage_init,
    coverage_initializers,
    preview_coverage_layout,
    default_bins,
    export_ucis,
    export_ucis_file,
    ignore_bins,
    import_ucis,
    import_ucis_file,
    illegal_bins,
    set_coverage_case_name,
    repeat,
    transition_bins,
)

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
    sv_type_expression,
    sv_packer_expression,
    sv_declaration,
    sv_codegen_context,
)
from .generator import GenerationResult, generate

from pathlib import Path
import sysconfig

__version__ = "1.5.0"


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


from .checked_imports import _install as _install_checked_imports

_install_checked_imports()
del _install_checked_imports

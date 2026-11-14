from .base import (
    FieldOptions as FieldOptions,
    TypeBase as TypeBase,
    BuiltInType as BuiltInType,
    UserDefinedType as UserDefinedType,
)

from .bit import Bit as Bit
from .logic import Logic as Logic, LogicValue as LogicValue, Reg as Reg
from .typespec import (
    Signed as Signed,
    Unsigned as Unsigned,
    SignedFamily as SignedFamily,
    TypeSpec as TypeSpec,
    is_type_spec as is_type_spec,
    is_materializable_type as is_materializable_type,
    materialize_type_spec as materialize_type_spec,
    type_spec_from_identity as type_spec_from_identity,
    type_spec_identity as type_spec_identity,
)
BitSigned = SignedFamily(Bit)
LogicSigned = SignedFamily(Logic)
RegSigned = SignedFamily(Logic, declaration_style="reg")
from .int import Int as Int, LongInt as LongInt
from .parameter import ParamRef as ParamRef, Parameter as Parameter
from .real import Real as Real, ShortReal as ShortReal, RealTime as RealTime
from .string import String as String
from .collection import (
    Array as Array,
    DynArray as DynArray,
    Queue as Queue,
    AssocArray as AssocArray,
)
from .remote_ref import RemoteRef as RemoteRef, RemoteRefValue as RemoteRefValue
from .limits import DecodeLimits as DecodeLimits
from .record_schema import (
    GeneratedDataClass as GeneratedDataClass,
    RecordField as RecordField,
    RecordSchema as RecordSchema,
)
from .capabilities import (
    RuntimeCapabilities as RuntimeCapabilities,
    STABLE_CAPABILITIES as STABLE_CAPABILITIES,
    require_runtime_compatible as require_runtime_compatible,
    runtime_capabilities as runtime_capabilities,
)

from .object import (
    ObjectRegistry as ObjectRegistry,
    CodecSession as CodecSession,
    PackContext as PackContext,
    UnpackContext as UnpackContext,
    SvObject as SvObject,
    allocate_object_number as allocate_object_number,
    clear_object_registry as clear_object_registry,
    get_object as get_object,
    register_object as register_object,
    reset_object_number_allocator as reset_object_number_allocator,
    unregister_object as unregister_object,
    svobj as svobj,
    Object as Object,
    new as new,
    SvStruct as SvStruct,
    SvPackedStruct as SvPackedStruct,
)
from .enum import Enum as Enum
from .errors import (
    CompatibilityError as CompatibilityError,
    CoverageDeclarationError as CoverageDeclarationError,
    CoverageError as CoverageError,
    ConstraintBackendError as ConstraintBackendError,
    ConstraintError as ConstraintError,
    ConstraintNameError as ConstraintNameError,
    ConstraintSyntaxError as ConstraintSyntaxError,
    ConstraintTypeError as ConstraintTypeError,
    ConstraintUnsupportedError as ConstraintUnsupportedError,
    DeclarationError as DeclarationError,
    DecodeError as DecodeError,
    EncodeError as EncodeError,
    ExternalStorageClosedError as ExternalStorageClosedError,
    ExternalStorageError as ExternalStorageError,
    StorageRelocationError as StorageRelocationError,
    StorageRelocationFailure as StorageRelocationFailure,
    LayeredRandomizationPriorityWarning as LayeredRandomizationPriorityWarning,
    RegistryError as RegistryError,
    ResourceLimitError as ResourceLimitError,
    SvTypesError as SvTypesError,
    UnsupportedTypeError as UnsupportedTypeError,
)
from .external_storage import (
    ExternalBorrowFieldStorage as ExternalBorrowFieldStorage,
    ExternalBorrowPathStorage as ExternalBorrowPathStorage,
    ExternalBorrowStorage as ExternalBorrowStorage,
    ExternalFieldStorage as ExternalFieldStorage,
    ExternalValueBinding as ExternalValueBinding,
    FieldDescriptor as FieldDescriptor,
    FieldIdentity as FieldIdentity,
    FieldIndex as FieldIndex,
    FieldKey as FieldKey,
    FieldMember as FieldMember,
    FieldOperation as FieldOperation,
    FieldPath as FieldPath,
    MemoryExternalFieldStorage as MemoryExternalFieldStorage,
    bind_external_value as bind_external_value,
)
from .constraint import (
    CONSTRAINT_IR_VERSION as CONSTRAINT_IR_VERSION,
    LayeredRandomizeStatus as LayeredRandomizeStatus,
    RandomContext as RandomContext,
    RandomizeStatus as RandomizeStatus,
    constraint as constraint,
    dist as dist,
    rand_layer as rand_layer,
    set_layered_randomization_reference_policy as set_layered_randomization_reference_policy,
    soft as soft,
    solve_before as solve_before,
    unique as unique,
)
from .randomizable import is_randomizable as is_randomizable
from .variable_access import (
    ElementBorrow as ElementBorrow,
    borrow_element as borrow_element,
    field as field,
    read_value as read_value,
    write_value as write_value,
)
from .storage_location import (
    ExternalStorageLocation as ExternalStorageLocation,
    external_storage_location as external_storage_location,
)
from .slot_identity import SlotIdentity as SlotIdentity, slot_identity as slot_identity
from .storage_relocation import StorageFactory as StorageFactory, relocated_slot as relocated_slot
from .storage_policy import (
    BorrowPurpose as BorrowPurpose,
    current_borrow_purpose as current_borrow_purpose,
    ExternalRefValidationStorage as ExternalRefValidationStorage,
    ExternalValueParameterStorage as ExternalValueParameterStorage,
)
from .checked_call import (
    CheckedAccessError as CheckedAccessError,
    CopyoutError as CopyoutError,
    Inout as Inout,
    Input as Input,
    Output as Output,
    Ref as Ref,
)
from .checked_source import run_checked as run_checked
from .checked_imports import (
    checked_project as checked_project,
    observe_module_sources as observe_module_sources,
)
from ._checked_callbacks import (
    checked_call_gateway as checked_call_gateway,
    checked_library_sources as checked_library_sources,
)
from .coverage import (
    CoverageDatabase as CoverageDatabase,
    CovPoint as CovPoint,
    CovPointArray as CovPointArray,
    CovPointOption as CovPointOption,
    CoverGroupOption as CoverGroupOption,
    CoverGroupTypeOption as CoverGroupTypeOption,
    CoverInput as CoverInput,
    CoverRef as CoverRef,
    CoverageInitCallIR as CoverageInitCallIR,
    CoverageInitIR as CoverageInitIR,
    Cross as Cross,
    CrossQueueType as CrossQueueType,
    CrossOption as CrossOption,
    bins as bins,
    covergroup as covergroup,
    coverage_init as coverage_init,
    coverage_initializers as coverage_initializers,
    preview_coverage_layout as preview_coverage_layout,
    default_bins as default_bins,
    export_ucis as export_ucis,
    export_ucis_file as export_ucis_file,
    ignore_bins as ignore_bins,
    import_ucis as import_ucis,
    import_ucis_file as import_ucis_file,
    illegal_bins as illegal_bins,
    set_coverage_case_name as set_coverage_case_name,
    repeat as repeat,
    transition_bins as transition_bins,
)

from .scope import (
    Scope as Scope,
    Package as Package,
    Namespace as Namespace,
    get_package as get_package,
)
from .schema import (
    BINARY_FORMAT_VERSION as BINARY_FORMAT_VERSION,
    GENERATOR_RUNTIME_ABI_VERSION as GENERATOR_RUNTIME_ABI_VERSION,
    OBJECT_ENVELOPE_VERSION as OBJECT_ENVELOPE_VERSION,
    SCHEMA_FORMAT_VERSION as SCHEMA_FORMAT_VERSION,
    SchemaDescriptor as SchemaDescriptor,
    EncodingDescriptor as EncodingDescriptor,
    unified_type_name as unified_type_name,
    checked_unpack as checked_unpack,
    schema_descriptor as schema_descriptor,
    encoding_descriptor as encoding_descriptor,
    sv_type_expression as sv_type_expression,
    sv_packer_expression as sv_packer_expression,
    sv_declaration as sv_declaration,
    sv_codegen_context as sv_codegen_context,
)
from .generator import GenerationResult as GenerationResult, generate as generate

from pathlib import Path
import sysconfig

__version__ = "1.6.0"


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

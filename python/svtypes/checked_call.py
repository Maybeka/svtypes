"""Parameter direction and copyout machinery for checked Python calls."""

import ast
import copy
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import partial
import inspect
from operator import index as integer_index
from types import AsyncGeneratorType, CoroutineType, FunctionType, GeneratorType, MethodType
from typing import Any

try:
    from annotationlib import Format as _AnnotationFormat
except ImportError:  # Python 3.11–3.13 have no deferred annotation machinery.
    _AnnotationFormat = None

from .base import TypeBase
from .collection import Array, AssocArray, CollectionBase, DynArray
from .errors import SvTypesError
from .object import ObjectDescriptor, SvObject, SvStruct
from .schema import encoding_descriptor
from .typespec import TypeSpec, materialize_type_spec
from .variable_access import (
    ElementBorrow,
    _HandleSlot,
    _Location,
    _has_indexed_binding,
    _location,
    _prepare,
    _snapshot,
    _retain_local_slot,
    _release_local_slot,
    field,
    read_value,
)
from .external_storage import ExternalValueBinding, _ExternalMapping, _ExternalSequence
from .storage_location import ExternalStorageLocation
from .slot_identity import SlotIdentity
from .checked_collections import _ExternalEdits, _LocalEdits


class CheckedAccessError(SvTypesError, TypeError):
    """A checked call violates the declared variable access contract."""


class CopyoutError(CheckedAccessError):
    """A parameter writeback could not be prepared or published."""

    def __init__(self, parameter: str, phase: str, completed: tuple[str, ...] = ()):
        self.parameter = parameter
        self.phase = phase
        self.completed = completed
        super().__init__(
            f"{parameter}: copyout {phase} failed; "
            f"completed parameters: {', '.join(completed) or '(none)'}"
        )


@dataclass(frozen=True, slots=True)
class _Contract:
    direction: str
    annotation: Any


class _Direction:
    @classmethod
    def __class_getitem__(cls, annotation: Any) -> _Contract:
        return _Contract(cls.__name__.lower(), annotation)


class Input(_Direction):
    """Initialize an independent formal from the input value."""


class Output(_Direction):
    """Start with an unassigned formal and publish on normal return."""


class Inout(_Direction):
    """Copy the input into a formal and publish on normal return."""


class Ref(_Direction):
    """Borrow the actual slot for the scope of the call."""


_UNASSIGNED = object()


class _OutputState:
    def __init__(self, name):
        self.name = name
        self.assigned = set()
        self.closed = False

    def first_missing(self, codec, path):
        if any(path[:length] in self.assigned for length in range(len(path), -1, -1)):
            return None
        if isinstance(codec, SvStruct):
            children = codec._SvObject__svtypes_members
            if not children:
                return path
        elif isinstance(codec, Array):
            children = ((index, codec._elem_template) for index in range(codec._size))
        else:
            return path
        for key, child in children:
            missing = self.first_missing(child, (*path, key))
            if missing is not None:
                return missing
        return None

    def label(self, path):
        return self.name + "".join(f"[{key}]" if isinstance(key, int) else f".{key}" for key in path)


class _Formal:
    def __init__(self, codec: Any, name: str, *, target: _Location | None = None, output=False):
        self._codec = codec
        self._name = name
        self._target = target
        self._value = _UNASSIGNED
        self._aggregate = isinstance(codec, (SvStruct, CollectionBase))
        self._closed = False
        self._output = _OutputState(name) if output and isinstance(codec, (SvStruct, Array)) else None
        self._output_path = ()
        if target is not None:
            _retain_local_slot(self, target.storage)

    def close(self) -> None:
        _release_local_slot(self)
        self._closed = True
        if self._output is not None and self._target is None:
            self._output.closed = True
        self._value = _UNASSIGNED
        self._target = None

    def _check_open(self) -> None:
        if self._closed or (self._output is not None and self._output.closed):
            raise CheckedAccessError(f"{self._name}: parameter scope has closed")

    @property
    def value(self) -> Any:
        self._check_open()
        self._check_assigned()
        if self._target is not None:
            return self._target.read()
        if self._aggregate:
            return read_value(self._value)
        return _snapshot(self._codec, self._value)

    def _check_assigned(self):
        if self._output is not None:
            missing = self._output.first_missing(self._codec, self._output_path)
            if missing is not None:
                label = self._output.label(missing)
                raise CheckedAccessError(f"{label}: Output has not been assigned")
        if self._target is None and self._value is _UNASSIGNED:
            raise CheckedAccessError(f"{self._name}: Output has not been assigned")

    def _fixed_output(self):
        self._check_open()
        if self._output is not None and isinstance(self._codec, (SvStruct, Array)):
            return self
        if self._target is not None and isinstance(self._target.storage, _Formal):
            return self._target.storage._fixed_output()
        return None

    def _fixed_child(self, key):
        self._check_open()
        if isinstance(self._codec, SvStruct):
            if not any(name == key for name, _ in self._codec._SvObject__svtypes_members):
                raise KeyError(key)
        else:
            key = integer_index(key)
            if key < 0:
                key += self._codec._size
            if not 0 <= key < self._codec._size:
                raise IndexError("Output array index out of range")
        if self._target is not None:
            storage = self._target.storage
        else:
            if self._value is _UNASSIGNED:
                self._value = (type(self._codec).codec_template() if isinstance(self._codec, SvStruct)
                               else copy.deepcopy(self._codec))
            storage = self._value
        variable = field(storage, key) if isinstance(self._codec, SvStruct) else storage[key]
        target = _location(variable)
        if target is None:
            raise CheckedAccessError(f"{self._name}: Output member has no writable variable slot")
        path = (*self._output_path, key)
        child = _Formal(target.codec, self._output.label(path), target=target)
        child._output, child._output_path = self._output, path
        return child

    def _projection(self):
        self._check_open()
        self._check_assigned()
        if self._target is None:
            return self._value
        storage = self._target.storage
        if isinstance(storage, ElementBorrow):
            storage = storage._projection()
        if isinstance(storage, _Formal):
            return storage._projection()
        if storage is None:
            raise CheckedAccessError(f"{self._name}: borrowed storage does not expose aggregate subslots")
        return storage

    @value.setter
    def value(self, value: Any) -> None:
        self._prepare_write(value)()

    def _prepare_write(self, value: Any):
        self._check_open()
        if self._target is not None:
            write = self._target.prepare(value)
            if self._output is None:
                return write

            def publish_child():
                self._check_open()
                write()
                self._output.assigned.add(self._output_path)

            return publish_child
        prepared = _prepare(self._codec, value)

        def publish():
            self._check_open()
            if not self._aggregate:
                self._value = prepared
            elif isinstance(self._codec, SvStruct):
                if self._value is _UNASSIGNED:
                    self._value = prepared
                else:
                    SvStruct.value.fset(self._value, prepared)
            else:
                storage = copy.deepcopy(self._codec) if self._value is _UNASSIGNED else self._value
                storage.value = prepared
                self._value = storage
            if self._output is not None:
                self._output.assigned.add(self._output_path)

        return publish

    def _location(self) -> _Location:
        self._check_open()
        return _Location(self._codec, lambda: self.value, self._prepare_write, self)


def _is_carrier(value: Any) -> bool:
    if isinstance(value, SvObject) and not isinstance(value, SvStruct):
        return False
    return isinstance(value, (
        TypeBase, _Formal, _HandleSlot, ElementBorrow, ObjectDescriptor,
        ExternalValueBinding, _ExternalSequence, _ExternalMapping,
        ExternalStorageLocation, SlotIdentity,
    ))


_protected_copy_memos = ContextVar("svtypes_protected_copy_memos", default=())


@contextmanager
def _protect_copy_memo(memo):
    token = _protected_copy_memos.set((*_protected_copy_memos.get(), memo))
    try:
        yield
    finally:
        _protected_copy_memos.reset(token)


def _contains_carrier(value: Any, seen: set[int] | None = None) -> bool:
    protected = _protected_copy_memos.get()
    if (_is_carrier(value) or isinstance(value, (_ExternalEdits, _LocalEdits))
            or (protected and any(value is memo for memo in protected))):
        return True
    kind = type(value)
    if kind not in (list, tuple, dict, set, frozenset, partial, FunctionType,
                    MethodType, GeneratorType, CoroutineType, AsyncGeneratorType):
        return False
    if seen is None:
        seen = set()
    if id(value) in seen:
        return False
    seen.add(id(value))
    if kind is dict:
        return any(_contains_carrier(key, seen) or _contains_carrier(item, seen)
                   for key, item in value.items())
    if kind is partial:
        return _contains_carrier((value.func, value.args, value.keywords), seen)
    if kind is FunctionType:
        captured = [value.__defaults__, value.__kwdefaults__]
        for cell in value.__closure__ or ():
            try:
                captured.append(cell.cell_contents)
            except ValueError:
                continue
        return _contains_carrier(captured, seen)
    if kind is MethodType:
        return _contains_carrier((value.__self__, value.__func__), seen)
    if kind in (GeneratorType, CoroutineType, AsyncGeneratorType):
        if kind is GeneratorType:
            from ._checked_callbacks import checked_generator_inputs
            inputs = checked_generator_inputs(value)
            if inputs is not None:
                return _contains_carrier(inputs, seen)
        frame = getattr(value, {GeneratorType: "gi_frame", CoroutineType: "cr_frame",
                                AsyncGeneratorType: "ag_frame"}[kind])
        return frame is not None and _contains_carrier(dict(frame.f_locals), seen)
    return any(_contains_carrier(item, seen) for item in value)


def _annotation_contract(annotation: Any) -> _Contract | None:
    if isinstance(annotation, _Contract):
        return annotation
    if isinstance(annotation, (TypeSpec, ObjectDescriptor)) or (
        isinstance(annotation, type) and issubclass(annotation, TypeBase)
    ):
        return _Contract("input", annotation)
    return None


def _checked_signature(function):
    """Resolve SvTypes contracts without requiring ordinary typing to resolve.

    Python annotations are not runtime validation rules. Resolved SvTypes
    declarations supply contracts; unresolved ordinary typing is left alone.
    """
    underlying = inspect.unwrap(function)
    if isinstance(underlying, partial):
        underlying = underlying.func
    if isinstance(underlying, type):
        underlying = underlying.__init__
    if isinstance(underlying, MethodType):
        underlying = underlying.__func__
    # Preserve Python's lazy-annotation caching and explicit annotation objects.
    # Unresolved ordinary names are forward references, not invalid contracts.
    annotate = getattr(underlying, "__annotate__", None)
    options = {} if _AnnotationFormat is None else {"annotation_format": _AnnotationFormat.FORWARDREF}
    try:
        signature = inspect.signature(function, eval_str=False, **options)
    except Exception:
        if _AnnotationFormat is None or annotate is None:
            raise
        # An ordinary deferred annotation may itself fail to evaluate. Inspect
        # its text without rejecting the callable or evaluating the return type.
        signature = inspect.signature(function, eval_str=False, annotation_format=_AnnotationFormat.STRING)
    namespace = dict(getattr(underlying, "__globals__", {}))
    for source in (underlying, getattr(underlying, "__annotate__", None)):
        if isinstance(source, FunctionType):
            for name, cell in zip(source.__code__.co_freevars, source.__closure__ or ()):
                try:
                    namespace[name] = cell.cell_contents
                except ValueError:
                    pass

    def resolve(annotation):
        if not isinstance(annotation, str):
            return annotation
        try:
            expression = ast.parse(annotation, mode="eval")
            # Also accept explicitly quoted contracts under future annotations.
            if isinstance(expression.body, ast.Constant) and isinstance(expression.body.value, str):
                expression = ast.parse(expression.body.value, mode="eval")
        except SyntaxError:
            return annotation
        root = expression.body
        while isinstance(root, ast.Subscript):
            root = root.value

        def lookup(node):
            if isinstance(node, ast.Name):
                return namespace.get(node.id)
            if isinstance(node, ast.Attribute):
                return inspect.getattr_static(lookup(node.value), node.attr, None)
            return None

        candidate = lookup(root)
        direction = isinstance(candidate, type) and issubclass(candidate, _Direction)
        try:
            # Use Python evaluation, including aliases and annotation factories;
            # do not impose a second grammar on valid parameter declarations.
            return eval(compile(expression, "<SvTypes parameter annotation>", "eval"), namespace)
        except Exception as exc:
            if direction or _annotation_contract(candidate) is not None:
                raise CheckedAccessError(f"cannot resolve the checked parameter annotation: {annotation}") from exc
            return annotation

    return signature.replace(parameters=[
        parameter.replace(annotation=resolve(parameter.annotation))
        for parameter in signature.parameters.values()
    ])


def _parameter_codec(annotation: Any, name: str) -> Any:
    if isinstance(annotation, ObjectDescriptor):
        return annotation
    if isinstance(annotation, type) and issubclass(annotation, SvObject):
        return annotation.codec_template()
    return materialize_type_spec(annotation, location=f"parameter {name}")


def _actual_location(actual: Any) -> _Location | None:
    if isinstance(actual, _Formal):
        return actual._location()
    if isinstance(actual, ObjectDescriptor) or not _is_carrier(actual):
        return None
    return _location(actual)


def _same_variable_declarations(formal, actual):
    """Wire compatibility alone cannot establish Python handle assignability."""
    if type(formal) is not type(actual):
        return False
    if isinstance(formal, ObjectDescriptor):
        expected = formal.registry.get(formal.cls_name)
        received = actual.registry.get(actual.cls_name)
        if expected is not None or received is not None:
            return expected is received
        return formal.registry is actual.registry and formal.cls_name == actual.cls_name
    if isinstance(formal, AssocArray):
        return (
            _same_variable_declarations(formal._key_template, actual._key_template)
            and _same_variable_declarations(formal._val_template, actual._val_template)
        )
    if isinstance(formal, (Array, DynArray)):
        return _same_variable_declarations(formal._elem_template, actual._elem_template)
    return True


@dataclass(frozen=True, slots=True)
class _Returned:
    value: Any
    site: str


def _return_parts(result):
    if type(result) is _Returned:
        return result.value, result.site
    return result, None


def _note_source(error, site):
    if site is not None and site not in getattr(error, "__notes__", ()):
        error.add_note(site)


def _checked_result(result, callback_result, callback_result_error):
    value, site = _return_parts(result)
    if callback_result is not None and callback_result(value):
        return value, site
    if _contains_carrier(value):
        message = "a checked call returned a variable carrier"
        if callback_result is not None:
            message += " outside the callback result contract"
    elif callback_result is not None:
        message = callback_result_error
    else:
        return value, site
    if isinstance(value, CoroutineType):
        value.close()
    error = CheckedAccessError(message)
    _note_source(error, site)
    raise error


def _invoke_checked(
    function, args, kwargs, *, execute=None, callback_value=None, callback_result=None,
    callback_result_error="a checked codec must return a new decoded structure and a byte count",
):
    """Apply the actual callable's signature and publish prepared outputs.

    The checked source executor is responsible for preventing carrier storage
    inside the function body. This layer owns formals and scoped forwarding.
    """
    if execute is None:
        execute = lambda func, args, kwargs: func(*args, **kwargs)
    if type(function) is partial and _contains_carrier(function):
        raise CheckedAccessError("partial cannot hold a variable carrier")
    try:
        signature = _checked_signature(function)
    except CheckedAccessError:
        raise
    except (TypeError, ValueError) as exc:
        if callback_value is not None or callback_result is not None or _contains_carrier(args) or _contains_carrier(kwargs):
            raise CheckedAccessError("callable has no inspectable carrier parameter contract") from exc
        result, _ = _checked_result(execute(function, args, kwargs), None, None)
        return result

    bound = signature.bind(*args, **kwargs)
    bound.apply_defaults()
    copyout = []
    with ExitStack() as scope:
        for parameter in signature.parameters.values():
            name = parameter.name
            actual = bound.arguments[name]
            contract = _annotation_contract(parameter.annotation)
            if _contains_carrier(parameter.default):
                raise CheckedAccessError(f"{name}: a parameter default cannot hold a variable carrier")
            if callback_value is not None and actual is callback_value:
                callback_value = None
                continue
            if contract is None:
                if _contains_carrier(actual):
                    raise CheckedAccessError(f"{name}: parameter has no variable carrier contract")
                continue
            if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
                raise CheckedAccessError(f"{name}: variadic variable parameters are unsupported")
            codec = _parameter_codec(contract.annotation, name)
            direction = contract.direction
            if _has_indexed_binding(actual):
                from .storage_policy import _value_argument_paths
                if direction == 'ref' or (direction in {'output', 'inout'} and not _value_argument_paths(actual)):
                    raise CheckedAccessError(
                        f"{name}: an external element parameter requires a stable element borrow"
                    )
            destination = _actual_location(actual)
            if direction != "input" and destination is None:
                raise CheckedAccessError(f"{name}: {direction} requires a writable variable")
            if direction == "ref":
                from .storage_location import external_storage_location
                location = external_storage_location(actual)
                if location is not None and location.kind == 'borrow':
                    validate = getattr(location.storage, 'validate_ref_borrow', None)
                    if validate is not None:
                        from .storage_policy import BorrowPurpose, _borrow_purpose_scope
                        with _borrow_purpose_scope(BorrowPurpose.REF):
                            validate(location.token)
                if (
                    encoding_descriptor(codec) != encoding_descriptor(destination.codec)
                    or not _same_variable_declarations(codec, destination.codec)
                ):
                    raise CheckedAccessError(f"{name}: Ref declaration does not match the actual variable")
                if isinstance(actual, ElementBorrow):
                    lease = scope.enter_context(actual.forward())
                    destination = _actual_location(lease)
                formal = _Formal(codec, name, target=destination)
            else:
                formal = _Formal(codec, name, output=direction == "output")
                if direction != "output":
                    initial = destination.read() if destination is not None else actual
                    if _contains_carrier(initial):
                        raise CheckedAccessError(f"{name}: Input value contains a variable carrier")
                    formal.value = initial
                if direction in ("output", "inout"):
                    copyout.append((name, formal, destination))
            scope.callback(formal.close)
            bound.arguments[name] = formal

        result, return_site = _checked_result(
            execute(function, bound.args, bound.kwargs), callback_result, callback_result_error,
        )
        writes = []
        for name, formal, destination in copyout:
            try:
                writes.append((name, destination.prepare(formal.value)))
            except Exception as exc:
                error = CopyoutError(name, "preparation")
                _note_source(error, return_site)
                raise error from exc
        completed = []
        for name, publish in writes:
            try:
                publish()
            except Exception as exc:
                error = CopyoutError(name, "publication", tuple(completed))
                _note_source(error, return_site)
                raise error from exc
            completed.append(name)
        return result

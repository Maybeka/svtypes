"""Source execution for the checked SvTypes variable convention."""

from __future__ import annotations

import __future__
import ast
import builtins
from contextlib import ExitStack, _GeneratorContextManager, contextmanager, nullcontext
from copy import deepcopy
from contextvars import ContextVar
from functools import partial, reduce, wraps
from itertools import accumulate, count, dropwhile, filterfalse, groupby, starmap, takewhile
import inspect
import operator
from pathlib import Path
import textwrap
from types import BuiltinMethodType, CodeType, FunctionType, MethodType, MethodWrapperType, WrapperDescriptorType
from typing import Any
from weakref import WeakKeyDictionary

from ._checked_callbacks import (
    callback_dispatch, callback_gateway, generator_inputs, is_callback_gateway,
    is_library_source, register_checked_generator,
)
from .base import TypeBase
from .checked_call import (
    CheckedAccessError,
    _Formal,
    _Returned,
    _actual_location,
    _annotation_contract,
    _checked_signature,
    _contains_carrier,
    _invoke_checked,
    _is_carrier,
    _note_source,
    _protect_copy_memo,
    _return_parts,
)
from .checked_collections import (
    _ExternalEdits, _LocalEdits, _MAPPING_EDITS, _ORDER_EDITS, _SEQUENCE_EDITS, _element_storage,
)
from .collection import Array, AssocArray, DynArray, Queue
from .errors import ExternalStorageError, ResourceLimitError, SvTypesError
from .external_storage import ExternalValueBinding
from .object import ObjectDescriptor, SvObject, SvStruct
from .typespec import TypeSpec
from .storage_location import external_storage_location
from .slot_identity import slot_identity
from .variable_access import (
    _HandleSlot, _borrow_binding, _bound_location, _has_indexed_binding, _prepare, _value_binding,
    field, read_value, write_value,
)


_AUGMENT = {
    node.__name__: getattr(operator, method)
    for node, method in (
        (ast.Add, "iadd"), (ast.Sub, "isub"), (ast.Mult, "imul"),
        (ast.MatMult, "imatmul"), (ast.Div, "itruediv"), (ast.FloorDiv, "ifloordiv"),
        (ast.Mod, "imod"), (ast.Pow, "ipow"), (ast.LShift, "ilshift"),
        (ast.RShift, "irshift"), (ast.BitOr, "ior"), (ast.BitXor, "ixor"),
        (ast.BitAnd, "iand"),
    )
}


_COLLECTION_MUTATIONS = _SEQUENCE_EDITS | _MAPPING_EDITS | _ORDER_EDITS
_SOURCE_ERRORS = (SvTypesError, TypeError, ValueError, LookupError, AttributeError)
_MISSING = object()


def _variable_factory(function):
    if isinstance(function, TypeSpec):
        return True
    return isinstance(function, type) and issubclass(function, TypeBase)


class _Address:
    def __init__(self, read, write, *, borrow=None, allow_value=None):
        self.read = read
        self.write = write
        self.borrow = borrow
        self.allow_value = allow_value

    value = property(lambda self: self.read(), lambda self, value: self.write(value))


class _PlainValue:
    def __init__(self, value):
        self.value = value


def _class_attribute(cls, name):
    for base in type.__getattribute__(cls, "__mro__"):
        namespace = type.__getattribute__(base, "__dict__")
        if name in namespace:
            return namespace[name]
    return _MISSING


def _data_descriptor(descriptor):
    cls = type(descriptor)
    return _class_attribute(cls, "__set__") is not _MISSING or _class_attribute(cls, "__delete__") is not _MISSING


def _special_method(owner, name):
    owner_type = type(owner)
    method = _class_attribute(owner_type, name)
    if method is _MISSING:
        raise AttributeError(name)
    descriptor_get = getattr(type(method), "__get__", None)
    return method if descriptor_get is None else descriptor_get(method, owner, owner_type)


class _CheckedKeywords:
    def __init__(self, mapping, runtime, site):
        self.mapping = mapping
        self.runtime = runtime
        self.site = site

    def keys(self):
        return self.runtime.callee(self.mapping.keys, self.site, False, None)()

    def __getitem__(self, key):
        lookup = _special_method(self.mapping, "__getitem__")
        return self.runtime.callee(lookup, self.site, False, None)(key)


def _variable_address(location, *, borrow=None):
    return _Address(location.read, lambda value: location.prepare(value)(), borrow=borrow)


def _indexed_binding(binding, key):
    if isinstance(binding.leaf.codec, AssocArray):
        return binding.child_key(key)
    index = operator.index(key)
    if index < 0:
        index += len(_bound_location(binding).read())
    return binding.child_index(index)


def _local_slice(owner, key, values):
    codec = owner._elem_template
    replacements = [_element_storage(codec, value) for value in values]
    updated = owner._elements.copy()
    updated[key] = replacements
    if isinstance(owner, Array):
        if len(updated) != owner._size:
            raise ValueError(f"Array slice assignment must preserve its size {owner._size}")
        for index, replacement in zip(range(*key.indices(owner._size)), replacements):
            current = _actual_location(owner._elements[index])
            if current is None:
                owner._elements[index] = replacement
            else:
                current.prepare(read_value(replacement))()
        return
    if len(updated) > owner._max_length:
        raise ResourceLimitError(f"{type(owner).__name__} length exceeds {owner._max_length}")
    owner._elements[:] = updated
    owner._bind_mode_elements()


class _Lower(ast.NodeTransformer):
    def __init__(self, runtime_name: str, filename: str, declaration_names: set[str], *, generator=False):
        self.runtime_name = runtime_name
        self.filename = filename
        self.declaration_names = declaration_names
        self.needs_scope = False
        self.generator = generator
        self.temporary_count = 0

    def visit(self, node):
        if not isinstance(node, ast.stmt):
            return super().visit(node)
        enclosing_scope = self.needs_scope
        self.needs_scope = False
        site = self.site(node)
        lowered = super().visit(node)
        needs_scope = self.needs_scope
        self.needs_scope = enclosing_scope
        if needs_scope and not self.generator:
            context = ast.withitem(self.emit(node, "statement_scope", site))
            return ast.copy_location(ast.With([context], lowered if isinstance(lowered, list) else [lowered]), node)
        return lowered

    def statements(self, statements):
        result = []
        for statement in statements:
            lowered = self.visit(statement)
            result.extend(lowered if isinstance(lowered, list) else [lowered])
        return result

    def temporary(self, node):
        self.temporary_count += 1
        return ast.copy_location(ast.Name(f"{self.runtime_name}_temporary_{self.temporary_count}", ast.Store()), node)

    def emit(self, node, method, *args):
        runtime = ast.Name(id=self.runtime_name, ctx=ast.Load())
        call = ast.Call(ast.Attribute(runtime, method, ast.Load()), list(args), [])
        return ast.copy_location(call, node)

    def site(self, node):
        return ast.Constant(f"{self.filename}:{node.lineno}: {ast.unparse(node)}")

    def fail(self, node, reason):
        raise CheckedAccessError(f"{self.site(node).value}: {reason}")

    def address(self, target, *, augmented=False):
        if isinstance(target, ast.Attribute):
            stable = augmented and target.attr == "value"
            if stable:
                self.needs_scope = True
            return self.emit(target, "attribute_address", self.visit(target.value),
                             ast.Constant(target.attr), self.site(target), ast.Constant(stable))
        if isinstance(target, ast.Subscript):
            key = self.key(target.slice)
            owner = self.value_path(target.value)
            if owner is not None:
                return self.emit(target, "value_index_address", owner, key, self.site(target))
            return self.emit(target, "item_address", self.visit(target.value), key, self.site(target))
        self.fail(target, "unsupported checked assignment target")

    def value_path(self, node):
        if isinstance(node, ast.Attribute) and node.attr == "value":
            self.needs_scope = True
            return self.emit(node, "value_owner", self.visit(node.value), self.site(node))
        if isinstance(node, ast.Subscript):
            parent = self.value_path(node.value)
            if parent is not None:
                return self.emit(node, "value_child", parent, self.key(node.slice), self.site(node))
        return None

    def key(self, node):
        if isinstance(node, ast.Slice):
            parts = [self.visit(item) if item is not None else ast.Constant(None)
                     for item in (node.lower, node.upper, node.step)]
            return self.emit(node, "slice_key", *parts)
        if isinstance(node, ast.Tuple):
            return ast.copy_location(ast.Tuple([self.key(item) for item in node.elts], ast.Load()), node)
        return self.visit(node)

    def visit_Attribute(self, node):
        return self.emit(node, "attribute", self.visit(node.value), ast.Constant(node.attr), self.site(node))

    def visit_Subscript(self, node):
        return self.emit(node, "subscript", self.visit(node.value), self.key(node.slice), self.site(node))

    def call(self, node, *, declaration=False):
        if (isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "value"):
            function = self.emit(node.func, "value_method", self.visit(node.func.value.value),
                                 ast.Constant(node.func.attr), self.site(node.func))
        elif (isinstance(node.func, ast.Attribute) and node.func.attr in _COLLECTION_MUTATIONS
              and (owner := self.value_path(node.func.value)) is not None):
            function = self.emit(node.func, "value_method", owner,
                                 ast.Constant(node.func.attr), self.site(node.func))
        else:
            function = self.visit(node.func)
        key = ast.Constant(f"{self.filename}:{node.lineno}:{node.col_offset}")
        has_captures = False

        def argument(expr, selector):
            nonlocal has_captures
            if isinstance(expr, ast.Starred):
                values = self.emit(expr, "positional_expansion", self.visit(expr.value), self.site(expr), key)
                return ast.copy_location(ast.Starred(values, ast.Load()), expr)
            lowered = self.visit(expr)
            if isinstance(expr, ast.Constant):
                return lowered
            has_captures = True
            return self.emit(expr, "capture_argument", key, lowered, self.site(expr), ast.Constant(selector))

        args = [argument(arg, index) for index, arg in enumerate(node.args)]
        keywords = [
            ast.keyword(item.arg, argument(item.value, item.arg) if item.arg else self.emit(
                item, "keyword_expansion", self.visit(item.value), self.site(item),
            ))
            for item in node.keywords
        ]
        if has_captures:
            self.needs_scope = True
        callee = self.emit(node, "callee", function, self.site(node), ast.Constant(declaration),
                           key if has_captures else ast.Constant(None))
        return ast.copy_location(ast.Call(callee, args, keywords), node)

    def visit_Call(self, node):
        return self.call(node)

    def assign(self, node, target, value):
        if isinstance(target, ast.Name):
            if isinstance(value, ast.Call) and target.id in self.declaration_names:
                # Only the actual SvTypes constructor may introduce a local slot.
                result = self.call(value, declaration=True)
            else:
                result = self.emit(value, "safe_value", self.visit(value), self.site(node))
            return ast.copy_location(ast.Assign([target], result), node)
        if isinstance(target, (ast.Tuple, ast.List)):
            decode_targets = (
                len(target.elts) == 2
                and all(isinstance(item, ast.Name) and item.id in self.declaration_names for item in target.elts)
                and target.elts[0].id != target.elts[1].id
            )
            result = self.emit(value, "unpack_values", self.visit(value), self.site(node), ast.Constant(decode_targets))
            return ast.copy_location(ast.Assign([self.assignment_target(target)], result), node)
        result = self.emit(node, "store", self.visit(value), self.address(target), self.site(node))
        return ast.copy_location(ast.Expr(result), node)

    def assignment_target(self, target):
        if isinstance(target, (ast.Tuple, ast.List)):
            target.elts = [self.assignment_target(child) for child in target.elts]
        elif isinstance(target, ast.Starred):
            target.value = self.assignment_target(target.value)
        elif not isinstance(target, ast.Name):
            return ast.copy_location(ast.Attribute(self.address(target), "value", ast.Store()), target)
        return target

    def visit_Assign(self, node):
        if len(node.targets) != 1:
            target = self.temporary(node)
            value = ast.copy_location(ast.Name(target.id, ast.Load()), node)
            first = ast.copy_location(ast.Assign([target], self.emit(
                node, "safe_value", self.visit(node.value), self.site(node))), node)
            return [first, *(self.assign(node, target, value) for target in node.targets)]
        return self.assign(node, node.targets[0], node.value)

    def visit_AnnAssign(self, node):
        if node.value is None:
            return node
        if isinstance(node.target, ast.Name):
            # Preserve class annotations (including deferred annotations) and
            # the ordinary local-variable semantics of annotated assignments.
            node.value = self.assign(node, node.target, node.value).value
        else:
            node.value = self.emit(node.value, "safe_value", self.visit(node.value), self.site(node))
            node.target = self.assignment_target(node.target)
        return node

    def visit_AugAssign(self, node):
        if isinstance(node.target, ast.Name):
            current = ast.copy_location(ast.Name(node.target.id, ast.Load()), node.target)
            finish = self.emit(node, "begin_name_aug", current, ast.Constant(type(node.op).__name__),
                               self.site(node))
            result = ast.Call(finish, [self.visit(node.value)], [])
            return ast.copy_location(ast.Assign([node.target], result), node)
        finish = self.emit(node, "begin_aug", self.address(node.target, augmented=True),
                           ast.Constant(type(node.op).__name__), self.site(node))
        return ast.copy_location(ast.Expr(ast.Call(finish, [self.visit(node.value)], [])), node)

    def visit_Return(self, node):
        site = self.site(node)
        value = self.visit(node.value) if node.value is not None else ast.Constant(None)
        return ast.copy_location(ast.Return(self.emit(
            node, "safe_value" if self.generator else "returned", value, site)), node)

    def visit_NamedExpr(self, node):
        node.value = self.emit(node, "safe_value", self.visit(node.value), self.site(node))
        return node

    def visit_For(self, node):
        node.iter = self.emit(node.iter, "iter_values", self.visit(node.iter), self.site(node.iter))
        if not isinstance(node.target, ast.Name):
            original = node.target
            node.target = self.temporary(original)
            bound = ast.copy_location(ast.Name(node.target.id, ast.Load()), original)
            node.body = [self.assign(original, original, bound), *self.statements(node.body)]
        else:
            node.body = self.statements(node.body)
        node.orelse = self.statements(node.orelse)
        return node

    def visit_comprehension(self, node):
        if node.is_async:
            return node
        node.iter = self.emit(node.iter, "iter_values", self.visit(node.iter), self.site(node.iter))
        node.target = self.assignment_target(node.target)
        node.ifs = [self.visit(condition) for condition in node.ifs]
        return node

    def visit_Delete(self, node):
        result = []
        for target in node.targets:
            if isinstance(target, ast.Name):
                result.append(ast.copy_location(ast.Delete([target]), node))
                continue
            if isinstance(target, ast.Subscript):
                owner = self.value_path(target.value)
                operation = self.emit(target, "delete_value_index", owner, self.key(target.slice), self.site(target)) if owner is not None else self.emit(
                    target, "delete_item", self.visit(target.value), self.key(target.slice), self.site(target))
            elif isinstance(target, ast.Attribute):
                operation = self.emit(target, "delete_attribute", self.visit(target.value), ast.Constant(target.attr), self.site(target))
            else:
                result.extend(self.visit_Delete(ast.copy_location(ast.Delete(list(target.elts)), node)))
                continue
            result.append(ast.copy_location(ast.Expr(operation), node))
        return result

    def visit_With(self, node):
        items = []
        for item in node.items:
            bindings = []
            if item.optional_vars is not None and not isinstance(item.optional_vars, ast.Name):
                original = item.optional_vars
                item.optional_vars = self.temporary(original)
                bindings.append(self.assign(original, original, ast.copy_location(ast.Name(item.optional_vars.id, ast.Load()), original)))
            context = item.context_expr
            item.context_expr = self.emit(context, "checked_context", self.visit(context),
                                          ast.Constant(item.optional_vars is not None), self.site(context))
            items.append((item, bindings))
        body = self.statements(node.body)
        for item, bindings in reversed(items):
            body = [ast.copy_location(ast.With([item], [*bindings, *body]), node)]
        return body[0]

    def visit_Match(self, node):
        node.subject = self.visit(node.subject)
        for case in node.cases:
            names = []
            for pattern in ast.walk(case.pattern):
                name = pattern.rest if isinstance(pattern, ast.MatchMapping) else getattr(pattern, "name", None)
                if name is not None and name not in names:
                    names.append(name)
            bindings = ast.Tuple([ast.Name(name, ast.Load()) for name in names], ast.Load())
            checked = self.emit(node, "pattern_bindings", bindings, self.site(node))
            case.guard = checked if case.guard is None else ast.BoolOp(ast.And(), [checked, self.visit(case.guard)])
            case.body = self.statements(case.body)
        return node

    def visit_FunctionDef(self, node):
        node.args.defaults = [self.visit(value) for value in node.args.defaults]
        node.args.kw_defaults = [
            self.visit(value) if value is not None else None for value in node.args.kw_defaults
        ]
        site = ast.Constant(f"{self.filename}:{node.lineno}: def {node.name}")
        node.decorator_list = [self.emit(node, "nested_function", site), *(self.visit(item) for item in node.decorator_list)]
        return node

    def visit_ClassDef(self, node):
        previous = self.declaration_names
        declarations = {target.id for statement in node.body
                        for target in ast.walk(statement)
                        if isinstance(target, ast.Name) and isinstance(target.ctx, ast.Store)}
        self.declaration_names = previous | declarations
        try:
            node.bases = [self.visit(base) for base in node.bases]
            node.keywords = [self.visit(keyword) for keyword in node.keywords]
            node.decorator_list = [self.emit(node, "class_declaration", self.site(node)),
                                   *(self.visit(value) for value in node.decorator_list)]
            body = []
            for statement in node.body:
                if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    # Preserve descriptors and __class__ cells. Methods are
                    # checked through their normal call/descriptor gateways.
                    body.append(statement)
                else:
                    body.extend(self.statements([statement]))
            node.body = body
            return node
        finally:
            self.declaration_names = previous

    def visit_Lambda(self, node):
        enclosing = self.needs_scope
        node.args.defaults = [self.visit(value) for value in node.args.defaults]
        node.args.kw_defaults = [self.visit(value) if value is not None else None for value in node.args.kw_defaults]
        node.body = self.visit(node.body)
        self.needs_scope = enclosing
        return self.emit(node, "lambda_function", node, self.site(node))

    def visit_GeneratorExp(self, node):
        if any(part.is_async for part in node.generators):
            return self.emit(node, "safe_value", node, self.site(node))
        node = self.generic_visit(node)
        node.elt = self.emit(node.elt, "safe_value", node.elt, self.site(node.elt))
        # The first iterable is evaluated now; the remaining expressions run
        # later under the generator's per-resume scope.
        self.needs_scope = True
        return self.emit(node, "generator_value", node, self.site(node))

    def visit_Yield(self, node):
        node.value = self.emit(node, "safe_value", self.visit(node.value) if node.value is not None else ast.Constant(None), self.site(node))
        return node

    def visit_YieldFrom(self, node):
        node.value = self.emit(node, "delegate_values", self.visit(node.value), self.site(node))
        return node

    # Defining ordinary asynchronous code is not a variable-access violation.
    # Native coroutine frames are checked for captured carriers at the boundary.
    visit_AsyncFunctionDef = visit_FunctionDef


def _cell(value):
    return (lambda: value).__closure__[0]


class _StatementScope:
    def __init__(self):
        self.calls = {}
        self.argument_offsets = {}
        self.parameter_contracts = {}
        self.borrows = ExitStack()

    def close(self):
        with self.borrows as cleanup:
            for _, leases in self.calls.values():
                cleanup.callback(leases.close)
            self.calls.clear()
            self.argument_offsets.clear()
            self.parameter_contracts.clear()


class _CheckedContext:
    def __init__(self, runtime, manager, bind, site):
        self.runtime = runtime
        self.bind = bind
        self.site = site
        self.enter = _special_method(manager, "__enter__")
        self.exit = _special_method(manager, "__exit__")

    def __enter__(self):
        result, origin = _return_parts(self.runtime.execute(self.enter, (), {}))
        if not self.bind:
            return None
        try:
            return self.runtime.safe_value(result, origin or self.site)
        except CheckedAccessError as error:
            # The real enter already succeeded, so its exit owns cleanup.
            self.__exit__(type(error), error, error.__traceback__)
            raise

    def __exit__(self, kind, error, traceback):
        result, origin = _return_parts(self.runtime.execute(self.exit, (kind, error, traceback), {}))
        return self.runtime.safe_value(result, origin or self.site)


class _ValueCallback:
    def __init__(self, receiver, operation, value):
        self.receiver = receiver
        self.operation = operation
        self.value = value
        self.fresh = {}
        self.transfers = {}

    @property
    def copying(self):
        return self.operation in ("__copy__", "__deepcopy__")

    @property
    def checks_result(self):
        return self.operation == "unpack" or (self.copying and _is_carrier(self.receiver))

    def matches_result(self, result):
        if self.copying:
            return isinstance(result, type(self.receiver)) and result is not self.receiver
        return (
            self.operation == "unpack" and type(result) is tuple and len(result) == 2
            and isinstance(result[0], type(self.receiver)) and type(result[1]) is int and result[1] >= 0
        )

    def allows_result(self, result):
        if not self.matches_result(result):
            return False
        value = result if self.copying else result[0]
        return self.fresh.get(id(value)) is value

    def remember(self, value):
        if self.checks_result and isinstance(value, type(self.receiver)) and value is not self.receiver:
            self.fresh[id(value)] = value

    def scope(self):
        if self.operation == "__deepcopy__" and self.value is not None:
            return _protect_copy_memo(self.value)
        return nullcontext()


_active_runtime = ContextVar("svtypes_checked_runtime", default=None)


class _GeneratorSteps:
    """Keep checked state scoped to each resume, never across a yield."""

    def __init__(self, runtime, generator, site):
        self.runtime = runtime
        self.generator = generator
        self.site = site

    def __iter__(self):
        return self

    def _step(self, method, *args):
        with self.runtime.active(), self.runtime.statement_scope(self.site):
            self.runtime.safe_value(generator_inputs(self.generator), self.site)
            try:
                return self.runtime.safe_value(method(*args), self.site)
            except StopIteration as done:
                self.runtime.safe_value(done.value, self.site)
                raise

    def __next__(self):
        return self._step(next, self.generator)

    def send(self, value):
        return self._step(self.generator.send, self.runtime.safe_value(value, self.site))

    def throw(self, *args):
        method = getattr(self.generator, "throw", None)
        if method is None:
            error = args[0] if isinstance(args[0], BaseException) else args[0](*args[1:2])
            raise error
        return self._step(method, *args)

    def close(self):
        method = getattr(self.generator, "close", None)
        return None if method is None else self._step(method)


class _Runtime:
    def __init__(self, root: Path, entry: FunctionType | None):
        self.root = root
        self.entry = entry
        self.compiled: WeakKeyDictionary[FunctionType, CodeType] = WeakKeyDictionary()
        self.scopes: list[_StatementScope] = []
        self.value_callback: _ValueCallback | None = None

    @contextmanager
    def statement_scope(self, site):
        scope = _StatementScope()
        self.scopes.append(scope)
        try:
            yield
        except _SOURCE_ERRORS as error:
            _note_source(error, site)
            raise
        finally:
            self.scopes.pop()
            scope.close()

    @staticmethod
    def acquire(value, site, *, ref_actual=False):
        from .storage_policy import BorrowPurpose, _borrow_purpose_scope
        purpose = BorrowPurpose.REF if ref_actual else BorrowPurpose.OPERATION
        with _borrow_purpose_scope(purpose):
            try:
                return _borrow_binding(_value_binding(value))
            except ExternalStorageError as exc:
                raise CheckedAccessError(f"{site}: stable element borrow required: {exc}") from exc

    def capture_argument(self, key, value, site, selector=None):
        if not _is_carrier(value):
            return value
        function, leases = self.scopes[-1].calls[key]
        if self.callback_parent(function) and self.value_callback.value is value:
            return value
        if any(function is helper for helper in (field, read_value, write_value, getattr, setattr)):
            return value
        scope = self.scopes[-1]
        if key not in scope.parameter_contracts:
            try:
                parameters = _checked_signature(function).parameters
            except (TypeError, ValueError, NameError):
                parameters = {}
            positional = tuple(parameter for parameter in parameters.values()
                               if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD))
            scope.parameter_contracts[key] = positional, parameters
        positional, parameters = scope.parameter_contracts[key]
        if type(selector) is int:
            index = selector + scope.argument_offsets.get(key, 0)
            parameter = positional[index] if 0 <= index < len(positional) else None
        else:
            parameter = parameters.get(selector)
        contract = _annotation_contract(parameter.annotation) if parameter is not None else None
        if contract is not None and contract.direction == 'input':
            # Evaluate actuals normally; _invoke_checked copies Inputs at entry.
            # Value parameters do not require a stable element lease.
            return value
        if contract is not None and contract.direction in {'output', 'inout'}:
            from .storage_policy import _value_argument_paths
            if _value_argument_paths(value):
                return value
        if not _has_indexed_binding(value):
            return value
        ref_actual = contract is not None and contract.direction in {"ref", "const ref"}
        return leases.enter_context(self.acquire(value, site, ref_actual=ref_actual))

    def checked_context(self, manager, bind, site):
        if isinstance(manager, _GeneratorContextManager):
            from .checked_imports import owns_source

            if owns_source(self.root, manager.gen.gi_code.co_filename):
                manager.gen = self.execute(manager.func, manager.args, manager.kwds)
        try:
            return _CheckedContext(self, manager, bind, site)
        except AttributeError as exc:
            raise TypeError(f"{type(manager).__name__} does not support the context manager protocol") from exc

    @contextmanager
    def active(self):
        from .checked_imports import checked_project

        token = _active_runtime.set(self)
        try:
            with checked_project(self.root), callback_dispatch(self.callback):
                yield
        finally:
            _active_runtime.reset(token)

    def nested_function(self, site):
        def decorate(function):
            self.safe_value(function, site)

            @wraps(function)
            def checked(*args, **kwargs):
                self.safe_value(function, site)
                if _active_runtime.get() is self:
                    return _invoke_checked(function, args, kwargs, execute=self.execute)
                runtime = _Runtime(self.root, function)
                with runtime.active():
                    return _invoke_checked(function, args, kwargs, execute=runtime.execute)

            return callback_gateway(checked)

        return decorate

    def lambda_function(self, function, site):
        self.safe_value(function, site)

        @wraps(function)
        def checked(*args, **kwargs):
            self.safe_value(function, site)
            with self.active(), self.statement_scope(site):
                return _invoke_checked(function, args, kwargs)

        return callback_gateway(checked)

    def class_declaration(self, site):
        def checked(cls):
            for member in vars(cls).values():
                if isinstance(member, (staticmethod, classmethod)):
                    self.safe_value(member.__func__, site)
                elif isinstance(member, property):
                    self.safe_value((member.fget, member.fset, member.fdel), site)
                elif isinstance(member, FunctionType):
                    self.safe_value(member, site)
            return cls
        return checked

    def generator_value(self, generator, site):
        self.safe_value(generator, site)

        def checked():
            return (yield from _GeneratorSteps(self, generator, site))

        return register_checked_generator(checked(), generator)

    def delegate_values(self, value, site):
        self.safe_value(value, site)
        return _GeneratorSteps(self, iter(value), site)

    def pattern_bindings(self, values, site):
        self.safe_value(values, site)
        return True

    returned = staticmethod(_Returned)

    @staticmethod
    def safe_value(value, site):
        if _contains_carrier(value):
            raise CheckedAccessError(f"{site}: variable carrier cannot be stored or aliased")
        return value

    def iter_values(self, iterable, site):
        method = self.project_method(iterable, "__iter__")
        if method is None and inspect.getattr_static(type(iterable), "__iter__", _MISSING) is _MISSING:
            getitem = self.project_method(iterable, "__getitem__")
            if getitem is not None:
                lookup = self.callee(getitem, site, False, None)
                for index in count():
                    try:
                        value = lookup(index)
                    except (IndexError, StopIteration):
                        return
                    yield self.safe_value(value, site)
        iterator = iter(iterable) if method is None else self.callee(method, site, False, None)()
        advance = self.project_method(iterator, "__next__")
        step = partial(next, iterator) if advance is None else self.callee(advance, site, False, None)
        while True:
            try:
                value = step()
            except StopIteration:
                return
            yield self.safe_value(value, site)

    def positional_expansion(self, iterable, site, key=None):
        count = 0
        for value in self.iter_values(self.safe_value(iterable, site), site):
            count += 1
            yield value
        if self.scopes and key in self.scopes[-1].calls:
            scope = self.scopes[-1]
            scope.argument_offsets[key] = scope.argument_offsets.get(key, 0) + count - 1

    def keyword_expansion(self, mapping, site):
        self.safe_value(mapping, site)
        if type(mapping) is dict:
            return mapping
        return _CheckedKeywords(mapping, self, site)

    def unpack_values(self, iterable, site, decode_targets):
        frame = self.value_callback
        if decode_targets and frame is not None and frame.transfers.pop(id(iterable), _MISSING) is iterable:
            return iterable
        if type(iterable) in (list, tuple):
            return self.safe_value(iterable, site)
        return self.iter_values(iterable, site)

    @staticmethod
    def slice_key(start, stop, step):
        return slice(start, stop, step)

    def attribute(self, owner, name, site):
        if name == "value":
            location = _actual_location(owner)
            if location is not None:
                try:
                    return location.read()
                except _SOURCE_ERRORS as error:
                    _note_source(error, site)
                    raise
        slot = self.declared_field(owner, name)
        if slot is not None:
            return slot
        if name in {"pop_front", "push_back"}:
            if isinstance(owner, _Formal) and isinstance(owner._codec, Queue):
                owner = owner._projection()
            binding = _value_binding(owner) if _is_carrier(owner) else None
            codec = binding.leaf.codec if binding is not None else owner
            if isinstance(codec, Queue):
                method = getattr(owner, name, None)
                if method is None or getattr(method, "__func__", None) is getattr(Queue, name):
                    if name == "pop_front":
                        pop = self.value_method(owner, "pop", site)
                        return lambda: pop(0)
                    return self.value_method(owner, "append", site)
        return self.read_attribute(owner, name, site)

    def read_attribute(self, owner, name, site):
        cls = type(owner)
        if cls is super and name != "__class__" and owner.__self_class__ is not None:
            receiver_type = owner.__self_class__
            bases = type.__getattribute__(receiver_type, "__mro__")
            start = next((index for index, base in enumerate(bases) if base is owner.__thisclass__), len(bases))
            for base in bases[start + 1:]:
                namespace = type.__getattribute__(base, "__dict__")
                if name in namespace:
                    instance = None if owner.__self__ is receiver_type else owner.__self__
                    getter = self.descriptor_getter(namespace[name], instance, receiver_type, site)
                    if getter is not None:
                        return getter()
                    break
        lookup = _class_attribute(cls, "__getattribute__")
        override = self.project_method(owner, "__getattribute__")
        default = lookup in (
            object.__getattribute__, type.__getattribute__, SvObject.__getattribute__, SvStruct.__getattribute__,
        )
        if override is None and not default:
            return getattr(owner, name)
        try:
            if override is not None:
                return self.callee(override, site, False, None)(name)
            return self.read_descriptor(owner, name, site, lookup)
        except AttributeError:
            fallback = self.project_method(owner, "__getattr__")
            if fallback is not None:
                return self.callee(fallback, site, False, None)(name)
            if _class_attribute(cls, "__getattr__") is not _MISSING:
                return _special_method(owner, "__getattr__")(name)
            raise

    def read_descriptor(self, owner, name, site, lookup):
        cls = type(owner)
        descriptor = _class_attribute(cls, name)
        instance = owner
        if lookup is type.__getattribute__:
            declared = _class_attribute(owner, name)
            if declared is not _MISSING and not _data_descriptor(descriptor):
                descriptor = declared
                instance, cls = None, owner
        getter = self.descriptor_getter(descriptor, instance, cls, site)
        if getter is not None and (
            lookup is type.__getattribute__ or inspect.getattr_static(owner, name, _MISSING) is descriptor
        ):
            return getter()
        return lookup(owner, name)

    def descriptor_getter(self, descriptor, instance, cls, site):
        if type(descriptor) is property and instance is not None and self.project_function(descriptor.fget):
            return self.callee(MethodType(descriptor.fget, instance), site, False, None)
        getter = self.project_method(descriptor, "__get__")
        if getter is not None:
            return partial(self.callee(getter, site, False, None), instance, cls)
        return None

    def write_attribute(self, owner, name, value, site):
        cls = type(owner)
        override = self.project_method(owner, "__setattr__")
        if override is not None:
            self.callee(override, site, False, None)(name, value)
            return
        assign = _class_attribute(cls, "__setattr__")
        if assign in (object.__setattr__, type.__setattr__, SvObject.__setattr__, SvStruct.__setattr__):
            self.write_descriptor(owner, name, value, site, assign)
        else:
            setattr(owner, name, value)

    def write_descriptor(self, owner, name, value, site, assign):
        descriptor = _class_attribute(type(owner), name)
        if type(descriptor) is property and self.project_function(descriptor.fset):
            setter = MethodType(descriptor.fset, owner)
            self.callee(setter, site, False, None)(value)
            return
        setter = self.project_method(descriptor, "__set__")
        if setter is not None:
            self.callee(setter, site, False, None)(owner, value)
            return
        assign(owner, name, value)

    @staticmethod
    def declared_field(owner, name):
        if isinstance(owner, _Formal) and isinstance(owner._codec, SvStruct):
            output = owner._fixed_output()
            if output is not None:
                try:
                    return output._fixed_child(name)
                except KeyError:
                    return None
            owner = owner._projection()
        binding = _value_binding(owner) if _is_carrier(owner) else None
        codec = binding.leaf.codec if binding is not None else owner
        if isinstance(codec, SvObject) and any(
            declared == name for declared, _ in codec._SvObject__svtypes_members
        ):
            if binding is not None:
                return ExternalValueBinding(binding.child_member(name))
            return field(owner, name)
        return None

    def attribute_address(self, owner, name, site, stable):
        if name == "value":
            location = _actual_location(owner)
            if location is not None:
                return _variable_address(location, borrow=owner if stable and _has_indexed_binding(owner) else None)
        if self.declared_field(owner, name) is not None:
            raise CheckedAccessError(f"{site}: declared field assignment requires its .value slot")
        return _Address(
            lambda: self.read_attribute(owner, name, site),
            lambda value: self.write_attribute(owner, name, value, site),
        )

    def subscript(self, owner, key, site):
        try:
            if isinstance(owner, _Formal) and isinstance(owner._codec, (Array, DynArray, AssocArray)):
                output = owner._fixed_output()
                if output is not None:
                    if isinstance(key, slice):
                        raise CheckedAccessError(f"{site}: slice a container's .value to get independent values")
                    return output._fixed_child(key)
                owner = owner._projection()
            binding = _value_binding(owner) if _is_carrier(owner) else None
            if binding is not None and isinstance(binding.leaf.codec, (Array, DynArray, AssocArray)):
                if isinstance(key, slice):
                    raise CheckedAccessError(f"{site}: slice a container's .value to get independent values")
                return ExternalValueBinding(binding)[key]
            if isinstance(owner, (Array, DynArray, AssocArray)):
                if isinstance(key, slice):
                    raise CheckedAccessError(f"{site}: slice a container's .value to get independent values")
                if isinstance(owner, AssocArray):
                    key = _prepare(owner._key_template, key)
            method = self.project_method(owner, "__getitem__")
            if method is not None:
                return self.callee(method, site, False, None)(key)
            return owner[key]
        except _SOURCE_ERRORS as error:
            _note_source(error, site)
            raise

    def item_address(self, owner, key, site):
        if _is_carrier(owner):
            raise CheckedAccessError(f"{site}: subscript assignment requires .value")

        def write_item(value):
            method = self.project_method(owner, "__setitem__")
            if method is None:
                operator.setitem(owner, key, value)
            else:
                self.callee(method, site, False, None)(key, value)

        frame = self.value_callback
        allow_value = None
        if frame is not None and frame.copying and frame.value is owner and type(key) is int and key == id(frame.receiver):
            def allow_value(value):
                return frame.fresh.get(id(value)) is value

        return _Address(lambda: self.subscript(owner, key, site), write_item, allow_value=allow_value)

    def delete_item(self, owner, key, site):
        self.safe_value(owner, site)
        method = self.project_method(owner, "__delitem__")
        if method is None:
            operator.delitem(owner, key)
        else:
            self.callee(method, site, False, None)(key)

    def delete_attribute(self, owner, name, site):
        if self.declared_field(owner, name) is not None or (_is_carrier(owner) and name == "value"):
            raise CheckedAccessError(f"{site}: a declared variable slot cannot be deleted")
        method = self.project_method(owner, "__delattr__")
        if method is None:
            delattr(owner, name)
        else:
            self.callee(method, site, False, None)(name)

    def value_owner(self, owner, site):
        if _actual_location(owner) is None:
            return _PlainValue(self.attribute(owner, "value", site))
        return self._stable_aggregate(owner, site)

    def _stable_aggregate(self, owner, site):
        if not _has_indexed_binding(owner):
            return owner
        lease = self.scopes[-1].borrows.enter_context(self.acquire(owner, site))
        projected = lease._projection()
        if projected is None:
            raise CheckedAccessError(f"{site}: external nested value access requires stable aggregate subslots")
        return projected

    def value_child(self, owner, key, site):
        location = _actual_location(owner)
        if location is None:
            return _PlainValue(self.attribute(owner, "value", site)[key])
        if isinstance(location.codec, SvStruct):
            child = self.declared_field(owner, key)
            if child is None:
                raise KeyError(key)
        else:
            child = self.subscript(owner, key, site)
        return self._stable_aggregate(child, site)

    def value_index_address(self, owner, key, site):
        location = _actual_location(owner)
        if location is None:
            return self.item_address(self.attribute(owner, "value", site), key, site)
        codec = location.codec
        if isinstance(codec, SvStruct):
            selected = _actual_location(self.declared_field(owner, key))
            if selected is None:
                raise KeyError(key)
            return _variable_address(selected)
        if not isinstance(codec, (Array, DynArray, AssocArray)):
            raise CheckedAccessError(f"{site}: checked indexed write requires a container")
        if isinstance(owner, _Formal):
            output = owner._fixed_output()
            if output is not None:
                if not isinstance(key, slice):
                    def select_output():
                        return output._fixed_child(key)._location()

                    return _Address(lambda: select_output().read(), lambda value: select_output().prepare(value)())

                def read_slice():
                    positions = range(*key.indices(codec._size))
                    return [output._fixed_child(index).value for index in positions]

                def write_slice(values):
                    positions = range(*key.indices(codec._size))
                    values = list(values)
                    if len(values) != len(positions):
                        raise ValueError("Array slice assignment must preserve its size")
                    writes = [output._fixed_child(index)._prepare_write(value)
                              for index, value in zip(positions, values)]
                    for publish in writes:
                        publish()

                return _Address(read_slice, write_slice)
            owner = owner._projection()
        binding = _value_binding(owner)
        if isinstance(key, slice):
            if isinstance(codec, AssocArray):
                raise CheckedAccessError(f"{site}: associative arrays do not support slices")
            if binding is not None:
                edits = _ExternalEdits(binding)
                return _Address(lambda: location.read()[key], lambda values: edits.replace_slice(key, values))
            if isinstance(owner, (Array, DynArray)):
                return _Address(lambda: read_value(owner)[key], lambda values: _local_slice(owner, key, values))
            raise CheckedAccessError(f"{site}: checked slice assignment requires a concrete container")

        def select():
            # Retain the container and key across the RHS. Re-evaluate the
            # lookup on write to match Python's subscript assignment.
            if binding is not None:
                return _bound_location(_indexed_binding(binding, key))
            if isinstance(owner, (Array, DynArray, AssocArray)):
                actual_key = _prepare(codec._key_template, key) if isinstance(codec, AssocArray) else key
                selected = _actual_location(owner._elements[actual_key])
                if selected is not None:
                    return selected
            raise CheckedAccessError(f"{site}: indexed target has no supported variable slot")

        def write_item(value):
            if isinstance(owner, AssocArray) and binding is None:
                actual_key = _prepare(codec._key_template, key)
                if actual_key not in owner._elements:
                    if len(owner._elements) >= owner._max_length:
                        raise ResourceLimitError("AssocArray length exceeds its limit")
                    owner._elements[actual_key] = _element_storage(codec._val_template, value)
                    return
            select().prepare(value)()

        return _Address(lambda: select().read(), write_item)

    def delete_value_index(self, owner, key, site):
        location = _actual_location(owner)
        if location is None:
            del self.attribute(owner, "value", site)[key]
            return
        if not isinstance(location.codec, (DynArray, AssocArray)):
            raise CheckedAccessError(f"{site}: deletion requires a dynamic or associative container")
        if isinstance(owner, _Formal):
            owner = owner._projection()
        binding = _value_binding(owner)
        if binding is not None:
            _ExternalEdits(binding).delete(key)
            return
        if isinstance(owner, AssocArray):
            key = _prepare(owner._key_template, key)
        del owner._elements[key]
        if isinstance(owner, DynArray):
            owner._bind_mode_elements()

    def value_method(self, owner, name, site):
        if isinstance(owner, _Formal) and isinstance(owner._codec, (Array, DynArray, AssocArray)):
            owner = owner._projection()
        if not _is_carrier(owner):
            return self.attribute(self.attribute(owner, "value", site), name, site)
        binding = _value_binding(owner)
        codec = binding.leaf.codec if binding is not None else owner
        if not isinstance(codec, (Array, DynArray, AssocArray)) or name not in _COLLECTION_MUTATIONS:
            return getattr(self.attribute(owner, "value", site), name)

        if isinstance(codec, AssocArray):
            supported = _MAPPING_EDITS
        elif isinstance(codec, DynArray):
            supported = _SEQUENCE_EDITS
        else:
            supported = frozenset()
        if binding is None and isinstance(codec, (Array, DynArray)):
            supported |= _ORDER_EDITS
        if name not in supported:
            raise CheckedAccessError(f"{site}: collection operation {name} is unsupported for this storage")
        if binding is not None:
            edits = _ExternalEdits(binding)
        else:
            edits = _LocalEdits(owner, lambda function, value: self.callee(function, site, False, None)(value))
        method = "pop_key" if isinstance(codec, AssocArray) and name == "pop" else name
        return getattr(edits, method)

    def store(self, value, address, site):
        if address.allow_value is None or not address.allow_value(value):
            self.safe_value(value, site)
        try:
            address.write(value)
        except _SOURCE_ERRORS as error:
            _note_source(error, site)
            raise

    def begin_aug(self, address, operation, site):
        lease = None
        if address.borrow is not None:
            lease = self.scopes[-1].borrows.enter_context(self.acquire(address.borrow, site))
            address = _variable_address(_actual_location(lease))
        current = self.safe_value(address.read(), site)

        def finish(rhs):
            try:
                value = _AUGMENT[operation](current, self.safe_value(rhs, site))
                self.store(value, address, site)
            finally:
                if lease is not None:
                    lease.close()

        return finish

    def begin_name_aug(self, current, operation, site):
        self.safe_value(current, site)

        def finish(rhs):
            return self.safe_value(_AUGMENT[operation](current, self.safe_value(rhs, site)), site)

        return finish

    def higher_order_arguments(self, function, args, kwargs, site):
        if any(function is candidate for candidate in (map, filter, reduce, starmap, dropwhile, takewhile, filterfalse)):
            position, keyword = 0, None
        elif function is groupby:
            position, keyword = 1, "key"
        elif function is accumulate:
            position, keyword = 1, "func"
        elif (
            any(function is candidate for candidate in (sorted, min, max, list.sort))
            or (type(function) is BuiltinMethodType and function.__name__ == "sort"
                and isinstance(function.__self__, list))
        ):
            position, keyword = None, "key"
        else:
            return args, kwargs

        if position is not None and len(args) > position and callable(args[position]):
            callback = self.higher_order_callback(args[position], site)
            args = (*args[:position], callback, *args[position + 1:])
        if keyword is not None and callable(kwargs.get(keyword)):
            kwargs = {**kwargs, keyword: self.higher_order_callback(kwargs[keyword], site)}
        return args, kwargs

    def higher_order_callback(self, function, site):
        self.safe_value(function, site)
        root = self.root

        def checked(*args, **kwargs):
            active = _active_runtime.get()
            if active is not None and active.root == root:
                return active.safe_value(active.callee(function, site, False, None)(*args, **kwargs), site)
            runtime = _Runtime(root, None)
            with runtime.active():
                return runtime.safe_value(runtime.callee(function, site, False, None)(*args, **kwargs), site)

        return checked

    def callee(self, function, site, declaration, key):
        if key is not None:
            scope = self.scopes[-1]
            leases = ExitStack()
            scope.calls[key] = (function, leases)
            scope.argument_offsets[key] = 0

        # These builtins derive semantics from their caller's frame. Returning
        # the original callable keeps the actual call in the user's frame,
        # including aliases and getattr(builtins, ...), rather than our wrapper.
        if any(function is native for native in (
            builtins.locals, builtins.globals, builtins.vars,
            builtins.eval, builtins.exec, builtins.super,
        )):
            return function

        def invoke(*args, **kwargs):
            try:
                if _variable_factory(function):
                    if _contains_carrier(args) or _contains_carrier(kwargs):
                        raise CheckedAccessError("variable declaration arguments must be values")
                    result = function(*args, **kwargs)
                    if declaration and isinstance(result, ObjectDescriptor):
                        return _HandleSlot(result)
                    if declaration or not _contains_carrier(result):
                        if declaration and self.value_callback is not None:
                            self.value_callback.remember(result)
                        return result
                    raise CheckedAccessError("a new variable must be declared in a named assignment")
                if function is field:
                    actual = inspect.signature(field).bind(*args, **kwargs).arguments
                    result = self.declared_field(actual["owner"], actual["name"])
                    if result is None:
                        result = field(*args, **kwargs)
                    return self.safe_value(result, site) if declaration else result
                if function is external_storage_location or function is slot_identity:
                    result = function(*args, **kwargs)
                    return self.safe_value(result, site) if declaration else result
                if function is read_value or function is write_value:
                    actual = inspect.signature(function).bind(*args, **kwargs).arguments
                    location = _actual_location(actual["variable"])
                    if location is not None:
                        if function is read_value:
                            return location.read()
                        self.store(actual["value"], _variable_address(location), site)
                        return None
                    return function(*args, **kwargs)
                if function is hasattr and not kwargs and len(args) == 2 and type(args[1]) is str:
                    try:
                        self.attribute(args[0], args[1], site)
                    except AttributeError:
                        return False
                    return True
                if function is getattr or function is setattr:
                    if not kwargs and len(args) in (2, 3) and type(args[1]) is str:
                        if function is setattr and len(args) == 3:
                            return self.store(self.safe_value(args[2], site), self.attribute_address(args[0], args[1], site, False), site)
                        if function is getattr:
                            try:
                                result = self.attribute(args[0], args[1], site)
                            except AttributeError:
                                if len(args) != 3:
                                    raise
                                result = args[2]
                            return self.safe_value(result, site) if declaration else result
                if function is delattr and not kwargs and len(args) == 2 and type(args[1]) is str:
                    return self.delete_attribute(args[0], args[1], site)
                frame = self.value_callback
                if self.callback_parent(function):
                    result = self.invoke_value_callback(function, args, kwargs, frame.receiver, frame.operation, frame.value)
                    if frame.checks_result and frame.matches_result(result):
                        if frame.copying:
                            frame.remember(result)
                        else:
                            frame.remember(result[0])
                            frame.transfers[id(result)] = result
                    return result
                if frame is not None and frame.copying and function is id and not kwargs and len(args) == 1:
                    value = args[0]
                    if value is frame.receiver or value is frame.value or frame.fresh.get(id(value)) is value:
                        return id(value)
                if function is deepcopy:
                    memo = args[1] if len(args) > 1 else kwargs.get("memo")
                    if memo is not None:
                        if frame is None or not frame.copying or memo is not frame.value:
                            raise CheckedAccessError("deepcopy in checked code uses its own memo")
                        return _invoke_checked(function, args, kwargs, callback_value=memo)
                if is_callback_gateway(function):
                    wrapper = function.__func__ if isinstance(function, MethodType) else function
                    if getattr(wrapper, "_svtypes_copy_operation", None) == "__deepcopy__":
                        raise CheckedAccessError("call deepcopy() to create a scoped memo")
                    result = function(*args, **kwargs)
                    if getattr(wrapper, '_svtypes_checked_call_gateway', False):
                        return self.safe_value(result, site)
                    return result
                if type(function) is partial:
                    if _contains_carrier(function):
                        raise CheckedAccessError("partial cannot hold a variable carrier")
                    target = self.callee(function.func, site, False, None)
                    return partial(target, *function.args, **function.keywords)(*args, **kwargs)
                if self.uses_default_constructor(function) and self.project_constructor(function):
                    return self.construct(function, args, kwargs, site)
                if type(function) in (MethodWrapperType, WrapperDescriptorType):
                    declaring = function.__objclass__
                    name = function.__name__
                    actuals = (function.__self__, *args) if type(function) is MethodWrapperType else args
                    if actuals and declaring in (object, type):
                        receiver, *forwarded = actuals
                        valid_receiver = declaring is object or issubclass(type(receiver), type)
                        if valid_receiver and declaring is type and name == "__call__" and self.project_constructor(receiver):
                            return self.construct(receiver, forwarded, kwargs, site)
                        if valid_receiver and not kwargs and forwarded and type(forwarded[0]) is str:
                            field_name = forwarded[0]
                            if name == "__getattribute__" and len(forwarded) == 1:
                                result = self.read_descriptor(receiver, field_name, site, declaring.__getattribute__)
                                return self.safe_value(result, site)
                            if name == "__setattr__" and len(forwarded) == 2:
                                value = self.safe_value(forwarded[1], site)
                                return self.write_descriptor(receiver, field_name, value, site, declaring.__setattr__)
                method = self.project_method(function, "__call__")
                if method is None:
                    args, kwargs = self.higher_order_arguments(function, args, kwargs, site)
                return _invoke_checked(
                    function if method is None else method, args, kwargs, execute=self.execute,
                )
            except _SOURCE_ERRORS as error:
                _note_source(error, site)
                raise

        if key is None:
            return invoke

        def call(*args, **kwargs):
            del scope.calls[key]
            scope.argument_offsets.pop(key, None)
            scope.parameter_contracts.pop(key, None)
            with leases:
                return invoke(*args, **kwargs)

        return call

    def callback_parent(self, function):
        frame = self.value_callback
        if frame is None or not isinstance(function, MethodType) or function.__self__ is not frame.receiver:
            return False
        return any(
            type.__getattribute__(base, "__dict__").get(frame.operation) is function.__func__
            for base in type.__getattribute__(type(frame.receiver), "__mro__")
        )

    def invoke_value_callback(self, function, args, kwargs, receiver, operation, value):
        frame = _ValueCallback(receiver, operation, value)
        underlying = function.__func__ if isinstance(function, MethodType) else function
        native = isinstance(underlying, FunctionType) and is_library_source(underlying.__code__.co_filename)
        native = native or not self.project_function(underlying)
        execute = None if native else partial(self.execute, value_callback=frame)
        validate_result = None
        if frame.checks_result:
            validate_result = frame.matches_result if native else frame.allows_result
        error = ("a checked copy hook must return a new variable of its declared type" if frame.copying
                 else "a checked codec must return a new decoded structure and a byte count")
        with frame.scope():
            return _invoke_checked(
                function, args, kwargs, execute=execute, callback_value=value,
                callback_result=validate_result, callback_result_error=error,
            )

    def callback(self, function, args, kwargs, *, codec_boundary=None, copy_boundary=None):
        method = self.project_method(function, "__call__")
        if method is not None:
            function = method
        underlying = function.__func__ if isinstance(function, MethodType) else function
        filename = underlying.__code__.co_filename if isinstance(underlying, FunctionType) else None
        if filename is not None and is_library_source(filename):
            return function(*args, **kwargs)
        if codec_boundary is not None:
            codec, operation = codec_boundary
            value = args[0] if operation == "pack" and args and isinstance(args[0], type(codec)) else None
            return self.invoke_value_callback(function, args, kwargs, codec, operation, value)
        if copy_boundary is not None:
            receiver, operation = copy_boundary
            memo = (args[0] if args else kwargs.get("memo")) if operation == "__deepcopy__" else None
            return self.invoke_value_callback(function, args, kwargs, receiver, operation, memo)
        return _invoke_checked(function, args, kwargs, execute=self.execute)

    def project_function(self, function):
        if not isinstance(function, FunctionType):
            return False
        from .checked_imports import owns_source

        filename = inspect.getsourcefile(function)
        return (filename is not None and Path(filename).is_file()
                and (function is self.entry or owns_source(self.root, filename)))

    def project_method(self, owner, name):
        descriptor = _class_attribute(type(owner), name)
        function = descriptor.__func__ if type(descriptor) in (staticmethod, classmethod) else descriptor
        if self.project_function(function):
            return descriptor.__get__(owner, type(owner))
        return None

    @staticmethod
    def uses_default_constructor(function):
        cls = type(function)
        return issubclass(cls, type) and _class_attribute(cls, "__call__") is type.__call__

    def project_constructor(self, cls):
        allocator = _class_attribute(cls, "__new__")
        initializer = _class_attribute(cls, "__init__")
        return any(
            self.project_function(method.__func__ if type(method) in (staticmethod, classmethod) else method)
            for method in (allocator, initializer)
        )

    def construct(self, cls, args, kwargs, site):
        allocator = type.__getattribute__(cls, "__new__")
        if allocator is object.__new__:
            instance = allocator(cls)
        else:
            instance = self.callee(allocator, site, False, None)(cls, *args, **kwargs)
        if cls in type.__getattribute__(type(instance), "__mro__"):
            initialize = _special_method(instance, "__init__")
            result = self.callee(initialize, site, False, None)(*args, **kwargs)
            if result is not None:
                raise TypeError(f"__init__() should return None, not {type(result).__name__!r}")
        return instance

    def execute(self, function, args, kwargs, *, value_callback=None):
        underlying = function.__func__ if isinstance(function, MethodType) else function
        if isinstance(underlying, FunctionType) and is_library_source(underlying.__code__.co_filename):
            return function(*args, **kwargs)
        if (self.project_function(underlying) and not inspect.iscoroutinefunction(underlying)
                and not inspect.isasyncgenfunction(underlying)):
            checked = self.compile(underlying)
            if checked is None:
                return function(*args, **kwargs)
            previous, self.value_callback = self.value_callback, value_callback
            try:
                result = checked(function.__self__, *args, **kwargs) if isinstance(function, MethodType) else checked(*args, **kwargs)
                if inspect.isgeneratorfunction(underlying):
                    return self.generator_value(result, f"{underlying.__code__.co_filename}:{underlying.__code__.co_firstlineno}")
                return result
            finally:
                self.value_callback = previous
        # Parameter/return checks still apply at the call boundary. An opaque
        # body or a function outside the project is not itself a violation.
        return function(*args, **kwargs)

    def compile(self, function: FunctionType):
        cached = self.compiled.get(function)
        if cached is not None:
            return self.bind_compiled(function, cached)
        filename = function.__code__.co_filename
        try:
            # getsourcelines() unwraps decorators, but compilation must use the
            # source that belongs to this exact code object and closure.
            source, index = inspect.findsource(function)
            lines, first = ((source, 1) if function.__code__.co_name == "<lambda>" else
                            (inspect.getblock(source[index:]), index + 1))
            tree = ast.parse(textwrap.dedent("".join(lines)), filename)
        except (OSError, TypeError, SyntaxError):
            return None
        if function.__code__.co_name == "<lambda>":
            ast.increment_lineno(tree, first - 1)
            positions = function.__code__.co_positions()
            position = next(((line, column) for line, end, column, stop in positions
                             if line is not None and column is not None and stop is not None
                             and (line != end or column != stop)), None)
            candidates = [node for node in ast.walk(tree) if isinstance(node, ast.Lambda)
                          and node.lineno == function.__code__.co_firstlineno]
            if position is not None:
                candidates = [node for node in candidates if
                              (node.body.lineno, node.body.col_offset) <= position <
                              (node.body.end_lineno, node.body.end_col_offset)]
            if len(candidates) != 1:
                return None
            expression = candidates[0]
            original = ast.copy_location(ast.FunctionDef(
                name="_svtypes_lambda", args=expression.args,
                body=[ast.copy_location(ast.Return(expression.body), expression)],
                decorator_list=[]), expression)
        elif len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
            return None
        else:
            original = tree.body[0]
            ast.increment_lineno(original, first - 1)
        runtime_name = "_svtypes_checked_runtime"
        used = {node.id for node in ast.walk(original) if isinstance(node, ast.Name)}
        used.update(node.arg for node in ast.walk(original) if isinstance(node, ast.arg))
        while runtime_name in used:
            runtime_name += "_"
        declaration_names = set(function.__code__.co_varnames) - set(function.__code__.co_cellvars)
        lower = _Lower(runtime_name, filename, declaration_names, generator=inspect.isgeneratorfunction(function))
        original.decorator_list = []
        original.returns = None
        original.args.defaults = []
        original.args.kw_defaults = [None] * len(original.args.kwonlyargs)
        for node in ast.walk(original.args):
            if isinstance(node, ast.arg):
                node.annotation = None
        original.body = lower.statements(original.body)

        names = [*function.__code__.co_freevars, runtime_name]
        wrapper = ast.FunctionDef(
            name="_svtypes_checked_factory",
            args=ast.arguments(posonlyargs=[], args=[ast.arg(arg=name) for name in names],
                               kwonlyargs=[], kw_defaults=[], defaults=[]),
            body=[original, ast.Return(ast.Name(original.name, ast.Load()))],
            decorator_list=[],
        )
        future_flags = function.__code__.co_flags & __future__.annotations.compiler_flag
        code = compile(ast.fix_missing_locations(ast.Module([wrapper], [])), filename, "exec",
                       flags=future_flags, dont_inherit=True)
        namespace: dict[str, Any] = {}
        exec(code, namespace)
        template = namespace["_svtypes_checked_factory"](*([None] * len(names)))
        self.compiled[function] = template.__code__
        return self.bind_compiled(function, template.__code__)

    def bind_compiled(self, function: FunctionType, code: CodeType):
        cells = dict(zip(function.__code__.co_freevars, function.__closure__ or ()))
        for name in code.co_freevars:
            if name not in cells:
                cells[name] = _cell(self)
        checked = FunctionType(code, function.__globals__, function.__name__,
                               function.__defaults__,
                               tuple(cells[name] for name in code.co_freevars))
        checked.__kwdefaults__ = function.__kwdefaults__
        return checked


def run_checked(function, /, *args, **kwargs):
    """Run a Python entry function with checked SvTypes variable access."""
    underlying = function.__func__ if isinstance(function, MethodType) else function
    if not callable(function):
        raise TypeError("run_checked() requires a callable")
    try:
        filename = inspect.getsourcefile(underlying)
    except TypeError:
        filename = None
    from .checked_imports import project_root

    source_available = filename is not None and Path(filename).is_file()
    root = project_root(filename, getattr(underlying, "__globals__", {}).get("__package__")) if source_available else Path.cwd()
    runtime = _Runtime(root, underlying)
    with runtime.active():
        try:
            return _invoke_checked(function, args, kwargs, execute=runtime.execute)
        except _SOURCE_ERRORS as error:
            if isinstance(underlying, FunctionType):
                _note_source(error, f"{filename}:{underlying.__code__.co_firstlineno}: {underlying.__qualname__}")
            raise

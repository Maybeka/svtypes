"""Import-time carrier binding checks for Python source in a checked project."""

from __future__ import annotations

import ast
import builtins
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from importlib.abc import MetaPathFinder
from importlib.machinery import PathFinder, SourceFileLoader
from importlib.util import decode_source
from pathlib import Path
import sys
import sysconfig
from threading import RLock

from .checked_call import CheckedAccessError, _contains_carrier
from ._checked_callbacks import is_library_source
from .checked_source import _variable_factory


_active_root: ContextVar[Path | None] = ContextVar("svtypes_checked_project", default=None)
_source_observers: ContextVar[tuple] = ContextVar("svtypes_source_observers", default=())
_finder_lock = RLock()
_finder_users = 0
_library = Path(__file__).resolve().parent
_dependency_roots = tuple(
    Path(sysconfig.get_path(kind)).resolve()
    for kind in ("stdlib", "platstdlib", "purelib", "platlib")
)


@lru_cache(maxsize=128)
def project_root(filename: str, package: str | None) -> Path:
    directory = Path(filename).resolve().parent
    for parent in (directory, *directory.parents):
        if any((parent / marker).exists() for marker in ("pyproject.toml", "setup.cfg", ".git")):
            return parent
    # For python -m pkg.mod, sibling subpackages belong to the same source tree.
    for name in reversed((package or "").split(".")):
        if directory.name != name:
            break
        directory = directory.parent
    return directory


def owns_source(root: Path, filename: str) -> bool:
    if filename.startswith("<") and filename.endswith(">"):
        return False
    source = Path(filename).resolve()
    return (
        source.is_relative_to(root)
        and not is_library_source(filename)
        and not any(source.is_relative_to(path) for path in _dependency_roots)
        and not {"site-packages", "dist-packages", ".venv"}.intersection(source.relative_to(root).parts)
    )


@contextmanager
def checked_project(root: str | Path):
    """Opt into carrier checks for fresh imports below ``root`` in this scope.

    Ordinary imports keep their native loader and bytecode cache. Already
    imported modules are not reloaded. Nested and concurrent scopes compose.
    ``run_checked`` selects its entry project's scope automatically.
    """
    global _finder_users
    token = _active_root.set(Path(root).resolve())
    with _finder_lock:
        if not _finder_users:
            index = next((i for i, finder in enumerate(sys.meta_path) if finder is PathFinder), len(sys.meta_path))
            sys.meta_path.insert(index, _project_finder)
        _finder_users += 1
    try:
        yield
    finally:
        _active_root.reset(token)
        with _finder_lock:
            _finder_users -= 1
            if not _finder_users and _project_finder in sys.meta_path:
                sys.meta_path.remove(_project_finder)


@contextmanager
def observe_module_sources(observer):
    """Observe original source bytes read by the checked module loader.

    The callback receives ``(filename, source_bytes)`` before parsing. It may
    reject a load by raising; it cannot replace the source or disable checks.
    Observers are scoped to the current execution context and compose when
    nested. Cached modules and other loaders do not produce observations.
    """
    if not callable(observer):
        raise TypeError("module source observer must be callable")
    token = _source_observers.set((*_source_observers.get(), observer))
    try:
        yield
    finally:
        _source_observers.reset(token)


class _ModuleGuards:
    @staticmethod
    def value(value, site):
        if _contains_carrier(value):
            raise CheckedAccessError(f"{site}: variable carrier cannot be stored or aliased during import")
        return value

    def declare(self, function, site):
        # Keep frame-sensitive builtins at the original call site, including
        # aliases. A wrapper would make them observe this module's frame.
        if any(function is native for native in (
            builtins.locals, builtins.globals, builtins.vars,
            builtins.eval, builtins.exec, builtins.super,
        )):
            return function

        def call(*args, **kwargs):
            self.value(args, site)
            self.value(kwargs, site)
            value = function(*args, **kwargs)
            return value if _variable_factory(function) else self.value(value, site)

        return call

    def iterate(self, values, site):
        for value in values:
            yield self.value(value, site)

    def import_bindings(self, names, site):
        namespace = sys._getframe(1).f_locals
        before = dict(namespace) if names is None else {
            name: namespace[name] for name in names if name in namespace
        }

        def restore_rejected():
            rejected = []
            for name in namespace if names is None else names:
                if name in namespace:
                    value = namespace[name]
                    if (name not in before or before[name] is not value) and _contains_carrier(value):
                        rejected.append(name)
            for name in rejected:
                if name in before:
                    namespace[name] = before[name]
                else:
                    del namespace[name]
            if rejected:
                names_text = ", ".join(rejected)
                raise CheckedAccessError(f"{site} ({names_text}): variable carrier cannot be imported")

        @contextmanager
        def bindings():
            try:
                yield
            except BaseException as original:
                try:
                    restore_rejected()
                except CheckedAccessError as rejection:
                    original.add_note(str(rejection))
                raise
            restore_rejected()

        return bindings()


_module_guards = _ModuleGuards()


class _ModuleBindings(ast.NodeTransformer):
    def __init__(self, filename: str, name: str):
        self.filename = filename
        self.name = name

    def guard(self, method, node, value):
        site = ast.Constant(f"{self.filename}:{node.lineno}: {ast.unparse(node)}")
        guards = ast.Name(self.name, ast.Load())
        return ast.copy_location(ast.Call(ast.Attribute(guards, method, ast.Load()), [value, site], []), node)

    def assigned(self, node, value, target):
        value = self.visit(value)
        if isinstance(target, ast.Name) and isinstance(value, ast.Call):
            value.func = self.guard("declare", node, value.func)
            return value
        return self.guard("value", node, value)

    def visit_Assign(self, node):
        target = node.targets[0] if len(node.targets) == 1 else None
        node.value = self.assigned(node, node.value, target)
        return node

    def visit_AnnAssign(self, node):
        if node.value is not None:
            node.value = self.assigned(node, node.value, node.target)
        return node

    def visit_NamedExpr(self, node):
        node.value = self.guard("value", node, self.visit(node.value))
        return node

    def visit_For(self, node):
        self.generic_visit(node)
        node.iter = self.guard("iterate", node.iter, node.iter)
        return node

    def visit_ImportFrom(self, node):
        if node.module == "__future__":
            return node
        names = None if node.names[0].name == "*" else tuple(
            dict.fromkeys(item.asname or item.name for item in node.names)
        )
        check = self.guard("import_bindings", node, ast.Constant(names))
        return ast.copy_location(ast.With([ast.withitem(check)], [node]), node)

    def visit_FunctionDef(self, node):
        # Python evaluates definitions in the original module namespace.
        # The function body is compiled when called through the checked entry.
        node.args.defaults = [self.guard("value", node, item) for item in node.args.defaults]
        node.args.kw_defaults = [
            self.guard("value", node, item) if item is not None else None
            for item in node.args.kw_defaults
        ]
        value = ast.Name(node.name, ast.Load())
        check = ast.Expr(self.guard("value", node, value))
        return [node, ast.copy_location(check, node)]

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        return node


class _ProjectSourceLoader(SourceFileLoader):
    def get_code(self, fullname):
        filename = self.get_filename(fullname)
        source_bytes = self.get_data(filename)
        for observer in _source_observers.get():
            observer(filename, source_bytes)
        source = decode_source(source_bytes)
        tree = ast.parse(source, filename)
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        names.update(node.name for node in ast.walk(tree)
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
        names.update(node.asname or node.name.split(".")[0]
                     for node in ast.walk(tree) if isinstance(node, ast.alias))
        helper = "_svtypes_module_guards"
        while helper in names:
            helper += "_"

        # A docstring and future statements must precede the guard import.
        offset = int(ast.get_docstring(tree, clean=False) is not None)
        while offset < len(tree.body):
            statement = tree.body[offset]
            if not isinstance(statement, ast.ImportFrom) or statement.module != "__future__":
                break
            offset += 1
        tree = _ModuleBindings(filename, helper).visit(tree)
        if offset < len(tree.body):
            imports = ast.ImportFrom("svtypes.checked_imports", [ast.alias("_module_guards", helper)], 0)
            tree.body.insert(offset, ast.copy_location(imports, tree.body[offset]))
        # The transformed code depends on the project policy. Do not write
        # it to a bytecode cache that may also be used by ordinary imports.
        return compile(ast.fix_missing_locations(tree), filename, "exec", dont_inherit=True)


class _ProjectFinder(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        root = _active_root.get()
        if root is None:
            return None
        spec = PathFinder.find_spec(fullname, path, target)
        if (
            spec is not None
            and type(spec.loader) is SourceFileLoader
            and owns_source(root, spec.origin)
        ):
            spec.loader = _ProjectSourceLoader(fullname, spec.origin)
            return spec
        return None


_project_finder = _ProjectFinder()

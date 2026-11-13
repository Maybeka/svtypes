"""Explicit entry points from library operations into user callbacks."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache, wraps
from inspect import CO_VARARGS, CO_VARKEYWORDS
from pathlib import Path
from types import FunctionType, MethodType
from weakref import WeakKeyDictionary


_dispatch = ContextVar("svtypes_checked_callback_dispatch", default=None)
_framework_sources = ContextVar("svtypes_checked_framework_sources", default=())
_library = Path(__file__).resolve().parent
_COPY_METHODS = ("__copy__", "__deepcopy__")
_checked_generators = WeakKeyDictionary()


def generator_inputs(generator):
    while generator in _checked_generators:
        generator = _checked_generators[generator]
    frame = getattr(generator, "gi_frame", None)
    if frame is None:
        return ()
    code = frame.f_code
    count = code.co_argcount + code.co_kwonlyargcount
    count += bool(code.co_flags & CO_VARARGS) + bool(code.co_flags & CO_VARKEYWORDS)
    names = (*code.co_freevars, *code.co_varnames[:count])
    return tuple(frame.f_locals[name] for name in names if name in frame.f_locals)


def register_checked_generator(wrapper, original):
    _checked_generators[wrapper] = original
    return wrapper


def checked_generator_inputs(generator):
    return generator_inputs(generator) if generator in _checked_generators else None


@lru_cache(maxsize=256)
def _source_path(filename: str) -> Path:
    return Path(filename).resolve()


def is_library_source(filename: str) -> bool:
    source = _source_path(filename)
    return source.is_relative_to(_library) or any(
        source == path or (directory and source.is_relative_to(path))
        for path, directory in _framework_sources.get()
    )


@contextmanager
def checked_library_sources(*sources):
    """Declare framework implementation sources for one checked execution scope.

    The framework is responsible for routing its user callbacks through checked
    entry points. This is an explicit integration boundary, not a security API.
    Sources may be exact files or package directories; unrelated user sources
    remain checked. Nested scopes restore the previous boundary on exit.
    """
    paths = []
    for source in sources:
        path = Path(source).resolve()
        if not path.is_file() and not path.is_dir():
            raise ValueError(f"checked library source does not exist: {path}")
        paths.append((path, path.is_dir()))
    token = _framework_sources.set((*_framework_sources.get(), *paths))
    try:
        yield
    finally:
        _framework_sources.reset(token)


@contextmanager
def callback_dispatch(dispatch):
    token = _dispatch.set(dispatch)
    try:
        yield
    finally:
        _dispatch.reset(token)


def call_callback(function, /, *args, **kwargs):
    dispatch = _dispatch.get()
    if dispatch is None:
        return function(*args, **kwargs)
    return dispatch(function, args, kwargs)


def call_codec(codec, operation, /, *args, **kwargs):
    function = getattr(codec, operation)
    dispatch = _dispatch.get()
    if dispatch is None:
        return function(*args, **kwargs)
    from .object import SvStruct

    boundary = (codec, operation) if isinstance(codec, SvStruct) else None
    return dispatch(function, args, kwargs, codec_boundary=boundary)


def call_method_callback(function, instance, /, *args, **kwargs):
    dispatch = _dispatch.get()
    if dispatch is None:
        return function(instance, *args, **kwargs)
    return dispatch(MethodType(function, instance), args, kwargs)


def callback_gateway(wrapper):
    wrapper._svtypes_callback_gateway = True
    return wrapper


def checked_call_gateway(function):
    """Mark a framework trampoline that applies its own checked call contract.

    Effective only inside checked_library_sources for this function's source.
    The trampoline must bind parameters through run_checked and route user
    callbacks through checked entry points. No wrapper or signature is added.
    """
    if not isinstance(function, FunctionType):
        raise TypeError('checked_call_gateway requires a Python function')
    function._svtypes_checked_call_gateway = True
    return callback_gateway(function)


def is_callback_gateway(function):
    if isinstance(function, MethodType):
        function = function.__func__
    return is_callback_wrapper(function)


def is_callback_wrapper(function):
    return (
        isinstance(function, FunctionType)
        and getattr(function, "_svtypes_callback_gateway", False)
        and is_library_source(function.__code__.co_filename)
    )


def guard_copy_method(name, method):
    if name not in _COPY_METHODS or not isinstance(method, FunctionType):
        return method
    if is_library_source(method.__code__.co_filename):
        return method

    @wraps(method)
    def guarded(self, *args, **kwargs):
        dispatch = _dispatch.get()
        if dispatch is None:
            return method(self, *args, **kwargs)
        return dispatch(MethodType(method, self), args, kwargs, copy_boundary=(self, name))

    guarded._svtypes_copy_operation = name
    return callback_gateway(guarded)


def guard_declared_copy_methods(cls):
    # copy invokes special methods itself, including when recursing into
    # an object graph. Keep its native memo and recursion behavior.
    for name in _COPY_METHODS:
        method = cls.__dict__.get(name)
        guarded = guard_copy_method(name, method)
        if guarded is not method:
            type.__setattr__(cls, name, guarded)

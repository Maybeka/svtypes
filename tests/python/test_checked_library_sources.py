import importlib.util
from pathlib import Path

import pytest
import svtypes
from svtypes._checked_callbacks import is_library_source
from svtypes.checked_imports import owns_source


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_library_source_scope_restores_exact_files_and_nested_directories(tmp_path):
    first = tmp_path / 'framework.py'
    first.write_text('', encoding='utf-8')
    other = tmp_path / 'user.py'
    other.write_text('', encoding='utf-8')
    nested = tmp_path / 'native'
    nested.mkdir()
    child = nested / 'helpers.py'
    child.write_text('', encoding='utf-8')
    assert not is_library_source(str(first))
    with svtypes.checked_library_sources(first):
        assert is_library_source(str(first))
        assert not owns_source(tmp_path, str(first))
        assert owns_source(tmp_path, str(other))
        with pytest.raises(RuntimeError, match='restore'):
            with svtypes.checked_library_sources(nested):
                assert is_library_source(str(child))
                raise RuntimeError('restore')
        assert not is_library_source(str(child))
    assert not is_library_source(str(first))
    with pytest.raises(ValueError, match='does not exist'):
        with svtypes.checked_library_sources(tmp_path / 'missing'):
            pass


def test_framework_constructor_runs_natively_but_user_initializer_remains_checked(tmp_path):
    framework = tmp_path / 'framework.py'
    framework.write_text('''from svtypes import SvObject, Int
class Native(SvObject):
    number = Int()
    def __init__(self):
        internal_slot = self.number
        internal_slot.value = 4
''', encoding='utf-8')
    native = load(framework, 'scope_native_framework')
    business = tmp_path / 'business.py'
    business.write_text('''from svtypes import Int
class Good(Native):
    def __init__(self):
        super().__init__()
        self.number.value += 3
class Bad(Native):
    def __init__(self):
        super().__init__()
        forbidden = self.number
def good():
    value = Good()
    return value.number.value
def bad():
    return Bad()
''', encoding='utf-8')
    spec = importlib.util.spec_from_file_location('scope_business', business)
    user = importlib.util.module_from_spec(spec)
    user.Native = native.Native
    spec.loader.exec_module(user)
    with pytest.raises(svtypes.CheckedAccessError, match='cannot be stored'):
        svtypes.run_checked(user.good)
    with svtypes.checked_library_sources(framework):
        assert svtypes.run_checked(user.good) == 7
        with pytest.raises(svtypes.CheckedAccessError, match='cannot be stored'):
            svtypes.run_checked(user.bad)
    with pytest.raises(svtypes.CheckedAccessError, match='cannot be stored'):
        svtypes.run_checked(user.good)


def test_scoped_library_function_keeps_checked_parameter_and_copyout_contracts(tmp_path):
    framework = tmp_path / 'parameter_framework.py'
    framework.write_text('''from svtypes import Int, Ref, Output, Inout
def exchange(seed: Int, observed: Output[Int], total: Inout[Int], shared: Ref[Int]):
    observed.value = seed.value
    seed.value = 99
    total.value += observed.value
    shared.value += 2
''', encoding='utf-8')
    native = load(framework, 'scope_parameter_framework')
    business = tmp_path / 'parameter_business.py'
    business.write_text('''from svtypes import Int
def main():
    seed = Int(3)
    observed = Int()
    total = Int(10)
    shared = Int(4)
    exchange(seed, observed, total, shared)
    return seed.value, observed.value, total.value, shared.value
''', encoding='utf-8')
    user = load(business, 'scope_parameter_business')
    user.exchange = native.exchange
    assert svtypes.run_checked(user.main) == (3, 3, 13, 6)
    with svtypes.checked_library_sources(framework):
        assert svtypes.run_checked(user.main) == (3, 3, 13, 6)


@pytest.mark.parametrize('operation, message', [
    ('untyped', 'parameter has no variable carrier contract'),
    ('leak', 'returned a variable carrier'),
    ('callback', 'cannot be stored'),
])
def test_library_function_scope_does_not_bypass_contract_or_business_callback(tmp_path, operation, message):
    framework = tmp_path / 'contract_framework.py'
    framework.write_text('''from svtypes import Int, Ref, run_checked
def untyped(value):
    return value
def leak(value: Ref[Int]):
    return value
def callback(value: Ref[Int], function):
    return run_checked(function, value)
''', encoding='utf-8')
    native = load(framework, 'scope_contract_framework')
    business = tmp_path / 'contract_business.py'
    business.write_text('''from svtypes import Int, Ref
def bad(value: Ref[Int]):
    forbidden = value
def main():
    value = Int(3)
    if operation == 'callback':
        return invoke(value, bad)
    return invoke(value)
''', encoding='utf-8')
    user = load(business, 'scope_contract_business')
    user.invoke = getattr(native, operation)
    user.operation = operation
    with svtypes.checked_library_sources(framework):
        with pytest.raises(svtypes.CheckedAccessError, match=message):
            svtypes.run_checked(user.main)


def gateway_modules(tmp_path):
    framework = tmp_path / 'gateway_framework.py'
    framework.write_text('''from svtypes import Int, Input, Output, Inout, Ref, checked_call_gateway, run_checked
calls = 0
def typed(seed: Input[Int], observed: Output[Int], total: Inout[Int], shared: Ref[Int]):
    global calls
    calls += 1
    observed.value = seed.value
    total.value += seed.value
    shared.value += 2
@checked_call_gateway
def exchange(seed, observed, total, shared):
    return run_checked(typed, seed, observed, total, shared)
@checked_call_gateway
def callback(value, function):
    return run_checked(function, value)
@checked_call_gateway
def leak(value):
    return value
''', encoding='utf-8')
    native = load(framework, 'checked_gateway_framework')
    business = tmp_path / 'gateway_business.py'
    business.write_text('''from svtypes import Int, Ref
def bad(value: Ref[Int]):
    forbidden = value
def main():
    seed = Int(3)
    observed = Int(77)
    total = Int(10)
    shared = Int(4)
    exchange(seed, observed, total, shared)
    return seed.value, observed.value, total.value, shared.value
def invoke_callback():
    value = Int(3)
    callback(value, bad)
def invoke_leak():
    value = Int(3)
    leak(value)
''', encoding='utf-8')
    user = load(business, 'checked_gateway_business')
    user.exchange, user.callback, user.leak = native.exchange, native.callback, native.leak
    return framework, native, user


def test_checked_call_gateway_applies_directions_once_and_preserves_function_identity(tmp_path):
    framework, native, user = gateway_modules(tmp_path)
    assert svtypes.checked_call_gateway(native.exchange) is native.exchange
    with svtypes.checked_library_sources(framework):
        assert svtypes.run_checked(user.main) == (3, 3, 13, 6)
    assert native.calls == 1


def test_checked_call_gateway_is_not_effective_outside_explicit_library_scope(tmp_path):
    framework, native, user = gateway_modules(tmp_path)
    for _ in range(2):
        with pytest.raises(svtypes.CheckedAccessError, match='parameter has no variable carrier contract'):
            svtypes.run_checked(user.main)
        with svtypes.checked_library_sources(framework):
            assert svtypes.run_checked(user.main) == (3, 3, 13, 6)
    assert native.calls == 2


@pytest.mark.parametrize('operation,message', [
    ('invoke_callback', 'cannot be stored'),
    ('invoke_leak', 'cannot be stored'),
])
def test_checked_call_gateway_does_not_allow_user_callback_or_result_carrier_escape(tmp_path, operation, message):
    framework, _, user = gateway_modules(tmp_path)
    with svtypes.checked_library_sources(framework):
        with pytest.raises(svtypes.CheckedAccessError, match=message):
            svtypes.run_checked(getattr(user, operation))


def test_checked_call_gateway_requires_an_ordinary_function():
    with pytest.raises(TypeError, match='requires a Python function'):
        svtypes.checked_call_gateway(object())

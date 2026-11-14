import importlib
import sys
import os
import subprocess

import pytest
import svtypes
from svtypes.checked_imports import _ProjectFinder, _ProjectSourceLoader, checked_project


@pytest.mark.parametrize('checked', [False, True])
def test_import_preserves_native_frame_builtins_and_aliases(tmp_path, checked):
    source = '''import builtins
answer = 42
namespace = globals()
local_namespace = locals()
variables = vars()
evaluate = builtins.eval
result = evaluate("answer + 1")
executed = exec("created = answer + 2")
alias = globals
aliased_namespace = alias()
class Ordinary:
    answer = 7
    namespace = locals()
'''
    (tmp_path / 'ordinary.py').write_text(source, encoding='utf-8')
    script = tmp_path / 'main.py'
    script.write_text('''import svtypes
import ordinary as m
assert m.namespace is vars(m)
assert m.local_namespace is vars(m)
assert m.variables is vars(m)
assert m.aliased_namespace is vars(m)
assert m.result == 43 and m.created == 44
assert m.Ordinary.namespace["answer"] == 7
''', encoding='utf-8')
    if checked:
        body = script.read_text()
        script.write_text('from pathlib import Path\nfrom svtypes import checked_project\n'
                          'with checked_project(Path(__file__).parent):\n'
                          + ''.join('    ' + line + '\n' for line in body.splitlines()))
    from pathlib import Path
    env = dict(os.environ, PYTHONPATH=str(Path(svtypes.__file__).resolve().parent.parent))
    result = subprocess.run([sys.executable, str(script)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_checked_finder_leaves_other_sources_for_later_finders(tmp_path, monkeypatch):
    source = tmp_path / 'outside_source.py'
    source.write_text('VALUE = 7\n', encoding='utf-8')
    monkeypatch.syspath_prepend(str(tmp_path))
    with checked_project(tmp_path / 'another_project'):
        assert _ProjectFinder().find_spec('outside_source') is None


def test_observer_sees_original_bytes_before_transformation_and_checks_remain(tmp_path):
    source = tmp_path / 'checked_bytes.py'
    raw = b'# coding: latin-1\r\nlabel = "caf\xe9"\r\n'
    source.write_bytes(raw)
    observations = []
    namespace = {'__name__': 'checked_bytes'}
    loader = _ProjectSourceLoader('checked_bytes', str(source))
    with svtypes.observe_module_sources(lambda filename, data: observations.append((filename, data))):
        exec(loader.get_code('checked_bytes'), namespace)
    assert observations == [(str(source), raw)]
    assert namespace['label'] == 'caf\xe9'

    source.write_text('from svtypes import Int\noriginal = Int()\nalias = original\n', encoding='utf-8')
    with svtypes.observe_module_sources(lambda filename, data: None):
        with pytest.raises(svtypes.CheckedAccessError, match='cannot be stored or aliased'):
            exec(loader.get_code('checked_bytes'), {'__name__': 'checked_bytes'})


def test_observers_nest_and_restore_after_rejection(tmp_path):
    source = tmp_path / 'checked_nested.py'
    source.write_text('VALUE = 7\n', encoding='utf-8')
    loader = _ProjectSourceLoader('checked_nested', str(source))
    observations = []

    def reject(filename, data):
        raise ValueError('source rejected')

    with svtypes.observe_module_sources(lambda filename, data: observations.append('outer')):
        with svtypes.observe_module_sources(lambda filename, data: observations.append('inner')):
            loader.get_code('checked_nested')
        with pytest.raises(ValueError, match='source rejected'):
            with svtypes.observe_module_sources(reject):
                loader.get_code('checked_nested')
        loader.get_code('checked_nested')
    loader.get_code('checked_nested')
    assert observations == ['outer', 'inner', 'outer', 'outer']
    with pytest.raises(TypeError, match='must be callable'):
        with svtypes.observe_module_sources(None):
            pass


def test_public_observer_composes_with_checked_project_import_and_module_cache(tmp_path, monkeypatch):
    name = 'checked_observer_actual_module'
    source = tmp_path / (name + '.py')
    raw = b'from svtypes import Int\nvalue = Int()\n'
    source.write_bytes(raw)
    monkeypatch.syspath_prepend(str(tmp_path))
    observations = []
    try:
        with checked_project(tmp_path), svtypes.observe_module_sources(
                lambda filename, data: observations.append((filename, data))):
            module = importlib.import_module(name)
            assert importlib.import_module(name) is module
        assert isinstance(module.value, svtypes.Int)
        assert observations == [(str(source), raw)]
    finally:
        sys.modules.pop(name, None)

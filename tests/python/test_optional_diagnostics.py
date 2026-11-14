"""Default paths avoid optional inspection; explicit diagnostics still work."""

import importlib
import sys
from importlib.machinery import SourceFileLoader

import pytest

from svtypes import (
    Bit, CodecSession, ExternalStorageError, Int, MemoryExternalFieldStorage,
    Object, ObjectRegistry, Queue, SvObject, bind_external_value, checked_project,
    constraint, encoding_descriptor, generate, schema_descriptor,
)


def test_declaration_and_ordinary_randomization_do_not_build_diagnostic_metadata(monkeypatch):
    import svtypes.schema as schema
    from svtypes.constraint.ir import ConstraintIR

    def unexpected(*args, **kwargs):
        pytest.fail("ordinary use requested diagnostic metadata")

    monkeypatch.setattr(schema, "_fingerprint", unexpected)
    monkeypatch.setattr(ConstraintIR, "digest", unexpected)

    class Ordinary(SvObject):
        value = Int(cov=False)

        @constraint
        def legal(self):
            self.value >= 0

    value = Ordinary()
    for _ in range(5):
        assert value.randomize()


def test_wire_metadata_is_lazy_independent_of_source_schema_and_invalidated(monkeypatch):
    import svtypes.schema as schema
    from svtypes.constraint.ir import ConstraintIR

    def unexpected(*args, **kwargs):
        pytest.fail("wire metadata traversed source-only metadata")

    class Parent(SvObject):
        value = Bit[8](cov=False)

    class Child(Parent):
        other = Bit[16](cov=False)

    expected = schema_descriptor(Child).encoding_fingerprint
    monkeypatch.setattr(schema, "schema_descriptor", unexpected)
    monkeypatch.setattr(schema, "_policies", unexpected)
    monkeypatch.setattr(ConstraintIR, "digest", unexpected)
    assert encoding_descriptor(Child).encoding_fingerprint == expected
    assert encoding_descriptor(Parent).encoding_fingerprint != expected
    first = Child().to_bytes()
    # A declaration refresh invalidates without hashing on the refresh path.
    fingerprint = schema._fingerprint
    monkeypatch.setattr(schema, "_fingerprint", unexpected)
    Child.refresh_declarations()
    assert Child._SvObject__svtypes_prepared_encoding_fingerprint is None
    monkeypatch.setattr(schema, "_fingerprint", fingerprint)
    assert encoding_descriptor(Child).encoding_fingerprint == expected
    assert len(Child().to_bytes()) == len(first)


def test_import_checks_are_explicit_and_native_loader_is_restored(tmp_path, monkeypatch):
    from svtypes.checked_imports import _ProjectFinder
    from svtypes import CheckedAccessError

    name = 'optional_diagnostics_import_fixture'
    (tmp_path / (name + '.py')).write_text('from svtypes import Int\na = Int(7)\nb = a\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    before = list(sys.meta_path)
    assert not any(isinstance(finder, _ProjectFinder) for finder in before)
    try:
        ordinary = importlib.import_module(name)
        assert type(ordinary.__loader__) is SourceFileLoader
        assert ordinary.a is ordinary.b
        sys.modules.pop(name)
        # A native .pyc must not bypass a subsequently enabled policy.
        with checked_project(tmp_path):
            with checked_project(tmp_path):
                with pytest.raises(CheckedAccessError, match='cannot be stored or aliased'):
                    importlib.import_module(name)
            assert any(isinstance(finder, _ProjectFinder) for finder in sys.meta_path)
        assert sys.meta_path == before
        restored = importlib.import_module(name)
        assert type(restored.__loader__) is SourceFileLoader
        assert restored.a is restored.b
    finally:
        sys.modules.pop(name, None)


@pytest.mark.parametrize('select_child', [False, True])
def test_external_handle_identity_is_independent_of_optional_diagnostics(select_child):
    import struct

    registry = ObjectRegistry()

    class Item(SvObject):
        value = Int(cov=False)

    registry.register(Item)

    class ChangedHeader(MemoryExternalFieldStorage):
        def read(self, key, descriptor, path):
            payload = bytearray(super().read(key, descriptor, path))
            start = payload.find(b'SVXO')
            length = struct.unpack_from('<I', payload, start + 16)[0]
            payload[start + 20 + length] ^= 1
            return bytes(payload)

    session = CodecSession()
    item = Item(session=session)
    item.value.value = 7
    codec = Queue[Object[Item](registry=registry)]()
    storage = ChangedHeader()
    storage.seed('items', codec, [item])
    item.value.value = 9
    ordinary = bind_external_value(codec, storage, 'items', session=session)
    assert list(ordinary.value) == [item]
    assert item.value.value == 9  # No replay of stale referent fields.
    strict = bind_external_value(codec, storage, 'items', session=session, diagnostics='strict')
    with pytest.raises(ExternalStorageError, match='fingerprint mismatch'):
        strict[0].value if select_child else list(strict.value)
    assert item.value.value == 9
    unavailable = bind_external_value(codec, storage, 'items', session=CodecSession())
    with pytest.raises(ExternalStorageError, match='unavailable'):
        list(unavailable.value)
    with pytest.raises(ValueError, match='diagnostics'):
        bind_external_value(codec, storage, 'items', diagnostics='invalid')


def test_checked_import_scope_is_context_local_and_removed_after_concurrent_scopes(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from svtypes.checked_imports import _ProjectFinder

    entered, exit_scope = Event(), Event()
    original = list(sys.meta_path)

    def worker():
        with checked_project(tmp_path):
            entered.set()
            assert exit_scope.wait(10)
            assert any(isinstance(finder, _ProjectFinder) for finder in sys.meta_path)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(worker)
        try:
            assert entered.wait(10)
            # Another thread's policy must not discover or transform our imports.
            assert _ProjectFinder().find_spec('anything') is None
            with checked_project(tmp_path):
                assert any(isinstance(finder, _ProjectFinder) for finder in sys.meta_path)
            assert any(isinstance(finder, _ProjectFinder) for finder in sys.meta_path)
        finally:
            exit_scope.set()
        future.result()
    assert sys.meta_path == original


def test_default_generation_skips_optional_manifests_and_source_hashes(tmp_path, monkeypatch):
    import svtypes.generator as generator
    import svtypes.schema as schema
    import svtypes.coverage.sv as coverage_sv

    class CodeOnly(SvObject):
        value = Bit[8](cov=False)

    def unexpected(*args, **kwargs):
        pytest.fail('code-only generation requested optional metadata')

    monkeypatch.setattr(generator, 'schema_descriptor', unexpected)
    monkeypatch.setattr(schema, '_policies', unexpected)
    monkeypatch.setattr(coverage_sv, 'observation_manifest', unexpected)
    result = generate(CodeOnly, tmp_path)
    assert not result.failed
    assert {path for path in result.written if not path.startswith('support/')} == {
        'CodeOnly.sv', 'CodeOnly.hpp', 'svtypes-manifest.json',
    }
    source = (tmp_path / 'CodeOnly.sv').read_text()
    assert 'bit [7:0] value;' in source
    assert 'function int layered_randomize' not in source
    assert any('function int layered_randomize' in path.read_text() for path in (tmp_path / 'support').glob('*.svh'))
    assert generate(CodeOnly, tmp_path, mode='check').is_current


def test_memory_storage_keeps_codec_contract_without_diagnostic_hashes(monkeypatch):
    import svtypes.schema as schema
    from svtypes import CompatibilityError

    storage = MemoryExternalFieldStorage()
    storage.seed('value', Bit[8](), 7)

    def unexpected(*args):
        pytest.fail('ordinary scalar storage computed a diagnostic hash')

    monkeypatch.setattr(schema, '_fingerprint', unexpected)
    monkeypatch.setattr(schema, '_policies', unexpected)
    value = bind_external_value(Bit[8](), storage, 'value')
    assert value.value == 7
    value.value = 9
    assert value.value == 9
    with pytest.raises(ExternalStorageError) as error:
        bind_external_value(Bit[16](), storage, 'value').value
    assert isinstance(error.value.__cause__, CompatibilityError)


def test_optional_generation_targets_are_independent(tmp_path):
    class MetadataOnly(SvObject):
        value = Int(cov=False)

    schema = generate(MetadataOnly, tmp_path / 'schema', targets={'schema'})
    coverage = generate(MetadataOnly, tmp_path / 'coverage', targets={'coverage'})
    assert set(schema.written) == {'MetadataOnly.schema.json', 'svtypes-manifest.json'}
    assert set(coverage.written) == {'MetadataOnly.coverage-manifest.json', 'svtypes-manifest.json'}

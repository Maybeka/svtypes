from dataclasses import FrozenInstanceError

import pytest
import svtypes


class Storage(svtypes.MemoryExternalFieldStorage):
    def __init__(self):
        super().__init__()
        self.reads = 0
        self.writes = 0

    def read(self, *args):
        self.reads += 1
        return super().read(*args)

    def write(self, *args):
        self.writes += 1
        return super().write(*args)


def test_field_location_is_metadata_only_and_expires_with_its_binding():
    storage = Storage()
    storage.seed('count', svtypes.Int(), 7)
    session = svtypes.CodecSession()
    bound = svtypes.bind_external_value(svtypes.Int(), storage, 'count', session=session)
    location = svtypes.external_storage_location(bound)
    assert isinstance(location, svtypes.ExternalStorageLocation)
    assert location.kind == 'field'
    assert location.storage is storage
    assert location.key == 'count'
    assert location.path == svtypes.FieldPath()
    assert location.descriptor.unified_type_name == 'svtypes.Int'
    assert not location.readonly
    assert location.session is session
    assert storage.reads == storage.writes == 0
    with pytest.raises(AttributeError, match='field locations use key/path'):
        _ = location.token
    with pytest.raises(FrozenInstanceError):
        location._key = 'another'
    bound.close()
    for name in ('kind', 'storage', 'key', 'path', 'descriptor', 'readonly', 'session'):
        with pytest.raises(svtypes.ExternalStorageClosedError):
            getattr(location, name)
    with pytest.raises(svtypes.ExternalStorageClosedError):
        svtypes.external_storage_location(bound)


def test_member_location_preserves_exact_path_and_readonly():
    class Packet(svtypes.SvStruct):
        words = svtypes.Queue[svtypes.Int]()

    packet = Packet()
    svtypes.write_value(packet, {'words': [1, 2]})
    storage = Storage()
    storage.seed('packet', Packet(), packet)
    bound = svtypes.bind_external_value(Packet(), storage, 'packet', readonly=True)
    selected = bound.value.words[1]
    storage.reads = 0
    location = svtypes.external_storage_location(selected)
    assert location.path == svtypes.FieldPath().member('words').index(1)
    assert location.key == 'packet'
    assert location.storage is storage
    assert location.readonly
    assert storage.reads == 0


def test_ref_formal_forwarding_preserves_endpoint_but_not_formal_lifetime():
    from svtypes.checked_call import _invoke_checked
    storage = Storage()
    storage.seed('count', svtypes.Int(), 3)
    bound = svtypes.bind_external_value(svtypes.Int(), storage, 'count')
    captured = []

    def inner(value: svtypes.Ref[svtypes.Int]):
        location = svtypes.external_storage_location(value)
        assert location.storage is storage
        assert location.key == 'count'
        captured.append(location)
        value.value += 2

    def outer(value: svtypes.Ref[svtypes.Int]):
        _invoke_checked(inner, (value,), {})
        assert value.value == 5

    _invoke_checked(outer, (bound,), {})
    assert bound.value == 5
    with pytest.raises(svtypes.CheckedAccessError, match='scope has closed'):
        _ = captured[0].storage
    assert svtypes.external_storage_location(bound).key == 'count'


def test_checked_business_code_cannot_save_or_return_storage_location():
    storage = Storage()
    storage.seed('count', svtypes.Int(), 3)
    bound = svtypes.bind_external_value(svtypes.Int(), storage, 'count')

    def save(value: svtypes.Ref[svtypes.Int]):
        saved = svtypes.external_storage_location(value)

    def returned(value: svtypes.Ref[svtypes.Int]):
        return svtypes.external_storage_location(value)

    with pytest.raises(svtypes.CheckedAccessError, match='cannot be stored'):
        svtypes.run_checked(save, bound)
    with pytest.raises(svtypes.CheckedAccessError, match='returned a variable carrier'):
        svtypes.run_checked(returned, bound)


@pytest.mark.parametrize('direction', [svtypes.Input, svtypes.Output, svtypes.Inout])
def test_value_direction_formals_do_not_expose_the_actual_external_slot(direction):
    storage = Storage()
    storage.seed('count', svtypes.Int(), 3)
    bound = svtypes.bind_external_value(svtypes.Int(), storage, 'count')
    captured = []

    def work(value):
        captured.append(svtypes.external_storage_location(value))
        value.value = 9

    work.__annotations__ = {'value': direction[svtypes.Int]}
    svtypes.run_checked(work, bound)
    assert captured == [None]
    assert bound.value == (3 if direction is svtypes.Input else 9)


def test_local_variables_and_element_borrows_have_no_external_location():
    scalar = svtypes.Int(3)
    words = svtypes.Queue[svtypes.Int]()
    words.value = [4]
    assert svtypes.external_storage_location(scalar) is None
    assert svtypes.external_storage_location(words) is None
    with svtypes.borrow_element(words, 0) as selected:
        assert svtypes.external_storage_location(selected) is None
    with pytest.raises(TypeError, match='requires a SvTypes variable'):
        svtypes.external_storage_location(3)


class TokenStorage(svtypes.MemoryExternalFieldStorage):
    def __init__(self):
        self.cells = [[4], [5]]
        self.acquired = 0
        self.released = []

    def acquire_borrow(self, key, descriptor, path):
        self.acquired += 1
        return self.cells[path.segments[0].index]

    def read_borrow(self, token):
        return svtypes.Int().pack(token[0])

    def write_borrow(self, token, payload):
        token[0], _ = svtypes.Int().unpack(payload)

    def release_borrow(self, token):
        self.released.append(token)


def test_stable_borrow_location_keeps_token_not_stale_index_and_acquires_no_lease():
    storage = TokenStorage()
    bound = svtypes.bind_external_value(svtypes.Queue[svtypes.Int](), storage, 'words', readonly=True)
    outer = svtypes.borrow_element(bound, 0)
    inner = outer.forward()
    location = svtypes.external_storage_location(inner)
    expected = storage.cells[0]
    assert location.kind == 'borrow'
    assert location.storage is storage
    assert location.token is expected
    assert location.readonly
    assert location.descriptor.unified_type_name == 'svtypes.Int'
    assert storage.acquired == 1
    storage.cells.insert(0, [99])
    assert location.token is expected
    with pytest.raises(AttributeError, match='do not expose an index/key path'):
        _ = location.path
    with pytest.raises(AttributeError, match='use token, not a field key'):
        _ = location.key
    inner.close()
    assert storage.released == []
    with pytest.raises(svtypes.ExternalStorageClosedError, match='borrow is closed'):
        _ = location.token
    assert outer.value == 4
    outer.close()
    assert storage.released == [expected]


def test_external_handle_slot_location_does_not_read_or_allocate_referent():
    registry = svtypes.ObjectRegistry()

    class Child(svtypes.SvObject):
        count = svtypes.Int()

    registry.register(Child)

    class Owner(svtypes.SvObject):
        child = svtypes.Object['Child'](registry=registry)

    storage = Storage()
    storage.seed('child', Owner.child, None)
    owner = Owner()
    owner.bind_external_storage(storage, {svtypes.FieldIdentity(Owner, 'child'): 'child'})
    location = svtypes.external_storage_location(svtypes.field(owner, 'child'))
    assert location.kind == 'field'
    assert location.key == 'child'
    assert location.path == svtypes.FieldPath()
    assert storage.reads == storage.writes == 0

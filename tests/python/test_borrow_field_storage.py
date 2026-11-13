import pytest
import svtypes


class AggregateStorage(svtypes.MemoryExternalFieldStorage):
    def __init__(self):
        super().__init__()
        self.tokens = {}
        self.operations = []
        self.whole_writes = 0

    def acquire_borrow(self, key, descriptor, path):
        token = object()
        self.tokens[token] = key, descriptor, path
        return token

    def read_borrow(self, token):
        return self.read(*self.tokens[token])

    def write_borrow(self, token, payload):
        self.whole_writes += 1
        self.write(*self.tokens[token], svtypes.FieldOperation.SET, payload)

    def release_borrow(self, token):
        del self.tokens[token]

    def read_borrow_path(self, token, path):
        key, descriptor, parent = self.tokens[token]
        return self.read(key, descriptor, svtypes.FieldPath((*parent.segments, *path.segments)))

    def write_borrow_path(self, token, path, operation, payload):
        self.operations.append((path, operation))
        key, descriptor, parent = self.tokens[token]
        return self.write(key, descriptor, svtypes.FieldPath((*parent.segments, *path.segments)),
                          operation, payload)


def setup():
    storage = AggregateStorage()
    codec = svtypes.Array[svtypes.Queue[svtypes.Int], 1]()
    storage.seed('rows', codec, [[1, 2]])
    return storage, svtypes.bind_external_value(codec, storage, 'rows')


def test_borrowed_queue_uses_relative_operations_without_whole_value_copyback():
    storage, bound = setup()

    def edit(queue: svtypes.Ref[svtypes.Queue[svtypes.Int]]):
        queue.value.append(9)
        queue.value.insert(0, 4)
        removed = queue.value.pop()
        queue.value[0] += 3
        queue.value[1] = 8
        return removed, queue.value

    def main(rows: svtypes.Ref[svtypes.Array[svtypes.Queue[svtypes.Int], 1]]):
        return edit(rows[0])

    assert svtypes.run_checked(main, bound) == (9, [7, 8, 2])
    assert storage.whole_writes == 0
    assert [operation for _, operation in storage.operations] == [
        svtypes.FieldOperation.APPEND, svtypes.FieldOperation.INSERT,
        svtypes.FieldOperation.POP, svtypes.FieldOperation.SET, svtypes.FieldOperation.SET,
    ]
    assert not storage.tokens


def test_relative_field_capability_does_not_claim_stable_child_borrowing():
    storage, bound = setup()

    def increment(value: svtypes.Ref[svtypes.Int]):
        value.value += 1

    def edit(queue: svtypes.Ref[svtypes.Queue[svtypes.Int]]):
        try:
            increment(queue[0])
        except svtypes.CheckedAccessError:
            queue.value[0] = 8
        else:
            raise AssertionError('field-path capability must not imply stable element identity')
        return queue.value

    def main(rows: svtypes.Ref[svtypes.Array[svtypes.Queue[svtypes.Int], 1]]):
        return edit(rows[0])

    assert svtypes.run_checked(main, bound) == [8, 2]
    assert not storage.tokens


def test_readonly_and_closed_borrowed_fields_never_reach_the_backend():
    storage, bound = setup()
    readonly = svtypes.bind_external_value(svtypes.Array[svtypes.Queue[svtypes.Int], 1](),
                                          storage, 'rows', readonly=True)

    def edit(queue: svtypes.Ref[svtypes.Queue[svtypes.Int]]):
        queue.value.append(3)

    def main(rows: svtypes.Ref[svtypes.Array[svtypes.Queue[svtypes.Int], 1]]):
        edit(rows[0])

    with pytest.raises(svtypes.ExternalStorageError, match='read-only'):
        svtypes.run_checked(main, readonly)
    assert not storage.operations and not storage.tokens
    with svtypes.borrow_element(bound, 0) as selected:
        # Framework-side projection retained only to check lease invalidation.
        values = selected._projection().value
        selected.close()
        with pytest.raises(svtypes.ExternalStorageClosedError, match='borrow is closed'):
            values.append(4)
    assert not storage.operations and not storage.tokens


class PopReplyStorage(AggregateStorage):
    def __init__(self, reply):
        super().__init__()
        self.reply = reply

    def write(self, key, descriptor, path, operation, payload):
        if operation is svtypes.FieldOperation.POP:
            # Model an update after the preliminary read but before removal.
            super().write(key, descriptor, path, svtypes.FieldOperation.SET,
                          svtypes.Int().pack(23))
            super().write(key, descriptor, path, operation, payload)
            return self.reply
        return super().write(key, descriptor, path, operation, payload)


@pytest.mark.parametrize('borrowed', [False, True])
@pytest.mark.parametrize('reply, expected', [(None, 2), (svtypes.Int().pack(23), 23)])
def test_pop_uses_backend_reply_with_legacy_none_fallback(borrowed, reply, expected):
    storage = PopReplyStorage(reply)
    codec = svtypes.Array[svtypes.Queue[svtypes.Int], 1]()
    storage.seed('rows', codec, [[1, 2]])
    bound = svtypes.bind_external_value(codec, storage, 'rows')

    def edit(queue: svtypes.Ref[svtypes.Queue[svtypes.Int]]):
        return queue.value.pop()

    def main(rows: svtypes.Ref[svtypes.Array[svtypes.Queue[svtypes.Int], 1]]):
        return edit(rows[0])

    result = svtypes.run_checked(main, bound) if borrowed else bound[0].value.pop()
    assert result == expected
    assert bound.value == [[1]]
    assert not storage.tokens


@pytest.mark.parametrize('method', ['pop', 'popitem'])
def test_checked_associative_pop_uses_removed_value(method):
    storage = PopReplyStorage(svtypes.Int().pack(23))
    codec = svtypes.AssocArray[svtypes.Int, svtypes.Int]()
    storage.seed('items', codec, {4: 2})
    bound = svtypes.bind_external_value(codec, storage, 'items')

    def main(items: svtypes.Ref[svtypes.AssocArray[svtypes.Int, svtypes.Int]]):
        if method == 'popitem':
            return items.value.popitem()
        return items.value.pop(4)

    assert svtypes.run_checked(main, bound) == ((4, 23) if method == 'popitem' else 23)
    assert bound.value == {}


@pytest.mark.parametrize('borrowed', [False, True])
def test_malformed_pop_reply_is_not_silently_replaced_by_the_snapshot(borrowed):
    storage = PopReplyStorage(b'')
    codec = svtypes.Array[svtypes.Queue[svtypes.Int], 1]()
    storage.seed('rows', codec, [[1, 2]])
    bound = svtypes.bind_external_value(codec, storage, 'rows')

    def edit(queue: svtypes.Ref[svtypes.Queue[svtypes.Int]]):
        return queue.value.pop()

    def main(rows: svtypes.Ref[svtypes.Array[svtypes.Queue[svtypes.Int], 1]]):
        return edit(rows[0])

    with pytest.raises(svtypes.ExternalStorageError, match='cannot be decoded'):
        if borrowed:
            svtypes.run_checked(main, bound)
        else:
            bound[0].value.pop()
    assert not storage.tokens


def test_checked_nested_value_methods_edit_the_bound_container_not_a_snapshot():
    storage, bound = setup()

    def main(rows: svtypes.Ref[svtypes.Array[svtypes.Queue[svtypes.Int], 1]]):
        rows.value[0].append(9)
        rows.value[0].insert(0, 4)
        removed = rows.value[0].pop()
        return removed, rows.value

    assert svtypes.run_checked(main, bound) == (9, [[4, 1, 2]])
    assert [operation for _, operation in storage.operations] == [
        svtypes.FieldOperation.APPEND, svtypes.FieldOperation.INSERT, svtypes.FieldOperation.POP,
    ]
    assert not storage.tokens and storage.whole_writes == 0


def test_checked_nested_mapping_methods_retain_the_original_field_storage():
    storage = AggregateStorage()
    codec = svtypes.Array[svtypes.AssocArray[svtypes.Int, svtypes.Int], 1]()
    storage.seed('rows', codec, [{4: 2}])
    bound = svtypes.bind_external_value(codec, storage, 'rows')

    def main(rows: svtypes.Ref[svtypes.Array[svtypes.AssocArray[svtypes.Int, svtypes.Int], 1]]):
        rows.value[0].update({5: 3})
        removed = rows.value[0].pop(4)
        return removed, rows.value

    assert svtypes.run_checked(main, bound) == (2, [{5: 3}])
    assert not storage.tokens and storage.whole_writes == 0


def test_checked_nested_value_mutation_respects_readonly_before_write():
    storage, _ = setup()
    bound = svtypes.bind_external_value(svtypes.Array[svtypes.Queue[svtypes.Int], 1](),
                                       storage, 'rows', readonly=True)

    def main(rows: svtypes.Ref[svtypes.Array[svtypes.Queue[svtypes.Int], 1]]):
        rows.value[0].append(9)

    with pytest.raises(svtypes.ExternalStorageError, match='read-only'):
        svtypes.run_checked(main, bound)
    assert not storage.tokens and not storage.operations

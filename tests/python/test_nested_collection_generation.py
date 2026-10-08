import pytest

from svtypes import Array, AssocArray, DynArray, Int, Queue, String, SvObject
from svtypes import sv_codegen_context, sv_type_expression, sv_packer_expression


def collection(kind, element):
    if kind == 'fixed':
        return Array[element, 2](rand=False, cov=False)
    if kind == 'assoc':
        return AssocArray[String, element](rand=False, cov=False)
    factory = Queue if kind == 'queue' else DynArray
    return factory[element](rand=False, cov=False)


@pytest.mark.parametrize('outer', ['fixed', 'queue', 'dynamic', 'assoc'])
@pytest.mark.parametrize('inner', ['fixed', 'queue', 'dynamic', 'assoc'])
def test_nested_collection_packer_uses_dependency_ordered_local_type(outer, inner):
    descriptor = collection(outer, collection(inner, Int(rand=False, cov=False)))
    cls = type('NestedValue', (SvObject,), {'values': descriptor})
    text = cls.to_sv_obj()
    alias = '__svtypes_values_element_t'
    assert 'typedef ' in text
    assert alias in text
    assert text.index('typedef ') < text.index('function void pack(')
    assert text.count('typedef ') == 1
    assert text.count(alias) >= 3
    assert cls.to_sv_obj() == text


def test_nested_collection_types_are_deduplicated_and_context_does_not_leak():
    class NestedValue(SvObject):
        first = Queue[Queue[Int]](rand=False, cov=False)
        second = DynArray[Queue[Int]](rand=False, cov=False)
        deep = Queue[Queue[Queue[Int]]](rand=False, cov=False)

    text = NestedValue.to_sv_obj()
    assert text.count('typedef ') == 2
    assert text.index('__svtypes_first_element_t') < text.index('__svtypes_deep_element_t')

    class FlatValue(SvObject):
        values = Queue[Int](rand=False, cov=False)

    assert 'typedef ' not in FlatValue.to_sv_obj()
    assert '__svtypes_first_element_t' not in FlatValue.to_sv_obj()


@pytest.mark.parametrize('outer', ['fixed', 'queue', 'dynamic', 'assoc'])
@pytest.mark.parametrize('inner', ['fixed', 'queue', 'dynamic', 'assoc'])
def test_public_render_context_handles_equivalent_collection_codecs(outer, inner):
    codec = collection(outer, collection(inner, Int()))
    equivalent_child = collection(inner, Int())
    before = sv_packer_expression(codec)
    with sv_codegen_context(codec, prefix='field') as declarations:
        assert len(declarations) == 1
        assert declarations[0].startswith('typedef ')
        assert sv_type_expression(equivalent_child) == 'field_0_t'
        assert 'field_0_t' in sv_packer_expression(codec)
    assert sv_packer_expression(codec) == before


def test_public_render_context_restores_after_exception_and_avoids_nested_name_collision():
    with sv_codegen_context(Queue[Queue[Int]](), prefix='field') as outer:
        assert len(outer) == 1
        with pytest.raises(RuntimeError):
            with sv_codegen_context(Queue[DynArray[Int]](), prefix='field') as inner:
                assert 'field_0_t_' in inner[0]
                raise RuntimeError('stop')
        assert sv_type_expression(Queue[Int]()) == 'field_0_t'
        assert sv_type_expression(DynArray[Int]()) != 'field_0_t_'
    assert sv_type_expression(Queue[Int]()) == 'int [$]'


@pytest.mark.parametrize('prefix', ['', 'bad-name', 'has space', None])
def test_public_render_context_rejects_invalid_identifier(prefix):
    with pytest.raises(ValueError, match='identifier'):
        with sv_codegen_context(Queue[Queue[Int]](), prefix=prefix):
            pass

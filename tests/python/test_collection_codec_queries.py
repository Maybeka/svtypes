import pytest

import svtypes


@pytest.mark.parametrize('key_type,value_type', [
    (svtypes.Int, svtypes.Int),
    (svtypes.String, svtypes.Bit[8]),
    (svtypes.BitSigned[16], svtypes.Logic[4]),
    (svtypes.String, svtypes.Queue[svtypes.Int]),
])
def test_associative_codec_queries_preserve_declared_types(key_type, value_type):
    value = svtypes.AssocArray[key_type, value_type]()
    before = svtypes.encoding_descriptor(value)
    for actual, declared in ((value.key_codec, key_type), (value.value_codec, value_type)):
        expected = svtypes.materialize_type_spec(declared, location='test collection query')
        assert svtypes.encoding_descriptor(actual) == svtypes.encoding_descriptor(expected)
        assert svtypes.sv_type_expression(actual) == svtypes.sv_type_expression(expected)
        assert svtypes.sv_packer_expression(actual) == svtypes.sv_packer_expression(expected)
    assert value.value == {}
    assert svtypes.encoding_descriptor(value) == before
    with pytest.raises(AttributeError):
        value.key_codec = svtypes.Int()
    with pytest.raises(AttributeError):
        value.value_codec = svtypes.Int()


def test_associative_codec_queries_do_not_depend_on_entries():
    value = svtypes.AssocArray[svtypes.String, svtypes.Int]()
    key, element = value.key_codec, value.value_codec
    value.value = {'mode': 7}
    assert value.key_codec is key
    assert value.value_codec is element
    assert value.value == {'mode': 7}

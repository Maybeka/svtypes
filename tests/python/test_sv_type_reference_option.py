from svtypes import Array, Bit, Package, Queue, SvObject, SvStruct, generate


def test_sv_type_reference_option_for_object_struct_and_package_generation(tmp_path):
    class Item(SvStruct):
        code = Bit[8]()
        data = Array[Bit[4], 3]()

    class Packet(SvObject):
        code = Bit[8]()
        data = Queue[Bit[4]]()
        item = Item()

    for cls in (Item, Packet):
        explicit = cls.to_sv_obj()
        inferred = cls.to_sv_obj(use_type_reference=True)
        assert 'type(this.' not in explicit
        assert 'type(value.' not in explicit
        assert 'type(this.' in inferred or 'type(value.' in inferred
        assert 'bit [7:0]' in explicit
        assert cls.to_sv_obj() == explicit

    explicit_dir = tmp_path / 'explicit'
    inferred_dir = tmp_path / 'inferred'
    generate([Item, Packet], explicit_dir, targets={'sv'})
    generate([Item, Packet], inferred_dir, targets={'sv'}, use_type_reference=True)
    explicit = '\n'.join(p.read_text() for p in explicit_dir.rglob('*.sv*'))
    inferred = '\n'.join(p.read_text() for p in inferred_dir.rglob('*.sv*'))
    assert 'type(this.' not in explicit and 'type(value.' not in explicit
    assert 'type(this.' in inferred and 'type(value.' in inferred

    package = Package('type_modes')
    package.register(Item)
    package.register(Packet)
    assert 'type(this.' not in package.to_sv_pkg()
    assert 'type(this.' in package.to_sv_pkg(use_type_reference=True)

import svtypes


def test_class_assembly_refreshes_inherited_fields_without_rewrapping_init():
    class Stub(svtypes.SvObject):
        pass

    class Child(Stub):
        local = svtypes.Int()

        def __init__(self):
            super().__init__()
            self.local.value = 7

    class Implementation(svtypes.SvObject):
        inherited = svtypes.Int()

    initializers = (Stub.__init__, Child.__init__)
    Stub.__bases__ = (Implementation,)
    Stub.refresh_declarations()
    Child.refresh_declarations()
    Child.refresh_declarations()
    assert (Stub.__init__, Child.__init__) == initializers
    value = Child()
    value.inherited.value = 41
    assert value.local.value == 7
    decoded = Child()
    decoded.from_bytes(value.to_bytes())
    assert decoded.inherited.value == 41
    assert decoded.local.value == 7
    assert value._svtypes_coverage_construction_depth == 0


def test_refresh_does_not_reexecute_user_class_hooks():
    calls = []

    class Mixin:
        def __init_subclass__(cls, **kwargs):
            calls.append(cls)
            super().__init_subclass__(**kwargs)

    class Model(svtypes.SvObject, Mixin):
        value = svtypes.Int()

    assert calls == [Model]
    Model.refresh_declarations()
    assert calls == [Model]

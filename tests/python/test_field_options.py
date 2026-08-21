from dataclasses import FrozenInstanceError

import pytest

from svtypes import (
    Array,
    AssocArray,
    Bit,
    DeclarationError,
    DynArray,
    Enum,
    FieldOptions,
    Int,
    Package,
    Parameter,
    Queue,
    String,
    SvObject,
    SvStruct,
    UnsupportedTypeError,
    schema_descriptor,
)


def test_field_options_are_public_immutable_three_state_metadata():
    field = Int(rand=None, plusarg=False, dump=True, cov=None, intelli=None)
    assert field.field_options == FieldOptions(
        rand=None,
        plusarg=False,
        dump=True,
        cov=None,
        intelli=None,
        pack_bytes=True,
    )
    assert field.rand is True
    assert field.plusarg is False
    with pytest.raises(FrozenInstanceError):
        field.field_options.rand = False


def test_rand_generation_and_supported_default_matrix():
    class RandomPayload(SvObject):
        scalar = Int()
        packed = Bit((2, 8))
        fixed = Array(Int(), 2)
        text = String()

    code = RandomPayload.to_sv_obj()
    assert "rand int scalar;" in code
    assert "rand bit [1:0] [7:0] packed;" in code
    assert "rand int fixed [2];" in code
    assert "\n  string text;" in code
    assert 'virtual function void apply_plusargs(string prefix = "");' in code
    assert '"scalar=%d"' in code
    assert '"packed=%h"' in code
    assert '"text=%s"' in code


def test_explicit_unsupported_rand_is_rejected():
    with pytest.raises(ValueError, match="rand=True is unsupported"):
        class Invalid(SvObject):
            text = String(rand=True)

    with pytest.raises(ValueError, match="plusarg=True is unsupported"):
        class InvalidPlusarg(SvObject):
            values = Array(Int(), 2, plusarg=True)

    with pytest.raises(ValueError, match="cov=True is unsupported"):
        class InvalidCoverage(SvObject):
            text = String(cov=True)


def test_pack_bytes_controls_encoding_fields_and_generated_pack_body():
    class Payload(SvObject):
        sent = Int()
        local = Int(pack_bytes=False)

    descriptor = schema_descriptor(Payload)
    assert [field["name"] for field in descriptor.schema["fields"]] == ["sent", "local"]
    assert len(descriptor.encoding_schema["fields"]) == 1
    assert "name" not in descriptor.encoding_schema["fields"][0]
    code = Payload.to_sv_obj()
    assert "int_packer::pack(sent, bytes);" in code
    assert "int_packer::pack(local, bytes);" not in code


def test_deferred_intelli_is_retained_without_affecting_generation_or_descriptors():
    class Baseline(SvObject):
        _svtypes_unified_type_name = "field_options.IntelliPayload"
        value = Int()

    class Marked(SvObject):
        _svtypes_unified_type_name = "field_options.IntelliPayload"
        value = Int(intelli=True)

    baseline = schema_descriptor(Baseline)
    marked = schema_descriptor(Marked)

    assert Marked._SvObject__svtypes_fields[0][1].intelli is True
    assert baseline.schema["fields"][0] == marked.schema["fields"][0]
    assert baseline.encoding_schema == marked.encoding_schema
    assert baseline.schema_fingerprint == marked.schema_fingerprint
    assert baseline.encoding_fingerprint == marked.encoding_fingerprint
    assert "intelli" not in Marked.to_sv_obj()
    assert "intelli" not in Marked.to_cpp_obj()


def test_generated_coverage_collector_is_nested_and_explicitly_sampled():
    class Covered(SvObject):
        scalar = Int()
        values = DynArray(Int())
        ignored = String()

    code = Covered.to_sv_obj()
    # The collector is a nested class of the generated class, so it can
    # reference the enclosing type and its parameters.
    assert "class Covered__svtypes_coverage;" in code
    assert "covergroup cg with function sample(Covered item);" in code
    assert "scalar_cp: coverpoint item.scalar;" in code
    assert "values_cp: coverpoint item.values.size();" in code
    assert "ignored_cp" not in code
    assert "function void sample(Covered item);" in code
    assert code.index("class Covered__svtypes_coverage;") < code.index("endclass")


def test_template_coverage_collector_references_enclosing_parameters():
    class Templated(SvObject):
        WIDTH = Parameter(Int)
        data = Int()

    code = Templated.to_sv_obj()
    assert "class Templated__svtypes_coverage;" in code
    assert "covergroup cg with function sample(Templated#(.WIDTH(WIDTH)) item);" in code
    assert "data_cp: coverpoint item.data;" in code
    assert "function void sample(Templated#(.WIDTH(WIDTH)) item);" in code
    assert code.index("class Templated__svtypes_coverage;") < code.index("endclass")


def test_no_cov_fields_means_no_coverage_collector():
    class Plain(SvObject):
        scalar = Int(cov=False)
        text = String()

    code = Plain.to_sv_obj()
    assert "__svtypes_coverage" not in code
    assert "covergroup" not in code


def test_coverage_field_type_expressions():
    class Color(Enum, width=8, signed=False):
        RED = 0
        BLUE = 1

    class Child(SvObject):
        x = Bit(4)

    class CoverageKinds(SvObject):
        tone = Color(cov=True)
        lookup = AssocArray(Bit(8), Bit(8), cov=True)
        items = Queue(Int(), cov=True)
        child = Child(cov=True)

    code = CoverageKinds.to_sv_obj()
    assert "tone_cp: coverpoint item.tone;" in code
    assert "lookup_cp: coverpoint item.lookup.num();" in code
    assert "items_cp: coverpoint item.items.size();" in code
    assert "child_cp: coverpoint (item.child == null);" in code
    assert "class CoverageKinds__svtypes_coverage;" in code


def test_specialization_is_not_a_generated_coverage_unit():
    class Templated(SvObject):
        WIDTH = Parameter(Int)
        data = Int()

    # The coverage collector lives on the template (parameterized instance);
    # a specialization is a Python-side binding and generates nothing.
    code = Templated.to_sv_obj()
    assert "class Templated__svtypes_coverage;" in code
    assert "covergroup cg with function sample(Templated#(.WIDTH(WIDTH)) item);" in code
    Spec = Templated.specialize(WIDTH=8)
    with pytest.raises(DeclarationError, match="Python-side binding"):
        Spec.to_sv_obj()


def test_struct_rejects_member_level_rand_and_pack_exclusion():
    with pytest.raises(ValueError, match="rand belongs to the containing field"):
        class InvalidRand(SvStruct):
            value = Int(rand=True)

    with pytest.raises(ValueError, match="pack_bytes=False is illegal"):
        class InvalidPack(SvStruct):
            value = Int(pack_bytes=False)

    with pytest.raises(ValueError, match="plusarg belongs to the containing field"):
        class InvalidPlusarg(SvStruct):
            value = Int(plusarg=True)

    with pytest.raises(ValueError, match="cov belongs to the containing field"):
        class InvalidCoverage(SvStruct):
            value = Int(cov=True)


def test_element_templates_and_scope_variables_reject_explicit_field_policies():
    with pytest.raises(DeclarationError, match="element template"):
        Queue(Int(rand=True))

    package = Package("invalid_policy_scope")
    with pytest.raises(DeclarationError, match="package/scope variable"):
        package.value = Int(cov=True)


def test_struct_field_owns_plusarg_policy_and_nested_objects_are_cycle_guarded():
    class Header(SvStruct):
        code = Int()

    class Payload(SvObject):
        header = Header(plusarg=True)

    generated = Payload.to_sv_obj()
    assert '"header.code=%d"' in generated
    assert "begin_plusarg_object(__svtypes_object_number)" in generated
    assert "end_plusarg_object()" in generated


def test_struct_rejects_nonpacked_members_and_inheritance():
    with pytest.raises(UnsupportedTypeError, match="not a portable packed value"):
        class DynamicMember(SvStruct):
            text = String()

    class Base(SvStruct):
        value = Int()

    with pytest.raises(UnsupportedTypeError, match="inheritance is not supported"):
        class Derived(Base):
            other = Int()


def test_python_dump_is_ordered_selective_and_cycle_safe():
    from svtypes import Object, get_package, svobj

    package = get_package("dump_policy")
    package.clear()

    @svobj(registry=package)
    class Node(SvObject):
        visible = Int()
        hidden = Int(dump=False)
        next = Object("Node", registry=package)

    node = Node()
    node.visible.value = 3
    node.hidden.value = 99
    node.next = node
    rendered = node.svtypes_sprint()
    assert rendered.startswith(f"Node#{node.svtypes_object_number}{{visible=3, next=")
    assert f"<ref#{node.svtypes_object_number}>" in rendered
    assert "hidden" not in rendered

    cpp = Node.to_cpp_obj()
    assert "std::string svtypes_sprint() const override" in cpp
    assert "visible=" in cpp
    assert "hidden=" not in cpp

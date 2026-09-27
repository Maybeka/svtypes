from __future__ import annotations

import pytest

from svtypes import Int, Parameter, SvObject
from svtypes.errors import DeclarationError
from svtypes.parameter_expr import ParameterExpr


def _depth(width, banks):
    return width * banks + 1


def _quad(width):
    return width * 4


def test_parameter_expr_parses_without_executing_and_evaluates_explicit_environment() -> None:
    expr = ParameterExpr.parse(_depth)
    assert expr.parameters == ("width", "banks")
    assert expr.render() == "((width * banks) + 1)"
    assert expr.evaluate({"width": 8, "banks": 4}) == 33


def test_parameter_expr_uses_sv_style_truncating_division_and_conditional_rendering() -> None:
    expr = ParameterExpr.parse(lambda width: width / -3 if width > 0 else 1)
    assert expr.render() == "((width > 0) ? (width / (-3)) : 1)"
    assert expr.evaluate({"width": 8}) == -2
    assert expr.evaluate({"width": 0}) == 1
    predicate = ParameterExpr.parse(lambda width: 1 if width > 0 and not width == 3 else 0)
    assert predicate.render() == "(((width > 0) && (!(width == 3))) ? 1 : 0)"


def test_parameter_expr_rejects_calls_and_free_names() -> None:
    with pytest.raises(DeclarationError, match="unsupported syntax Call"):
        ParameterExpr.parse(lambda width: abs(width))
    with pytest.raises(DeclarationError, match="not a parameter-expression argument"):
        ParameterExpr.parse(lambda width: width + OUTER)
    with pytest.raises(DeclarationError, match="exactly match referenced"):
        ParameterExpr.parse(lambda width, unused: width)


def test_parameter_expression_default_renders_from_ast_in_sv_and_cpp_templates() -> None:
    class Template(SvObject):
        width = Parameter[Int]()
        depth = Parameter[Int](_quad)

    expression = Template.depth.expression
    assert expression is not None
    assert expression.render() == "(width * 4)"
    assert "parameter int depth = (width * 4)" in Template.to_sv_obj()
    assert "int32_t depth = (width * 4)" in Template.to_cpp_obj()

    with pytest.raises(DeclarationError, match="unknown parameter"):
        class BadTemplate(SvObject):
            width = Parameter[Int]()
            depth = Parameter[Int](lambda banks: banks * 4)

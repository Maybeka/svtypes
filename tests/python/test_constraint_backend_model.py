from __future__ import annotations

import pytest

from svtypes.constraint.backend.model import SolveRequest, SolveResult
from svtypes.constraint.ir import BOOL, Expr


def test_solve_request_and_result_are_backend_neutral_runtime_values():
    state = {"limit": 7}
    request = SolveRequest((), ("value",), state, {})
    state["limit"] = 8
    assert request.random_paths == ("value",)
    assert request.state == {"limit": 7}
    with pytest.raises(TypeError):
        request.state["limit"] = 9

    sat = SolveResult.sat({"value": 3})
    assert sat.is_sat is True
    assert sat.assignments == {"value": 3}
    with pytest.raises(TypeError):
        sat.assignments["value"] = 4

    unsat = SolveResult.unsat()
    assert unsat.is_sat is False
    assert unsat.assignments is None


def test_solve_request_snapshots_backend_assumptions():
    assumption = Expr("bool", (True,), BOOL)
    request = SolveRequest((), (), {}, {}, assumptions=[assumption])
    assert request.assumptions == (assumption,)

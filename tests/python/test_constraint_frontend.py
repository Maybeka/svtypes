from __future__ import annotations

import pytest

from svtypes import (
    Array,
    Bit,
    ConstraintNameError,
    ConstraintSyntaxError,
    ConstraintTypeError,
    ConstraintUnsupportedError,
    DeclarationError,
    Enum,
    Logic,
    String,
    SvObject,
    SvStruct,
    constraint,
)


class FrontMode(Enum, width=8, signed=False):
    READ = 0
    WRITE = 1
    IDLE = 2


class FrontHeader(SvStruct):
    addr = Bit(16)
    extra = Bit(8)


def test_frontend_rejects_assignment_return_elif_and_empty_body():
    with pytest.raises(ConstraintSyntaxError, match="assignment"):
        class BadAssign(SvObject):
            addr = Bit(8)

            @constraint
            def legal(self):
                self.addr = 1

    with pytest.raises(ConstraintSyntaxError, match="Return"):
        class BadReturn(SvObject):
            addr = Bit(8)

            @constraint
            def legal(self):
                return self.addr == 1

    with pytest.raises(ConstraintSyntaxError, match="elif"):
        class BadElif(SvObject):
            addr = Bit(8)
            flag = Bit(1)

            @constraint
            def legal(self):
                if self.flag:
                    self.addr == 1
                elif self.addr:
                    self.addr == 2

    with pytest.raises(ConstraintSyntaxError, match="empty"):
        class BadEmpty(SvObject):
            addr = Bit(8)

            @constraint
            def legal(self):
                """doc only"""


def test_frontend_rejects_lambda_fstring_part_select_and_extra_decorator():
    with pytest.raises(ConstraintSyntaxError, match="lambda"):
        class BadLambda(SvObject):
            addr = Bit(8)

            @constraint
            def legal(self):
                self.addr == (lambda: 1)

    with pytest.raises(ConstraintSyntaxError, match="f-string"):
        class BadFString(SvObject):
            addr = Bit(8)

            @constraint
            def legal(self):
                f"{self.addr}"

    with pytest.raises(ConstraintUnsupportedError, match="part-select"):
        class BadSlice(SvObject):
            addr = Bit(8)

            @constraint
            def legal(self):
                self.addr[3:0] == 0

    def deco(fn):
        return fn

    with pytest.raises(ConstraintSyntaxError, match="decorator stack"):
        class BadStack(SvObject):
            addr = Bit(8)

            @constraint
            @deco
            def legal(self):
                self.addr == 1


def test_frontend_rejects_python_calls_pass_is_and_extra_args():
    with pytest.raises(ConstraintSyntaxError, match="function calls"):
        class BadCall(SvObject):
            addr = Bit(8)

            @constraint
            def legal(self):
                abs(self.addr) == 1

    with pytest.raises(ConstraintSyntaxError, match="pass"):
        class BadPass(SvObject):
            addr = Bit(8)

            @constraint
            def legal(self):
                self.addr == 1
                pass

    with pytest.raises(ConstraintSyntaxError, match="is / is not"):
        class BadIs(SvObject):
            addr = Bit(8)

            @constraint
            def legal(self):
                self.addr is None

    with pytest.raises(ConstraintSyntaxError, match="exactly one parameter"):
        class BadArgs(SvObject):
            addr = Bit(8)

            @constraint
            def legal(self, extra=1):
                self.addr == 1


def test_frontend_rejects_range_step_and_for_else():
    with pytest.raises(ConstraintUnsupportedError, match="step"):
        class BadStep(SvObject):
            words = Array(Bit(8), 4)

            @constraint
            def legal(self):
                for i in range(0, 4, 2):
                    self.words[i] != 0

    with pytest.raises(ConstraintSyntaxError, match="for-else"):
        class BadForElse(SvObject):
            words = Array(Bit(8), 2)

            @constraint
            def legal(self):
                for i in range(2):
                    self.words[i] != 0
                else:
                    self.words[0] == 1


def test_frontend_legal_chained_boolean_membership_loop_ternary_and_paths():
    class Legal(SvObject):
        addr = Bit(8)
        length = Bit(8)
        burst = Bit(1)
        mode = FrontMode()
        header = FrontHeader(rand=True)
        words = Array(Bit(8), 3)
        flag = Bit(8)

        @constraint
        def legal(self):
            0 <= self.addr < 16
            self.length == 1 or self.length == 2
            not (self.burst == 1 and self.length == 0)
            self.mode in (FrontMode.READ, FrontMode.WRITE)
            self.mode not in (FrontMode.IDLE,)
            self.header.addr % 4 == 0
            for i in range(3):
                self.words[i] != 0xFF
            self.flag == (1 if self.burst else 2)
            if self.burst:
                self.length != 0
            else:
                self.length == 1

    obj = Legal()
    assert obj.randomize() is True
    sv = Legal.to_sv_obj()
    assert "(mode inside {READ, WRITE})" in sv
    assert "!(mode inside {IDLE})" in sv
    assert "8'hff" in sv
    assert "if (" in sv
    assert "} else {" in sv
    assert 0 <= obj.addr.value < 16
    assert obj.length.value in (1, 2)
    assert int(obj.mode.value) in (0, 1)
    assert int(obj.mode.value) != 2
    assert obj.header.addr.value % 4 == 0
    assert all(word.value != 0xFF for word in obj.words)
    assert obj.flag.value == (1 if obj.burst.value else 2)
    if obj.burst.value:
        assert obj.length.value != 0
    else:
        assert obj.length.value == 1


def test_frontend_rejects_string_and_object_handle_paths():
    with pytest.raises(ConstraintTypeError):
        class BadString(SvObject):
            label = String()

            @constraint
            def legal(self):
                self.label == 1

    class Inner(SvObject):
        addr = Bit(8)

    with pytest.raises(ConstraintNameError, match="cannot access"):
        class Outer(SvObject):
            child = Inner()

            @constraint
            def legal(self):
                self.child.addr == 1


def test_constraint_on_svstruct_is_rejected():
    with pytest.raises(DeclarationError, match="SvStruct"):
        class BadStruct(SvStruct):
            addr = Bit(8)

            @constraint
            def legal(self):
                self.addr == 1

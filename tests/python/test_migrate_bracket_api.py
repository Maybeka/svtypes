from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


_TOOL = Path(__file__).resolve().parents[2] / "tools" / "migrate_bracket_api.py"
_SPEC = importlib.util.spec_from_file_location("migrate_bracket_api", _TOOL)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)


def test_migration_tool_rewrites_nested_unambiguous_type_specs(tmp_path: Path) -> None:
    source = tmp_path / "model.py"
    source.write_text(
        """from svtypes import Array, Bit, DynArray, Int, Object, Parameter

payload = Array(Bit(8), 4, rand=True)
children = DynArray(Object(\"Child\"))
width = Parameter(Int)(8)
limit = Parameter()(4)
""",
        encoding="utf-8",
    )
    rendered, findings, replacements = _MODULE.transform(source)
    assert findings == []
    assert replacements == 4
    assert "payload = Array[Bit[8], 4](rand=True)" in rendered
    assert "children = DynArray[Object[\"Child\"]]()" in rendered
    assert "width = Parameter[Int](8)" in rendered
    assert "limit = Parameter[Int](4)" in rendered


def test_migration_tool_reports_ambiguous_legacy_positional_arguments(tmp_path: Path) -> None:
    source = tmp_path / "model.py"
    source.write_text("value = Bit(8, 1, True, 'h')\n", encoding="utf-8")
    _, findings, replacements = _MODULE.transform(source)
    assert replacements == 0
    assert len(findings) == 1
    assert "positional signed" in findings[0].message


def test_normalizer_removes_zero_argument_descriptors_inside_collection_specs(tmp_path: Path) -> None:
    source = tmp_path / "model.py"
    source.write_text(
        "payload = Array[Bit[8](), Queue[Int()]()]()\n",
        encoding="utf-8",
    )
    rendered, replacements = _MODULE.normalize_nested_specs(source)
    assert replacements == 2
    assert rendered == "payload = Array[Bit[8], Queue[Int]]()\n"


def test_migration_tool_expands_source_directories_deterministically(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    source = nested / "model.py"
    source.write_text("value = Bit(8)\n", encoding="utf-8")
    ignored = nested / "notes.txt"
    ignored.write_text("not Python", encoding="utf-8")

    assert _MODULE._source_paths([tmp_path]) == [source]


def test_migration_tool_reports_single_bit_literals_as_ambiguous(tmp_path: Path) -> None:
    """``Bit(1)`` is a legacy width and a current single-bit value.

    Rewriting it to ``Bit[1]()`` would silently turn value 1 into value 0, so
    the tool must report it instead of patching.
    """

    source = tmp_path / "model.py"
    source.write_text(
        "from svtypes import Bit, Logic, Reg\n"
        "a = Bit(0)\n"
        "b = Bit(1)\n"
        "c = Logic(1, rand=True)\n"
        "d = Reg(0)\n"
        "e = Bit(8)\n",
        encoding="utf-8",
    )
    rendered, findings, replacements = _MODULE.transform(source)

    assert replacements == 1
    assert [finding.message for finding in findings] == [
        "Bit(0) is ambiguous: a legacy width or a single-bit value; "
        "confirm the intended meaning before migrating",
        "Bit(1) is ambiguous: a legacy width or a single-bit value; "
        "confirm the intended meaning before migrating",
        "Logic(1) is ambiguous: a legacy width or a single-bit value; "
        "confirm the intended meaning before migrating",
        "Reg(0) is ambiguous: a legacy width or a single-bit value; "
        "confirm the intended meaning before migrating",
    ]
    assert "a = Bit(0)" in rendered
    assert "b = Bit(1)" in rendered
    assert "c = Logic(1, rand=True)" in rendered
    assert "d = Reg(0)" in rendered
    assert "e = Bit[8]()" in rendered


def test_migration_tool_leaves_the_single_bit_shorthand_runnable(tmp_path: Path) -> None:
    """A width-less call has no legacy width, so the tool leaves it alone.

    The unchanged ``Bit()``/``Logic()``/``Reg()`` spelling must stay valid at
    runtime, otherwise the tool would recommend code that cannot execute.
    """

    source = tmp_path / "model.py"
    source.write_text(
        "from svtypes import Bit, Logic, Reg\n"
        "one = Bit()\n"
        "flag = Logic()\n"
        "historical = Reg()\n",
        encoding="utf-8",
    )
    rendered, findings, replacements = _MODULE.transform(source)

    assert replacements == 0
    assert [finding.message for finding in findings] == [
        "Bit() has no legacy width; leave unchanged",
        "Logic() has no legacy width; leave unchanged",
        "Reg() has no legacy width; leave unchanged",
    ]

    namespace: dict = {}
    exec(compile(rendered, str(source), "exec"), namespace)
    assert namespace["one"].width == 1
    assert namespace["flag"].width == 1
    assert namespace["historical"].width == 1
    assert namespace["historical"].sv_declaration_style == "reg"

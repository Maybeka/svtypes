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

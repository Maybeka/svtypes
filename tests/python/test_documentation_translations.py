"""Guard the declared Chinese/English documentation pairs against drift."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS = REPO_ROOT / "docs"


def test_declared_document_translation_pairs_are_current() -> None:
    manifest = json.loads((DOCS / "translation_manifest.json").read_text(encoding="utf-8"))
    assert manifest["pairs"]
    for pair in manifest["pairs"]:
        english = DOCS / pair["english"]
        chinese = DOCS / pair["chinese"]
        assert english.is_file()
        assert chinese.is_file()
        digest = hashlib.sha256(english.read_bytes()).hexdigest()
        assert digest == pair["source_sha256"], (
            f"{english.name} changed: update {chinese.name} and its source_sha256 together"
        )
        chinese_text = chinese.read_text(encoding="utf-8")
        assert "中文正本" in chinese_text
        assert english.name in chinese_text

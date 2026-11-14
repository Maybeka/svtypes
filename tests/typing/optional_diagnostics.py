"""Public opt-in scopes and generation metadata targets."""

from pathlib import Path

from svtypes import (
    ExternalValueBinding, GenerationResult, Int, MemoryExternalFieldStorage,
    SvObject, bind_external_value, checked_project, generate,
)


def configure(root: Path, output: Path, model: type[SvObject]) -> GenerationResult:
    with checked_project(root):
        binding: ExternalValueBinding = bind_external_value(
            Int(), MemoryExternalFieldStorage(), "value", diagnostics="strict",
        )
        binding.close()
    return generate(model, output, targets={"sv", "schema", "coverage"})

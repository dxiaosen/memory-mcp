import ast
from pathlib import Path

_MEMORY_ROOT = Path(__file__).parents[2] / "src" / "agent_lab" / "memory"
_FORBIDDEN_MODULE_PARTS = {"investment", "research_question"}
_FORBIDDEN_CORE_TYPE_CONSTANTS = {
    "hypothesis",
    "validation_condition",
    "risk",
    "catalyst",
    "time_horizon",
    "open_question",
}


def test_memory_core_does_not_import_formal_scenarios() -> None:
    imported_modules: set[str] = set()
    for path in _MEMORY_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)

    assert not any(
        part in module
        for module in imported_modules
        for part in _FORBIDDEN_MODULE_PARTS
    )


def test_memory_core_does_not_define_formal_scenario_type_constants() -> None:
    string_constants: set[str] = set()
    for path in _MEMORY_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        string_constants.update(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        )

    assert _FORBIDDEN_CORE_TYPE_CONSTANTS.isdisjoint(string_constants)

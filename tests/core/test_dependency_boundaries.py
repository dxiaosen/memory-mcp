import ast
from pathlib import Path

_MEMORY_ROOT = Path(__file__).parents[2] / "src" / "memory_mcp" / "core"
_CORE_LAYER_ROOTS = (
    _MEMORY_ROOT / "domain",
    _MEMORY_ROOT / "application",
    _MEMORY_ROOT / "ports",
)
_FORBIDDEN_MODULE_PARTS = {"investment", "research_question"}
_FORBIDDEN_CORE_DEPENDENCIES = {
    "httpx",
    "mcp",
    "starlette",
    "uvicorn",
    "memory_mcp.extraction",
    "memory_mcp.server",
}
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


def test_domain_application_and_ports_do_not_depend_on_transport_or_infrastructure() -> (
    None
):
    imported_modules: set[str] = set()
    for root in _CORE_LAYER_ROOTS:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_modules.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported_modules.add(node.module)

    assert not any(
        module == forbidden or module.startswith(f"{forbidden}.")
        for module in imported_modules
        for forbidden in _FORBIDDEN_CORE_DEPENDENCIES
    )


def test_infrastructure_adapters_do_not_import_server_composition() -> None:
    imported_modules: set[str] = set()
    adapters_root = _MEMORY_ROOT / "adapters"
    for path in adapters_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)

    assert not any(
        module == "memory_mcp.server" or module.startswith("memory_mcp.server.")
        for module in imported_modules
    )

import ast
from pathlib import Path

_MEMORY_ROOT = Path(__file__).parents[2] / "server" / "src" / "memory_mcp" / "core"
_CORE_LAYER_ROOTS = (
    _MEMORY_ROOT / "domain",
    _MEMORY_ROOT / "application",
    _MEMORY_ROOT / "ports",
)
_FORBIDDEN_MODULE_PARTS = {"investment", "research_question"}
# Third-party transport/infrastructure that Core must never import.
_FORBIDDEN_CORE_DEPENDENCIES = {
    "httpx",
    "mcp",
    "starlette",
    "uvicorn",
    "memory_mcp.extraction",
}
_FORBIDDEN_CORE_TYPE_CONSTANTS = {
    "evidence_claim",
    "hypothesis",
    "ongoing_research",
    "research_decision",
    "research_preference",
    "research_question",
    "thesis",
    "validation_condition",
    "risk",
    "catalyst",
    "time_horizon",
    "open_question",
}


def _collect_imports(*roots: Path) -> set[str]:
    """收集给定目录树下所有 .py 的导入模块名，用于依赖边界断言。"""

    imported_modules: set[str] = set()
    for path in _iter_python_files(*roots):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)
    return imported_modules


def _iter_python_files(*roots: Path):
    """遍历目录树下的 .py 文件，目录不存在时立即失败而非静默通过。"""

    for root in roots:
        if not root.exists():
            raise AssertionError(
                f"expected source root not found: {root}; "
                "test path is stale — update _MEMORY_ROOT"
            )
        yield from root.rglob("*.py")


def test_memory_core_does_not_import_formal_profiles() -> None:
    imported_modules = _collect_imports(_MEMORY_ROOT)

    assert not any(
        part in module
        for module in imported_modules
        for part in _FORBIDDEN_MODULE_PARTS
    )


def test_memory_core_does_not_define_formal_profile_type_constants() -> None:
    string_constants: set[str] = set()
    for path in _iter_python_files(_MEMORY_ROOT):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        string_constants.update(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        )

    assert _FORBIDDEN_CORE_TYPE_CONSTANTS.isdisjoint(string_constants)


def _is_transport_or_root_leak(module: str) -> bool:
    """Core 可依赖 ``memory_mcp.core.*``；其余第三方传输依赖或回引根包均违规。"""

    if any(
        module == forbidden or module.startswith(f"{forbidden}.")
        for forbidden in _FORBIDDEN_CORE_DEPENDENCIES
    ):
        return True
    if module == "memory_mcp" or module.startswith("memory_mcp."):
        return not (module == "memory_mcp.core" or module.startswith("memory_mcp.core."))
    return False


def test_domain_application_and_ports_do_not_depend_on_transport_or_infrastructure() -> (
    None
):
    imported_modules = _collect_imports(*_CORE_LAYER_ROOTS)

    assert not any(_is_transport_or_root_leak(module) for module in imported_modules)


def test_infrastructure_adapters_do_not_import_server_composition() -> None:
    """适配器可依赖 ``memory_mcp.core``，但不得回引根包的组合根、认证、
    设置、工具或 schema 等传输/基础设施边界。"""

    imported_modules = _collect_imports(_MEMORY_ROOT / "adapters")

    assert not any(_is_transport_or_root_leak(module) for module in imported_modules)

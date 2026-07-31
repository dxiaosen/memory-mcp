"""保护顶层包的轻量导入边界。"""

import ast
from pathlib import Path
from sys import stdlib_module_names

_ROOT = Path(__file__).parents[1]


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.partition(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.partition(".")[0])
    return roots


def test_root_package_does_not_eagerly_import_feature_modules() -> None:
    package_init = _ROOT / "server" / "src" / "memory_mcp" / "__init__.py"
    tree = ast.parse(
        package_init.read_text(encoding="utf-8"),
        filename=str(package_init),
    )

    eager_imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]

    assert eager_imports == []


def test_namespace_packages_do_not_eagerly_import_runtime_modules() -> None:
    source_root = _ROOT / "server" / "src" / "memory_mcp"
    for relative_path in (Path("core/adapters/__init__.py"),):
        package_init = source_root / relative_path
        tree = ast.parse(
            package_init.read_text(encoding="utf-8"),
            filename=str(package_init),
        )

        eager_imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        assert eager_imports == [], relative_path


def test_server_source_does_not_depend_on_agent_distribution() -> None:
    source_root = _ROOT / "server" / "src" / "memory_mcp"

    for path in source_root.rglob("*.py"):
        assert "memory_mcp_agent" not in _import_roots(path), path


def test_agent_source_has_only_lightweight_runtime_dependencies() -> None:
    source_root = _ROOT / "agent" / "src" / "memory_mcp_agent"
    allowed_external_imports = {"httpx", "pydantic", "pydantic_settings"}

    for path in source_root.rglob("*.py"):
        external_imports = (
            _import_roots(path) - stdlib_module_names - {"memory_mcp_agent"}
        )
        assert external_imports <= allowed_external_imports, (
            path,
            external_imports,
        )

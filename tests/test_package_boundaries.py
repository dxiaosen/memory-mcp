"""保护顶层包的轻量导入边界。"""

import ast
from pathlib import Path


def test_root_package_does_not_eagerly_import_feature_modules() -> None:
    package_init = Path(__file__).parents[1] / "src" / "memory_mcp" / "__init__.py"
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
    source_root = Path(__file__).parents[1] / "src" / "memory_mcp"
    for relative_path in (
        Path("server/__init__.py"),
        Path("core/adapters/__init__.py"),
    ):
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

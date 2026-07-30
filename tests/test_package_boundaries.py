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

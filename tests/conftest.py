"""共享 pytest 配置。

不定义 autouse fixture；只提供按目录自动标记与 marker 注册。
测试辅助代码在 tests/support/，生产代码不得导入本目录。
"""

from pathlib import Path

import pytest

# 目录 → 层级 marker 映射。新增测试目录时在此登记。
_LAYER_BY_DIR = {
    "unit": "unit",
    "contract": "contract",
    "integration": "integration",
    "end_to_end": "integration",
    "evaluation": "evaluation",
}


def pytest_configure(config: pytest.Config) -> None:
    """注册测试层级 marker，使 -m unit/contract/integration/evaluation 可用。"""

    for marker in ("unit", "contract", "integration", "evaluation"):
        config.addinivalue_line(
            "markers",
            f"{marker}: mark test as a {marker}-level test (see docs/testing.md)",
        )


def pytest_collection_modifyitems(
    items: list[pytest.Item],
) -> None:
    """按测试文件所在目录自动标记层级，无需逐个 @pytest.mark。"""

    for item in items:
        parts = Path(item.fspath).parts
        # 取 tests/ 下的第一级子目录
        if "tests" in parts:
            idx = parts.index("tests")
            if idx + 1 < len(parts):
                layer_dir = parts[idx + 1]
                marker = _LAYER_BY_DIR.get(layer_dir)
                if marker:
                    item.add_marker(marker)

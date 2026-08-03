"""默认敏感内容检测和脱敏适配器。"""

import re
from dataclasses import dataclass

from memory_mcp.core.ports import SensitiveInspection


@dataclass(frozen=True, slots=True)
class SensitiveRule:
    """一条可配置的禁止内容正则规则。"""

    category: str
    pattern: re.Pattern[str]


DEFAULT_SENSITIVE_RULES = (
    SensitiveRule(
        "credential",
        re.compile(
            r"(?:password|passwd|api[_\-\s]?key|access[_\-\s]?token|"
            r"secret|密码|口令)\s*(?:是|为|[:：=])\s*"
            r"[^\s,，;；。.!！?？]+",
            re.IGNORECASE,
        ),
    ),
    SensitiveRule(
        "account_secret",
        re.compile(
            r"(?:account|账户|账号)\s*(?:是|为|[:：=])\s*"
            r"[A-Za-z0-9._@-]{4,}",
            re.IGNORECASE,
        ),
    ),
    SensitiveRule(
        "real_holding",
        re.compile(
            r"(?:(?:真实)?(?:持仓|仓位|持有)\s*(?:是|为|[:：=])?\s*"
            r"(?:\d+(?:\.\d+)?%?|\d+\s*(?:股|份))|"
            r"\b(?:holding|position)\s*(?:is|:|=)\s*\d+(?:\.\d+)?%?)",
            re.IGNORECASE,
        ),
    ),
    SensitiveRule(
        "transaction_instruction",
        re.compile(
            r"(?:(?:请|马上|现在|替我|帮我)?\s*"
            r"(?:买入|卖出|下单|申购|赎回)\s*[^。.!！?\n]+|"
            r"\b(?:buy|sell|place\s+an?\s+order)\s+[^.!?\n]+)",
            re.IGNORECASE,
        ),
    ),
)


class RegexSensitiveContentGuard:
    """在模型调用和持久化之前移除配置的禁止内容。"""

    def __init__(
        self,
        rules: tuple[SensitiveRule, ...] = DEFAULT_SENSITIVE_RULES,
    ) -> None:
        self._rules = rules

    @classmethod
    def from_config(
        cls,
        configured: list[dict[str, str]] | None,
    ) -> RegexSensitiveContentGuard:
        """从服务端配置构造；未配置时回退默认规则。"""

        if not configured:
            return cls()
        rules = tuple(
            SensitiveRule(
                category=item["category"],
                pattern=re.compile(item["pattern"]),
            )
            for item in configured
        )
        return cls(rules=rules or DEFAULT_SENSITIVE_RULES)

    def inspect(self, text: str) -> SensitiveInspection:
        """按规则顺序脱敏，命中片段替换为 ``[REDACTED:<category>]``。"""

        redacted = text
        matched_categories: list[str] = []
        for rule in self._rules:
            redacted, match_count = rule.pattern.subn(
                f"[REDACTED:{rule.category}]",
                redacted,
            )
            if match_count and rule.category not in matched_categories:
                matched_categories.append(rule.category)
        return SensitiveInspection(
            redacted_text=redacted,
            categories=tuple(matched_categories),
        )

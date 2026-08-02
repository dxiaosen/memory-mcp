"""评测数据、隔离边界和安全输出的最小回归集。"""

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.metrics import _recall_labels
from evals.runner import _live_predictions, _run_payload, _validate_output_path, main
from evals.schema import (
    INVESTMENT_MEMORY_TYPES,
    INVESTMENT_RELATION_TYPES,
    CandidateCase,
    RecallCase,
    RecallCorpusItem,
    RelationCase,
    load_dataset,
)

_DATASET = Path("evals/cases.json")


def test_recall_benchmark_preserves_production_empty_result() -> None:
    case = RecallCase(
        id="recall-unrelated-empty",
        category="semantic-recall",
        task="recall",
        query="量子生物学实验进展",
        top_k=1,
        corpus=(
            RecallCorpusItem(
                label="report-format",
                subject="公司深度报告格式",
                memory_type="research_preference",
                content="先列关键风险，再给核心结论。",
            ),
        ),
        expected=frozenset(),
    )

    assert _recall_labels(case) == frozenset()


def test_offline_benchmark_is_honest_deterministic_and_safe() -> None:
    dataset = load_dataset(_DATASET)

    payload = _run_payload(dataset, dataset_path=_DATASET, live_model=False)
    rendered = json.dumps(payload, ensure_ascii=False)

    assert payload["candidate"] is None
    assert payload["relation"] is None
    assert payload["recall_at_k"] == 1.0
    assert payload["safety_pass_rate"] == 1.0
    assert payload["thresholds_met"] is True
    assert payload["failed_case_ids"] == ()
    assert payload["categories"]["durable-research-context"] == {
        "case_count": 9,
        "evaluated_count": 0,
        "failed_count": 0,
        "pass_rate": None,
    }
    assert payload["categories"]["semantic-recall"]["pass_rate"] == 1.0
    assert payload["run"]["model_tasks"] == []
    assert payload["run"]["deterministic_tasks"] == ["recall", "safety"]
    assert "以后写公司深度报告时" not in rendered
    assert "research-pass-2026" not in rendered


def test_dataset_contract_covers_investment_dimensions_and_rejects_identity(
    tmp_path: Path,
) -> None:
    dataset = load_dataset(_DATASET)
    candidate_types = {
        label
        for case in dataset.cases
        if isinstance(case, CandidateCase)
        for label in case.expected
    }
    relation_types = {
        label.split("|", maxsplit=1)[0]
        for case in dataset.cases
        if isinstance(case, RelationCase)
        for label in case.expected
    }
    assert candidate_types == INVESTMENT_MEMORY_TYPES
    assert relation_types == INVESTMENT_RELATION_TYPES

    raw = json.loads(_DATASET.read_text(encoding="utf-8"))
    raw["cases"][0]["owner_id"] = "forged-owner"
    target = tmp_path / "invalid.json"
    target.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_dataset(target)


def test_output_contract_rejects_missing_parent_and_writes_safe_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="parent directory"):
        _validate_output_path(tmp_path / "missing" / "report.json")

    output = tmp_path / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["evals.runner", "--dataset", str(_DATASET), "--output", str(output)],
    )
    assert main() == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["run"]["mode"] == "offline"
    assert payload["candidate"] is None
    assert payload["relation"] is None


def test_live_model_requires_existing_model_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for name in (
        "MEMORY_MCP_MODEL_NAME",
        "MEMORY_MCP_MODEL_API_KEY",
        "MEMORY_MCP_MODEL_PROVIDER",
        "MEMORY_MCP_MODEL_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="MEMORY_MCP_MODEL_NAME"):
        _live_predictions(load_dataset(Path(__file__).parents[2] / _DATASET))

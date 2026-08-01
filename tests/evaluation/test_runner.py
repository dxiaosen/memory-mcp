import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.metrics import evaluate_dataset
from evals.runner import _live_predictions, run_evaluation
from evals.schema import CandidateCase, RelationCase, load_dataset

_DATASET = Path("evals/cases.json")


def test_offline_evaluation_meets_thresholds_without_live_predictor() -> None:
    dataset = load_dataset(_DATASET)

    def forbidden_live_predictor(_dataset):
        raise AssertionError("offline evaluation must not create a model client")

    report = run_evaluation(
        dataset,
        live_model=False,
        live_predictor=forbidden_live_predictor,
    )

    assert report.thresholds_met is True
    assert report.failed_case_ids == ()
    assert report.case_counts == {
        "candidate": 6,
        "relation": 4,
        "recall": 3,
        "safety": 4,
    }


def test_false_positive_reduces_candidate_precision() -> None:
    dataset = load_dataset(_DATASET)
    negative = next(
        case
        for case in dataset.cases
        if isinstance(case, CandidateCase) and not case.expected
    )

    report = evaluate_dataset(
        dataset,
        model_predictions={negative.id: frozenset({"preference"})},
    )

    assert report.candidate.false_positive == 1
    assert report.candidate.precision < 1.0
    assert report.thresholds_met is False
    assert negative.id in report.failed_case_ids


def test_live_mode_uses_explicit_predictor_and_label_sets() -> None:
    dataset = load_dataset(_DATASET)
    called = False

    def predictor(current_dataset):
        nonlocal called
        called = True
        return {
            case.id: case.baseline
            for case in current_dataset.cases
            if isinstance(case, CandidateCase | RelationCase)
        }

    report = run_evaluation(
        dataset,
        live_model=True,
        live_predictor=predictor,
    )

    assert called is True
    assert report.thresholds_met is True


@pytest.mark.parametrize(
    "mutation",
    (
        {"task": "unknown"},
        {"owner_id": "forged-owner"},
        {"token": "forged-token"},
    ),
)
def test_invalid_case_contract_fails_before_evaluation(
    tmp_path: Path,
    mutation: dict[str, str],
) -> None:
    payload = json.loads(_DATASET.read_text(encoding="utf-8"))
    payload["cases"][0].update(mutation)
    target = tmp_path / "invalid.json"
    target.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_dataset(target)


def test_duplicate_case_id_is_rejected(tmp_path: Path) -> None:
    payload = json.loads(_DATASET.read_text(encoding="utf-8"))
    payload["cases"][1]["id"] = payload["cases"][0]["id"]
    target = tmp_path / "duplicate.json"
    target.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError, match="case ids"):
        load_dataset(target)


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

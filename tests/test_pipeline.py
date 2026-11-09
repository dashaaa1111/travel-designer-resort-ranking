"""Проверки итоговых данных (раздел 16 ТЗ) и сверка с контрольным результатом."""

import json
import math

import pytest

from resort_ranking.pipeline import PROJECT_ROOT, build_model

CONTROL = PROJECT_ROOT / "data" / "processed" / "model_data.json"
SCORE_KEYS = [
    "region_trips", "region_growth", "avg_stay", "pistes", "lifts", "lodging", "amenities",
    "snow_days", "freezing_days", "snowfall", "airport", "transport", "price",
]


@pytest.fixture(scope="module")
def model():
    return build_model()


def test_weights_sum_to_100(model):
    assert sum(model["weights"].values()) == pytest.approx(100.0, abs=0.001)
    assert set(model["weights"]) == set(SCORE_KEYS)


def test_32_unique_resorts(model):
    ids = [r["resort_id"] for r in model["model_rows"]]
    assert len(ids) == 32 and len(set(ids)) == 32


def test_no_missing_trips_2025(model):
    assert all(r["trips_2025"] for r in model["model_rows"])


def test_scores_in_range(model):
    for row in model["model_rows"]:
        for key in SCORE_KEYS:
            assert 0.0 <= row[f"{key}_score"] <= 100.0, (row["resort_id"], key)
        assert 0.0 <= row["popularity_score_default"] <= 100.0
        assert 0.0 <= row["confidence_score"] <= 100.0


def test_no_nan_or_inf(model):
    for row in model["model_rows"]:
        for key, value in row.items():
            if isinstance(value, float):
                assert math.isfinite(value), (row["resort_id"], key)


def test_ranks_start_at_1_and_follow_score(model):
    rows = model["model_rows"]
    assert rows[0]["rank_default"] == 1
    scores = [r["popularity_score_default"] for r in rows]
    assert scores == sorted(scores, reverse=True)


def test_deterministic():
    assert build_model() == build_model()


@pytest.mark.skipif(not CONTROL.exists(), reason="нет контрольного файла")
def test_matches_control_result(model):
    control = json.loads(CONTROL.read_text(encoding="utf-8"))["model_rows"]
    control_by_id = {r["resort_id"]: r for r in control}
    for row in model["model_rows"]:
        ref = control_by_id[row["resort_id"]]
        assert abs(row["popularity_score_default"] - ref["popularity_score_default"]) <= 0.2
        assert row["rank_default"] == ref["rank_default"]

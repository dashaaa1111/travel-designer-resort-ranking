"""Тесты шага 6: изменение весов, Монте-Карло, интервалы, группы."""

import random
from pathlib import Path

import pytest

from resort_ranking.scoring import assign_ranks
from resort_ranking.stability import (
    COMPONENTS, assign_groups, check_components, dirichlet_weights, drop_metric,
    leave_one_out, mark_distinct_neighbours, percentile, price_bounds, rank_intervals,
    ranks_with, reweight_component, run_stability, simulate_ranks, spearman,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW = PROJECT_ROOT / "data" / "raw"
HAS_DATA = (RAW / "resort_open_data_v2.json").exists() and (RAW / "prices_manual_2026.csv").exists()

WEIGHTS = {
    "region_trips": 25, "region_growth": 10, "avg_stay": 5, "pistes": 10, "lifts": 7,
    "lodging": 5, "amenities": 3, "snow_days": 8, "freezing_days": 6, "snowfall": 6,
    "airport": 8, "transport": 2, "price": 5,
}


def _row(rid, base, price=1000.0, status="operating"):
    """Курорт, у которого все 13 баллов равны base."""
    row = {key + "_score": float(base) for key in WEIGHTS}
    row.update(resort_id=rid, name_ru=rid, region="X", operating_status=status,
               adult_skipass_avg_rub=price, popularity_score_default=float(base),
               confidence_score=100.0, quality_flags="")
    return row


def _rows():
    rows = [_row("R1", 90), _row("R2", 60), _row("R3", 59, price=None), _row("R4", 10, status="closed")]
    for row, rank in zip(rows, assign_ranks([r["popularity_score_default"] for r in rows])):
        row["rank_default"] = rank
    return rows


# ---------- веса ----------

def test_components_cover_every_weight_once():
    check_components(WEIGHTS)
    with pytest.raises(ValueError):
        check_components({**WEIGHTS, "extra": 1})


@pytest.mark.parametrize("component", list(COMPONENTS))
def test_reweight_component_keeps_sum_and_scales_component(component):
    new = reweight_component(WEIGHTS, component, 1.2)
    assert sum(new.values()) == pytest.approx(100.0, abs=0.001)
    before = sum(WEIGHTS[k] for k in COMPONENTS[component])
    after = sum(new[k] for k in COMPONENTS[component])
    assert after == pytest.approx(before * 1.2)
    inside = COMPONENTS[component][0]
    assert new[inside] / WEIGHTS[inside] == pytest.approx(1.2)   # внутри компонента пропорции те же


def test_drop_metric_zeroes_metric_and_keeps_sum():
    new = drop_metric(WEIGHTS, "region_trips")
    assert new["region_trips"] == 0
    assert sum(new.values()) == pytest.approx(100.0)
    assert new["pistes"] / new["lifts"] == pytest.approx(10 / 7)


def test_dirichlet_weights_sum_to_100_and_are_reproducible():
    a = dirichlet_weights(WEIGHTS, random.Random(1), 200)
    b = dirichlet_weights(WEIGHTS, random.Random(1), 200)
    assert a == b
    assert sum(a.values()) == pytest.approx(100.0)
    assert all(v >= 0 for v in a.values())


def test_dirichlet_mean_is_close_to_base_weights():
    rng = random.Random(7)
    draws = [dirichlet_weights(WEIGHTS, rng, 200) for _ in range(2000)]
    mean_trips = sum(d["region_trips"] for d in draws) / len(draws)
    assert mean_trips == pytest.approx(25, abs=0.5)


# ---------- места и симуляции ----------

def test_ranks_with_base_weights_match_pipeline_rank():
    rows = _rows()
    assert ranks_with(rows, WEIGHTS) == [r["rank_default"] for r in rows]


def test_price_override_changes_only_missing_price_resort():
    rows = _rows()
    assert ranks_with(rows, WEIGHTS, {"R3": 100.0})[2] == 2          # дешёвая цена поднимает R3 выше R2


def test_simulation_is_reproducible_and_collapses_without_noise():
    rows = _rows()
    a = simulate_ranks(rows, WEIGHTS, n=50, seed=3, concentration=200, price_range=(0, 100))
    b = simulate_ranks(rows, WEIGHTS, n=50, seed=3, concentration=200, price_range=(0, 100))
    assert a == b
    calm = simulate_ranks(rows, WEIGHTS, n=20, seed=3, concentration=1e7, price_range=(50, 50))
    assert all(sim == [1, 2, 3, 4] for sim in calm)


def test_percentile_nearest_rank():
    values = list(range(1, 101))
    assert percentile(values, 5) == 5
    assert percentile(values, 50) == 50
    assert percentile(values, 95) == 95
    assert percentile([7], 5) == 7


def test_intervals_contain_base_rank_and_probabilities_are_valid():
    rows = _rows()
    sims = simulate_ranks(rows, WEIGHTS, n=200, seed=1, concentration=200, price_range=(0, 100))
    intervals = rank_intervals(rows, sims, low=5, high=95, top_k=(1, 2))
    for item in intervals:
        assert item["rank_p05"] <= item["rank_median"] <= item["rank_p95"]
        assert 0 <= item["p_top1"] <= item["p_top2"] <= 1
    assert intervals[0]["p_top1"] == 1.0                                  # R1 далеко впереди


# ---------- группы ----------

RULES = [{"group": "A", "label": "a", "top_k": 1, "min_probability": 0.8},
         {"group": "B", "label": "b", "top_k": 2, "min_probability": 0.5}]
DEFAULT = {"group": "D", "label": "d"}
CLOSED = {"group": "X", "label": "x"}


def test_groups_first_matching_rule_and_closed_resorts():
    items = [
        {"operating_status": "operating", "p_top1": 0.9, "p_top2": 1.0},
        {"operating_status": "operating", "p_top1": 0.1, "p_top2": 0.6},
        {"operating_status": "operating", "p_top1": 0.0, "p_top2": 0.1},
        {"operating_status": "closed", "p_top1": 1.0, "p_top2": 1.0},
    ]
    assert [g["group"] for g in assign_groups(items, RULES, DEFAULT, CLOSED)] == ["A", "B", "D", "X"]


def test_distinct_neighbours():
    items = [{"resort_id": "a", "rank_default": 1, "lo": 1, "hi": 1},
             {"resort_id": "b", "rank_default": 2, "lo": 2, "hi": 4},
             {"resort_id": "c", "rank_default": 3, "lo": 3, "hi": 5}]
    marked = mark_distinct_neighbours(items, "lo", "hi")
    assert [m["distinct_from_next"] for m in marked] == [True, False, None]


# ---------- сравнение порядков ----------

def test_spearman():
    assert spearman([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 1, 2], [1, 1, 2]) == pytest.approx(1.0)


def test_leave_one_out_has_every_metric():
    table = leave_one_out(_rows(), WEIGHTS)
    assert {t["metric"] for t in table} == set(WEIGHTS)
    assert all(-1 <= t["spearman"] <= 1 for t in table)


def test_price_bounds_only_for_missing_price():
    bounds = price_bounds(_rows(), WEIGHTS)
    assert [b["resort_id"] for b in bounds] == ["R3"]
    assert bounds[0]["rank_if_cheap"] <= bounds[0]["rank_default"] <= bounds[0]["rank_if_expensive"]


# ---------- на реальных файлах ----------

@pytest.mark.skipif(not HAS_DATA, reason="нет нового сбора или ручных цен")
def test_real_data_stability_tables():
    result = run_stability(PROJECT_ROOT, n_simulations=300)
    intervals = result["intervals"]
    assert len(intervals) == 32 and len({i["resort_id"] for i in intervals}) == 32
    for item in intervals:
        assert 1 <= item["rank_p05"] <= item["rank_default"] <= item["rank_p95"] <= 32
        assert item["group"] in {"A", "B", "C", "D", "X"}
        if item["operating_status"] != "operating":
            assert item["group"] == "X"
    assert len(result["component_summary"]) == 5
    assert len(result["leave_one_out"]) == 13


@pytest.mark.skipif(not HAS_DATA, reason="нет нового сбора или ручных цен")
def test_real_data_is_reproducible():
    a = run_stability(PROJECT_ROOT, n_simulations=100)["intervals"]
    b = run_stability(PROJECT_ROOT, n_simulations=100)["intervals"]
    assert a == b

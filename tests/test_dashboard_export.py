"""Тесты экспорта для дашборда: даты, компоненты, источники и четыре таблицы на реальных данных."""

from pathlib import Path

import pytest

from resort_ranking.dashboard_export import (
    METRICS, build_dashboard, component_values, price_attempts, price_provenance, raw_value,
    scenarios_long, to_iso_date,
)
from resort_ranking.stability import COMPONENTS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW = PROJECT_ROOT / "data" / "raw"
HAS_DATA = (RAW / "resort_open_data_v2.json").exists() and (RAW / "prices_manual_2026.csv").exists()

WEIGHTS = {
    "region_trips": 25, "region_growth": 10, "avg_stay": 5, "pistes": 10, "lifts": 7,
    "lodging": 5, "amenities": 3, "snow_days": 8, "freezing_days": 6, "snowfall": 6,
    "airport": 8, "transport": 2, "price": 5,
}


# ---------- даты ----------

@pytest.mark.parametrize("value, expected", [
    (46279, "2026-09-14"),                       # серийный номер Excel
    (46279.51, "2026-09-14"),
    ("27.10.2026", "2026-10-27"),
    ("2026-10-26T18:18:39.560917+00:00", "2026-10-26"),
    ("2026-09-14T12:13:58.823Z", "2026-09-14"),
    ("2026-10-15", "2026-10-15"),
    ("", ""), (None, ""), ("не дата", ""),
])
def test_to_iso_date(value, expected):
    assert to_iso_date(value) == expected


# ---------- компоненты и сырые значения ----------

def test_metrics_cover_every_weight():
    assert set(METRICS) == set(WEIGHTS)


def test_component_contributions_sum_to_index():
    row = {k + "_score": float(i * 7 % 100) for i, k in enumerate(WEIGHTS)}
    total = sum(row[k + "_score"] * w for k, w in WEIGHTS.items()) / 100
    comp = component_values(row, WEIGHTS)
    assert sum(comp[f"{c}_contribution"] for c in COMPONENTS) == pytest.approx(total, abs=0.01)
    for c in COMPONENTS:
        assert 0 <= comp[f"{c}_score"] <= 100


def test_component_score_of_equal_scores_is_that_score():
    row = {k + "_score": 40.0 for k in WEIGHTS}
    comp = component_values(row, WEIGHTS)
    assert all(comp[f"{c}_score"] == 40.0 for c in COMPONENTS)


def test_raw_value_amenities_and_missing():
    row = {"food_objects_6km": 3, "attraction_objects_6km": None, "adult_skipass_avg_rub": None}
    assert raw_value(row, "amenities") == 3.0
    assert raw_value(row, "price") is None


# ---------- происхождение цены ----------

def test_price_provenance_combines_files_and_takes_latest_date():
    rows = [
        {"resort_id": "R1", "adult_price_min_rub": 1000, "adult_price_max_rub": 1200, "month": "2026-01",
         "source_url": "https://a", "checked_at": 46279, "source_file": "prices_monthly"},
        {"resort_id": "R1", "adult_price_min_rub": 1500, "adult_price_max_rub": None, "month": "2025/26",
         "source_url": "https://b", "checked_at": "01.11.2026", "source_file": "prices_manual_2026"},
        {"resort_id": "R2", "adult_price_min_rub": None, "adult_price_max_rub": None, "month": "2026-01"},
    ]
    result = price_provenance(rows)
    assert set(result) == {"R1"}                                  # строка без цены не считается источником
    assert result["R1"]["source_id"] == "PRICES_ARCHIVE;PRICES_MANUAL_2026"
    assert result["R1"]["collected_at"] == "2026-11-01"
    assert result["R1"]["source_url"] == "https://a"


def test_price_attempts_explain_missing_price(tmp_path):
    path = tmp_path / "p.csv"
    path.write_text(
        "resort_id;season;pricing_model;source_url;checked_at\n"
        "R1;2025/26;per_ride;https://x ;27.10.2026\n", encoding="utf-8-sig")
    item = price_attempts(path)["R1"]
    assert item["source_url"] == "https://x" and item["collected_at"] == "2026-10-27"
    assert "per_ride" in item["note"]
    assert price_attempts(tmp_path / "нет.csv") == {}


def test_scenarios_long_shift_sign():
    def model(ranks):
        return {"model_rows": [{"resort_id": rid, "name_ru": rid, "rank_default": r, "rank_operating": r,
                                "popularity_score_default": 50.0, "confidence_score": 100.0}
                               for rid, r in ranks.items()]}
    table = scenarios_long({"baseline": model({"A": 1, "B": 2}), "other": model({"A": 2, "B": 1})}, "other")
    shift = {(r["scenario"], r["resort_id"]): r["shift_vs_baseline"] for r in table}
    assert shift[("other", "B")] == 1 and shift[("other", "A")] == -1   # >0: поднялся
    assert shift[("baseline", "A")] == 0
    assert all(r["is_primary"] == (r["scenario"] == "other") for r in table)


# ---------- на реальных файлах ----------

@pytest.fixture(scope="module")
def tables():
    if not HAS_DATA:
        pytest.skip("нет нового сбора или ручных цен")
    return build_dashboard(PROJECT_ROOT)


def test_table_sizes_and_keys(tables):
    resorts = tables["dashboard_resorts"]
    assert len(resorts) == 32 and len({r["resort_id"] for r in resorts}) == 32
    assert len(tables["dashboard_metrics_long"]) == 32 * len(METRICS)
    assert len(tables["dashboard_scenarios_long"]) % 32 == 0
    ids = {r["resort_id"] for r in resorts}
    assert {r["resort_id"] for r in tables["dashboard_metrics_long"]} == ids
    assert {r["resort_id"] for r in tables["dashboard_scenarios_long"]} == ids


def test_components_sum_to_popularity_score(tables):
    for r in tables["dashboard_resorts"]:
        total = sum(r[f"{c}_contribution"] for c in COMPONENTS)
        assert total == pytest.approx(r["popularity_score"], abs=0.02), r["resort_id"]


def test_long_contributions_match_index(tables):
    by_resort = {}
    for row in tables["dashboard_metrics_long"]:
        by_resort[row["resort_id"]] = by_resort.get(row["resort_id"], 0) + row["contribution"]
    for r in tables["dashboard_resorts"]:
        assert by_resort[r["resort_id"]] == pytest.approx(r["popularity_score"], abs=0.02)


def test_every_measured_value_has_source_and_date(tables):
    for row in tables["dashboard_metrics_long"]:
        assert 0 <= row["score"] <= 100
        if row["imputed"]:
            assert row["note"] and row["score"] == 50.0, (row["resort_id"], row["metric"])
        else:
            assert row["source_id"] and row["collected_at"], (row["resort_id"], row["metric"])


def test_sources_are_known_and_dated(tables):
    known = {s["source_id"]: s for s in tables["sources"]}
    for row in tables["dashboard_metrics_long"]:
        for sid in filter(None, row["source_id"].split(";")):
            assert sid in known, sid
    for s in tables["sources"]:
        if s["used_in_primary"]:
            assert s["collected_at"], s["source_id"]


def test_groups_and_flags_are_consistent(tables):
    for r in tables["dashboard_resorts"]:
        assert r["rank_p05"] <= r["rank"] <= r["rank_p95"]
        assert r["flag_missing_price"] == (not r["has_price"])
        if r["operating_status"] != "operating":
            assert r["group"] == "X" and r["flag_not_operating"]


def test_primary_scenario_matches_ranks(tables):
    primary = {r["resort_id"]: r["rank"] for r in tables["dashboard_scenarios_long"] if r["is_primary"]}
    assert primary == {r["resort_id"]: r["rank"] for r in tables["dashboard_resorts"]}

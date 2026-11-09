"""Тесты сценариев: baseline = контрольный результат ТЗ, подключение нового сбора и ручных цен."""

import csv
import json

import pytest

from resort_ranking.overlays import apply_collected, apply_operating_status, load_manual_prices
from resort_ranking.pipeline import PROJECT_ROOT, available_scenarios, build_model, compare_scenarios

CONTROL = PROJECT_ROOT / "data" / "processed" / "model_data.json"
RAW = PROJECT_ROOT / "data" / "raw"
OVERRIDES = PROJECT_ROOT / "data" / "reference" / "coordinate_overrides.csv"
HAS_V2 = (RAW / "resort_open_data_v2.json").exists()
HAS_PRICES = (RAW / "prices_manual_2026.csv").exists()


# ---------- baseline == контроль ТЗ ----------

@pytest.fixture(scope="module")
def baseline():
    return build_model(scenario="baseline")


@pytest.mark.skipif(not CONTROL.exists(), reason="нет контрольного файла")
def test_baseline_reproduces_control_scores_ranks_and_confidence(baseline):
    control = {r["resort_id"]: r for r in json.loads(CONTROL.read_text(encoding="utf-8"))["model_rows"]}
    for row in baseline["model_rows"]:
        ref = control[row["resort_id"]]
        assert abs(row["popularity_score_default"] - ref["popularity_score_default"]) < 1e-9
        assert row["rank_default"] == ref["rank_default"]
        assert abs(row["confidence_score"] - ref["confidence_score"]) < 1e-9, row["resort_id"]
        assert abs(row["completeness_pct"] - ref["completeness_pct"]) < 1e-9, row["resort_id"]


def test_flags_use_tz_names_and_missing_price_is_visible(baseline):
    all_flags = " ".join(r["quality_flags"] for r in baseline["model_rows"])
    assert "OSM radii overlap" not in all_flags
    for row in baseline["model_rows"]:
        if row["adult_skipass_avg_rub"] is None:
            assert "missing price" in row["quality_flags"], row["resort_id"]
        else:
            assert "missing price" not in row["quality_flags"], row["resort_id"]


@pytest.mark.skipif(not OVERRIDES.exists(), reason="нет справочника исправлений")
def test_rank_operating_skips_closed_resorts(baseline):
    rows = baseline["model_rows"]
    closed = [r for r in rows if r["operating_status"] != "operating"]
    working = [r for r in rows if r["operating_status"] == "operating"]
    assert all(r["rank_operating"] is None for r in closed)
    assert [r["rank_operating"] for r in working] == list(range(1, len(working) + 1))


# ---------- ручные цены ----------

HEADER = ["resort_id", "resort_name", "season", "tariff_product", "day_type", "adult_price_min_rub",
          "adult_price_max_rub", "pricing_model", "comment"]


def write_prices(path, rows, delimiter=";"):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=delimiter)
        writer.writerow(HEADER)
        writer.writerows(rows)


def test_manual_prices_filter_models_peak_and_duplicates(tmp_path):
    path = tmp_path / "p.csv"
    write_prices(path, [
        ["R1", "A", "2025/26", "день", "будни", "2000", "2500", "day_pass", ""],
        ["R1", "A", "2025/26", "день", "будни", "2000", "2500", "day_pass", ""],              # дубль
        ["R1", "A", "2025/26", "день", "новогодние праздники", "3600", "3600", "day_pass", ""],  # пик
        ["R2", "B", "2025/26", "3 часа", "", "4 230", "4 700", "hourly", ""],
        ["R3", "C", "2025/26", "", "", "", "", "per_ride", "оплата за подъём"],
    ])
    only_day = load_manual_prices(path, ["day_pass"])
    assert [(r["resort_id"], r["adult_price_min_rub"]) for r in only_day] == [("R1", 2000.0)]
    with_hourly = load_manual_prices(path, ["day_pass", "hourly"])
    assert {r["resort_id"] for r in with_hourly} == {"R1", "R2"}
    assert [r for r in with_hourly if r["resort_id"] == "R2"][0]["adult_price_min_rub"] == 4230.0   # пробел в числе
    with_peak = load_manual_prices(path, ["day_pass"], exclude_peak=False)
    assert len(with_peak) == 2


def test_manual_prices_accept_comma_delimiter_and_open_ended_price(tmp_path):
    path = tmp_path / "p.csv"
    write_prices(path, [["R1", "A", "2025/26", "день", "", "1700", "", "day_pass", "от 1700"]], delimiter=",")
    row = load_manual_prices(path, ["day_pass"])[0]
    assert row["adult_price_min_rub"] == 1700.0 and row["adult_price_max_rub"] is None


def test_manual_prices_reject_min_above_max_and_bad_model(tmp_path):
    path = tmp_path / "p.csv"
    write_prices(path, [["R1", "A", "2025/26", "день", "", "4250", "2000", "day_pass", ""]])
    with pytest.raises(ValueError):
        load_manual_prices(path, ["day_pass"])
    write_prices(path, [["R1", "A", "2025/26", "день", "", "100", "200", "day-pass", ""]])
    with pytest.raises(ValueError):
        load_manual_prices(path, ["day_pass"])


# ---------- подключение нового сбора ----------

def test_apply_collected_replaces_fields_and_clears_old_errors():
    base = [{"resort_id": "R1", "latitude": 1.0, "food_objects_6km": 5, "osm_error": "old", "avg_snowfall_cm_per_season": 100}]
    new = {"R1": {"latitude": 2.0, "food_objects_6km": 9, "avg_snowfall_cm_per_season": 50}}
    result = apply_collected(base, new, features=True, weather=False)[0]
    assert result["latitude"] == 2.0 and result["food_objects_6km"] == 9
    assert "osm_error" not in result                      # ошибка старого сбора не переносится
    assert result["avg_snowfall_cm_per_season"] == 100    # погоду не трогали
    assert base[0]["latitude"] == 1.0                     # исходные данные не изменены


def test_apply_collected_requires_every_resort():
    with pytest.raises(KeyError):
        apply_collected([{"resort_id": "R1"}], {}, features=True, weather=False)


def test_operating_status_defaults_to_operating():
    result = apply_operating_status([{"resort_id": "R1"}, {"resort_id": "R2"}],
                                    {"R2": {"operating_status": "closed", "статус_проверки": "не найдена"}})
    assert [r["operating_status"] for r in result] == ["operating", "closed"]


# ---------- сценарии на реальных файлах (если они есть) ----------

@pytest.mark.skipif(not (HAS_V2 and HAS_PRICES), reason="нет нового сбора или ручных цен")
def test_all_scenarios_are_valid_and_comparable():
    _, skipped, runnable = available_scenarios(PROJECT_ROOT)
    assert not skipped
    models = {name: build_model(scenario=name) for name in runnable}
    for model in models.values():
        rows = model["model_rows"]
        assert len(rows) == 32 and len({r["resort_id"] for r in rows}) == 32
        assert rows[0]["rank_default"] == 1
        for row in rows:
            assert 0.0 <= row["popularity_score_default"] <= 100.0
            assert 0.0 <= row["confidence_score"] <= 100.0
    table = compare_scenarios(models)
    assert len(table) == 32 and "shift_verified" in table[0]


@pytest.mark.skipif(not (HAS_V2 and HAS_PRICES), reason="нет нового сбора или ручных цен")
def test_hourly_scenario_has_at_least_as_many_prices_as_verified():
    with_price = lambda m: sum(r["adult_skipass_avg_rub"] is not None for r in m["model_rows"])
    assert with_price(build_model(scenario="verified_hourly")) >= with_price(build_model(scenario="verified"))

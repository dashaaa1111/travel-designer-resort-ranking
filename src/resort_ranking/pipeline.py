"""Запуск расчёта: данные -> признаки -> баллы -> рейтинг -> витрины и сравнение сценариев."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .data_io import (
    load_matrix, load_scenarios, load_weights, parse_rosstat_csv, write_csv, write_json,
)
from .features import attach_prices, build_distributions
from .overlays import (
    apply_collected, apply_operating_status, load_collected, load_manual_prices,
    load_operating_status,
)
from .quality import completeness, quality_flags
from .regions import build_region_metrics
from .scoring import assign_ranks, confidence_score, score_resort, total_score

PROJECT_ROOT = Path(__file__).resolve().parents[2]

RESORT_FIELDS = [
    "operating_status", "coordinate_review_status",
    "latitude", "longitude",
    "piste_segments_6km", "lift_lines_6km", "lodging_objects_6km",
    "food_objects_6km", "attraction_objects_6km", "transport_objects_6km",
    "avg_snowfall_cm_per_season", "avg_freezing_days_per_season",
    "avg_snow_days_per_season", "nearest_airport_distance_km",
    "adult_skipass_avg_rub", "price_months_count", "completeness_pct",
    "coordinate_quality", "quality_flags",
]
REGION_FIELDS = [
    "trips_2022", "trips_2023", "trips_2024", "trips_2025",
    "nights_2023", "nights_2024", "nights_2025",
    "trips_growth_2025", "avg_stay_nights_2025",
]
SCORE_COLUMNS_SUFFIX = ("_score",)
NON_FEATURE_COLUMNS = {"popularity_score_default", "confidence_score", "rank_default", "rank_operating", "scenario"}


def load_price_rows(root: Path, config: dict[str, Any], spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Строки цен сценария: архивные месячные цены + (если включено) ручные цены 2026."""
    raw = root / "data" / "raw"
    prices = [{**row, "source_file": "prices_monthly"} for row in load_matrix(raw / "prices_monthly.json")]
    if spec["manual_prices"]:
        prices = prices + load_manual_prices(
            raw / "prices_manual_2026.csv", spec["price_models"],
            exclude_peak=config.get("exclude_peak_prices", True),
        )
    return prices


def build_model(root: Path = PROJECT_ROOT, scenario: str = "baseline") -> dict[str, Any]:
    """Считает рейтинг для одного сценария и возвращает таблицы. Файлы не пишет."""
    raw = root / "data" / "raw"
    config = load_scenarios(root / "config" / "scenarios.yaml")
    spec = config["scenarios"][scenario]
    weights = load_weights(root / "config" / "weights.yaml")

    resorts = load_matrix(raw / "resorts.json")
    if spec["features"] == "collected_v2" or spec["weather"] == "collected_v2":
        collected = load_collected(raw / "resort_open_data_v2.json")
        resorts = apply_collected(
            resorts, collected,
            features=spec["features"] == "collected_v2",
            weather=spec["weather"] == "collected_v2",
        )
    resorts = apply_operating_status(
        resorts, load_operating_status(root / "data" / "reference" / "coordinate_overrides.csv")
    )

    prices = load_price_rows(root, config, spec)
    trips_rows = parse_rosstat_csv(raw / "rosstat_tourist_trips.csv")
    nights_rows = parse_rosstat_csv(raw / "rosstat_tourist_nights.csv")

    region_metrics = build_region_metrics(resorts, trips_rows, nights_rows)
    resorts = attach_prices(resorts, prices)
    for resort in resorts:                         # полнота и флаги считаются кодом
        resort["completeness_pct"] = completeness(resort)
        has_price = bool(resort["adult_skipass_avg_rub"])
        resort["quality_flags"] = "; ".join(quality_flags(resort, has_price))
    dist = build_distributions(resorts, region_metrics)

    rows: list[dict[str, Any]] = []
    for resort in resorts:
        region = region_metrics[resort["region"]]
        scores = score_resort(resort, region, dist)
        rows.append({
            "scenario": scenario,
            "resort_id": resort["resort_id"],
            "name_ru": resort["name_ru"],
            "region": resort["region"],
            **{key: resort.get(key) for key in RESORT_FIELDS},
            **{key: region.get(key) for key in REGION_FIELDS},
            **scores,
            "popularity_score_default": total_score(scores, weights),
            "confidence_score": confidence_score(resort, spec["confidence"]),
        })

    rows.sort(key=lambda item: item["popularity_score_default"], reverse=True)
    for row, rank in zip(rows, assign_ranks([r["popularity_score_default"] for r in rows])):
        row["rank_default"] = rank

    # место среди работающих курортов: именно этот список подходит для каталога
    operating = [r for r in rows if r["operating_status"] == "operating"]
    for row in rows:
        row["rank_operating"] = None
    for row, rank in zip(operating, assign_ranks([r["popularity_score_default"] for r in operating])):
        row["rank_operating"] = rank

    return {
        "scenario": scenario,
        "weights": weights,
        "regions": list(region_metrics.values()),
        "resorts": resorts,
        "model_rows": rows,
    }


def feature_rows(model_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Витрина resort_features: все признаки без баллов и рейтинга."""
    return [
        {k: v for k, v in row.items() if not k.endswith(SCORE_COLUMNS_SUFFIX) and k not in NON_FEATURE_COLUMNS}
        for row in model_rows
    ]


def compare_scenarios(models: dict[str, dict[str, Any]], baseline: str = "baseline") -> list[dict[str, Any]]:
    """Таблица: по строке на курорт, место и балл в каждом сценарии, сдвиг относительно baseline."""
    by_id = {name: {r["resort_id"]: r for r in m["model_rows"]} for name, m in models.items()}
    base_rows = models[baseline]["model_rows"]
    table = []
    for base in base_rows:
        rid = base["resort_id"]
        row: dict[str, Any] = {
            "resort_id": rid, "name_ru": base["name_ru"], "region": base["region"],
            "operating_status": by_id[baseline][rid]["operating_status"],
        }
        for name in models:
            current = by_id[name][rid]
            row[f"rank_{name}"] = current["rank_default"]
            row[f"score_{name}"] = round(current["popularity_score_default"], 2)
            if name != baseline:
                row[f"shift_{name}"] = base["rank_default"] - current["rank_default"]   # >0: поднялся
        table.append(row)
    return table


def available_scenarios(root: Path) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    """Сценарии, для которых есть все входные файлы, и причины пропуска остальных."""
    config = load_scenarios(root / "config" / "scenarios.yaml")
    raw = root / "data" / "raw"
    runnable, skipped = {}, {}
    for name, spec in config["scenarios"].items():
        missing = []
        if "collected_v2" in (spec["features"], spec["weather"]) and not (raw / "resort_open_data_v2.json").exists():
            missing.append("resort_open_data_v2.json")
        if spec["manual_prices"] and not (raw / "prices_manual_2026.csv").exists():
            missing.append("prices_manual_2026.csv")
        if missing:
            skipped[name] = "нет файлов: " + ", ".join(missing)
        else:
            runnable[name] = spec
    return config, skipped, runnable


def main(root: Path = PROJECT_ROOT, only: str | None = None) -> None:
    config, skipped, runnable = available_scenarios(root)
    names = [only] if only else list(runnable)
    for name in names:
        if name not in runnable:
            raise SystemExit(f"Сценарий {name!r} нельзя запустить: {skipped.get(name, 'нет в config/scenarios.yaml')}")

    models = {name: build_model(root, name) for name in names}
    mart = root / "data" / "mart"
    for name, model in models.items():
        write_csv(mart / "scenarios" / f"resort_scores_{name}.csv", model["model_rows"])
    for name, reason in skipped.items():
        print(f"Сценарий {name} пропущен ({reason})")

    primary = config["primary"] if config["primary"] in models else names[0]
    model = models[primary]
    write_json(mart / "model_data.json", model)
    write_csv(mart / "resort_scores.csv", model["model_rows"])
    write_csv(mart / "resort_features.csv", feature_rows(model["model_rows"]))
    if "baseline" in models and len(models) > 1:
        write_csv(mart / "scenario_comparison.csv", compare_scenarios(models))

    print(f"Основной сценарий: {primary}")
    for row in model["model_rows"][:12]:
        place = row["rank_operating"] if row["rank_operating"] is not None else "-"
        print(f"{row['rank_default']:>2} (каталог {place:>2}). {row['name_ru']:<24} {row['popularity_score_default']:.1f}")

    if "baseline" in models and len(models) > 1:
        base_top10 = {r["resort_id"] for r in models["baseline"]["model_rows"][:10]}
        print("\nСравнение с baseline:")
        for name, other in models.items():
            if name == "baseline":
                continue
            top10 = {r["resort_id"] for r in other["model_rows"][:10]}
            print(f"  {name}: в топ-10 общих с baseline {len(base_top10 & top10)} из 10")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default=None, help="Запустить только один сценарий")
    main(only=parser.parse_args().scenario)

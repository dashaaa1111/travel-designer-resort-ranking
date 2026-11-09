"""Prepare resort popularity prototype inputs from local and Rosstat datasets."""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)


def load_matrix(path: Path) -> list[dict[str, Any]]:
    """Convert a JSON matrix with a header row into records."""
    matrix = json.loads(path.read_text(encoding="utf-8"))
    header = matrix[0]
    return [dict(zip(header, row, strict=True)) for row in matrix[1:]]


def parse_rosstat_csv(path: Path) -> list[dict[str, Any]]:
    """Read a semicolon-delimited Rosstat mirror export."""
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file, delimiter=";")
        rows: list[dict[str, Any]] = []
        for row in reader:
            clean: dict[str, Any] = {"series": row["Ряд"]}
            for key, value in row.items():
                if key == "Ряд":
                    continue
                clean[key] = float(value) if value not in (None, "") else None
            rows.append(clean)
        return rows


def split_series(value: str) -> tuple[str, str]:
    """Split '<region> — <period>' using the final separator."""
    region, period = value.rsplit(" — ", 1)
    return region.strip(), period.strip()


def canonical_region(value: str) -> str:
    """Normalize known differences between resort and Rosstat region labels."""
    normalized = value.replace("–", "-").replace("—", "-").replace("ё", "е")
    normalized = " ".join(normalized.split()).strip()
    aliases = {
        "Кемеровская область - Кузбасс": "Кемеровская область",
        "Республика Северная Осетия - Алания": "Республика Северная Осетия — Алания",
        "Республика Северная Осетия-Алания": "Республика Северная Осетия — Алания",
        "Республика Алтай": "Республика Алтай",
    }
    return aliases.get(normalized, normalized)


def minmax(values: list[float], value: float, *, log: bool = False, inverse: bool = False) -> float:
    """Return a 0-100 min-max score."""
    transformed = [math.log1p(item) if log else item for item in values]
    current = math.log1p(value) if log else value
    low, high = min(transformed), max(transformed)
    score = 50.0 if high == low else 100.0 * (current - low) / (high - low)
    return 100.0 - score if inverse else score


def clipped_growth_score(value: float | None) -> float:
    """Map -25% to 0 and +25% to 100, clipping outside the range."""
    if value is None:
        return 50.0
    return max(0.0, min(100.0, 50.0 + 200.0 * value))


def clipped_stay_score(value: float | None) -> float:
    """Map 1-7 nights per trip to 0-100."""
    if value is None:
        return 50.0
    return max(0.0, min(100.0, (value - 1.0) / 6.0 * 100.0))


resorts = load_matrix(RAW_DIR / "resorts.json")
prices = load_matrix(RAW_DIR / "prices_monthly.json")
trips_rows = parse_rosstat_csv(RAW_DIR / "rosstat_tourist_trips.csv")
nights_rows = parse_rosstat_csv(RAW_DIR / "rosstat_tourist_nights.csv")

regions_needed = {canonical_region(str(row["region"])) for row in resorts}
region_metrics: dict[str, dict[str, Any]] = {
    region: {
        "region": region,
        "trips_2022": None,
        "trips_2023": None,
        "trips_2024": None,
        "trips_2025": None,
        "nights_2023": None,
        "nights_2024": None,
        "nights_2025": None,
    }
    for region in sorted(regions_needed)
}

for row in trips_rows:
    region_raw, period = split_series(str(row["series"]))
    region = canonical_region(region_raw)
    if region not in region_metrics:
        continue
    if period == "январь-декабрь":
        for year in (2023, 2024, 2025):
            region_metrics[region][f"trips_{year}"] = row.get(str(year))
    elif period == "январь-декабрь*":
        region_metrics[region]["trips_2022"] = row.get("2022")

for row in nights_rows:
    region_raw, period = split_series(str(row["series"]))
    region = canonical_region(region_raw)
    if region not in region_metrics or period != "январь-декабрь":
        continue
    for year in (2023, 2024, 2025):
        region_metrics[region][f"nights_{year}"] = row.get(str(year))

for record in region_metrics.values():
    trips_2024 = record["trips_2024"]
    trips_2025 = record["trips_2025"]
    nights_2025 = record["nights_2025"]
    record["trips_growth_2025"] = (
        trips_2025 / trips_2024 - 1.0 if trips_2024 and trips_2025 else None
    )
    record["avg_stay_nights_2025"] = (
        nights_2025 / trips_2025 if nights_2025 and trips_2025 else None
    )

price_values: dict[str, list[float]] = defaultdict(list)
price_months: dict[str, set[str]] = defaultdict(set)
for row in prices:
    if not row.get("resort_id"):
        continue
    for column in ("adult_price_min_rub", "adult_price_max_rub"):
        if row.get(column) is not None:
            price_values[str(row["resort_id"])].append(float(row[column]))
    if row.get("month"):
        price_months[str(row["resort_id"])].add(str(row["month"]))

for resort in resorts:
    resort_id = str(resort["resort_id"])
    vals = price_values.get(resort_id, [])
    resort["adult_skipass_avg_rub"] = sum(vals) / len(vals) if vals else None
    resort["price_months_count"] = len(price_months.get(resort_id, set()))
    resort["region"] = canonical_region(str(resort["region"]))

trips_levels = [float(row["trips_2025"]) for row in region_metrics.values() if row["trips_2025"]]
pistes = [float(row["piste_segments_6km"] or 0) for row in resorts]
lifts = [float(row["lift_lines_6km"] or 0) for row in resorts]
lodging = [float(row["lodging_objects_6km"] or 0) for row in resorts]
amenities = [float((row["food_objects_6km"] or 0) + (row["attraction_objects_6km"] or 0)) for row in resorts]
snow_days = [float(row["avg_snow_days_per_season"] or 0) for row in resorts]
freezing_days = [float(row["avg_freezing_days_per_season"] or 0) for row in resorts]
snowfall = [float(row["avg_snowfall_cm_per_season"] or 0) for row in resorts]
airport_distance = [float(row["nearest_airport_distance_km"] or 0) for row in resorts]
transport = [float(row["transport_objects_6km"] or 0) for row in resorts]
observed_prices = [float(row["adult_skipass_avg_rub"]) for row in resorts if row["adult_skipass_avg_rub"]]

weights = {
    "region_trips": 25.0,
    "region_growth": 10.0,
    "avg_stay": 5.0,
    "pistes": 10.0,
    "lifts": 7.0,
    "lodging": 5.0,
    "amenities": 3.0,
    "snow_days": 8.0,
    "freezing_days": 6.0,
    "snowfall": 6.0,
    "airport": 8.0,
    "transport": 2.0,
    "price": 5.0,
}

model_rows: list[dict[str, Any]] = []
for resort in resorts:
    region = region_metrics[resort["region"]]
    price = resort["adult_skipass_avg_rub"]
    scores = {
        "region_trips_score": minmax(trips_levels, float(region["trips_2025"]), log=True),
        "region_growth_score": clipped_growth_score(region["trips_growth_2025"]),
        "avg_stay_score": clipped_stay_score(region["avg_stay_nights_2025"]),
        "pistes_score": minmax(pistes, float(resort["piste_segments_6km"] or 0), log=True),
        "lifts_score": minmax(lifts, float(resort["lift_lines_6km"] or 0), log=True),
        "lodging_score": minmax(lodging, float(resort["lodging_objects_6km"] or 0), log=True),
        "amenities_score": minmax(amenities, float((resort["food_objects_6km"] or 0) + (resort["attraction_objects_6km"] or 0)), log=True),
        "snow_days_score": minmax(snow_days, float(resort["avg_snow_days_per_season"] or 0)),
        "freezing_days_score": minmax(freezing_days, float(resort["avg_freezing_days_per_season"] or 0)),
        "snowfall_score": minmax(snowfall, float(resort["avg_snowfall_cm_per_season"] or 0), log=True),
        "airport_score": minmax(airport_distance, float(resort["nearest_airport_distance_km"] or 0), log=True, inverse=True),
        "transport_score": minmax(transport, float(resort["transport_objects_6km"] or 0), log=True),
        "price_score": minmax(observed_prices, float(price), log=True, inverse=True) if price else 50.0,
    }
    total = sum(scores[key + "_score"] * weight for key, weight in weights.items()) / 100.0
    confidence = (
        50.0 * float(resort.get("completeness_pct") or 0)
        + 25.0 * (1.0 if price else 0.0)
        + 25.0 * (1.0 if resort.get("coordinate_quality") == "resort_or_complex_page" else 0.0)
    )
    row = {
        "resort_id": resort["resort_id"],
        "name_ru": resort["name_ru"],
        "region": resort["region"],
        **{key: resort.get(key) for key in [
            "piste_segments_6km", "lift_lines_6km", "lodging_objects_6km",
            "food_objects_6km", "attraction_objects_6km", "transport_objects_6km",
            "avg_snowfall_cm_per_season", "avg_freezing_days_per_season",
            "avg_snow_days_per_season", "nearest_airport_distance_km",
            "adult_skipass_avg_rub", "price_months_count", "completeness_pct",
            "coordinate_quality", "quality_flags",
        ]},
        **{key: region.get(key) for key in [
            "trips_2022", "trips_2023", "trips_2024", "trips_2025",
            "nights_2023", "nights_2024", "nights_2025",
            "trips_growth_2025", "avg_stay_nights_2025",
        ]},
        **scores,
        "popularity_score_default": total,
        "confidence_score": confidence,
    }
    model_rows.append(row)

model_rows.sort(key=lambda item: item["popularity_score_default"], reverse=True)
for rank, row in enumerate(model_rows, start=1):
    row["rank_default"] = rank

missing_regions = [region for region, values in region_metrics.items() if not values["trips_2025"]]
if missing_regions:
    raise RuntimeError(f"Missing Rosstat regions: {missing_regions}")

payload = {
    "weights": weights,
    "regions": list(region_metrics.values()),
    "resorts": resorts,
    "model_rows": model_rows,
}
(PROCESSED_DIR / "model_data.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
)

for row in model_rows[:12]:
    print(f"{row['rank_default']:>2}. {row['name_ru']:<24} {row['popularity_score_default']:.1f}")

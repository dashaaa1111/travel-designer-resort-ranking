"""Полнота данных, флаги качества и признак точных координат. Считается кодом, а не берётся из JSON."""

from __future__ import annotations

from typing import Any

# 11 обязательных полей (как в исходном скрипте сбора; по ним рассчитан контрольный completeness_pct)
REQUIRED_FIELDS = [
    "latitude", "longitude", "coordinate_source_url",
    "piste_segments_6km", "lift_lines_6km", "lodging_objects_6km", "food_objects_6km",
    "open_meteo_elevation_m", "winter_mean_temperature_c", "avg_snowfall_cm_per_season",
    "nearest_airport_distance_km",
]

# Какие значения coordinate_quality считаются «точными» для confidence_score
EXACT_COORDINATE_QUALITIES = {
    "tz": {"resort_or_complex_page"},
    "verified": {"resort_or_complex_page", "manually_verified"},
}


def completeness(resort: dict[str, Any]) -> float:
    """Доля заполненных обязательных полей из 11."""
    filled = sum(resort.get(field) not in (None, "") for field in REQUIRED_FIELDS)
    return filled / len(REQUIRED_FIELDS)


def has_exact_coordinates(resort: dict[str, Any], mode: str) -> bool:
    return resort.get("coordinate_quality") in EXACT_COORDINATE_QUALITIES[mode]


def quality_flags(resort: dict[str, Any], has_price: bool) -> list[str]:
    """Флаги из ТЗ (check coordinates, coordinates are proxy, OSM radial overlap,
    source error, missing price) и дополнительный флаг not operating."""
    flags: list[str] = []
    quality = resort.get("coordinate_quality")
    if quality == "manual_review_required":
        flags.append("check coordinates")
    if quality == "nearby_feature_proxy":
        flags.append("coordinates are proxy")
    if resort.get("osm_overlap_warning"):
        flags.append("OSM radial overlap")
    if any(key.endswith("_error") and value for key, value in resort.items()):
        flags.append("source error")
    if not has_price:
        flags.append("missing price")
    if resort.get("operating_status") not in (None, "", "operating"):
        flags.append("not operating")
    return flags

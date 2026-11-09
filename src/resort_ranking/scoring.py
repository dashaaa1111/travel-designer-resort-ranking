"""Формулы баллов, итогового индекса, уверенности и ранга. Без чтения файлов."""

from __future__ import annotations

import math
from typing import Any

from .features import _num, amenities_value
from .quality import has_exact_coordinates


def minmax(values: list[float], value: float, *, log: bool = False, inverse: bool = False) -> float:
    """Балл 0-100 по min-max. log=True берёт log1p, inverse=True переворачивает шкалу."""
    transformed = [math.log1p(item) if log else item for item in values]
    current = math.log1p(value) if log else value
    low, high = min(transformed), max(transformed)
    score = 50.0 if high == low else 100.0 * (current - low) / (high - low)
    score = max(0.0, min(100.0, score))  # защита от погрешности float (100.00000000000001)
    return 100.0 - score if inverse else score


def clipped_growth_score(value: float | None) -> float:
    """-25% -> 0, +25% -> 100, за пределами обрезается. Нет данных -> 50."""
    if value is None:
        return 50.0
    return max(0.0, min(100.0, 50.0 + 200.0 * value))


def clipped_stay_score(value: float | None) -> float:
    """1-7 ночей на поездку -> 0-100. Нет данных -> 50."""
    if value is None:
        return 50.0
    return max(0.0, min(100.0, (value - 1.0) / 6.0 * 100.0))


def score_resort(
    resort: dict[str, Any], region: dict[str, Any], dist: dict[str, list[float]]
) -> dict[str, float]:
    """13 нормированных баллов одного курорта."""
    price = resort["adult_skipass_avg_rub"]
    return {
        "region_trips_score": minmax(dist["trips"], float(region["trips_2025"]), log=True),
        "region_growth_score": clipped_growth_score(region["trips_growth_2025"]),
        "avg_stay_score": clipped_stay_score(region["avg_stay_nights_2025"]),
        "pistes_score": minmax(dist["pistes"], _num(resort, "piste_segments_6km"), log=True),
        "lifts_score": minmax(dist["lifts"], _num(resort, "lift_lines_6km"), log=True),
        "lodging_score": minmax(dist["lodging"], _num(resort, "lodging_objects_6km"), log=True),
        "amenities_score": minmax(dist["amenities"], amenities_value(resort), log=True),
        "snow_days_score": minmax(dist["snow_days"], _num(resort, "avg_snow_days_per_season")),
        "freezing_days_score": minmax(dist["freezing_days"], _num(resort, "avg_freezing_days_per_season")),
        "snowfall_score": minmax(dist["snowfall"], _num(resort, "avg_snowfall_cm_per_season"), log=True),
        "airport_score": minmax(dist["airport"], _num(resort, "nearest_airport_distance_km"), log=True, inverse=True),
        "transport_score": minmax(dist["transport"], _num(resort, "transport_objects_6km"), log=True),
        # нет цены -> нейтральные 50 баллов (допущение исходной методики)
        "price_score": minmax(dist["prices"], float(price), log=True, inverse=True) if price else 50.0,
    }


def total_score(scores: dict[str, float], weights: dict[str, float]) -> float:
    """Взвешенная сумма баллов, деленная на 100."""
    return sum(scores[key + "_score"] * weight for key, weight in weights.items()) / 100.0


def confidence_score(resort: dict[str, Any], mode: str = "tz") -> float:
    """Уверенность 0-100: полнота данных (50) + есть цена (25) + точные координаты (25).

    mode="tz": точные только координаты со страницы курорта (контрольный расчёт ТЗ).
    mode="verified": точными считаются и проверенные вручную (manually_verified).
    """
    has_price = bool(resort["adult_skipass_avg_rub"])
    exact_coordinates = has_exact_coordinates(resort, mode)
    return (
        50.0 * float(resort.get("completeness_pct") or 0)
        + 25.0 * (1.0 if has_price else 0.0)
        + 25.0 * (1.0 if exact_coordinates else 0.0)
    )


def assign_ranks(scores: list[float]) -> list[int]:
    """Место = 1 + число курортов с более высоким баллом (как в ТЗ; при равенстве места одинаковы)."""
    return [1 + sum(1 for other in scores if other > current) for current in scores]

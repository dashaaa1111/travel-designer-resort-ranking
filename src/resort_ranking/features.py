"""Сборка признаков курортов: цены и распределения для нормировки."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .regions import canonical_region


def attach_prices(
    resorts: list[dict[str, Any]], prices: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Добавляет среднюю цену взрослого ски-пасса и число месяцев с ценой.

    Возвращает копии строк, исходные данные не меняются.
    """
    values: dict[str, list[float]] = defaultdict(list)
    months: dict[str, set[str]] = defaultdict(set)
    for row in prices:
        if not row.get("resort_id"):
            continue
        rid = str(row["resort_id"])
        for column in ("adult_price_min_rub", "adult_price_max_rub"):
            if row.get(column) is not None:
                values[rid].append(float(row[column]))
        if row.get("month"):
            months[rid].add(str(row["month"]))

    result = []
    for resort in resorts:
        rid = str(resort["resort_id"])
        vals = values.get(rid, [])
        item = dict(resort)
        item["adult_skipass_avg_rub"] = sum(vals) / len(vals) if vals else None
        item["price_months_count"] = len(months.get(rid, set()))
        item["region"] = canonical_region(str(item["region"]))
        result.append(item)
    return result


def _num(row: dict[str, Any], key: str) -> float:
    """Число из поля; пустое значение считается нулём (как в исходной методике)."""
    return float(row.get(key) or 0)


def amenities_value(resort: dict[str, Any]) -> float:
    """Питание + достопримечательности в радиусе 6 км."""
    return float((resort["food_objects_6km"] or 0) + (resort["attraction_objects_6km"] or 0))


def build_distributions(
    resorts: list[dict[str, Any]], region_metrics: dict[str, dict[str, Any]]
) -> dict[str, list[float]]:
    """Наборы значений, по которым считаются минимум и максимум для нормировки."""
    return {
        "trips": [float(r["trips_2025"]) for r in region_metrics.values() if r["trips_2025"]],
        "pistes": [_num(r, "piste_segments_6km") for r in resorts],
        "lifts": [_num(r, "lift_lines_6km") for r in resorts],
        "lodging": [_num(r, "lodging_objects_6km") for r in resorts],
        "amenities": [amenities_value(r) for r in resorts],
        "snow_days": [_num(r, "avg_snow_days_per_season") for r in resorts],
        "freezing_days": [_num(r, "avg_freezing_days_per_season") for r in resorts],
        "snowfall": [_num(r, "avg_snowfall_cm_per_season") for r in resorts],
        "airport": [_num(r, "nearest_airport_distance_km") for r in resorts],
        "transport": [_num(r, "transport_objects_6km") for r in resorts],
        "prices": [float(r["adult_skipass_avg_rub"]) for r in resorts if r["adult_skipass_avg_rub"]],
    }

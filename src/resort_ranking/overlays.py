"""Подключение нового сбора, статусов работы курортов и ручных цен к базовым данным."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

COLLECTED_FEATURE_FIELDS = [
    "latitude", "longitude", "coordinate_quality", "coordinate_source_url",
    "piste_segments_6km", "lift_lines_6km", "lodging_objects_6km", "food_objects_6km",
    "transport_objects_6km", "attraction_objects_6km", "osm_overlap_warning",
    "nearest_airport_distance_km",
]
COLLECTED_WEATHER_FIELDS = [
    "open_meteo_elevation_m", "winter_mean_temperature_c", "avg_snowfall_cm_per_season",
    "avg_freezing_days_per_season", "avg_snow_days_per_season", "max_daily_snowfall_cm",
]
FEATURE_ERROR_KEYS = ("coordinate_error", "osm_error", "airport_error")
WEATHER_ERROR_KEYS = ("weather_error",)

ALLOWED_PRICE_MODELS = {"day_pass", "hourly", "per_ride", "not_published"}


def load_collected(path: Path) -> dict[str, dict[str, Any]]:
    """Строки нового сбора по resort_id."""
    if not path.exists():
        raise FileNotFoundError(f"Нет файла {path}. Сначала выполните python src/collect_with_overrides.py")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {row["resort_id"]: row for row in payload["resorts"]}


def apply_collected(
    resorts: list[dict[str, Any]],
    collected: dict[str, dict[str, Any]],
    *,
    features: bool,
    weather: bool,
) -> list[dict[str, Any]]:
    """Заменяет поля курортов значениями из нового сбора. Возвращает копии строк."""
    result = []
    for resort in resorts:
        item = dict(resort)
        new = collected[item["resort_id"]]            # KeyError, если курорта нет в новом сборе
        groups = []
        if features:
            groups.append((COLLECTED_FEATURE_FIELDS, FEATURE_ERROR_KEYS))
        if weather:
            groups.append((COLLECTED_WEATHER_FIELDS, WEATHER_ERROR_KEYS))
        for fields, error_keys in groups:
            for key in fields:
                item[key] = new.get(key)
            for key in error_keys:
                if new.get(key):
                    item[key] = new[key]
                else:
                    item.pop(key, None)
        result.append(item)
    return result


def load_operating_status(path: Path) -> dict[str, dict[str, str]]:
    """Статус работы и статус проверки координат из справочника исправлений."""
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as file:
        return {row["resort_id"]: row for row in csv.DictReader(file)}


def apply_operating_status(
    resorts: list[dict[str, Any]], status: dict[str, dict[str, str]]
) -> list[dict[str, Any]]:
    result = []
    for resort in resorts:
        item = dict(resort)
        row = status.get(item["resort_id"], {})
        item["operating_status"] = row.get("operating_status") or "operating"
        item["coordinate_review_status"] = row.get("статус_проверки", "")
        result.append(item)
    return result


def _number(value: str | None) -> float | None:
    text = (value or "").strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
    return float(text) if text else None


def load_manual_prices(
    path: Path, price_models: list[str], exclude_peak: bool = True
) -> list[dict[str, Any]]:
    """Ручные цены 2026: берёт строки нужных pricing_model, убирает пиковые и дубли.

    Возвращает строки в том же виде, что prices_monthly.json (adult_price_min_rub / max / month).
    """
    if not path.exists():
        raise FileNotFoundError(f"Нет файла с ручными ценами: {path}")
    text = path.read_text(encoding="utf-8-sig")
    first_line = text.splitlines()[0] if text else ""
    delimiter = ";" if first_line.count(";") > first_line.count(",") else ","
    rows = list(csv.DictReader(text.splitlines(), delimiter=delimiter))

    result: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for raw in rows:
        row = {(key or "").strip(): (value or "").strip() for key, value in raw.items()}
        model = row.get("pricing_model", "")
        if model not in ALLOWED_PRICE_MODELS:
            raise ValueError(f"{row.get('resort_id')}: неверный pricing_model {model!r}")
        low, high = _number(row.get("adult_price_min_rub")), _number(row.get("adult_price_max_rub"))
        if model not in price_models or (low is None and high is None):
            continue
        if low is not None and high is not None and low > high:
            raise ValueError(f"{row['resort_id']}: минимальная цена {low} больше максимальной {high}")
        if exclude_peak and "новогод" in row.get("day_type", "").lower():
            continue
        key = (row["resort_id"], row.get("tariff_product"), row.get("day_type"), low, high)
        if key in seen:                       # полностью одинаковые строки (как у Игоры) считаем один раз
            continue
        seen.add(key)
        result.append({
            "resort_id": row["resort_id"],
            "adult_price_min_rub": low,
            "adult_price_max_rub": high,
            "month": row.get("season") or "season",
            "pricing_model": model,
            "source_url": row.get("source_url", ""),      # для дашборда: откуда взята цена
            "checked_at": row.get("checked_at", ""),
            "source_file": "prices_manual_2026",
        })
    return result

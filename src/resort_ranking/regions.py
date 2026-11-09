"""Региональные показатели: поездки и ночёвки из выгрузок Росстата."""

from __future__ import annotations

from typing import Any


def split_series(value: str) -> tuple[str, str]:
    """'<регион> — <период>' -> (регион, период), делим по последнему разделителю."""
    region, period = value.rsplit(" — ", 1)
    return region.strip(), period.strip()


def canonical_region(value: str) -> str:
    """Приводит названия регионов из справочника курортов и Росстата к одному виду."""
    normalized = value.replace("–", "-").replace("—", "-").replace("ё", "е")
    normalized = " ".join(normalized.split()).strip()
    aliases = {
        "Кемеровская область - Кузбасс": "Кемеровская область",
        "Республика Северная Осетия - Алания": "Республика Северная Осетия — Алания",
        "Республика Северная Осетия-Алания": "Республика Северная Осетия — Алания",
        "Республика Алтай": "Республика Алтай",
    }
    return aliases.get(normalized, normalized)


def build_region_metrics(
    resorts: list[dict[str, Any]],
    trips_rows: list[dict[str, Any]],
    nights_rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Поездки, ночёвки, рост и длительность пребывания по каждому региону курортов."""
    regions_needed = {canonical_region(str(row["region"])) for row in resorts}
    metrics: dict[str, dict[str, Any]] = {
        region: {
            "region": region,
            "trips_2022": None, "trips_2023": None, "trips_2024": None, "trips_2025": None,
            "nights_2023": None, "nights_2024": None, "nights_2025": None,
        }
        for region in sorted(regions_needed)
    }

    for row in trips_rows:
        region_raw, period = split_series(str(row["series"]))
        region = canonical_region(region_raw)
        if region not in metrics:
            continue
        if period == "январь-декабрь":
            for year in (2023, 2024, 2025):
                metrics[region][f"trips_{year}"] = row.get(str(year))
        elif period == "январь-декабрь*":  # 2022 в выгрузке помечен звёздочкой
            metrics[region]["trips_2022"] = row.get("2022")

    for row in nights_rows:
        region_raw, period = split_series(str(row["series"]))
        region = canonical_region(region_raw)
        if region not in metrics or period != "январь-декабрь":
            continue
        for year in (2023, 2024, 2025):
            metrics[region][f"nights_{year}"] = row.get(str(year))

    for record in metrics.values():
        t24, t25, n25 = record["trips_2024"], record["trips_2025"], record["nights_2025"]
        record["trips_growth_2025"] = t25 / t24 - 1.0 if t24 and t25 else None
        record["avg_stay_nights_2025"] = n25 / t25 if n25 and t25 else None

    missing = [name for name, values in metrics.items() if not values["trips_2025"]]
    if missing:
        raise RuntimeError(f"Нет данных Росстата по регионам: {missing}")
    return metrics

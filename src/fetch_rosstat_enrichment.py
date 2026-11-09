#!/usr/bin/env python3
"""Collect comparable tourism indicators for the resort regions from Roschart/Rosstat."""

from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
MODEL_PATH = PROCESSED_DIR / "model_data.json"
OUTPUT_PATH = PROCESSED_DIR / "rosstat_enrichment.json"
API = "https://roschart.ru/api/dataset/{}?format=json"


DATASETS = {
    "ksr_persons": "sr-mes-sub-07-2026-chislennost-razmeschennyh-lic-v-kollektiv",
    "ksr_revenue": "sr-mes-sub-07-2026-dohody-kollektivnyh-sredstv-razmescheniya",
    "tourism_gva": "vds-ti-okved2-vrp-valovaya-dobavlennaya-stoimost-turistskoy",
    "tourism_grp_share": "vds-ti-okved2-vrp-dolya-valovoy-dobavlennoy-stoimosti-turist",
    "hotel_services_dec": "plat-122025-osnovnye-pokazateli-platnyh-uslug-naseleniyu-za-3",
    "tour_operator_services_dec": "plat-122025-osnovnye-pokazateli-platnyh-uslug-naseleniyu-za-15",
    "recreation_bases": "sr-god-sub-2025-bazy-otdyha1-2025",
    "campings": "sr-god-sub-2025-kempingi1-2025",
    "modular": "sr-god-sub-2025-modulnye-nekapitalnye-sredstva-razmescheniya",
    "other_accommodation": "sr-god-sub-2025-inye-sredstva-razmescheniya1-2025",
}


ALIASES = {
    "кабардино балкарская республика": "Кабардино-Балкарская Республика",
    "карачаево черкесская республика": "Карачаево-Черкесская Республика",
    "республика северная осетия алания": "Республика Северная Осетия — Алания",
    "республика северная осетия": "Республика Северная Осетия — Алания",
    "республика северная осетия — алания": "Республика Северная Осетия — Алания",
    "кемеровская область кузбасс": "Кемеровская область",
}


def norm(value: str) -> str:
    value = value.lower().replace("ё", "е")
    value = re.sub(r"[¹²³⁴⁵⁶⁷⁸⁹⁰*]+", "", value)
    value = value.replace("–", "—").replace("-", " ").replace("—", " ")
    value = re.sub(r"\([^)]*\)", "", value)
    value = re.sub(r"[^а-яa-z0-9]+", " ", value).strip()
    return value


def fetch(dataset_id: str) -> dict:
    url = API.format(dataset_id)
    last_error = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Travel-Designer-student-workshop/1.0"})
            with urllib.request.urlopen(req, timeout=120) as response:
                payload = json.load(response)
            if not payload.get("ok"):
                raise RuntimeError(payload)
            return payload["dataset"]
        except Exception as exc:  # network retry
            last_error = exc
            time.sleep(5 + attempt * 5)
    raise RuntimeError(f"Could not fetch {dataset_id}: {last_error}")


def period_value(series: dict, periods: list[str], period: str):
    try:
        return series["values"][periods.index(period)]
    except (ValueError, IndexError, TypeError):
        return None


def find_region_series(dataset: dict, region: str) -> list[dict]:
    target = norm(region)
    matches = []
    for series in dataset["series"]:
        row = str(series.get("row") or series.get("label") or "")
        row_n = norm(row)
        canonical = ALIASES.get(row_n)
        if row_n == target or (canonical and norm(canonical) == target):
            matches.append(series)
    return matches


def annual_and_winter_share(dataset: dict, region: str, period: str = "2025"):
    rows = {str(s.get("col") or "").strip().lower(): s for s in find_region_series(dataset, region)}
    annual = period_value(rows.get("январь-декабрь", {}), dataset["periods"], period)
    q1 = period_value(rows.get("январь-март", {}), dataset["periods"], period)
    sep = period_value(rows.get("январь-сентябрь", {}), dataset["periods"], period)
    share = None
    if annual not in (None, 0) and q1 is not None and sep is not None:
        share = (q1 + annual - sep) / annual
    return annual, share


def scalar(dataset: dict, region: str, period: str | None = None, col_index: int = 0):
    matches = find_region_series(dataset, region)
    if not matches:
        return None
    if period is not None:
        return period_value(matches[0], dataset["periods"], period)
    values = matches[col_index].get("values", []) if len(matches) > col_index else []
    return values[0] if values else None


def values_by_columns(dataset: dict, region: str) -> dict[str, float | None]:
    """Return a period-name/value map for cross-sectional datasets."""
    matches = find_region_series(dataset, region)
    if not matches:
        return {}
    values = matches[0].get("values", [])
    return {
        str(period).strip(): values[idx] if idx < len(values) else None
        for idx, period in enumerate(dataset.get("periods", []))
    }


def pick_by_hint(values: dict[str, float | None], hints: tuple[str, ...], fallback_index: int):
    for key, value in values.items():
        if any(h in key.lower() for h in hints):
            return value
    seq = list(values.values())
    return seq[fallback_index] if fallback_index < len(seq) else None


def main():
    model = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    regions = [r["region"] for r in model["regions"]]
    loaded = {}
    for idx, (name, dataset_id) in enumerate(DATASETS.items()):
        if idx:
            time.sleep(3.2)
        print(f"Fetching {name}: {dataset_id}", flush=True)
        loaded[name] = fetch(dataset_id)

    records = []
    for region in regions:
        persons, persons_winter = annual_and_winter_share(loaded["ksr_persons"], region)
        revenue_thousand, revenue_winter = annual_and_winter_share(loaded["ksr_revenue"], region)

        recreation = values_by_columns(loaded["recreation_bases"], region)
        camping = values_by_columns(loaded["campings"], region)
        modular = values_by_columns(loaded["modular"], region)
        other = values_by_columns(loaded["other_accommodation"], region)

        record = {
            "region": region,
            "ksr_persons_2025": persons,
            "ksr_persons_winter_share_2025": persons_winter,
            "ksr_revenue_mln_rub_2025": None if revenue_thousand is None else revenue_thousand / 1000,
            "ksr_revenue_winter_share_2025": revenue_winter,
            "tourism_gva_mln_rub_2024": scalar(loaded["tourism_gva"], region, "2024"),
            "tourism_grp_share_pct_2024": scalar(loaded["tourism_grp_share"], region, "2024"),
            "hotel_services_dec_mln_rub_2025": None,
            "tour_operator_services_dec_mln_rub_2025": None,
            "recreation_bases_count_2025": pick_by_hint(recreation, ("число", "количество"), 0),
            "recreation_bases_places_2025": pick_by_hint(recreation, ("мест",), 2),
            "campings_count_2025": pick_by_hint(camping, ("число", "количество"), 0),
            "campings_places_2025": pick_by_hint(camping, ("мест",), 2),
            "modular_count_2025": pick_by_hint(modular, ("число", "количество"), 0),
            "modular_places_2025": pick_by_hint(modular, ("мест",), 2),
            "other_accommodation_count_2025": pick_by_hint(other, ("число", "количество"), 0),
            "other_accommodation_places_2025": pick_by_hint(other, ("мест",), 2),
        }

        hotel_raw = scalar(loaded["hotel_services_dec"], region)
        tour_raw = scalar(loaded["tour_operator_services_dec"], region)
        record["hotel_services_dec_mln_rub_2025"] = None if hotel_raw is None else hotel_raw / 1000
        record["tour_operator_services_dec_mln_rub_2025"] = None if tour_raw is None else tour_raw / 1000
        records.append(record)

    sources = []
    for key, ds in loaded.items():
        sources.append({
            "key": key,
            "dataset_id": ds["id"],
            "title": ds["title"],
            "unit": ds.get("unit"),
            "frequency": ds.get("freq"),
            "official_source_file": ds.get("source_file"),
            "dataset_url": ds.get("source_url"),
            "content_hash": ds.get("content_hash"),
        })

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "records": records,
        "sources": sources,
    }
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {OUTPUT_PATH} with {len(records)} regions", flush=True)


if __name__ == "__main__":
    main()

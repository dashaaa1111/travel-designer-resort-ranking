"""Экспорт для дашборда: четыре плоские таблицы в data/dashboard/.

  dashboard_resorts.csv         строка на курорт: место, интервал, группа, 5 компонентов,
                                флаги качества, ключевые сырые значения, дата обновления
  dashboard_metrics_long.csv    строка на курорт × показатель: сырое значение, единица, балл,
                                вес, вклад в индекс, источник, ссылка, дата сбора
  dashboard_scenarios_long.csv  строка на курорт × сценарий: место, балл, сдвиг к baseline
  sources.csv                   справочник источников с датами и тем, какие показатели они дают

Модуль ничего не пересчитывает по-своему: баллы и места берутся из build_model(),
интервалы и группы из run_stability(). Таблицы соединяются по resort_id и source_id.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from .data_io import load_scenarios, load_weights, write_csv
from .overlays import load_collected, load_operating_status
from .pipeline import PROJECT_ROOT, available_scenarios, build_model, load_price_rows
from .stability import COMPONENT_LABELS, COMPONENTS, run_stability

# показатель -> (подпись, поле с сырым значением, единица, вид источника)
METRICS: dict[str, tuple[str, str | None, str, str]] = {
    "region_trips": ("Поездки в регион, 2025", "trips_2025", "поездок", "rosstat_trips"),
    "region_growth": ("Рост поездок 2025 к 2024", "trips_growth_2025", "доля", "rosstat_trips"),
    "avg_stay": ("Ночей на одну поездку, 2025", "avg_stay_nights_2025", "ночей", "rosstat_nights"),
    "pistes": ("Сегменты трасс в радиусе 6 км", "piste_segments_6km", "шт.", "osm"),
    "lifts": ("Линии подъёмников в радиусе 6 км", "lift_lines_6km", "шт.", "osm"),
    "lodging": ("Объекты размещения в радиусе 6 км", "lodging_objects_6km", "шт.", "osm"),
    "amenities": ("Питание и места интереса в радиусе 6 км", None, "шт.", "osm"),
    "snow_days": ("Снежные дни за сезон", "avg_snow_days_per_season", "дней", "weather"),
    "freezing_days": ("Морозные дни за сезон", "avg_freezing_days_per_season", "дней", "weather"),
    "snowfall": ("Снегопад за сезон", "avg_snowfall_cm_per_season", "см", "weather"),
    "airport": ("Расстояние до аэропорта по прямой", "nearest_airport_distance_km", "км", "airport"),
    "transport": ("Объекты транспорта в радиусе 6 км", "transport_objects_6km", "шт.", "osm"),
    "price": ("Средняя цена взрослого ски-пасса", "adult_skipass_avg_rub", "руб.", "price"),
}
API_SOURCE_KIND = {"osm": "SRC_OSM", "weather": "SRC_OPEN_METEO", "airport": "SRC_OURAIRPORTS"}
FLAG_COLUMNS = {
    "flag_check_coordinates": "check coordinates",
    "flag_coordinates_proxy": "coordinates are proxy",
    "flag_osm_overlap": "OSM radial overlap",
    "flag_source_error": "source error",
    "flag_missing_price": "missing price",
    "flag_not_operating": "not operating",
}


# ---------- даты ----------

def to_iso_date(value: Any) -> str:
    """Любая дата из файлов проекта -> 'YYYY-MM-DD'; пусто -> ''.

    Понимает серийный номер Excel (46279), '27.10.2026', '2026-10-26T18:18:39+00:00'.
    """
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        return (date(1899, 12, 30) + timedelta(days=int(value))).isoformat()
    text = str(value).strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:10], fmt).date().isoformat()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        try:
            return to_iso_date(float(text))
        except ValueError:
            return ""


def latest(dates: list[str]) -> str:
    valid = [d for d in dates if d]
    return max(valid) if valid else ""


# ---------- справочник источников ----------

def api_sources(path: Path, version: str) -> dict[str, dict[str, Any]]:
    """Источники API из файла сбора: SRC_OSM -> OSM_V2 и т. д."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    result = {}
    for item in payload.get("sources", []):
        if not item.get("source_id"):
            continue
        sid = f"{item['source_id'].removeprefix('SRC_')}_{version}"
        result[sid] = {
            "source_id": sid,
            "name": f"{item['name']} (сбор {version})",
            "url": item.get("url") or "",
            "documentation": item.get("documentation") or "",
            "data_used": item.get("data_used") or "",
            "file": str(path.relative_to(path.parents[2])),
            "period": "",
            "collected_at": to_iso_date(item.get("collected_at")),
            "date_basis": "collected_at в файле сбора",
        }
    return result


def file_sources(root: Path) -> dict[str, dict[str, Any]]:
    """Источники из config/sources.yaml; пустые даты дополняются из строк файлов."""
    config = yaml.safe_load((root / "config" / "sources.yaml").read_text(encoding="utf-8"))
    result = {}
    for sid, item in config.items():
        result[sid] = {
            "source_id": sid, "name": item["name"], "url": item.get("url") or "",
            "documentation": "", "data_used": item.get("data_used") or "",
            "file": item.get("file") or "", "period": item.get("period") or "",
            "collected_at": to_iso_date(item.get("collected_at")),
            "date_basis": item.get("date_basis") or "",
        }
    raw = root / "data" / "raw"
    matrix = json.loads((raw / "prices_monthly.json").read_text(encoding="utf-8"))
    archive_dates = [to_iso_date(row[matrix[0].index("checked_at")]) for row in matrix[1:]]
    manual_dates = [to_iso_date(r.get("checked_at")) for r in _manual_price_rows(raw / "prices_manual_2026.csv")]
    overrides = load_operating_status(root / "data" / "reference" / "coordinate_overrides.csv")
    fill = {
        "PRICES_ARCHIVE": latest(archive_dates),
        "PRICES_MANUAL_2026": latest(manual_dates),
        "COORDINATES_MANUAL": latest([to_iso_date(r.get("дата_проверки")) for r in overrides.values()]),
    }
    for sid, value in fill.items():
        if sid in result and not result[sid]["collected_at"]:
            result[sid]["collected_at"] = value
    return result


def _manual_price_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8-sig")
    first = text.splitlines()[0] if text else ""
    delimiter = ";" if first.count(";") > first.count(",") else ","
    return list(csv.DictReader(text.splitlines(), delimiter=delimiter))


def build_sources(root: Path) -> dict[str, dict[str, Any]]:
    raw = root / "data" / "raw"
    sources = {**api_sources(raw / "resort_open_data.json", "V1")}
    if (raw / "resort_open_data_v2.json").exists():
        sources.update(api_sources(raw / "resort_open_data_v2.json", "V2"))
    sources.update(file_sources(root))
    return sources


# ---------- откуда взят каждый показатель ----------

def price_provenance(price_rows: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """По курорту: какие файлы цен участвовали в средней, ссылка и последняя дата проверки."""
    result: dict[str, dict[str, Any]] = {}
    for row in price_rows:
        if row.get("adult_price_min_rub") is None and row.get("adult_price_max_rub") is None:
            continue
        item = result.setdefault(str(row["resort_id"]), {"ids": set(), "urls": [], "dates": [], "months": []})
        item["ids"].add("PRICES_MANUAL_2026" if row.get("source_file") == "prices_manual_2026" else "PRICES_ARCHIVE")
        if row.get("source_url"):
            item["urls"].append(str(row["source_url"]).strip())
        item["dates"].append(to_iso_date(row.get("checked_at")))
        item["months"].append(str(row.get("month") or ""))
    return {
        rid: {
            "source_id": ";".join(sorted(v["ids"])),
            "source_url": next((u for u in v["urls"] if u), ""),
            "collected_at": latest(v["dates"]),
            "data_period": ", ".join(sorted({m for m in v["months"] if m})),
            "note": "",
        }
        for rid, v in result.items()
    }


def price_attempts(path: Path) -> dict[str, dict[str, str]]:
    """Курорты, для которых цену искали вручную: тип тарифа, ссылка и дата проверки.

    Нужно, чтобы у курорта без дневного билета (оплата за подъём) на дашборде было видно,
    что цену проверяли, а балл 50 поставлен по правилу ТЗ, а не из-за пропуска сбора.
    """
    result: dict[str, dict[str, Any]] = {}
    for row in _manual_price_rows(path):
        rid = (row.get("resort_id") or "").strip()
        item = result.setdefault(rid, {"models": set(), "url": "", "dates": [], "season": ""})
        item["models"].add((row.get("pricing_model") or "").strip())
        item["url"] = item["url"] or (row.get("source_url") or "").strip()
        item["dates"].append(to_iso_date(row.get("checked_at")))
        item["season"] = item["season"] or (row.get("season") or "").strip()
    return {
        rid: {
            "source_id": "PRICES_MANUAL_2026",
            "source_url": v["url"],
            "collected_at": latest(v["dates"]),
            "data_period": v["season"],
            "note": "цена проверена, но в расчёт не вошла (тариф: " + ", ".join(sorted(v["models"]))
                    + "); балл 50 по правилу ТЗ",
        }
        for rid, v in result.items() if rid
    }


def metric_provenance(
    kind: str, resort: dict[str, Any], spec: dict[str, Any], sources: dict[str, dict[str, Any]],
    collected_v2: dict[str, dict[str, Any]], prices: dict[str, dict[str, str]],
    attempts: dict[str, dict[str, str]] | None = None,
) -> dict[str, str]:
    """source_id, source_url, collected_at, data_period, note для одного показателя одного курорта."""
    rid = resort["resort_id"]
    if kind in ("rosstat_trips", "rosstat_nights"):
        sid = "ROSSTAT_TRIPS" if kind == "rosstat_trips" else "ROSSTAT_NIGHTS"
        period = "2024-2025" if kind == "rosstat_trips" else "2025"
        return {"source_id": sid, "source_url": sources[sid]["url"],
                "collected_at": sources[sid]["collected_at"], "data_period": period, "note": ""}
    if kind == "price":
        missing = {"source_id": "", "source_url": "", "collected_at": "", "data_period": "",
                   "note": "цена не найдена; балл 50 по правилу ТЗ"}
        return prices.get(rid) or (attempts or {}).get(rid) or missing

    uses_v2 = spec["weather" if kind == "weather" else "features"] == "collected_v2"
    sid = f"{API_SOURCE_KIND[kind].removeprefix('SRC_')}_{'V2' if uses_v2 else 'V1'}"
    v2_row = collected_v2.get(rid, {}) if uses_v2 else {}
    if kind == "weather":
        period = f"{resort.get('weather_period_start', '')} - {resort.get('weather_period_end', '')}".strip(" -")
    elif kind == "osm" and v2_row.get("osm_data_timestamp"):
        period = f"снимок OSM на {to_iso_date(v2_row['osm_data_timestamp'])}"
    else:
        period = "на дату сбора"
    return {"source_id": sid, "source_url": sources[sid]["url"],
            "collected_at": sources[sid]["collected_at"], "data_period": period, "note": ""}


# ---------- таблицы ----------

def raw_value(row: dict[str, Any], metric: str) -> float | None:
    field = METRICS[metric][1]
    if field is None:   # amenities = питание + места интереса
        return float((row.get("food_objects_6km") or 0) + (row.get("attraction_objects_6km") or 0))
    value = row.get(field)
    return None if value in (None, "") else float(value)


def component_values(row: dict[str, Any], weights: dict[str, float]) -> dict[str, float]:
    """По 5 компонентам: вклад в индекс (сумма вкладов = индекс) и балл 0-100 внутри компонента."""
    result = {}
    for component, keys in COMPONENTS.items():
        weight = sum(weights[k] for k in keys)
        weighted = sum(row[k + "_score"] * weights[k] for k in keys)
        result[f"{component}_contribution"] = round(weighted / 100.0, 3)
        result[f"{component}_score"] = round(weighted / weight, 2) if weight else 0.0
    return result


def metrics_long(
    rows: list[dict[str, Any]], resorts_by_id: dict[str, dict[str, Any]], weights: dict[str, float],
    spec: dict[str, Any], scenario: str, sources: dict[str, dict[str, Any]],
    collected_v2: dict[str, dict[str, Any]], prices: dict[str, dict[str, str]],
    attempts: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    means = {m: sum(r[m + "_score"] for r in rows) / len(rows) for m in METRICS}
    table = []
    for row in rows:
        resort = {**resorts_by_id[row["resort_id"]], **row}
        for metric, (label, _, unit, kind) in METRICS.items():
            component = next(c for c, keys in COMPONENTS.items() if metric in keys)
            score = row[metric + "_score"]
            value = raw_value(row, metric)
            imputed = value is None   # нет данных -> нейтральные 50 баллов (правило ТЗ)
            table.append({
                "scenario": scenario,
                "resort_id": row["resort_id"],
                "name_ru": row["name_ru"],
                "metric": metric,
                "metric_label": label,
                "component": component,
                "component_label": COMPONENT_LABELS[component],
                "raw_value": None if imputed else round(value, 4),
                "unit": unit,
                "score": round(score, 2),
                "score_mean_32": round(means[metric], 2),
                "weight": weights[metric],
                "contribution": round(score * weights[metric] / 100.0, 3),
                "imputed": imputed,
                **metric_provenance(kind, resort, spec, sources, collected_v2, prices, attempts),
            })
    return table


def resorts_table(
    rows: list[dict[str, Any]], resorts_by_id: dict[str, dict[str, Any]], weights: dict[str, float],
    scenario: str, stability: dict[str, Any], long_rows: list[dict[str, Any]],
    collected_v2: dict[str, dict[str, Any]], prices: dict[str, dict[str, str]],
    attempts: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    attempts = attempts or {}
    intervals = {i["resort_id"]: i for i in stability["intervals"]}
    bounds = {b["resort_id"]: b for b in stability["price_bounds"]}
    updated: dict[str, list[str]] = {}
    for item in long_rows:
        updated.setdefault(item["resort_id"], []).append(item["collected_at"])

    components = {r["resort_id"]: component_values(r, weights) for r in rows}
    mean_contribution = {
        c: sum(v[f"{c}_contribution"] for v in components.values()) / len(components) for c in COMPONENTS
    }
    table = []
    for row in rows:
        rid = row["resort_id"]
        resort, iv, comp = resorts_by_id[rid], intervals[rid], components[rid]
        flags = [f for f in (row.get("quality_flags") or "").split("; ") if f]
        delta = {c: comp[f"{c}_contribution"] - mean_contribution[c] for c in COMPONENTS}
        v2 = collected_v2.get(rid, {})
        bound = bounds.get(rid, {})
        table.append({
            "scenario": scenario,
            "resort_id": rid,
            "name_ru": row["name_ru"],
            "region": row["region"],
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "operating_status": row["operating_status"],
            "rank": row["rank_default"],
            "rank_operating": row["rank_operating"],
            "popularity_score": round(row["popularity_score_default"], 2),
            "confidence_score": round(row["confidence_score"], 1),
            "rank_p05": iv["rank_p05"],
            "rank_median": iv["rank_median"],
            "rank_p95": iv["rank_p95"],
            "interval_label": f"{iv['rank_p05']}–{iv['rank_p95']}" if iv["rank_p05"] != iv["rank_p95"] else str(iv["rank_p05"]),
            "interval_width": iv["interval_width"],
            "p_top5": iv["p_top5"],
            "p_top10": iv["p_top10"],
            "p_top15": iv.get("p_top15"),
            "group": iv["group"],
            "group_label": iv["group_label"],
            "distinct_from_next": iv["distinct_from_next"],
            **comp,
            "main_driver": COMPONENT_LABELS[max(delta, key=delta.get)],
            "main_drag": COMPONENT_LABELS[min(delta, key=delta.get)],
            "quality_flags": "; ".join(flags),
            **{col: flag in flags for col, flag in FLAG_COLUMNS.items()},
            "has_price": bool(row.get("adult_skipass_avg_rub")),
            "rank_if_expensive": bound.get("rank_if_expensive"),
            "rank_if_cheap": bound.get("rank_if_cheap"),
            "price_swing": bound.get("swing"),
            "trips_2025": row.get("trips_2025"),
            "trips_growth_2025": row.get("trips_growth_2025"),
            "avg_stay_nights_2025": row.get("avg_stay_nights_2025"),
            "nearest_airport_iata": v2.get("nearest_airport_iata") or resort.get("nearest_airport_iata") or "",
            "nearest_airport_distance_km": row.get("nearest_airport_distance_km"),
            "adult_skipass_avg_rub": row.get("adult_skipass_avg_rub"),
            "price_source_url": (prices.get(rid) or attempts.get(rid) or {}).get("source_url", ""),
            "coordinate_source_url": resort.get("coordinate_source_url") or "",
            "coordinate_checked_at": to_iso_date(v2.get("coordinate_checked_at")),
            "data_updated_at": latest(updated.get(rid, [])),
        })
    return table


def scenarios_long(models: dict[str, dict[str, Any]], primary: str) -> list[dict[str, Any]]:
    base = {r["resort_id"]: r["rank_default"] for r in models["baseline"]["model_rows"]} if "baseline" in models else {}
    table = []
    for name, model in models.items():
        for row in model["model_rows"]:
            rid = row["resort_id"]
            table.append({
                "scenario": name,
                "is_primary": name == primary,
                "resort_id": rid,
                "name_ru": row["name_ru"],
                "rank": row["rank_default"],
                "rank_operating": row["rank_operating"],
                "popularity_score": round(row["popularity_score_default"], 2),
                "confidence_score": round(row["confidence_score"], 1),
                "shift_vs_baseline": base[rid] - row["rank_default"] if base else None,  # >0: поднялся
            })
    return table


def sources_table(sources: dict[str, dict[str, Any]], long_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    used: dict[str, set[str]] = {}
    for item in long_rows:
        for sid in filter(None, item["source_id"].split(";")):
            used.setdefault(sid, set()).add(item["metric"])
    used.setdefault("COORDINATES_MANUAL", set()).add("coordinates")
    table = []
    for sid, item in sorted(sources.items()):
        table.append({**item, "used_in_primary": sid in used, "metrics": ", ".join(sorted(used.get(sid, [])))})
    return table


# ---------- запуск ----------

def build_dashboard(root: Path = PROJECT_ROOT) -> dict[str, list[dict[str, Any]]]:
    """Все четыре таблицы. Файлы не пишет."""
    config, _, runnable = available_scenarios(root)
    primary = config["primary"] if config["primary"] in runnable else next(iter(runnable))
    spec = config["scenarios"][primary]
    weights = load_weights(root / "config" / "weights.yaml")

    models = {name: build_model(root, name) for name in runnable}
    model = models[primary]
    rows = model["model_rows"]
    resorts_by_id = {r["resort_id"]: r for r in model["resorts"]}
    raw = root / "data" / "raw"
    uses_v2 = "collected_v2" in (spec["features"], spec["weather"])
    collected_v2 = load_collected(raw / "resort_open_data_v2.json") if uses_v2 else {}
    prices = price_provenance(load_price_rows(root, config, spec))
    attempts = price_attempts(raw / "prices_manual_2026.csv") if spec["manual_prices"] else {}
    sources = build_sources(root)

    stability = run_stability(root)
    long_rows = metrics_long(rows, resorts_by_id, weights, spec, primary, sources, collected_v2, prices,
                             attempts)
    return {
        "dashboard_resorts": resorts_table(rows, resorts_by_id, weights, primary, stability, long_rows,
                                           collected_v2, prices, attempts),
        "dashboard_metrics_long": long_rows,
        "dashboard_scenarios_long": scenarios_long(models, primary),
        "sources": sources_table(sources, long_rows),
    }


def main(root: Path = PROJECT_ROOT) -> None:
    tables = build_dashboard(root)
    out = root / "data" / "dashboard"
    for name, rows in tables.items():
        write_csv(out / f"{name}.csv", rows)
        print(f"{name}.csv: {len(rows)} строк")
    resorts = tables["dashboard_resorts"]
    print(f"Сценарий: {resorts[0]['scenario']}, дата обновления данных: {max(r['data_updated_at'] for r in resorts)}")
    missing = [s["source_id"] for s in tables["sources"] if s["used_in_primary"] and not s["collected_at"]]
    if missing:
        print("Нет даты у источников:", ", ".join(missing))


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args()
    main()

#!/usr/bin/env python3
"""Сбор открытых данных по ПРОВЕРЕННЫМ координатам курортов.

Повторяет main() из collect_resort_open_data.py, но после шага Википедии
подставляет координаты из data/reference/coordinate_overrides.csv. Поэтому OSM,
климат и расстояние до аэропорта считаются по исправленным точкам.
Исходный скрипт не изменён. Результат пишется в НОВЫЙ файл, старый снимок
data/raw/resort_open_data.json остаётся для сравнения.

Отличия от исходного скрипта (все описаны в README проекта):
  * координаты берутся из справочника исправлений;
  * ответы серверов кэшируются в data/cache/: повторный запуск повторяет только то,
    что не удалось, а удачные запросы не отправляются заново;
  * при ответах 429/503 и таймаутах выдерживаются длинные паузы (30, 60, 120, 240 с);
  * пустой ответ Overpass (remark: runtime error) не считается успехом и не кэшируется;
  * погода запрашивается у модели ERA5 (как указано в каталоге источников данных),
    пакетами по 4 курорта; если снегопад пуст, это ошибка, а не нулевые снежные дни;
  * пауза между запросами к Википедии после каждого курорта, повтор после 429.

Запуск (из корня проекта):
    python src/collect_with_overrides.py --dry-run       # без сети, только проверка
    python src/collect_with_overrides.py --limit 4       # пробный запуск на 4 курортах
    python src/collect_with_overrides.py                 # полный сбор
    python src/collect_with_overrides.py --no-cache      # игнорировать кэш
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import time
import urllib.error
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import collect_resort_open_data as base  # лежит рядом, в src/

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OVERRIDES_PATH = PROJECT_ROOT / "data" / "reference" / "coordinate_overrides.csv"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "raw" / "resort_open_data_v2.json"
TEST_OUTPUT = PROJECT_ROOT / "data" / "collected" / "resort_open_data_v2_test.json"
CACHE_DIR = PROJECT_ROOT / "data" / "cache"

RETRY_WAITS = (30, 60, 120, 240)          # секунды между повторами при перегрузке сервера
RETRYABLE_CODES = {429, 500, 502, 503, 504}
SNOW_METRICS = ("avg_snowfall_cm_per_season", "avg_snow_days_per_season", "max_daily_snowfall_cm")

VERIFIED_STATUSES = {"подтверждена", "неточная"}  # у обоих есть проверенная точка (lat_final/lon_final)


def load_overrides(path: Path = OVERRIDES_PATH) -> dict[str, dict[str, str]]:
    """Читает справочник исправлений и проверяет его."""
    if not path.exists():
        raise FileNotFoundError(f"Нет файла {path}. Сначала выполните ячейку шага 1, которая его создаёт.")
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))

    required = {"resort_id", "lat_final", "lon_final", "статус_проверки", "operating_status"}
    missing_cols = required - set(rows[0].keys()) if rows else required
    if missing_cols:
        raise ValueError(f"В {path.name} нет колонок: {sorted(missing_cols)}")

    ids = [row["resort_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("В справочнике исправлений повторяются resort_id")
    for row in rows:
        lat, lon = float(row["lat_final"]), float(row["lon_final"])
        if not (41 <= lat <= 82 and 19 <= lon <= 180):
            raise ValueError(f"{row['resort_id']}: координаты вне России ({lat}, {lon})")
    return {row["resort_id"]: row for row in rows}


def apply_overrides(
    resorts: list[dict[str, Any]], overrides: dict[str, dict[str, str]]
) -> int:
    """Подставляет проверенные координаты в собранные строки. Возвращает число применённых."""
    applied = 0
    for resort in resorts:
        row = overrides.get(resort["resort_id"])
        if row is None:
            raise KeyError(f"{resort['resort_id']} нет в справочнике исправлений")

        # что вернула Википедия, сохраняем для сравнения
        resort["wiki_latitude"] = resort["latitude"]
        resort["wiki_longitude"] = resort["longitude"]
        resort["wiki_coordinate_quality"] = resort.get("coordinate_quality")
        resort["wiki_coordinate_source_url"] = resort.get("coordinate_source_url", "")

        resort["latitude"] = float(row["lat_final"])
        resort["longitude"] = float(row["lon_final"])
        resort["coordinate_distance_from_seed_km"] = round(
            base.haversine_km(
                resort["fallback_lat"], resort["fallback_lon"],
                resort["latitude"], resort["longitude"],
            ), 1,
        )
        resort["operating_status"] = row.get("operating_status") or "operating"
        resort["coordinate_review_status"] = row["статус_проверки"]
        resort["coordinate_checked_at"] = row.get("дата_проверки", "")

        if row["статус_проверки"] in VERIFIED_STATUSES:
            resort["coordinate_source"] = "Manual review"
            resort["coordinate_quality"] = "manually_verified"
            resort["coordinate_source_title"] = "Ручная проверка по карте и сайту курорта"
            if row.get("ссылка_на_подтверждение"):
                resort["coordinate_source_url"] = row["ссылка_на_подтверждение"]
            # Википедия не нашла страницу, но точку мы проверили вручную: убираем ложный флаг "source error".
            # Ключ НЕ должен оканчиваться на "_error", иначе add_quality_fields снова выставит флаг.
            if resort.get("coordinate_error"):
                resort["wiki_lookup_note"] = resort.pop("coordinate_error")
            applied += 1
        # «не найдена»: координата остаётся прежней, качество не повышаем
    return applied


class InvalidResponse(RuntimeError):
    """Сервер ответил 200, но данных в ответе нет (например, таймаут Overpass внутри ответа)."""


def validate_response(url: str, text: str) -> None:
    """Бросает InvalidResponse, если ответ формально успешный, но пустой или с ошибкой."""
    if url.startswith(base.OVERPASS_ENDPOINT):
        payload = json.loads(text)
        remark = str(payload.get("remark", ""))
        if "error" in remark.lower() or not payload.get("elements"):
            raise InvalidResponse(f"Overpass вернул пустой ответ: {remark or 'нет elements'}")
    elif url.startswith(base.OPEN_METEO_ENDPOINT):
        payload = json.loads(text)
        items = payload if isinstance(payload, list) else [payload]
        if any(item.get("error") for item in items) or not all(item.get("daily", {}).get("time") for item in items):
            raise InvalidResponse(f"Open-Meteo вернул пустой ответ: {str(payload)[:150]}")


def make_resilient_request(original, cache_dir: Path | None, waits: tuple[float, ...] = RETRY_WAITS):
    """Заменяет request_with_retry: кэш удачных ответов + длинные паузы при перегрузке."""

    def request(session, method, url, *, attempts=3, timeout=120, **kwargs):
        key = hashlib.sha256(
            json.dumps([method, url, kwargs.get("params"), kwargs.get("data")], sort_keys=True, default=str).encode()
        ).hexdigest()
        path = cache_dir / f"{key}.json" if cache_dir else None
        if path and path.exists():
            blob = json.loads(path.read_text(encoding="utf-8"))
            return base.HttpResponse(blob["body"].encode("utf-8"), "utf-8")

        last: Exception | None = None
        for wait in (0, *waits):
            if wait:
                print(f"  сервер не ответил ({last}); жду {wait:.0f} с и повторяю", flush=True)
                time.sleep(wait)
            try:
                response = original(session, method, url, attempts=attempts, timeout=timeout, **kwargs)
                validate_response(url, response.text)
            except InvalidResponse as error:
                last = error
                continue
            except RuntimeError as error:
                code = getattr(error.__cause__, "code", None)
                last = RuntimeError(f"HTTP {code}" if code else f"сетевая ошибка: {error.__cause__ or error}")
                if code is None or code in RETRYABLE_CODES:
                    continue
                raise last from error.__cause__
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"url": url, "body": response.text}, ensure_ascii=False), encoding="utf-8")
            return response
        raise RuntimeError(f"Не удалось получить данные: {last}") from last

    return request


def collect_weather_era5(
    session: Any, resorts: list[dict[str, Any]], batch_size: int = 4, pause: float = 3.0
) -> list[dict[str, Any]]:
    """Погода ERA5 (модель из каталога источников) пакетами по batch_size курортов."""
    print(f"Open-Meteo: модель ERA5, пакеты по {batch_size}", flush=True)
    weather_daily: list[dict[str, Any]] = []
    for start in range(0, len(resorts), batch_size):
        batch = resorts[start : start + batch_size]
        params = {
            "latitude": ",".join(str(r["latitude"]) for r in batch),
            "longitude": ",".join(str(r["longitude"]) for r in batch),
            "start_date": base.WEATHER_START,
            "end_date": base.WEATHER_END,
            "daily": "temperature_2m_mean,temperature_2m_min,temperature_2m_max,precipitation_sum,snowfall_sum",
            "timezone": "auto",
            "models": "era5",
        }
        try:
            raw = base.request_with_retry(session, "GET", base.OPEN_METEO_ENDPOINT, params=params, timeout=180).json()
            payload = raw if isinstance(raw, list) else [raw]
            if len(payload) != len(batch):
                raise RuntimeError(f"Open-Meteo вернул {len(payload)} точек вместо {len(batch)}")
            for resort, item in zip(batch, payload, strict=True):
                resort["open_meteo_elevation_m"] = item.get("elevation")
                resort["open_meteo_timezone"] = item.get("timezone", "")
                resort["weather_model"] = "ERA5"
                daily = item.get("daily", {})
                rows = []
                for index, date_value in enumerate(daily.get("time", [])):
                    rows.append({
                        "resort_id": resort["resort_id"],
                        "date": date_value,
                        "ski_season": base.ski_season(date_value),
                        "month": int(date_value[5:7]),
                        "temperature_mean_c": base.value_at(daily, "temperature_2m_mean", index),
                        "temperature_min_c": base.value_at(daily, "temperature_2m_min", index),
                        "temperature_max_c": base.value_at(daily, "temperature_2m_max", index),
                        "precipitation_mm": base.value_at(daily, "precipitation_sum", index),
                        "snowfall_cm": base.value_at(daily, "snowfall_sum", index),
                    })
                weather_daily.extend(rows)
                if not any(isinstance(r["snowfall_cm"], (int, float)) for r in rows):
                    resort["weather_error"] = "В ответе Open-Meteo нет данных о снегопаде (snowfall_sum пуст)"
            print(f"Open-Meteo: {min(start + batch_size, len(resorts))}/{len(resorts)}", flush=True)
        except (ValueError, RuntimeError, urllib.error.URLError) as error:
            for resort in batch:
                resort["weather_error"] = str(error)
            print(f"Open-Meteo: пакет не удался: {error}", flush=True)
        time.sleep(pause)
    return weather_daily


def drop_unreliable_snow(resorts: list[dict[str, Any]], weather_monthly: list[dict[str, Any]]) -> None:
    """Если погода не собралась, снежные показатели должны быть пустыми, а не нулевыми."""
    broken = {r["resort_id"] for r in resorts if r.get("weather_error")}
    for resort in resorts:
        if resort["resort_id"] in broken:
            for key in SNOW_METRICS:
                resort[key] = None
    for row in weather_monthly:
        if row["resort_id"] in broken:
            row["avg_monthly_snowfall_cm"] = None
            row["avg_snow_days"] = None


def collect_wikipedia_politely(
    session: Any,
    seeds: list[dict[str, Any]],
    pause: float = 1.5,
    retry_rounds: int = 3,
    retry_pause: float = 20.0,
) -> list[dict[str, Any]]:
    """Запросы к Википедии с паузой после КАЖДОГО курорта и повтором после ответа 429.

    В первом сборе у 18 курортов остался "HTTP 429 Too Many Requests": исходный скрипт
    делает паузу только раз в 4 запроса. Здесь пауза чаще, а отказы повторяются.
    """
    print(f"Wikipedia: {len(seeds)} курортов, пауза {pause} с между запросами", flush=True)
    resorts = []
    for index, seed in enumerate(seeds, start=1):
        resorts.append(base.enrich_wikipedia(session, seed))
        time.sleep(pause)
        if index % 8 == 0 or index == len(seeds):
            print(f"Wikipedia: {index}/{len(seeds)}", flush=True)

    for round_no in range(1, retry_rounds + 1):
        failed = [i for i, r in enumerate(resorts) if "429" in str(r.get("coordinate_error", ""))]
        if not failed:
            break
        print(f"Wikipedia: повтор {round_no}/{retry_rounds}, {len(failed)} курортов после 429, "
              f"пауза {retry_pause:.0f} с", flush=True)
        time.sleep(retry_pause)
        for i in failed:
            resorts[i] = base.enrich_wikipedia(session, seeds[i])
            time.sleep(pause)
    return resorts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=None, help="Куда сохранить JSON")
    parser.add_argument("--dry-run", action="store_true", help="Проверить входные файлы без обращения к сети")
    parser.add_argument("--limit", type=int, default=None, help="Взять только первые N курортов (пробный запуск)")
    parser.add_argument("--no-cache", action="store_true", help="Не использовать кэш ответов (data/cache)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    seeds = base.load_seeds()
    overrides = load_overrides()
    if set(overrides) != {seed["resort_id"] for seed in seeds}:
        raise ValueError("resort_id в справочнике исправлений и в resorts.json не совпадают")

    if args.limit:
        seeds = seeds[: args.limit]
    output_path = args.output or (TEST_OUTPUT if args.limit else DEFAULT_OUTPUT)

    if args.dry_run:
        verified = sum(1 for row in overrides.values() if row["статус_проверки"] in VERIFIED_STATUSES)
        print(f"Входные файлы корректны: {len(seeds)} курортов в работе, "
              f"{len(overrides)} строк в справочнике исправлений, {verified} с проверенной точкой")
        print(f"Результат будет записан в: {output_path.resolve()}")
        print(f"Кэш ответов: {'выключен' if args.no_cache else CACHE_DIR}")
        return

    collected_at = datetime.now(UTC).isoformat()
    session = base.HttpClient(base.USER_AGENT)
    base.request_with_retry = make_resilient_request(base.request_with_retry, None if args.no_cache else CACHE_DIR)

    resorts = collect_wikipedia_politely(session, seeds)
    applied = apply_overrides(resorts, overrides)          # ← отличие от исходного скрипта
    print(f"Manual overrides: применено {applied} из {len(resorts)}", flush=True)
    base.collect_osm(session, resorts)
    weather_daily = collect_weather_era5(session, resorts)
    weather_monthly = base.aggregate_weather(resorts, weather_daily)
    drop_unreliable_snow(resorts, weather_monthly)
    base.collect_airports(session, resorts)
    base.add_quality_fields(resorts, collected_at)

    sources = base.source_catalog(collected_at)
    sources.append({
        "name": "Ручная проверка координат",
        "method": "coordinate_overrides.csv: проверка по картам и официальным сайтам курортов",
        "collected_at": collected_at,
    })
    output = {
        "collectedAt": collected_at,
        "resorts": resorts,
        "weatherMonthly": weather_monthly,
        "sources": sources,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(resorts)} resorts and {len(weather_monthly)} monthly climate rows to {output_path}", flush=True)


if __name__ == "__main__":
    main()

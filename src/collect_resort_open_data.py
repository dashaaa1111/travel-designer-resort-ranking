#!/usr/bin/env python3
"""Collect open data for the Travel Designer ski resort project.

The script uses four public sources:

* MediaWiki Action API for coordinates and Wikidata identifiers;
* OpenStreetMap Overpass API for nearby infrastructure;
* Open-Meteo Historical Weather API for daily climate data;
* OurAirports CSV for the nearest scheduled airport.

The result is written to data/collected/resort_open_data.json.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "collected" / "resort_open_data.json"

WIKIPEDIA_ENDPOINT = "https://ru.wikipedia.org/w/api.php"
OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"
OPEN_METEO_ENDPOINT = "https://archive-api.open-meteo.com/v1/archive"
OURAIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"

USER_AGENT = "TravelDesignerStudentWorkshop/1.0 open-data-research"
WEATHER_START = "2021-11-01"
WEATHER_END = "2026-04-30"

OSM_METRICS = [
    ("piste_segments_6km", "way", '["piste:type"="downhill"]'),
    (
        "lift_lines_6km",
        "way",
        '["aerialway"~"cable_car|gondola|chair_lift|drag_lift|t-bar|j-bar|platter|magic_carpet|rope_tow|mixed_lift"]',
    ),
    (
        "lodging_objects_6km",
        "nwr",
        '["tourism"~"hotel|hostel|guest_house|chalet|apartment"]',
    ),
    (
        "food_objects_6km",
        "nwr",
        '["amenity"~"restaurant|cafe|fast_food|bar|food_court"]',
    ),
    (
        "transport_objects_6km",
        "nwr",
        '["public_transport"~"station|platform|stop_position"]',
    ),
    (
        "attraction_objects_6km",
        "nwr",
        '["tourism"~"attraction|viewpoint|museum|theme_park"]',
    ),
]

MONTH_NAMES_RU = {
    1: "Январь",
    2: "Февраль",
    3: "Март",
    4: "Апрель",
    11: "Ноябрь",
    12: "Декабрь",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Path to the output JSON file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate input files and configuration without network requests",
    )
    return parser.parse_args()


def load_matrix(path: Path) -> list[dict[str, Any]]:
    """Read a JSON matrix whose first row contains column names."""
    matrix = json.loads(path.read_text(encoding="utf-8"))
    if not matrix or not isinstance(matrix[0], list):
        raise ValueError(f"Expected a JSON matrix with a header row: {path}")
    header = matrix[0]
    return [dict(zip(header, row, strict=True)) for row in matrix[1:]]


def load_seeds() -> list[dict[str, Any]]:
    """Load the resort list and retain current coordinates as fallbacks."""
    rows = load_matrix(RAW_DIR / "resorts.json")
    seeds = []
    for row in rows:
        seeds.append(
            {
                "resort_id": row["resort_id"],
                "name_ru": row["name_ru"],
                "region": row["region"],
                "wiki_query": f"{row['name_ru']} горнолыжный курорт",
                "fallback_lat": float(row["latitude"]),
                "fallback_lon": float(row["longitude"]),
            }
        )
    return seeds


def normalize(text: str) -> str:
    text = text.lower().replace("ё", "е")
    return re.sub(r"[^a-zа-я0-9]+", " ", text, flags=re.IGNORECASE).strip()


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    value = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(value))


class HttpResponse:
    """Small response wrapper used by the standard-library HTTP client."""

    def __init__(self, body: bytes, encoding: str = "utf-8") -> None:
        self.body = body
        self.encoding = encoding

    @property
    def text(self) -> str:
        return self.body.decode(self.encoding, errors="replace")

    def json(self) -> Any:
        return json.loads(self.text)


class HttpClient:
    def __init__(self, user_agent: str) -> None:
        self.headers = {"User-Agent": user_agent}

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        timeout: int = 120,
    ) -> HttpResponse:
        if params:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{urllib.parse.urlencode(params)}"
        encoded_data = None
        headers = dict(self.headers)
        if data is not None:
            encoded_data = urllib.parse.urlencode(data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(
            url, data=encoded_data, headers=headers, method=method
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return HttpResponse(response.read(), charset)


def request_with_retry(
    session: HttpClient,
    method: str,
    url: str,
    *,
    attempts: int = 3,
    timeout: int = 120,
    **kwargs: Any,
) -> HttpResponse:
    """Make an HTTP request with a short incremental backoff."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return session.request(method, url, timeout=timeout, **kwargs)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = error
            if attempt < attempts:
                time.sleep(1.5 * attempt)
    raise RuntimeError(f"Request failed after {attempts} attempts: {url}") from last_error


def enrich_wikipedia(
    session: HttpClient, seed: dict[str, Any]
) -> dict[str, Any]:
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": seed["wiki_query"],
        "gsrlimit": 5,
        "prop": "coordinates|pageprops|info",
        "inprop": "url",
        "format": "json",
        "origin": "*",
    }
    try:
        payload = request_with_retry(
            session,
            "GET",
            WIKIPEDIA_ENDPOINT,
            params=params,
            timeout=30,
        ).json()
        pages = [
            page
            for page in payload.get("query", {}).get("pages", {}).values()
            if page.get("coordinates")
        ]
        name_tokens = [token for token in normalize(seed["name_ru"]).split() if token]
        candidates = []
        for page in pages:
            title = normalize(page.get("title", ""))
            coordinate = page["coordinates"][0]
            distance = haversine_km(
                seed["fallback_lat"],
                seed["fallback_lon"],
                float(coordinate["lat"]),
                float(coordinate["lon"]),
            )
            score = sum(3 for token in name_tokens if token in title)
            if re.search(r"горнолыж|курорт|комплекс|фанпарк|центр", title):
                score += 5
            if re.search(r"станция|железнодорож|село|деревня|поселок", title):
                score -= 4
            score += 4 if distance <= 15 else 1 if distance <= 40 else -6
            candidates.append((score, distance, page))

        candidates.sort(key=lambda item: (-item[0], item[1]))
        if not candidates or candidates[0][1] > 40 or candidates[0][0] < 1:
            raise ValueError("No suitable coordinate page")

        _, distance, page = candidates[0]
        coordinate = page["coordinates"][0]
        title = normalize(page.get("title", ""))
        return {
            **seed,
            "latitude": float(coordinate["lat"]),
            "longitude": float(coordinate["lon"]),
            "coordinate_source": "Wikipedia",
            "coordinate_source_title": page.get("title", ""),
            "coordinate_source_url": page.get("fullurl", ""),
            "wikidata_id": page.get("pageprops", {}).get("wikibase_item", ""),
            "coordinate_quality": (
                "resort_or_complex_page"
                if re.search(r"горнолыж|курорт|комплекс|фанпарк|центр", title)
                else "nearby_feature_proxy"
            ),
            "coordinate_distance_from_seed_km": round(distance, 1),
        }
    except (ValueError, KeyError, urllib.error.URLError, RuntimeError) as error:
        return {
            **seed,
            "latitude": seed["fallback_lat"],
            "longitude": seed["fallback_lon"],
            "coordinate_source": "Seed estimate",
            "coordinate_source_title": "",
            "coordinate_source_url": "",
            "wikidata_id": "",
            "coordinate_quality": "manual_review_required",
            "coordinate_distance_from_seed_km": 0,
            "coordinate_error": str(error),
        }


def collect_wikipedia(
    session: HttpClient, seeds: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    print(f"Wikipedia: starting {len(seeds)} resorts", flush=True)
    resorts = []
    for index, seed in enumerate(seeds, start=1):
        resorts.append(enrich_wikipedia(session, seed))
        if index % 4 == 0 or index == len(seeds):
            print(f"Wikipedia: {index}/{len(seeds)}", flush=True)
            time.sleep(0.35)
    return resorts


def overpass_batch_query(batch: list[dict[str, Any]]) -> str:
    lines = ["[out:json][timeout:120];"]
    for resort_index, resort in enumerate(batch):
        for metric_index, (metric, element_type, osm_filter) in enumerate(OSM_METRICS):
            set_name = f"s{resort_index}_{metric_index}"
            lines.append(
                f"{element_type}(around:6000,{resort['latitude']},{resort['longitude']})"
                f"{osm_filter}->.{set_name};"
            )
            count_expression = (
                f"{set_name}.count(ways)"
                if element_type == "way"
                else (
                    f"{set_name}.count(nodes)+{set_name}.count(ways)"
                    f"+{set_name}.count(relations)"
                )
            )
            lines.append(
                f'make stats resort_id="{resort["resort_id"]}",'
                f'metric="{metric}",value={count_expression};out;'
            )
    return "\n".join(lines)


def collect_osm(
    session: HttpClient, resorts: list[dict[str, Any]]
) -> None:
    print("OpenStreetMap: starting batches", flush=True)
    for start in range(0, len(resorts), 4):
        batch = resorts[start : start + 4]
        try:
            payload = request_with_retry(
                session,
                "POST",
                OVERPASS_ENDPOINT,
                data={"data": overpass_batch_query(batch)},
                timeout=180,
            ).json()
            by_id = {resort["resort_id"]: resort for resort in batch}
            for element in payload.get("elements", []):
                tags = element.get("tags", {})
                resort = by_id.get(tags.get("resort_id"))
                metric = tags.get("metric")
                if resort is not None and metric:
                    try:
                        resort[metric] = int(float(tags.get("value")))
                    except (TypeError, ValueError):
                        resort[metric] = None
            timestamp = payload.get("osm3s", {}).get("timestamp_osm_base", "")
            for resort in batch:
                resort["osm_data_timestamp"] = timestamp
                resort["osm_radius_km"] = 6
                for metric, _, _ in OSM_METRICS:
                    resort.setdefault(metric, None)
            print(
                f"OpenStreetMap: {min(start + 4, len(resorts))}/{len(resorts)}",
                flush=True,
            )
        except (ValueError, urllib.error.URLError, RuntimeError) as error:
            for resort in batch:
                resort["osm_error"] = str(error)
                resort["osm_radius_km"] = 6
                for metric, _, _ in OSM_METRICS:
                    resort[metric] = None
            print(f"OpenStreetMap batch failed: {error}", flush=True)
        time.sleep(0.8)

    for resort in resorts:
        neighbors = []
        for other in resorts:
            if other["resort_id"] == resort["resort_id"]:
                continue
            distance = haversine_km(
                resort["latitude"],
                resort["longitude"],
                other["latitude"],
                other["longitude"],
            )
            if distance < 12:
                neighbors.append((distance, other["name_ru"]))
        neighbors.sort()
        resort["osm_overlap_warning"] = (
            "Possible overlap with "
            + ", ".join(f"{name} ({distance:.1f} km)" for distance, name in neighbors)
            if neighbors
            else ""
        )


def ski_season(date_value: str) -> str:
    year = int(date_value[:4])
    month = int(date_value[5:7])
    return f"{year}-{str(year + 1)[-2:]}" if month >= 11 else f"{year - 1}-{str(year)[-2:]}"


def collect_weather(
    session: HttpClient, resorts: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    print("Open-Meteo: starting batches", flush=True)
    weather_daily = []
    for start in range(0, len(resorts), 8):
        batch = resorts[start : start + 8]
        params = {
            "latitude": ",".join(str(resort["latitude"]) for resort in batch),
            "longitude": ",".join(str(resort["longitude"]) for resort in batch),
            "start_date": WEATHER_START,
            "end_date": WEATHER_END,
            "daily": (
                "temperature_2m_mean,temperature_2m_min,temperature_2m_max,"
                "precipitation_sum,snowfall_sum"
            ),
            "timezone": "auto",
            "models": "era5_land",
        }
        try:
            payload_raw = request_with_retry(
                session,
                "GET",
                OPEN_METEO_ENDPOINT,
                params=params,
                timeout=180,
            ).json()
            payload = payload_raw if isinstance(payload_raw, list) else [payload_raw]
            for resort, item in zip(batch, payload, strict=False):
                resort["open_meteo_elevation_m"] = item.get("elevation")
                resort["open_meteo_timezone"] = item.get("timezone", "")
                daily = item.get("daily", {})
                for index, date_value in enumerate(daily.get("time", [])):
                    weather_daily.append(
                        {
                            "resort_id": resort["resort_id"],
                            "date": date_value,
                            "ski_season": ski_season(date_value),
                            "month": int(date_value[5:7]),
                            "temperature_mean_c": value_at(
                                daily, "temperature_2m_mean", index
                            ),
                            "temperature_min_c": value_at(
                                daily, "temperature_2m_min", index
                            ),
                            "temperature_max_c": value_at(
                                daily, "temperature_2m_max", index
                            ),
                            "precipitation_mm": value_at(
                                daily, "precipitation_sum", index
                            ),
                            "snowfall_cm": value_at(daily, "snowfall_sum", index),
                        }
                    )
            print(
                f"Open-Meteo: {min(start + 8, len(resorts))}/{len(resorts)}",
                flush=True,
            )
        except (ValueError, urllib.error.URLError, RuntimeError) as error:
            for resort in batch:
                resort["weather_error"] = str(error)
            print(f"Open-Meteo batch failed: {error}", flush=True)
        time.sleep(0.5)
    return weather_daily


def value_at(values: dict[str, Any], field: str, index: int) -> Any:
    items = values.get(field) or []
    return items[index] if index < len(items) else None


def mean(values: Iterable[Any]) -> float | None:
    valid = [float(value) for value in values if isinstance(value, (int, float))]
    return fmean(valid) if valid else None


def total(values: Iterable[Any]) -> float | None:
    valid = [float(value) for value in values if isinstance(value, (int, float))]
    return sum(valid) if valid else None


def aggregate_weather(
    resorts: list[dict[str, Any]], weather_daily: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_resort: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in weather_daily:
        by_resort[row["resort_id"]].append(row)

    weather_monthly = []
    for resort in resorts:
        rows = by_resort[resort["resort_id"]]
        season_ids = sorted({row["ski_season"] for row in rows})
        season_snow = [
            total(row["snowfall_cm"] for row in rows if row["ski_season"] == season)
            for season in season_ids
        ]
        season_freeze = [
            sum(
                1
                for row in rows
                if row["ski_season"] == season
                and isinstance(row["temperature_mean_c"], (int, float))
                and row["temperature_mean_c"] < 0
            )
            for season in season_ids
        ]
        season_snow_days = [
            sum(
                1
                for row in rows
                if row["ski_season"] == season
                and isinstance(row["snowfall_cm"], (int, float))
                and row["snowfall_cm"] >= 0.1
            )
            for season in season_ids
        ]
        resort["winter_mean_temperature_c"] = mean(
            row["temperature_mean_c"] for row in rows
        )
        resort["avg_snowfall_cm_per_season"] = mean(season_snow)
        resort["avg_freezing_days_per_season"] = mean(season_freeze)
        resort["avg_snow_days_per_season"] = mean(season_snow_days)
        valid_snow = [
            row["snowfall_cm"]
            for row in rows
            if isinstance(row["snowfall_cm"], (int, float))
        ]
        resort["max_daily_snowfall_cm"] = max(valid_snow) if valid_snow else None
        resort["weather_period_start"] = WEATHER_START
        resort["weather_period_end"] = WEATHER_END

        for month in (11, 12, 1, 2, 3, 4):
            month_rows = [row for row in rows if row["month"] == month]
            years = sorted({row["date"][:4] for row in month_rows})
            groups = [
                [row for row in month_rows if row["date"].startswith(year)]
                for year in years
            ]
            weather_monthly.append(
                {
                    "resort_id": resort["resort_id"],
                    "month": month,
                    "month_name_ru": MONTH_NAMES_RU[month],
                    "years_count": len(years),
                    "mean_temperature_c": mean(
                        row["temperature_mean_c"] for row in month_rows
                    ),
                    "avg_monthly_precipitation_mm": mean(
                        total(row["precipitation_mm"] for row in group)
                        for group in groups
                    ),
                    "avg_monthly_snowfall_cm": mean(
                        total(row["snowfall_cm"] for row in group) for group in groups
                    ),
                    "avg_freezing_days": mean(
                        sum(
                            1
                            for row in group
                            if isinstance(row["temperature_mean_c"], (int, float))
                            and row["temperature_mean_c"] < 0
                        )
                        for group in groups
                    ),
                    "avg_snow_days": mean(
                        sum(
                            1
                            for row in group
                            if isinstance(row["snowfall_cm"], (int, float))
                            and row["snowfall_cm"] >= 0.1
                        )
                        for group in groups
                    ),
                }
            )
    return weather_monthly


def collect_airports(
    session: HttpClient, resorts: list[dict[str, Any]]
) -> None:
    print("OurAirports: downloading airport reference", flush=True)
    try:
        response = request_with_retry(
            session, "GET", OURAIRPORTS_URL, timeout=120
        )
        airport_rows = [
            row
            for row in csv.DictReader(io.StringIO(response.text))
            if row["iso_country"] == "RU"
            and row["scheduled_service"] == "yes"
            and row["iata_code"]
            and row["type"] in {"large_airport", "medium_airport"}
        ]
        for resort in resorts:
            nearest = min(
                airport_rows,
                key=lambda airport: haversine_km(
                    resort["latitude"],
                    resort["longitude"],
                    float(airport["latitude_deg"]),
                    float(airport["longitude_deg"]),
                ),
            )
            distance = haversine_km(
                resort["latitude"],
                resort["longitude"],
                float(nearest["latitude_deg"]),
                float(nearest["longitude_deg"]),
            )
            resort["nearest_scheduled_airport"] = nearest["name"]
            resort["nearest_airport_iata"] = nearest["iata_code"]
            resort["nearest_airport_distance_km"] = round(distance, 1)
            resort["nearest_airport_municipality"] = nearest["municipality"]
        print(
            f"OurAirports: {len(airport_rows)} Russian scheduled medium/large airports",
            flush=True,
        )
    except (ValueError, urllib.error.URLError, RuntimeError) as error:
        for resort in resorts:
            resort["airport_error"] = str(error)
        print(f"OurAirports failed: {error}", flush=True)


def add_quality_fields(
    resorts: list[dict[str, Any]], collected_at: str
) -> None:
    for resort in resorts:
        required = [
            resort.get("latitude"),
            resort.get("longitude"),
            resort.get("coordinate_source_url"),
            resort.get("piste_segments_6km"),
            resort.get("lift_lines_6km"),
            resort.get("lodging_objects_6km"),
            resort.get("food_objects_6km"),
            resort.get("open_meteo_elevation_m"),
            resort.get("winter_mean_temperature_c"),
            resort.get("avg_snowfall_cm_per_season"),
            resort.get("nearest_airport_distance_km"),
        ]
        resort["completeness_pct"] = sum(
            value not in (None, "") for value in required
        ) / len(required)
        flags = []
        if resort.get("coordinate_quality") == "manual_review_required":
            flags.append("check coordinates")
        if resort.get("coordinate_quality") == "nearby_feature_proxy":
            flags.append("coordinates are proxy")
        if resort.get("osm_overlap_warning"):
            flags.append("OSM radii overlap")
        if any(key.endswith("_error") and value for key, value in resort.items()):
            flags.append("source error")
        resort["quality_flags"] = "; ".join(flags)
        resort["collected_at"] = collected_at


def source_catalog(collected_at: str) -> list[dict[str, str]]:
    return [
        {
            "source_id": "SRC_WIKIPEDIA",
            "name": "Русская Википедия и Wikidata",
            "url": WIKIPEDIA_ENDPOINT,
            "documentation": "https://www.mediawiki.org/wiki/API:Main_page",
            "data_used": "Координаты, название страницы, Wikidata ID",
            "access": "Без токена, MediaWiki Action API",
            "method": "Поиск страницы по названию и выбор координат рядом с контрольной точкой",
            "collected_at": collected_at,
        },
        {
            "source_id": "SRC_OSM",
            "name": "OpenStreetMap через Overpass API",
            "url": OVERPASS_ENDPOINT,
            "documentation": "https://wiki.openstreetmap.org/wiki/Overpass_API",
            "data_used": "Трассы, подъёмники и инфраструктура в радиусе 6 км",
            "access": "Без токена на публичном сервере; соблюдать лимиты",
            "method": "Пространственные запросы around:6000 по координате курорта",
            "collected_at": collected_at,
        },
        {
            "source_id": "SRC_OPEN_METEO",
            "name": "Open-Meteo Historical Weather API",
            "url": OPEN_METEO_ENDPOINT,
            "documentation": "https://open-meteo.com/en/docs/historical-weather-api",
            "data_used": "Температура, осадки и снегопад за зимние сезоны",
            "access": "Ключ не требуется для базового доступа",
            "method": "Дневные данные ERA5-Land с агрегацией по сезону и месяцу",
            "collected_at": collected_at,
        },
        {
            "source_id": "SRC_OURAIRPORTS",
            "name": "OurAirports",
            "url": "https://ourairports.com/data/",
            "documentation": "https://ourairports.com/help/data-dictionary.html",
            "data_used": "Координаты, IATA и регулярное сообщение российских аэропортов",
            "access": "Без токена, открытый CSV",
            "method": "Ближайший по прямой medium или large airport",
            "collected_at": collected_at,
        },
    ]


def main() -> None:
    args = parse_args()
    seeds = load_seeds()
    if args.dry_run:
        print(f"Input is valid: {len(seeds)} resorts")
        print(f"Output: {args.output.resolve()}")
        print("Sources: MediaWiki, Overpass, Open-Meteo, OurAirports")
        return

    collected_at = datetime.now(UTC).isoformat()
    session = HttpClient(USER_AGENT)

    resorts = collect_wikipedia(session, seeds)
    collect_osm(session, resorts)
    weather_daily = collect_weather(session, resorts)
    weather_monthly = aggregate_weather(resorts, weather_daily)
    collect_airports(session, resorts)
    add_quality_fields(resorts, collected_at)

    output = {
        "collectedAt": collected_at,
        "resorts": resorts,
        "weatherMonthly": weather_monthly,
        "sources": source_catalog(collected_at),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"Saved {len(resorts)} resorts and {len(weather_monthly)} monthly climate rows "
        f"to {args.output}",
        flush=True,
    )


if __name__ == "__main__":
    main()

"""Тесты применения справочника исправлений (без сети)."""

import csv
import json
import urllib.error

import pytest

import collect_with_overrides as cw


def make_resort(rid, lat, lon):
    return {
        "resort_id": rid, "latitude": lat, "longitude": lon,
        "fallback_lat": lat, "fallback_lon": lon,
        "coordinate_quality": "manual_review_required", "coordinate_source_url": "",
    }


def make_override(rid, lat, lon, status, url=""):
    return {"resort_id": rid, "lat_final": str(lat), "lon_final": str(lon),
            "статус_проверки": status, "operating_status": "operating",
            "ссылка_на_подтверждение": url, "дата_проверки": "2026-10-15"}


def test_override_replaces_coordinates_and_marks_quality():
    resorts = [make_resort("R1", 55.0, 60.0)]
    overrides = {"R1": make_override("R1", 55.1, 60.2, "неточная", "https://example.org")}
    applied = cw.apply_overrides(resorts, overrides)
    r = resorts[0]
    assert applied == 1
    assert (r["latitude"], r["longitude"]) == (55.1, 60.2)
    assert r["wiki_latitude"] == 55.0                       # прежнее значение сохранено
    assert r["coordinate_quality"] == "manually_verified"
    assert r["coordinate_source_url"] == "https://example.org"
    assert r["coordinate_distance_from_seed_km"] > 0


def test_not_found_keeps_quality():
    resorts = [make_resort("R1", 55.0, 60.0)]
    overrides = {"R1": make_override("R1", 55.0, 60.0, "не найдена")}
    assert cw.apply_overrides(resorts, overrides) == 0
    assert resorts[0]["coordinate_quality"] == "manual_review_required"


def test_missing_resort_in_overrides_raises():
    with pytest.raises(KeyError):
        cw.apply_overrides([make_resort("R1", 55.0, 60.0)], {})


def test_load_overrides_rejects_coordinates_outside_russia(tmp_path):
    path = tmp_path / "o.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["resort_id", "lat_final", "lon_final", "статус_проверки", "operating_status"])
        w.writeheader()
        w.writerow({"resort_id": "R1", "lat_final": "10", "lon_final": "60", "статус_проверки": "подтверждена", "operating_status": "operating"})
    with pytest.raises(ValueError):
        cw.load_overrides(path)


def test_load_overrides_rejects_duplicates(tmp_path):
    path = tmp_path / "o.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["resort_id", "lat_final", "lon_final", "статус_проверки", "operating_status"])
        w.writeheader()
        for _ in range(2):
            w.writerow({"resort_id": "R1", "lat_final": "55", "lon_final": "60", "статус_проверки": "подтверждена", "operating_status": "operating"})
    with pytest.raises(ValueError):
        cw.load_overrides(path)


def test_wiki_lookup_error_is_not_left_as_source_error():
    resort = make_resort("R1", 55.0, 60.0)
    resort["coordinate_error"] = "No suitable coordinate page"
    cw.apply_overrides([resort], {"R1": make_override("R1", 55.1, 60.2, "неточная")})
    assert "coordinate_error" not in resort
    assert not any(k.endswith("_error") and v for k, v in resort.items())
    assert resort["wiki_lookup_note"] == "No suitable coordinate page"


def test_wikipedia_is_retried_after_429():
    calls = []

    def fake_enrich(session, seed):
        calls.append(seed["resort_id"])
        if seed["resort_id"] == "R2" and calls.count("R2") < 3:      # первые два раза отказ
            return {**seed, "coordinate_error": "HTTP 429 Too Many Requests"}
        return {**seed, "latitude": 1.0, "longitude": 2.0}

    original = cw.base.enrich_wikipedia
    cw.base.enrich_wikipedia = fake_enrich
    try:
        result = cw.collect_wikipedia_politely(
            None, [{"resort_id": "R1"}, {"resort_id": "R2"}], pause=0, retry_rounds=3, retry_pause=0)
    finally:
        cw.base.enrich_wikipedia = original
    assert not any(r.get("coordinate_error") for r in result)
    assert calls.count("R2") == 3 and calls.count("R1") == 1


# ---------- устойчивость сбора ----------

def _http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, None)


def _runtime_from(code):
    error = RuntimeError("Request failed after 3 attempts")
    error.__cause__ = _http_error(code)
    return error


def test_retry_after_429_then_cache_hit(tmp_path):
    calls = []

    def original(session, method, url, **kwargs):
        calls.append(url)
        if len(calls) < 3:
            raise _runtime_from(429)
        return cw.base.HttpResponse(b'{"ok": 1}', "utf-8")

    request = cw.make_resilient_request(original, tmp_path, waits=(0.001, 0.001, 0.001))
    first = request(None, "GET", "http://example.org/a", params={"q": 1})
    assert first.json() == {"ok": 1} and len(calls) == 3
    second = request(None, "GET", "http://example.org/a", params={"q": 1})   # из кэша
    assert second.json() == {"ok": 1} and len(calls) == 3


def test_permanent_error_is_not_retried(tmp_path):
    calls = []

    def original(session, method, url, **kwargs):
        calls.append(1)
        raise _runtime_from(404)

    request = cw.make_resilient_request(original, tmp_path, waits=(0.001, 0.001))
    with pytest.raises(RuntimeError):
        request(None, "GET", "http://example.org/b")
    assert len(calls) == 1
    assert list(tmp_path.glob("*.json")) == []          # ошибка в кэш не попала


def test_empty_overpass_answer_is_not_cached(tmp_path):
    bad = json.dumps({"elements": [], "remark": "runtime error: Query timed out"}).encode()
    good = json.dumps({"elements": [{"tags": {}}]}).encode()
    answers = [bad, good]

    def original(session, method, url, **kwargs):
        return cw.base.HttpResponse(answers.pop(0), "utf-8")

    request = cw.make_resilient_request(original, tmp_path, waits=(0.001,))
    response = request(None, "POST", cw.base.OVERPASS_ENDPOINT, data={"data": "q"})
    assert response.json()["elements"]
    assert len(list(tmp_path.glob("*.json"))) == 1       # закэширован только удачный ответ


def test_missing_snowfall_is_an_error_not_zero_snow_days():
    daily = {"time": ["2024-01-01", "2024-01-02"], "temperature_2m_mean": [-5.0, -6.0],
             "temperature_2m_min": [-8.0, -9.0], "temperature_2m_max": [-2.0, -3.0],
             "precipitation_sum": [0.0, 1.0], "snowfall_sum": [None, None]}
    payload = [{"elevation": 100, "timezone": "UTC", "daily": daily}]

    class FakeResponse:
        def json(self):
            return payload

    original = cw.base.request_with_retry
    cw.base.request_with_retry = lambda *a, **k: FakeResponse()
    try:
        resorts = [{"resort_id": "R1", "latitude": 55.0, "longitude": 60.0}]
        cw.collect_weather_era5(None, resorts, batch_size=4, pause=0)
    finally:
        cw.base.request_with_retry = original
    assert "снегопад" in resorts[0]["weather_error"]
    resorts[0].update({"avg_snowfall_cm_per_season": None, "avg_snow_days_per_season": 0.0, "max_daily_snowfall_cm": None})
    cw.drop_unreliable_snow(resorts, [])
    assert resorts[0]["avg_snow_days_per_season"] is None        # не нулевое значение
    assert resorts[0]["weather_model"] == "ERA5"

"""Тесты полноты данных и флагов качества."""

from resort_ranking.quality import completeness, has_exact_coordinates, quality_flags, REQUIRED_FIELDS


def full_resort(**extra):
    resort = {field: 1 for field in REQUIRED_FIELDS}
    resort.update({"coordinate_quality": "resort_or_complex_page", "operating_status": "operating"})
    resort.update(extra)
    return resort


def test_completeness_counts_filled_fields():
    assert completeness(full_resort()) == 1.0
    assert completeness(full_resort(coordinate_source_url="")) == 10 / 11
    assert completeness(full_resort(avg_snowfall_cm_per_season=None, nearest_airport_distance_km=None)) == 9 / 11


def test_zero_is_a_value_not_a_gap():
    assert completeness(full_resort(piste_segments_6km=0)) == 1.0


def test_flags_match_tz_names():
    resort = full_resort(coordinate_quality="manual_review_required", osm_overlap_warning="overlap")
    assert quality_flags(resort, has_price=True) == ["check coordinates", "OSM radial overlap"]
    assert quality_flags(full_resort(coordinate_quality="nearby_feature_proxy"), True) == ["coordinates are proxy"]


def test_missing_price_and_not_operating_flags():
    flags = quality_flags(full_resort(operating_status="closed"), has_price=False)
    assert flags == ["missing price", "not operating"]


def test_source_error_flag_only_when_error_is_filled():
    assert "source error" in quality_flags(full_resort(osm_error="timeout"), True)
    assert "source error" not in quality_flags(full_resort(osm_error=""), True)
    assert "source error" in quality_flags(full_resort(weather_error="нет снегопада"), True)


def test_clean_resort_has_no_flags():
    assert quality_flags(full_resort(), has_price=True) == []


def test_exact_coordinates_depend_on_mode():
    resort = full_resort(coordinate_quality="manually_verified")
    assert not has_exact_coordinates(resort, "tz")
    assert has_exact_coordinates(resort, "verified")
    assert has_exact_coordinates(full_resort(), "tz")

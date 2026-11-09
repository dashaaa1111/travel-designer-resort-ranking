"""Тесты формул на маленьких примерах с известным ответом."""

import pytest

from resort_ranking.scoring import assign_ranks, clipped_growth_score, clipped_stay_score, minmax


def test_minmax_basic():
    assert minmax([0, 10], 5) == 50.0
    assert minmax([0, 10], 0) == 0.0
    assert minmax([0, 10], 10) == 100.0


def test_minmax_inverse():
    assert minmax([0, 10], 0, inverse=True) == 100.0
    assert minmax([0, 10], 10, inverse=True) == 0.0


def test_minmax_constant_column_gives_50():
    assert minmax([7, 7, 7], 7) == 50.0


def test_minmax_log_is_monotonic():
    values = [0, 10, 1000]
    assert minmax(values, 10, log=True) < minmax(values, 1000, log=True)
    assert minmax(values, 1000, log=True) == pytest.approx(100.0)


def test_growth_score():
    assert clipped_growth_score(None) == 50.0
    assert clipped_growth_score(0.0) == 50.0
    assert clipped_growth_score(0.25) == 100.0
    assert clipped_growth_score(-0.25) == 0.0
    assert clipped_growth_score(1.0) == 100.0   # обрезается сверху
    assert clipped_growth_score(-1.0) == 0.0    # обрезается снизу


def test_stay_score():
    assert clipped_stay_score(None) == 50.0
    assert clipped_stay_score(1.0) == 0.0
    assert clipped_stay_score(4.0) == 50.0
    assert clipped_stay_score(7.0) == 100.0
    assert clipped_stay_score(20.0) == 100.0


def test_ranks_with_ties():
    assert assign_ranks([10.0, 10.0, 5.0]) == [1, 1, 3]
    assert assign_ranks([3.0, 2.0, 1.0]) == [1, 2, 3]


def test_minmax_never_leaves_0_100_range():
    # на этих данных обычный расчёт даёт 100.00000000000001
    values = [0, 3, 82 + 259, 341]
    for value in values:
        assert 0.0 <= minmax(values, value, log=True) <= 100.0
        assert 0.0 <= minmax(values, value, log=True, inverse=True) <= 100.0

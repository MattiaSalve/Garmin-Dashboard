from datetime import date

from garmin_dashboards.fitness import ATL_DAYS, CTL_DAYS, fitness_series


def test_empty():
    assert fitness_series({}) == []


def test_single_day_and_decay():
    d0 = date(2026, 1, 1)
    out = fitness_series({d0: 100.0}, end=date(2026, 1, 3))
    assert len(out) == 3
    assert out[0]["fitness"] == 100 / CTL_DAYS
    assert out[0]["fatigue"] == 100 / ATL_DAYS
    # Fatigue rises faster than fitness, then both decay on rest days.
    assert out[0]["fatigue"] > out[0]["fitness"]
    assert out[2]["fatigue"] < out[0]["fatigue"]
    # Form on day 1 uses day 0's values (both zero) and is negative afterwards.
    assert out[0]["form"] == 0
    assert out[1]["form"] < 0

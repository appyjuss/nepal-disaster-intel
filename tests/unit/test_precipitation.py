from datetime import date, timedelta

import pytest

from ndip.domain.precipitation import DailyRainfall, antecedent_index, summarise

EVENT = date(2026, 8, 26)

# The measured Trishuli series, 2026-08-15..2026-08-26 (Open-Meteo ERA5).
TRISHULI = [
    (date(2026, 8, 15), 6.1),
    (date(2026, 8, 16), 19.0),
    (date(2026, 8, 17), 3.7),
    (date(2026, 8, 18), 18.2),
    (date(2026, 8, 19), 3.4),
    (date(2026, 8, 20), 5.5),
    (date(2026, 8, 21), 3.6),
    (date(2026, 8, 22), 14.7),
    (date(2026, 8, 23), 6.5),
    (date(2026, 8, 24), 17.7),
    (date(2026, 8, 25), 5.9),
    (date(2026, 8, 26), 2.9),
]
SERIES = [DailyRainfall(on=d, precipitation_mm=mm) for d, mm in TRISHULI]


def test_trishuli_reads_as_saturation_not_cloudburst():
    """The finding that shaped V1: a wet fortnight, not a wet day."""
    ctx = summarise(SERIES, EVENT)
    assert ctx.on_event_day_mm == pytest.approx(2.9)
    assert ctx.window_totals_mm[7] == pytest.approx(57.3)
    assert ctx.window_totals_mm[14] == pytest.approx(104.3)
    assert ctx.is_saturation_pattern is True
    assert ctx.is_cloudburst_pattern is False


def test_a_true_cloudburst_is_classified_as_one():
    series = [
        DailyRainfall(on=EVENT - timedelta(days=i), precipitation_mm=1.0) for i in range(1, 15)
    ]
    series.append(DailyRainfall(on=EVENT, precipitation_mm=200.0))
    ctx = summarise(series, EVENT)
    assert ctx.is_cloudburst_pattern is True
    assert ctx.is_saturation_pattern is False


def test_decay_weights_recent_rain_above_old_rain():
    recent = [DailyRainfall(on=EVENT - timedelta(days=1), precipitation_mm=100.0)]
    old = [DailyRainfall(on=EVENT - timedelta(days=25), precipitation_mm=100.0)]
    assert antecedent_index(recent, EVENT) > antecedent_index(old, EVENT) * 5


def test_event_day_rain_is_excluded_from_the_antecedent_index():
    """API measures what the ground was like *before* the trigger."""
    assert antecedent_index([DailyRainfall(on=EVENT, precipitation_mm=500.0)], EVENT) == 0.0


@pytest.mark.parametrize("k", [0.0, 1.0, 1.5, -0.1])
def test_invalid_recession_constant_rejected(k):
    with pytest.raises(ValueError):
        antecedent_index(SERIES, EVENT, k=k)


def test_negative_rainfall_rejected():
    with pytest.raises(ValueError):
        DailyRainfall(on=EVENT, precipitation_mm=-1.0)

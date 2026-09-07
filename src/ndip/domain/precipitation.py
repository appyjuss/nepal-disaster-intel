"""Antecedent rainfall. Pure arithmetic over a daily series."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

# Standard recession constant for the antecedent precipitation index. 0.9 means
# rain from 7 days ago still contributes ~48% of its depth to today's wetness.
DEFAULT_RECESSION_K = 0.9


@dataclass(frozen=True, slots=True)
class DailyRainfall:
    on: date
    precipitation_mm: float

    def __post_init__(self) -> None:
        if self.precipitation_mm < 0:
            raise ValueError("precipitation cannot be negative")


@dataclass(frozen=True, slots=True)
class RainfallContext:
    """What the rain was doing around an event. Context, never measurement —
    the source reanalysis is far coarser than the terrain."""

    event_date: date
    on_event_day_mm: float
    window_totals_mm: dict[int, float]
    antecedent_index_mm: float
    max_daily_mm: float
    max_daily_on: date | None

    @property
    def is_cloudburst_pattern(self) -> bool:
        """Event-day rain dominates the fortnight — a short, intense trigger."""
        fortnight = self.window_totals_mm.get(14, 0.0)
        return fortnight > 0 and (self.on_event_day_mm / fortnight) >= 0.4

    @property
    def is_saturation_pattern(self) -> bool:
        """Sustained antecedent wetness with no event-day spike."""
        return not self.is_cloudburst_pattern and self.antecedent_index_mm >= 40.0


def antecedent_index(
    series: list[DailyRainfall],
    as_of: date,
    lookback_days: int = 30,
    k: float = DEFAULT_RECESSION_K,
) -> float:
    """Decayed sum of rainfall in the `lookback_days` strictly before `as_of`.

    A plain window sum treats rain 30 days ago as equal to rain yesterday. The
    decay is what makes this track soil wetness rather than just wet weather.
    """
    if not 0.0 < k < 1.0:
        raise ValueError("recession constant must be in (0, 1)")
    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive")

    by_date = {d.on: d.precipitation_mm for d in series}
    total = 0.0
    for offset in range(1, lookback_days + 1):
        depth = by_date.get(as_of - timedelta(days=offset))
        if depth is not None:
            total += (k**offset) * depth
    return total


def summarise(
    series: list[DailyRainfall],
    event_date: date,
    windows: tuple[int, ...] = (7, 14, 30),
    k: float = DEFAULT_RECESSION_K,
) -> RainfallContext:
    by_date = {d.on: d.precipitation_mm for d in series}

    window_totals: dict[int, float] = {}
    for days in windows:
        window_totals[days] = sum(
            by_date.get(event_date - timedelta(days=offset), 0.0) for offset in range(1, days + 1)
        )

    peak = max(series, key=lambda d: d.precipitation_mm, default=None)
    return RainfallContext(
        event_date=event_date,
        on_event_day_mm=by_date.get(event_date, 0.0),
        window_totals_mm=window_totals,
        antecedent_index_mm=antecedent_index(series, event_date, max(windows), k),
        max_daily_mm=peak.precipitation_mm if peak else 0.0,
        max_daily_on=peak.on if peak else None,
    )

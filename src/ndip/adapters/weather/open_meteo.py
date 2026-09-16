"""Open-Meteo ERA5 archive. Free, no key, ~25 km grid.

The coarse grid is the reason every output built on this must carry the
'context, not measurement' caveat — see docs/contract-v1.md.
"""

from __future__ import annotations

from datetime import date

import structlog

from ndip.adapters.http import build_client, get_json
from ndip.domain.geometry import BBox
from ndip.domain.precipitation import DailyRainfall

log = structlog.get_logger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
SOURCE_ID = "open-meteo/era5"
NOMINAL_GRID_KM = 25.0


def fetch_daily_rainfall(
    aoi: BBox, start: date, end: date, *, timezone: str = "Asia/Kathmandu"
) -> list[DailyRainfall]:
    """Daily precipitation at the AOI centroid for [start, end]."""
    if start > end:
        raise ValueError(f"start {start} is after end {end}")

    lon, lat = aoi.centroid
    with build_client() as client:
        payload = get_json(
            client,
            ARCHIVE_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "daily": "precipitation_sum",
                "timezone": timezone,
            },
        )

    daily = payload.get("daily") or {}
    days = daily.get("time") or []
    depths = daily.get("precipitation_sum") or []
    if len(days) != len(depths):
        raise ValueError(f"open-meteo returned {len(days)} dates and {len(depths)} values")

    series = [
        DailyRainfall(on=date.fromisoformat(day), precipitation_mm=float(depth))
        for day, depth in zip(days, depths, strict=True)
        if depth is not None
    ]
    log.info("weather.fetch.complete", days=len(series), lat=lat, lon=lon, source=SOURCE_ID)
    return series

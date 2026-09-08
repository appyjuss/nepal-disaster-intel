"""The conditions a changed region sat in: what the weather had been doing, and
what the ground was like before anything moved."""

from __future__ import annotations

from dataclasses import dataclass

from ndip.domain.precipitation import RainfallContext
from ndip.domain.terrain import cardinal


@dataclass(frozen=True, slots=True)
class TerrainContext:
    elevation_m: float
    slope_deg: float
    aspect_deg: float
    distance_to_drainage_m: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.slope_deg <= 90.0:
            raise ValueError(f"slope out of range: {self.slope_deg}")
        if self.distance_to_drainage_m < 0:
            raise ValueError("distance to drainage cannot be negative")

    @property
    def aspect_cardinal(self) -> str:
        return cardinal(self.aspect_deg)


@dataclass(frozen=True, slots=True)
class EventContext:
    """One region's conditions. Rainfall is shared across the area — the reanalysis
    grid is far coarser than the gap between two polygons — while terrain is measured
    inside each one."""

    polygon_id: str
    terrain: TerrainContext
    rainfall: RainfallContext

    @property
    def rainfall_pattern(self) -> str:
        if self.rainfall.is_cloudburst_pattern:
            return "cloudburst"
        if self.rainfall.is_saturation_pattern:
            return "saturation"
        return "inconclusive"

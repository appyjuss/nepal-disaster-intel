"""What sat inside or beside a changed region.

Exposure is not damage. Nothing here claims a road was cut or a building fell —
only that it stood within the detected area, or within reach of it.
"""

from __future__ import annotations

from dataclasses import dataclass

# Far enough to catch what a slope failure or flood can reach without being
# reported as inside it. Runout commonly extends a few hundred metres beyond a
# detected scar, and radar cannot see thin debris tails at all.
DEFAULT_BUFFER_M = 500.0


@dataclass(frozen=True, slots=True)
class FeatureCount:
    within: int
    in_buffer: int

    def __post_init__(self) -> None:
        if self.within < 0 or self.in_buffer < 0:
            raise ValueError("counts cannot be negative")
        if self.in_buffer < self.within:
            raise ValueError(
                f"buffer count {self.in_buffer} is below the count inside the polygon "
                f"{self.within}; the buffer contains the polygon"
            )

    @property
    def nearby_only(self) -> int:
        """In the buffer but not inside the changed area."""
        return self.in_buffer - self.within


@dataclass(frozen=True, slots=True)
class Exposure:
    polygon_id: str
    roads: FeatureCount
    bridges: FeatureCount
    buildings: FeatureCount
    population_within: float
    population_in_buffer: float
    buffer_m: float

    def __post_init__(self) -> None:
        if self.population_within < 0 or self.population_in_buffer < 0:
            raise ValueError("population cannot be negative")
        if self.buffer_m <= 0:
            raise ValueError("buffer must be positive")

    @property
    def is_empty(self) -> bool:
        """Nothing mapped stood inside or beside this region."""
        return (
            self.buildings.in_buffer == 0
            and self.roads.in_buffer == 0
            and self.population_in_buffer <= 0
        )

    @property
    def touches_a_bridge(self) -> bool:
        """Bridges are called out separately because on a river corridor a single
        bridge decides whether a valley stays connected."""
        return self.bridges.in_buffer > 0

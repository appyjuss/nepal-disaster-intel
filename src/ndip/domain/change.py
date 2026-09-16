"""Surface-change detections and the rules that decide whether to trust one.

The dominant false-positive source for SAR change detection in mountainous terrain
is geometric: layover and shadow move with viewing geometry, real ground change does
not. So agreement across orbit geometries is the confidence signal. See ADR 0001.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# Below this, a polygon is indistinguishable from SAR speckle at 10 m pixel spacing.
DEFAULT_MIN_MAPPING_UNIT_M2 = 2_000.0

# Slopes below this are alluvial/valley-floor: change there is more likely flood,
# river migration or harvest than slope failure. Not a rejection, a classification.
FLAT_TERRAIN_MAX_SLOPE_DEG = 8.0


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    REJECTED = "rejected"


class ChangeClass(StrEnum):
    """What the change is *consistent with*. Never an assertion of cause."""

    SLOPE_FAILURE_LIKE = "slope_failure_like"
    VALLEY_FLOOR_LIKE = "valley_floor_like"
    UNCLASSIFIED = "unclassified"


@dataclass(frozen=True, slots=True)
class ChangePolygon:
    """One detected surface-change region.

    `attributes` is an open bag for values the core only forwards to outputs
    (tile ids, source item ids, display labels). Values the confidence rules
    compute on get a declared field.
    """

    polygon_id: str
    area_m2: float
    mean_slope_deg: float
    backscatter_delta_db: float
    detected_in: frozenset[str]
    attributes: dict[str, object]

    def __post_init__(self) -> None:
        if self.area_m2 <= 0:
            raise ValueError("area_m2 must be positive")
        if not 0.0 <= self.mean_slope_deg <= 90.0:
            raise ValueError(f"mean_slope_deg out of range: {self.mean_slope_deg}")
        if not self.detected_in:
            raise ValueError("detected_in must name at least one geometry")

    @property
    def geometry_agreement(self) -> bool:
        """Seen from both an ascending and a descending look direction."""
        return {"asc", "desc"} <= self.detected_in

    def classify(self) -> ChangeClass:
        if self.mean_slope_deg <= FLAT_TERRAIN_MAX_SLOPE_DEG:
            return ChangeClass.VALLEY_FLOOR_LIKE
        return ChangeClass.SLOPE_FAILURE_LIKE

    def confidence(
        self, min_mapping_unit_m2: float = DEFAULT_MIN_MAPPING_UNIT_M2
    ) -> Confidence:
        if self.area_m2 < min_mapping_unit_m2:
            return Confidence.REJECTED
        if self.geometry_agreement:
            return Confidence.HIGH
        # A single geometry can still be convincing if the radiometric change is large,
        # but it never reaches HIGH — that grade is reserved for geometric corroboration.
        if abs(self.backscatter_delta_db) >= 3.0:
            return Confidence.MEDIUM
        return Confidence.LOW


def reportable(
    polygons: list[ChangePolygon],
    min_mapping_unit_m2: float = DEFAULT_MIN_MAPPING_UNIT_M2,
) -> list[ChangePolygon]:
    """Polygons that clear the V1 acceptance bar, largest first.

    Rejected polygons are dropped here but are still written to the silver layer —
    the pipeline keeps its own false positives so the threshold stays auditable.
    """
    kept = [
        p
        for p in polygons
        if p.confidence(min_mapping_unit_m2) is not Confidence.REJECTED
    ]
    return sorted(kept, key=lambda p: p.area_m2, reverse=True)

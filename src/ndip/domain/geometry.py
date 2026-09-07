"""Pure geometry value objects. No I/O, no GDAL, no frameworks."""

from __future__ import annotations

from dataclasses import dataclass

WGS84 = "EPSG:4326"


@dataclass(frozen=True, slots=True)
class BBox:
    """An axis-aligned bounding box in EPSG:4326, degrees."""

    west: float
    south: float
    east: float
    north: float

    def __post_init__(self) -> None:
        if not -180.0 <= self.west < self.east <= 180.0:
            raise ValueError(f"longitude out of order or range: {self.west}..{self.east}")
        if not -90.0 <= self.south < self.north <= 90.0:
            raise ValueError(f"latitude out of order or range: {self.south}..{self.north}")

    @classmethod
    def parse(cls, text: str) -> BBox:
        """Parse 'w,s,e,n'. Used at adapter boundaries on untrusted input."""
        parts = [p.strip() for p in text.split(",")]
        if len(parts) != 4:
            raise ValueError(f"expected 4 comma-separated numbers, got {len(parts)}: {text!r}")
        try:
            west, south, east, north = (float(p) for p in parts)
        except ValueError as exc:
            raise ValueError(f"non-numeric bbox component in {text!r}") from exc
        return cls(west, south, east, north)

    def as_list(self) -> list[float]:
        return [self.west, self.south, self.east, self.north]

    @property
    def centroid(self) -> tuple[float, float]:
        """(lon, lat) — the point weather adapters sample."""
        return ((self.west + self.east) / 2.0, (self.south + self.north) / 2.0)

    def intersects(self, other: BBox) -> bool:
        return not (
            self.east <= other.west
            or other.east <= self.west
            or self.north <= other.south
            or other.north <= self.south
        )

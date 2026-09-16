"""Use case: assemble the conditions around each corroborated change.

Everything here comes from data already in the lakehouse or already fetched for
detection. Nothing new is downloaded except the drainage network.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import structlog
from pyproj import Transformer
from shapely import wkb as shapely_wkb
from shapely import wkt as shapely_wkt
from shapely.ops import transform as shapely_transform
from shapely.strtree import STRtree

from ndip.adapters.raster.vectorize import zonal_mean, zonal_values
from ndip.adapters.terrain.dem import TerrainGrid
from ndip.domain.context import EventContext, TerrainContext
from ndip.domain.precipitation import DailyRainfall, summarise
from ndip.domain.terrain import circular_mean_degrees

log = structlog.get_logger(__name__)

# Beyond this the exact figure stops carrying information: everything is simply
# far from a channel. Recorded as the cap rather than left missing.
MAX_DRAINAGE_SEARCH_M = 5_000.0


@dataclass(frozen=True, slots=True)
class ContextResult:
    event_id: str
    contexts: list[EventContext]
    polygons: dict[str, dict]

    def summary_lines(self) -> list[str]:
        if not self.contexts:
            return [f"No context for {self.event_id}"]
        elev = [c.terrain.elevation_m for c in self.contexts]
        dist = [c.terrain.distance_to_drainage_m for c in self.contexts]
        near = sum(1 for d in dist if d <= 100.0)
        r = self.contexts[0].rainfall
        return [
            f"Event context for {self.event_id}",
            f"  {len(self.contexts)} regions",
            f"  elevation      {min(elev):.0f} to {max(elev):.0f} m",
            f"  drainage       median {np.median(dist):.0f} m, "
            f"{near} region(s) within 100 m of a channel",
            "  aspect         "
            + ", ".join(
                f"{k}:{v}"
                for k, v in sorted(
                    _tally(c.terrain.aspect_cardinal for c in self.contexts).items(),
                    key=lambda kv: -kv[1],
                )
            ),
            f"  rainfall       event day {r.on_event_day_mm:.1f} mm, "
            f"7d {r.window_totals_mm.get(7, 0):.0f}, 14d {r.window_totals_mm.get(14, 0):.0f}, "
            f"30d {r.window_totals_mm.get(30, 0):.0f}, index {r.antecedent_index_mm:.0f} mm "
            f"-> {self.contexts[0].rainfall_pattern}",
        ]


def _tally(items) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return out


def build_context(
    event_id: str,
    event_date: date,
    polygons: list[dict],
    *,
    grid: TerrainGrid,
    rainfall_series: list[DailyRainfall],
    drainage_wkb: list[bytes],
    projected_epsg: int,
) -> ContextResult:
    if not polygons:
        raise LookupError(f"no polygons to describe for {event_id}")

    rainfall = summarise(rainfall_series, event_date)
    to_metres = Transformer.from_crs(
        "EPSG:4326", f"EPSG:{projected_epsg}", always_xy=True
    ).transform

    channels = [shapely_wkb.loads(bytes(w)) for w in drainage_wkb]
    projected = [shapely_transform(to_metres, c) for c in channels]
    tree = STRtree(projected) if projected else None

    contexts: list[EventContext] = []
    for record in polygons:
        # Silver stores geometry in WGS84 while the terrain grid is projected. Every
        # measurement below happens in the grid's own coordinates; rasterising a
        # lon/lat polygon against a metre grid selects no pixels at all.
        here = shapely_transform(to_metres, shapely_wkt.loads(record["geometry_wkt"]))

        elevation = zonal_mean(grid.elevation, here, grid.transform)
        slope = zonal_mean(grid.slope_deg, here, grid.transform)
        if not np.isfinite(elevation) or not np.isfinite(slope):
            # Substituting zero here would report a Himalayan slope as flat ground at
            # sea level, which is a plausible-looking number and completely wrong.
            raise ValueError(
                f"no terrain pixels under polygon {record['polygon_id']}; "
                f"grid is {grid.crs} and the polygon was projected to EPSG:{projected_epsg}"
            )
        # Aspect is a compass bearing, so it cannot be averaged arithmetically.
        aspect = circular_mean_degrees(zonal_values(grid.aspect_deg, here, grid.transform))

        distance = MAX_DRAINAGE_SEARCH_M
        if tree is not None:
            nearest = tree.nearest(here)
            if nearest is not None:
                distance = min(float(here.distance(projected[int(nearest)])), MAX_DRAINAGE_SEARCH_M)

        contexts.append(
            EventContext(
                polygon_id=record["polygon_id"],
                terrain=TerrainContext(
                    elevation_m=float(elevation),
                    slope_deg=float(np.clip(slope, 0.0, 90.0)),
                    aspect_deg=float(aspect),
                    distance_to_drainage_m=distance,
                ),
                rainfall=rainfall,
            )
        )

    log.info(
        "context.complete",
        event_id=event_id,
        regions=len(contexts),
        drainage_features=len(channels),
    )
    return ContextResult(
        event_id=event_id, contexts=contexts, polygons={p["polygon_id"]: p for p in polygons}
    )

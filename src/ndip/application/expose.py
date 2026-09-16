"""Use case: what stood inside or beside each corroborated change.

Reads silver rather than re-running detection, so exposure can be recomputed
against a newer Overture release without touching a single pixel of imagery.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog
from shapely import wkt as shapely_wkt

from ndip.adapters.osm.overture import OvertureExtract, count_features
from ndip.adapters.population.worldpop import PopulationRaster, population_within
from ndip.adapters.raster.vectorize import buffer_metres
from ndip.domain.exposure import DEFAULT_BUFFER_M, Exposure, FeatureCount

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ExposureResult:
    event_id: str
    exposures: list[Exposure]
    polygons: dict[str, dict]
    buffer_m: float
    overture_release: str
    population_product: str
    population_bias: float

    def summary_lines(self) -> list[str]:
        lines = [
            f"Exposure for {self.event_id}",
            f"  {len(self.exposures)} corroborated regions, {self.buffer_m:.0f} m buffer",
        ]
        if not self.exposures:
            return lines
        empty = sum(1 for e in self.exposures if e.is_empty)
        bridges = sum(1 for e in self.exposures if e.touches_a_bridge)
        people = sum(e.population_in_buffer for e in self.exposures)
        over = (self.population_bias - 1) * 100
        lines += [
            f"  nothing mapped nearby:      {empty}",
            f"  a bridge within the buffer: {bridges}",
            f"  buildings in buffer, total: {sum(e.buildings.in_buffer for e in self.exposures)}",
            f"  roads in buffer, total:     {sum(e.roads.in_buffer for e in self.exposures)}",
            f"  population in buffer, total:{people:>9,.0f}",
            f"    (that product runs ~{over:.0f}% above the national estimate — indicative only)",
        ]
        worst = max(self.exposures, key=lambda e: e.buildings.in_buffer)
        if worst.buildings.in_buffer:
            lines.append(
                f"  most exposed: {worst.polygon_id[-12:]} — "
                f"{worst.buildings.in_buffer} buildings, {worst.roads.in_buffer} roads, "
                f"{worst.bridges.in_buffer} bridges, {worst.population_in_buffer:,.0f} people"
            )
        return lines


def assess_exposure(
    event_id: str,
    polygons: list[dict],
    *,
    extract: OvertureExtract,
    population: PopulationRaster,
    projected_epsg: int,
    buffer_m: float = DEFAULT_BUFFER_M,
) -> ExposureResult:
    if not polygons:
        raise LookupError(f"no polygons to assess for {event_id}")

    shapes = {p["polygon_id"]: shapely_wkt.loads(p["geometry_wkt"]) for p in polygons}
    buffers = {
        pid: buffer_metres(geom, buffer_m, projected_epsg=projected_epsg)
        for pid, geom in shapes.items()
    }

    counts = count_features(extract, {pid: (shapes[pid].wkt, buffers[pid].wkt) for pid in shapes})
    pop_within = population_within(population, shapes)
    pop_buffer = population_within(population, buffers)

    exposures = []
    for pid in shapes:
        c = counts.get(pid, {})
        exposures.append(
            Exposure(
                polygon_id=pid,
                roads=FeatureCount(c.get("roads_within", 0), c.get("roads_in_buffer", 0)),
                bridges=FeatureCount(c.get("bridges_within", 0), c.get("bridges_in_buffer", 0)),
                buildings=FeatureCount(
                    c.get("buildings_within", 0), c.get("buildings_in_buffer", 0)
                ),
                population_within=pop_within.get(pid, 0.0),
                # A polygon's own cells are inside its buffer, so the buffered sum can
                # never be the smaller of the two.
                population_in_buffer=max(pop_buffer.get(pid, 0.0), pop_within.get(pid, 0.0)),
                buffer_m=buffer_m,
            )
        )
    exposures.sort(key=lambda e: e.buildings.in_buffer, reverse=True)

    log.info(
        "exposure.complete",
        event_id=event_id,
        regions=len(exposures),
        with_buildings=sum(1 for e in exposures if e.buildings.in_buffer),
    )
    return ExposureResult(
        event_id=event_id,
        exposures=exposures,
        polygons={p["polygon_id"]: p for p in polygons},
        buffer_m=buffer_m,
        overture_release=extract.release,
        population_product=population.product_id,
        population_bias=population.bias,
    )

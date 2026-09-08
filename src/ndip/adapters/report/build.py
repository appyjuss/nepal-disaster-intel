"""Render the event report from the lakehouse.

The page reads gold, and gold only, for everything it asserts about a region. It
is generated as part of the pipeline rather than by hand, so it cannot drift from
the tables it describes.
"""

from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import structlog
from pyiceberg.catalog import Catalog
from shapely import wkt as shapely_wkt

from ndip.adapters.lakehouse.schemas import (
    CHANGE_POLYGONS,
    EVENT_CONTEXT,
    EXPOSURE,
    RAINFALL_DAILY,
    STAC_ITEMS,
)
from ndip.adapters.population.worldpop import NATIONAL_SUM_BIAS
from ndip.domain.event import DisasterEvent

log = structlog.get_logger(__name__)

TEMPLATE_DIR = Path(__file__).parent
DATA_MARKER = "__NDIP_DATA__"

# Coarse enough to stay a reasonable page weight, fine enough that the valleys read.
HILLSHADE_DEGREES_PER_PIXEL = 0.0008
HILLSHADE_AZIMUTH_DEG = 315.0
HILLSHADE_ALTITUDE_DEG = 45.0
# Geometry simplification tolerance, about fifteen metres.
SIMPLIFY_DEGREES = 0.00015
# Below this a detection is valley floor rather than hillside.
LOWLAND_MAX_ELEVATION_M = 1_500.0


@dataclass(frozen=True, slots=True)
class ReportInputs:
    """Everything the page needs, already read out of the lakehouse."""

    polygons: list[dict]
    total_regions: int
    exposure: dict[str, dict]
    context: dict[str, dict]
    rainfall: list[dict]
    radar: list[dict]
    optical: list[dict]


def read_inputs(catalog: Catalog, event: DisasterEvent) -> ReportInputs:
    def rows(table: str) -> list[dict]:
        return [
            r
            for r in catalog.load_table(table).scan().to_arrow().to_pylist()
            if r.get("event_id") == event.event_id
        ]

    all_polygons = rows(CHANGE_POLYGONS)
    graded = [r for r in all_polygons if r["confidence"] in ("high", "medium")]
    graded.sort(key=lambda r: -r["area_m2"])
    stac = rows(STAC_ITEMS)

    return ReportInputs(
        polygons=graded,
        total_regions=len(all_polygons),
        exposure={r["polygon_id"]: r for r in rows(EXPOSURE)},
        context={r["polygon_id"]: r for r in rows(EVENT_CONTEXT)},
        rainfall=sorted(
            (
                {"d": str(r["observed_on"]), "mm": round(r["precipitation_mm"], 1)}
                for r in rows(RAINFALL_DAILY)
            ),
            key=lambda r: r["d"],
        ),
        radar=sorted(
            (
                {
                    "t": str(r["acquired_at"])[:16],
                    "track": r["relative_orbit"],
                    "orbit": r["orbit_state"],
                }
                for r in stac
                if r["collection"] == "sentinel-1-grd"
            ),
            key=lambda r: r["t"],
        ),
        optical=sorted(
            (
                {"t": str(r["acquired_at"])[:10], "cloud": round(r["cloud_cover"], 1)}
                for r in stac
                if r["collection"] == "sentinel-2-l2a" and r["cloud_cover"] is not None
            ),
            key=lambda r: r["t"],
        ),
    )


def hillshade_png_base64(elevation: np.ndarray, *, pixel_size_m: float = 90.0) -> str:
    """A shaded relief image of the area, for the page to draw detections onto.

    Embedded rather than fetched: a published page cannot reach a tile server, and
    a basemap that silently fails to load leaves the detections floating in space.
    """
    from PIL import Image

    dz_dy, dz_dx = np.gradient(elevation.astype(np.float64), pixel_size_m, pixel_size_m)
    slope = np.arctan(np.hypot(dz_dx, dz_dy))
    aspect = np.arctan2(-dz_dx, dz_dy)
    az, alt = np.radians(HILLSHADE_AZIMUTH_DEG), np.radians(HILLSHADE_ALTITUDE_DEG)
    shade = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)

    low, high = np.nanmin(shade), np.nanmax(shade)
    normalised = np.clip((shade - low) / (high - low), 0.0, 1.0)

    image = Image.fromarray((normalised * 255).astype(np.uint8), mode="L").convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return base64.b64encode(buffer.getvalue()).decode()


def build_payload(
    event: DisasterEvent,
    inputs: ReportInputs,
    *,
    hillshade: str,
    bronze_rows: int,
    threshold_db: float,
    min_area_m2: float,
    band: str,
    resolution_m: float,
    buffer_m: float,
    overture_release: str,
) -> dict:
    polygons = []
    for record in inputs.polygons:
        pid = record["polygon_id"]
        geometry = shapely_wkt.loads(record["geometry_wkt"]).simplify(
            SIMPLIFY_DEGREES, preserve_topology=True
        )
        parts = geometry.geoms if hasattr(geometry, "geoms") else [geometry]
        exposure = inputs.exposure.get(pid, {})
        context = inputs.context.get(pid, {})
        polygons.append(
            {
                "id": pid[-12:],
                "rings": [
                    [[round(x, 5), round(y, 5)] for x, y in part.exterior.coords] for part in parts
                ],
                "ha": round(record["area_m2"] / 1e4, 2),
                "slope": round(record["mean_slope_deg"], 1),
                "db": round(record["backscatter_delta_db"], 1),
                "conf": record["confidence"],
                "cls": record["change_class"],
                "seen": record["detected_in"],
                "lat": round(record["centroid_lat"], 5),
                "lon": round(record["centroid_lon"], 5),
                "elev": _round_or_none(context.get("elevation_m")),
                "drain": _round_or_none(context.get("distance_to_drainage_m")),
                "buildings": exposure.get("buildings_in_buffer"),
                "bridges": exposure.get("bridges_in_buffer"),
                "people": _round_or_none(exposure.get("population_in_buffer")),
            }
        )

    high = [p for p in polygons if p["conf"] == "high"]
    dbs = [p["db"] for p in high]
    lowland = [p for p in high if p["elev"] is not None and p["elev"] <= LOWLAND_MAX_ELEVATION_M]
    highland = [p for p in high if p["elev"] is not None and p["elev"] > LOWLAND_MAX_ELEVATION_M]
    exposures = list(inputs.exposure.values())
    contexts = list(inputs.context.values())
    first_context = contexts[0] if contexts else {}

    facts = {
        "total": inputs.total_regions,
        "graded": len(polygons),
        "high": len(high),
        "tracks": len({r["track"] for r in inputs.radar if r["track"] is not None}),
        "bronze_rows": bronze_rows,
        "threshold_db": threshold_db,
        "mmu_m2": min_area_m2,
        "band": band,
        "resolution_m": resolution_m,
        "rain_event_day": first_context.get("precipitation_event_day_mm", 0.0),
        "rain_30d": first_context.get("precipitation_30d_mm", 0.0),
        "api": first_context.get("antecedent_index_mm", 0.0),
        "pattern": first_context.get("rainfall_pattern", "unclear"),
        "high_all_darkening": bool(dbs) and all(d < 0 for d in dbs),
        "high_db_min": min(dbs, default=0.0),
        "high_db_max": max(dbs, default=0.0),
        "valley_floor": len(lowland),
        "elev_low_min": min((p["elev"] for p in lowland), default=0),
        "elev_low_max": max((p["elev"] for p in lowland), default=0),
        "drain_max_low": max((p["drain"] or 0 for p in lowland), default=0),
        "high_alt": bool(highland),
        "high_alt_elev": highland[0]["elev"] if highland else None,
        "high_alt_slope": highland[0]["slope"] if highland else None,
    }
    if exposures:
        facts["exposure"] = {
            "buildings": sum(e["buildings_in_buffer"] for e in exposures),
            "roads": sum(e["roads_in_buffer"] for e in exposures),
            "bridges": sum(e["bridges_in_buffer"] for e in exposures),
            "people": round(sum(e["population_in_buffer"] for e in exposures)),
            "empty": sum(
                1
                for e in exposures
                if e["buildings_in_buffer"] == 0
                and e["roads_in_buffer"] == 0
                and e["population_in_buffer"] <= 0
            ),
            "with_bridge": sum(1 for e in exposures if e["bridges_in_buffer"] > 0),
            "bias": NATIONAL_SUM_BIAS,
            "release": overture_release,
            "buffer_m": buffer_m,
        }

    return {
        "polygons": polygons,
        "rain": inputs.rainfall,
        "radar": inputs.radar,
        "optical": inputs.optical,
        "aoi": event.aoi.as_list(),
        "hillshade": hillshade,
        "facts": facts,
        "counts": {"total": facts["total"], "high": facts["high"]},
    }


def render(payload: dict, destination: Path) -> Path:
    """Write the self-contained page. No external requests at view time."""
    head = (TEMPLATE_DIR / "_head.html").read_text()
    body = (TEMPLATE_DIR / "_body.html").read_text()
    script = (TEMPLATE_DIR / "_js.html").read_text()
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        head + body + f'<script id="ndip-data">window.__NDIP__={data};</script>\n' + script
    )
    log.info("report.rendered", path=str(destination), bytes=destination.stat().st_size)
    return destination


def _round_or_none(value):
    return None if value is None else round(float(value))

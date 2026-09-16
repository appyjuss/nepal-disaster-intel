"""Silver writers. Interpreted results, with the parameters that produced them."""

from __future__ import annotations

from datetime import UTC, datetime

import pyarrow as pa
import structlog
from pyiceberg.catalog import Catalog
from pyproj import Transformer
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform

from ndip.adapters.lakehouse.catalog import ensure_table
from ndip.adapters.lakehouse.schemas import (
    CHANGE_POLYGONS,
    CHANGE_POLYGONS_KEY,
    CHANGE_POLYGONS_PARTITION,
    CHANGE_POLYGONS_SCHEMA,
)
from ndip.application.detect import DetectionResult

log = structlog.get_logger(__name__)

# Geometry is stored in WGS84 because that is what every downstream consumer
# expects. Areas and slopes are measured in the projected CRS, where a metre is a
# metre, and that CRS is recorded per row so the numbers stay checkable.
STORAGE_CRS = "EPSG:4326"


class SilverWriter:
    def __init__(self, catalog: Catalog) -> None:
        self._catalog = catalog

    def write_change_polygons(
        self,
        result: DetectionResult,
        *,
        threshold_db: float,
        min_mapping_unit_m2: float,
    ) -> int:
        if not result.changes:
            log.info("silver.change_polygons.skipped", reason="no polygons")
            return 0

        table = ensure_table(
            self._catalog, CHANGE_POLYGONS, CHANGE_POLYGONS_SCHEMA, CHANGE_POLYGONS_PARTITION
        )
        source_crs = result.tracks[0].pair.crs
        to_wgs84 = Transformer.from_crs(source_crs, STORAGE_CRS, always_xy=True).transform
        frame_ids = sorted({fid for t in result.tracks for fid in t.pair.frame_ids})
        now = datetime.now(UTC)

        rows = []
        for change in result.changes:
            geometry: BaseGeometry = result.geometries_of[change.polygon_id]
            wgs84 = shapely_transform(to_wgs84, geometry)
            centroid = wgs84.centroid
            rows.append(
                {
                    "event_id": result.event.event_id,
                    "polygon_id": change.polygon_id,
                    "geometry_wkt": wgs84.wkt,
                    "centroid_lon": float(centroid.x),
                    "centroid_lat": float(centroid.y),
                    "area_m2": change.area_m2,
                    "mean_slope_deg": change.mean_slope_deg,
                    "backscatter_delta_db": change.backscatter_delta_db,
                    "detected_in": "+".join(sorted(change.detected_in)),
                    "confidence": change.confidence(min_mapping_unit_m2).value,
                    "change_class": change.classify().value,
                    "crs": source_crs,
                    "resolution_m": float(change.attributes.get("resolution_m", 0.0)),
                    "band": str(change.attributes.get("band", "")),
                    "threshold_db": threshold_db,
                    "min_mapping_unit_m2": min_mapping_unit_m2,
                    "source_frame_ids": ",".join(frame_ids),
                    "detected_at": now,
                }
            )

        df = pa.Table.from_pylist(rows, schema=CHANGE_POLYGONS_SCHEMA.as_arrow())
        outcome = table.upsert(df, join_cols=CHANGE_POLYGONS_KEY)
        log.info(
            "silver.change_polygons.written",
            event_id=result.event.event_id,
            rows=len(rows),
            inserted=outcome.rows_inserted,
            updated=outcome.rows_updated,
        )
        return len(rows)


def read_change_polygons(
    catalog: Catalog, *, event_id: str, confidence: tuple[str, ...] = ("high",)
) -> list[dict]:
    """Silver polygons for one event, as plain records.

    Gold reads silver rather than re-running detection, so exposure can be
    recomputed against a new Overture release without touching any imagery.
    """
    table = catalog.load_table(CHANGE_POLYGONS)
    rows = table.scan().to_arrow().to_pylist()
    kept = [r for r in rows if r["event_id"] == event_id and r["confidence"] in confidence]
    kept.sort(key=lambda r: r["area_m2"], reverse=True)
    log.info(
        "silver.change_polygons.read",
        event_id=event_id,
        confidence=list(confidence),
        rows=len(kept),
    )
    return kept

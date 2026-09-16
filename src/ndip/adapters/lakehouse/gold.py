"""Gold writers. One row per answered question, with its evidence attached."""

from __future__ import annotations

from datetime import UTC, datetime

import pyarrow as pa
import structlog
from pyiceberg.catalog import Catalog

from ndip.adapters.lakehouse.catalog import ensure_table
from ndip.adapters.lakehouse.schemas import (
    EVENT_CONTEXT,
    EVENT_CONTEXT_KEY,
    EVENT_CONTEXT_PARTITION,
    EVENT_CONTEXT_SCHEMA,
    EXPOSURE,
    EXPOSURE_KEY,
    EXPOSURE_PARTITION,
    EXPOSURE_SCHEMA,
)
from ndip.application.context import ContextResult
from ndip.application.expose import ExposureResult

log = structlog.get_logger(__name__)


class GoldWriter:
    def __init__(self, catalog: Catalog) -> None:
        self._catalog = catalog

    def write_exposure(self, result: ExposureResult) -> int:
        if not result.exposures:
            log.info("gold.exposure.skipped", reason="no exposures")
            return 0

        table = ensure_table(self._catalog, EXPOSURE, EXPOSURE_SCHEMA, EXPOSURE_PARTITION)
        now = datetime.now(UTC)
        rows = []
        for e in result.exposures:
            p = result.polygons[e.polygon_id]
            rows.append(
                {
                    "event_id": result.event_id,
                    "polygon_id": e.polygon_id,
                    "area_m2": p["area_m2"],
                    "confidence": p["confidence"],
                    "change_class": p["change_class"],
                    "centroid_lon": p["centroid_lon"],
                    "centroid_lat": p["centroid_lat"],
                    "buffer_m": e.buffer_m,
                    "roads_within": e.roads.within,
                    "roads_in_buffer": e.roads.in_buffer,
                    "bridges_within": e.bridges.within,
                    "bridges_in_buffer": e.bridges.in_buffer,
                    "buildings_within": e.buildings.within,
                    "buildings_in_buffer": e.buildings.in_buffer,
                    "population_within": e.population_within,
                    "population_in_buffer": e.population_in_buffer,
                    "overture_release": result.overture_release,
                    "population_product": result.population_product,
                    "population_bias": result.population_bias,
                    "source_frame_ids": p.get("source_frame_ids", ""),
                    "assessed_at": now,
                }
            )

        df = pa.Table.from_pylist(rows, schema=EXPOSURE_SCHEMA.as_arrow())
        outcome = table.upsert(df, join_cols=EXPOSURE_KEY)
        log.info(
            "gold.exposure.written",
            event_id=result.event_id,
            rows=len(rows),
            inserted=outcome.rows_inserted,
            updated=outcome.rows_updated,
        )
        return len(rows)

    def write_event_context(
        self,
        result: ContextResult,
        *,
        event_date,
        rainfall_source: str,
        rainfall_grid_km: float,
        terrain_source: str,
        drainage_source: str,
    ) -> int:
        if not result.contexts:
            log.info("gold.event_context.skipped", reason="no contexts")
            return 0

        table = ensure_table(
            self._catalog, EVENT_CONTEXT, EVENT_CONTEXT_SCHEMA, EVENT_CONTEXT_PARTITION
        )
        now = datetime.now(UTC)
        rows = []
        for c in result.contexts:
            r = c.rainfall
            aspect = c.terrain.aspect_deg
            rows.append(
                {
                    "event_id": result.event_id,
                    "polygon_id": c.polygon_id,
                    "event_date": event_date,
                    "elevation_m": c.terrain.elevation_m,
                    "slope_deg": c.terrain.slope_deg,
                    # Flat ground has no aspect; storing a number there would invent
                    # a direction the terrain does not have.
                    "aspect_deg": None if aspect != aspect else aspect,
                    "aspect_cardinal": c.terrain.aspect_cardinal,
                    "distance_to_drainage_m": c.terrain.distance_to_drainage_m,
                    "precipitation_event_day_mm": r.on_event_day_mm,
                    "precipitation_7d_mm": r.window_totals_mm.get(7, 0.0),
                    "precipitation_14d_mm": r.window_totals_mm.get(14, 0.0),
                    "precipitation_30d_mm": r.window_totals_mm.get(30, 0.0),
                    "antecedent_index_mm": r.antecedent_index_mm,
                    "rainfall_pattern": c.rainfall_pattern,
                    "rainfall_source": rainfall_source,
                    "rainfall_grid_km": rainfall_grid_km,
                    "terrain_source": terrain_source,
                    "drainage_source": drainage_source,
                    "built_at": now,
                }
            )

        df = pa.Table.from_pylist(rows, schema=EVENT_CONTEXT_SCHEMA.as_arrow())
        outcome = table.upsert(df, join_cols=EVENT_CONTEXT_KEY)
        log.info(
            "gold.event_context.written",
            event_id=result.event_id,
            rows=len(rows),
            inserted=outcome.rows_inserted,
            updated=outcome.rows_updated,
        )
        return len(rows)

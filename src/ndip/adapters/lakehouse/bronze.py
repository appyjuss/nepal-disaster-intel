"""Bronze writers. Converts fetched records into Iceberg rows, idempotently.

Re-running an ingest is normal — a retried job, a resumed backfill, a rerun after
a crash. Every write here upserts on the record's natural key, so a second run
updates in place instead of doubling the table.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime

import pyarrow as pa
import structlog
from pyiceberg.catalog import Catalog

from ndip.adapters.lakehouse.catalog import ensure_table
from ndip.adapters.lakehouse.schemas import (
    RAINFALL_DAILY,
    RAINFALL_DAILY_KEY,
    RAINFALL_DAILY_PARTITION,
    RAINFALL_DAILY_SCHEMA,
    STAC_ITEMS,
    STAC_ITEMS_KEY,
    STAC_ITEMS_PARTITION,
    STAC_ITEMS_SCHEMA,
)
from ndip.adapters.stac.client import StacItem
from ndip.domain.precipitation import DailyRainfall

log = structlog.get_logger(__name__)


class BronzeWriter:
    def __init__(self, catalog: Catalog) -> None:
        self._catalog = catalog

    def write_stac_items(
        self, items: Sequence[StacItem], *, event_id: str, source_endpoint: str
    ) -> int:
        if not items:
            log.info("bronze.stac_items.skipped", event_id=event_id, reason="no items")
            return 0

        table = ensure_table(self._catalog, STAC_ITEMS, STAC_ITEMS_SCHEMA, STAC_ITEMS_PARTITION)
        now = datetime.now(UTC)
        rows = [
            {
                "event_id": event_id,
                "collection": item.scene.collection,
                "item_id": item.scene.item_id,
                "acquired_at": item.scene.acquired_at.astimezone(UTC),
                "orbit_state": item.scene.orbit_state.value if item.scene.orbit_state else None,
                "relative_orbit": item.scene.relative_orbit,
                "cloud_cover": item.scene.cloud_cover,
                "assets": item.assets,
                "raw": json.dumps(item.raw, separators=(",", ":"), sort_keys=True),
                "source_endpoint": source_endpoint,
                "ingested_at": now,
            }
            for item in items
        ]
        return self._upsert(table, rows, STAC_ITEMS_SCHEMA, STAC_ITEMS_KEY, STAC_ITEMS, event_id)

    def write_rainfall(
        self,
        series: Sequence[DailyRainfall],
        *,
        event_id: str,
        latitude: float,
        longitude: float,
        source: str,
        grid_resolution_km: float | None = None,
    ) -> int:
        if not series:
            log.info("bronze.rainfall.skipped", event_id=event_id, reason="empty series")
            return 0

        table = ensure_table(
            self._catalog, RAINFALL_DAILY, RAINFALL_DAILY_SCHEMA, RAINFALL_DAILY_PARTITION
        )
        now = datetime.now(UTC)
        rows = [
            {
                "event_id": event_id,
                "observed_on": day.on,
                "precipitation_mm": day.precipitation_mm,
                "latitude": latitude,
                "longitude": longitude,
                "source": source,
                "grid_resolution_km": grid_resolution_km,
                "ingested_at": now,
            }
            for day in series
        ]
        return self._upsert(
            table, rows, RAINFALL_DAILY_SCHEMA, RAINFALL_DAILY_KEY, RAINFALL_DAILY, event_id
        )

    @staticmethod
    def _upsert(table, rows, schema, join_cols, name: str, event_id: str) -> int:
        arrow_schema = schema.as_arrow()
        df = pa.Table.from_pylist(rows, schema=arrow_schema)
        result = table.upsert(df, join_cols=join_cols)
        log.info(
            "bronze.upsert.complete",
            table=name,
            event_id=event_id,
            rows=len(rows),
            inserted=result.rows_inserted,
            updated=result.rows_updated,
        )
        return len(rows)


def read_rainfall(catalog: Catalog, *, event_id: str) -> list[DailyRainfall]:
    """The daily series already landed in bronze, back as domain values."""
    rows = catalog.load_table(RAINFALL_DAILY).scan().to_arrow().to_pylist()
    series = [
        DailyRainfall(on=r["observed_on"], precipitation_mm=r["precipitation_mm"])
        for r in rows
        if r["event_id"] == event_id
    ]
    series.sort(key=lambda d: d.on)
    log.info("bronze.rainfall.read", event_id=event_id, days=len(series))
    return series

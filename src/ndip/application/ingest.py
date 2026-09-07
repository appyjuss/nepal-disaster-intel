"""Use case: land discovered records in the bronze layer.

Bronze is deliberately dumb. It records what each source returned, with enough
provenance to answer "where did this row come from" months later, and leaves
every judgement to silver.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from ndip.adapters.lakehouse.bronze import BronzeWriter
from ndip.adapters.weather.open_meteo import NOMINAL_GRID_KM, SOURCE_ID
from ndip.application.discover import DiscoveryResult

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BronzeIngestReport:
    event_id: str
    radar_rows: int
    optical_rows: int
    rainfall_rows: int

    @property
    def total_rows(self) -> int:
        return self.radar_rows + self.optical_rows + self.rainfall_rows

    def summary_lines(self) -> list[str]:
        return [
            f"Bronze ingest for {self.event_id}",
            f"  bronze.stac_items    radar   {self.radar_rows:>5} rows",
            f"  bronze.stac_items    optical {self.optical_rows:>5} rows",
            f"  bronze.rainfall_daily        {self.rainfall_rows:>5} rows",
            f"  total                        {self.total_rows:>5} rows",
        ]


def ingest_bronze(
    result: DiscoveryResult, *, writer: BronzeWriter, source_endpoint: str
) -> BronzeIngestReport:
    event_id = result.event.event_id
    longitude, latitude = result.event.aoi.centroid

    radar_rows = writer.write_stac_items(
        result.radar_items, event_id=event_id, source_endpoint=source_endpoint
    )
    optical_rows = writer.write_stac_items(
        result.optical_candidates, event_id=event_id, source_endpoint=source_endpoint
    )
    rainfall_rows = writer.write_rainfall(
        result.rainfall_series,
        event_id=event_id,
        latitude=latitude,
        longitude=longitude,
        source=SOURCE_ID,
        grid_resolution_km=NOMINAL_GRID_KM,
    )

    report = BronzeIngestReport(
        event_id=event_id,
        radar_rows=radar_rows,
        optical_rows=optical_rows,
        rainfall_rows=rainfall_rows,
    )
    log.info("ingest.bronze.complete", event_id=event_id, rows=report.total_rows)
    return report

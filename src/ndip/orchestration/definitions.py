"""Dagster asset graph — the adapter that turns four commands into one.

Each asset owns one medallion layer and declares what it reads, so the dependency
between layers is enforced by the graph rather than by remembering an order. Only
the first asset touches the network; everything downstream reads the lakehouse.
"""

# No `from __future__ import annotations` here: Dagster resolves the config class
# from the annotation at decoration time and a string annotation defeats it.
import os
from pathlib import Path

from dagster import AssetExecutionContext, Config, Definitions, MaterializeResult, asset

from ndip.adapters.config import DEFAULT_DATA_DIR, load_settings
from ndip.adapters.lakehouse.bronze import BronzeWriter, read_rainfall, read_scenes
from ndip.adapters.lakehouse.catalog import build_catalog
from ndip.adapters.lakehouse.gold import GoldWriter
from ndip.adapters.lakehouse.silver import SilverWriter, read_change_polygons
from ndip.adapters.osm.overture import DEFAULT_RELEASE, ensure_extract, load_drainage
from ndip.adapters.population.worldpop import ensure_raster
from ndip.adapters.raster.rtc import DEFAULT_RESOLUTION_M, RtcLoader
from ndip.adapters.report.build import build_payload, hillshade_png_base64, read_inputs, render
from ndip.adapters.stac.client import EARTH_SEARCH, S1_GRD, StacSearch
from ndip.adapters.terrain.dem import (
    load_dem_for_report,
    load_slope_degrees,
    load_terrain_grid,
    utm_epsg,
)
from ndip.adapters.weather.open_meteo import NOMINAL_GRID_KM, fetch_daily_rainfall
from ndip.adapters.weather.open_meteo import SOURCE_ID as RAINFALL_SOURCE
from ndip.application.context import build_context
from ndip.application.detect import detect_change
from ndip.application.discover import discover
from ndip.application.events import REGISTRY, TRISHULI_2026_08_26
from ndip.application.expose import assess_exposure
from ndip.application.ingest import ingest_bronze
from ndip.domain.change import DEFAULT_MIN_MAPPING_UNIT_M2
from ndip.domain.detection import DEFAULT_CHANGE_THRESHOLD_DB
from ndip.domain.event import best_pair_per_track
from ndip.domain.exposure import DEFAULT_BUFFER_M


class PipelineConfig(Config):
    event_id: str = TRISHULI_2026_08_26.event_id
    band: str = "vv"
    threshold_db: float = DEFAULT_CHANGE_THRESHOLD_DB
    resolution_m: int = DEFAULT_RESOLUTION_M
    min_area_m2: float = DEFAULT_MIN_MAPPING_UNIT_M2
    buffer_m: float = DEFAULT_BUFFER_M
    overture_release: str = DEFAULT_RELEASE
    confidence: str = "high"
    report_path: str = "docs/trishuli-report.html"


def _event(config: PipelineConfig):
    return REGISTRY[config.event_id]


def _cache() -> Path:
    return Path(os.environ.get("NDIP_DATA", DEFAULT_DATA_DIR))


def _catalog():
    return build_catalog(load_settings())


def _grades(config: PipelineConfig) -> tuple[str, ...]:
    return tuple(g.strip() for g in config.confidence.split(",") if g.strip())


@asset(group_name="bronze", description="Acquisitions and rainfall as the sources returned them")
def bronze_observations(
    context: AssetExecutionContext, config: PipelineConfig
) -> MaterializeResult:
    event = _event(config)
    with StacSearch(EARTH_SEARCH) as search:
        discovery = discover(event, search=search, fetch_rainfall=fetch_daily_rainfall)
    report = ingest_bronze(discovery, writer=BronzeWriter(_catalog()), source_endpoint=EARTH_SEARCH)
    context.log.info(f"{report.total_rows} bronze rows for {event.event_id}")
    return MaterializeResult(
        metadata={
            "radar_rows": report.radar_rows,
            "optical_rows": report.optical_rows,
            "rainfall_rows": report.rainfall_rows,
            "radar_tracks": len(discovery.radar_pairs),
            "dual_geometry": discovery.has_dual_geometry,
        }
    )


@asset(
    group_name="silver",
    deps=[bronze_observations],
    description="Surface change graded by agreement between look directions",
)
def silver_change_polygons(
    context: AssetExecutionContext, config: PipelineConfig
) -> MaterializeResult:
    event = _event(config)
    catalog = _catalog()
    # Read the acquisitions that were actually ingested, not whatever the remote
    # catalogue holds today. That is what makes this dependency real.
    scenes = read_scenes(catalog, event_id=event.event_id, collection=S1_GRD)
    pairs = best_pair_per_track(scenes, event)
    if not pairs:
        raise ValueError(f"no event-bracketing pair in bronze for {event.event_id}")

    detection = detect_change(
        event,
        pairs,
        loader=RtcLoader(resolution_m=config.resolution_m),
        load_slope=lambda res_m, shape: load_slope_degrees(
            event.aoi, resolution_m=res_m, shape=shape
        ),
        band=config.band,
        threshold_db=config.threshold_db,
        min_mapping_unit_m2=config.min_area_m2,
    )
    written = SilverWriter(catalog).write_change_polygons(
        detection, threshold_db=config.threshold_db, min_mapping_unit_m2=config.min_area_m2
    )
    graded = {}
    for change in detection.changes:
        key = change.confidence(config.min_area_m2).value
        graded[key] = graded.get(key, 0) + 1
    context.log.info(f"{written} polygons, {graded.get('high', 0)} corroborated")
    return MaterializeResult(
        metadata={
            "rows": written,
            "tracks": len(pairs),
            **{f"graded_{k}": v for k, v in graded.items()},
        }
    )


@asset(
    group_name="gold",
    deps=[silver_change_polygons],
    description="What stood inside and beside each corroborated change",
)
def gold_exposure(context: AssetExecutionContext, config: PipelineConfig) -> MaterializeResult:
    event = _event(config)
    catalog = _catalog()
    polygons = read_change_polygons(catalog, event_id=event.event_id, confidence=_grades(config))
    if not polygons:
        raise ValueError(f"no {config.confidence} polygons in silver for {event.event_id}")

    result = assess_exposure(
        event.event_id,
        polygons,
        extract=ensure_extract(event.aoi, _cache() / "overture", release=config.overture_release),
        population=ensure_raster(_cache() / "population"),
        projected_epsg=utm_epsg(event.aoi),
        buffer_m=config.buffer_m,
    )
    written = GoldWriter(catalog).write_exposure(result)
    return MaterializeResult(
        metadata={
            "rows": written,
            "buildings_in_buffer": sum(e.buildings.in_buffer for e in result.exposures),
            "with_a_bridge": sum(1 for e in result.exposures if e.touches_a_bridge),
            "nothing_nearby": sum(1 for e in result.exposures if e.is_empty),
        }
    )


@asset(
    group_name="gold",
    deps=[silver_change_polygons],
    description="Terrain and rainfall conditions around each corroborated change",
)
def gold_event_context(context: AssetExecutionContext, config: PipelineConfig) -> MaterializeResult:
    event = _event(config)
    catalog = _catalog()
    polygons = read_change_polygons(catalog, event_id=event.event_id, confidence=_grades(config))
    if not polygons:
        raise ValueError(f"no {config.confidence} polygons in silver for {event.event_id}")
    series = read_rainfall(catalog, event_id=event.event_id)
    if not series:
        raise ValueError(f"no rainfall in bronze for {event.event_id}")

    extract = ensure_extract(event.aoi, _cache() / "overture", release=config.overture_release)
    result = build_context(
        event.event_id,
        event.occurred_on,
        polygons,
        grid=load_terrain_grid(event.aoi, resolution_m=config.resolution_m),
        rainfall_series=series,
        drainage_wkb=load_drainage(extract),
        projected_epsg=utm_epsg(event.aoi),
    )
    written = GoldWriter(catalog).write_event_context(
        result,
        event_date=event.occurred_on,
        rainfall_source=RAINFALL_SOURCE,
        rainfall_grid_km=NOMINAL_GRID_KM,
        terrain_source="copernicus-dem-glo-30",
        drainage_source=f"overture/{extract.release}/base/water",
    )
    elevations = [c.terrain.elevation_m for c in result.contexts]
    return MaterializeResult(
        metadata={
            "rows": written,
            "elevation_min_m": round(min(elevations)),
            "elevation_max_m": round(max(elevations)),
            "pattern": result.contexts[0].rainfall_pattern,
        }
    )


@asset(
    group_name="report",
    deps=[gold_exposure, gold_event_context],
    description="A self-contained page reading gold, regenerated with the pipeline",
)
def report_page(context: AssetExecutionContext, config: PipelineConfig) -> MaterializeResult:
    event = _event(config)
    inputs = read_inputs(_catalog(), event)
    if not inputs.polygons:
        raise ValueError(f"nothing to report for {event.event_id}")

    payload = build_payload(
        event,
        inputs,
        hillshade=hillshade_png_base64(load_dem_for_report(event.aoi)),
        bronze_rows=len(inputs.radar) + len(inputs.optical) + len(inputs.rainfall),
        threshold_db=config.threshold_db,
        min_area_m2=config.min_area_m2,
        band=config.band,
        resolution_m=float(config.resolution_m),
        buffer_m=config.buffer_m,
        overture_release=config.overture_release,
    )
    written = render(payload, Path(config.report_path))
    size_kb = round(written.stat().st_size / 1024)
    context.log.info(f"report written to {written} ({size_kb} KB)")
    return MaterializeResult(
        metadata={
            "path": str(written),
            "size_kb": size_kb,
            "regions_shown": len(payload["polygons"]),
        }
    )


defs = Definitions(
    assets=[
        bronze_observations,
        silver_change_polygons,
        gold_exposure,
        gold_event_context,
        report_page,
    ]
)

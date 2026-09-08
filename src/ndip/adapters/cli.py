"""CLI adapter. Parses and validates untrusted argv, then calls the use case."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import replace
from pathlib import Path

import structlog

from ndip.adapters.config import DEFAULT_DATA_DIR, load_settings
from ndip.adapters.lakehouse.bronze import BronzeWriter, read_rainfall
from ndip.adapters.lakehouse.catalog import build_catalog
from ndip.adapters.lakehouse.gold import GoldWriter
from ndip.adapters.lakehouse.silver import SilverWriter, read_change_polygons
from ndip.adapters.osm.overture import DEFAULT_RELEASE, ensure_extract, load_drainage
from ndip.adapters.population.worldpop import ensure_raster
from ndip.adapters.raster.rtc import DEFAULT_RESOLUTION_M, RtcLoader
from ndip.adapters.stac.client import EARTH_SEARCH, StacSearch
from ndip.adapters.terrain.dem import load_slope_degrees, load_terrain_grid, utm_epsg
from ndip.adapters.weather.open_meteo import (
    NOMINAL_GRID_KM,
    fetch_daily_rainfall,
)
from ndip.adapters.weather.open_meteo import (
    SOURCE_ID as RAINFALL_SOURCE,
)
from ndip.application.context import build_context
from ndip.application.detect import detect_change
from ndip.application.discover import discover
from ndip.application.events import REGISTRY, TRISHULI_2026_08_26
from ndip.application.expose import assess_exposure
from ndip.application.ingest import ingest_bronze
from ndip.domain.change import DEFAULT_MIN_MAPPING_UNIT_M2
from ndip.domain.detection import DEFAULT_CHANGE_THRESHOLD_DB
from ndip.domain.exposure import DEFAULT_BUFFER_M
from ndip.domain.geometry import BBox


def _configure_logging(verbose: bool) -> None:
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if verbose else logging.INFO
        ),
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ndip", description="Nepal Disaster Intelligence")
    sub = parser.add_subparsers(dest="command", required=True)

    d = sub.add_parser("discover", help="find usable acquisitions and rainfall context")
    d.add_argument("--event", default=TRISHULI_2026_08_26.event_id, choices=sorted(REGISTRY))
    d.add_argument("--bbox", help="override AOI as 'w,s,e,n' in EPSG:4326")
    d.add_argument("--endpoint", default=EARTH_SEARCH)
    d.add_argument("-v", "--verbose", action="store_true")

    i = sub.add_parser("ingest", help="discover, then land the results in bronze")
    i.add_argument("--event", default=TRISHULI_2026_08_26.event_id, choices=sorted(REGISTRY))
    i.add_argument("--bbox", help="override AOI as 'w,s,e,n' in EPSG:4326")
    i.add_argument("--endpoint", default=EARTH_SEARCH)
    i.add_argument("-v", "--verbose", action="store_true")

    c = sub.add_parser("detect", help="detect surface change and write silver polygons")
    c.add_argument("--event", default=TRISHULI_2026_08_26.event_id, choices=sorted(REGISTRY))
    c.add_argument("--bbox", help="override AOI as 'w,s,e,n' in EPSG:4326")
    c.add_argument("--endpoint", default=EARTH_SEARCH)
    c.add_argument("--band", default="vv", choices=["vv", "vh"])
    c.add_argument("--threshold-db", type=float, default=DEFAULT_CHANGE_THRESHOLD_DB)
    c.add_argument("--resolution-m", type=int, default=DEFAULT_RESOLUTION_M)
    c.add_argument("--min-area-m2", type=float, default=DEFAULT_MIN_MAPPING_UNIT_M2)
    c.add_argument("--no-write", action="store_true", help="detect but do not persist")
    c.add_argument("-v", "--verbose", action="store_true")

    x = sub.add_parser("expose", help="what stood in and beside each corroborated change")
    x.add_argument("--event", default=TRISHULI_2026_08_26.event_id, choices=sorted(REGISTRY))
    x.add_argument("--bbox", help="override AOI as 'w,s,e,n' in EPSG:4326")
    x.add_argument("--buffer-m", type=float, default=DEFAULT_BUFFER_M)
    x.add_argument("--release", default=DEFAULT_RELEASE, help="Overture release")
    x.add_argument("--confidence", default="high", help="comma-separated silver grades to assess")
    x.add_argument("--refresh", action="store_true", help="re-download the Overture extract")
    x.add_argument("-v", "--verbose", action="store_true")

    k = sub.add_parser("context", help="terrain and rainfall conditions per corroborated change")
    k.add_argument("--event", default=TRISHULI_2026_08_26.event_id, choices=sorted(REGISTRY))
    k.add_argument("--bbox", help="override AOI as 'w,s,e,n' in EPSG:4326")
    k.add_argument("--resolution-m", type=int, default=DEFAULT_RESOLUTION_M)
    k.add_argument("--release", default=DEFAULT_RELEASE, help="Overture release")
    k.add_argument("--confidence", default="high")
    k.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    event = REGISTRY[args.event]
    if args.bbox:
        try:
            event = replace(event, aoi=BBox.parse(args.bbox))
        except ValueError as exc:
            parser.error(f"invalid --bbox: {exc}")

    if args.command == "context":
        settings = load_settings()
        catalog = build_catalog(settings)
        grades = tuple(g.strip() for g in args.confidence.split(",") if g.strip())
        polygons = read_change_polygons(catalog, event_id=event.event_id, confidence=grades)
        if not polygons:
            print(
                f"error: no {'/'.join(grades)} polygons in silver for {event.event_id}; "
                "run 'ndip detect' first",
                file=sys.stderr,
            )
            return 2
        series = read_rainfall(catalog, event_id=event.event_id)
        if not series:
            print(
                f"error: no rainfall in bronze for {event.event_id}; run 'ndip ingest' first",
                file=sys.stderr,
            )
            return 2

        cache = Path(os.environ.get("NDIP_DATA", DEFAULT_DATA_DIR))
        extract = ensure_extract(event.aoi, cache / "overture", release=args.release)
        grid = load_terrain_grid(event.aoi, resolution_m=args.resolution_m)
        result = build_context(
            event.event_id,
            event.occurred_on,
            polygons,
            grid=grid,
            rainfall_series=series,
            drainage_wkb=load_drainage(extract),
            projected_epsg=utm_epsg(event.aoi),
        )
        print()
        for line in result.summary_lines():
            print(line)
        written = GoldWriter(catalog).write_event_context(
            result,
            event_date=event.occurred_on,
            rainfall_source=RAINFALL_SOURCE,
            rainfall_grid_km=NOMINAL_GRID_KM,
            terrain_source="copernicus-dem-glo-30",
            drainage_source=f"overture/{extract.release}/base/water",
        )
        print(f"\n  wrote {written} rows to gold.event_context\n")
        return 0

    if args.command == "expose":
        settings = load_settings()
        catalog = build_catalog(settings)
        grades = tuple(g.strip() for g in args.confidence.split(",") if g.strip())
        polygons = read_change_polygons(catalog, event_id=event.event_id, confidence=grades)
        if not polygons:
            print(
                f"error: no {'/'.join(grades)} polygons in silver for {event.event_id}; "
                "run 'ndip detect' first",
                file=sys.stderr,
            )
            return 2

        cache = Path(os.environ.get("NDIP_DATA", DEFAULT_DATA_DIR))
        extract = ensure_extract(
            event.aoi, cache / "overture", release=args.release, refresh=args.refresh
        )
        population = ensure_raster(cache / "population")
        result = assess_exposure(
            event.event_id,
            polygons,
            extract=extract,
            population=population,
            projected_epsg=utm_epsg(event.aoi),
            buffer_m=args.buffer_m,
        )
        print()
        for line in result.summary_lines():
            print(line)
        written = GoldWriter(catalog).write_exposure(result)
        print(f"\n  wrote {written} rows to gold.exposure\n")
        return 0

    with StacSearch(args.endpoint) as search:
        try:
            result = discover(event, search=search, fetch_rainfall=fetch_daily_rainfall)
        except LookupError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    print()
    for line in result.summary_lines():
        print(line)

    if args.command == "detect":
        loader = RtcLoader(resolution_m=args.resolution_m)
        try:
            detection = detect_change(
                event,
                result.radar_pairs,
                loader=loader,
                load_slope=lambda res_m, shape: load_slope_degrees(
                    event.aoi, resolution_m=res_m, shape=shape
                ),
                band=args.band,
                threshold_db=args.threshold_db,
                min_mapping_unit_m2=args.min_area_m2,
            )
        except LookupError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

        print()
        for line in detection.summary_lines():
            print(line)

        if not args.no_write:
            writer = SilverWriter(build_catalog(load_settings()))
            written = writer.write_change_polygons(
                detection,
                threshold_db=args.threshold_db,
                min_mapping_unit_m2=args.min_area_m2,
            )
            print(f"\n  wrote {written} rows to silver.change_polygons")
        print()
        return 0

    if args.command == "ingest":
        writer = BronzeWriter(build_catalog(load_settings()))
        report = ingest_bronze(result, writer=writer, source_endpoint=args.endpoint)
        print()
        for line in report.summary_lines():
            print(line)

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

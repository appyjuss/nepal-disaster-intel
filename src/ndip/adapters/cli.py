"""CLI adapter. Parses and validates untrusted argv, then calls the use case."""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace

import structlog

from ndip.adapters.config import load_settings
from ndip.adapters.lakehouse.bronze import BronzeWriter
from ndip.adapters.lakehouse.catalog import build_catalog
from ndip.adapters.lakehouse.silver import SilverWriter
from ndip.adapters.raster.rtc import DEFAULT_RESOLUTION_M, RtcLoader
from ndip.adapters.stac.client import EARTH_SEARCH, StacSearch
from ndip.adapters.terrain.dem import load_slope_degrees
from ndip.adapters.weather.open_meteo import fetch_daily_rainfall
from ndip.application.detect import detect_change
from ndip.application.discover import discover
from ndip.application.events import REGISTRY, TRISHULI_2026_08_26
from ndip.application.ingest import ingest_bronze
from ndip.domain.change import DEFAULT_MIN_MAPPING_UNIT_M2
from ndip.domain.detection import DEFAULT_CHANGE_THRESHOLD_DB
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

    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    event = REGISTRY[args.event]
    if args.bbox:
        try:
            event = replace(event, aoi=BBox.parse(args.bbox))
        except ValueError as exc:
            parser.error(f"invalid --bbox: {exc}")

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

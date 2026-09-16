"""Overture Maps roads, bridges and buildings.

Overture publishes GeoParquet on public S3 with a bbox column per row, so a study
area is a filtered scan rather than a download of the planet. The extract is cached
locally because a release is immutable: re-running a pipeline should not re-read it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb
import structlog

from ndip.domain.geometry import BBox

log = structlog.get_logger(__name__)

OVERTURE_BUCKET = "s3://overturemaps-us-west-2"
OVERTURE_REGION = "us-west-2"
DEFAULT_RELEASE = "2026-08-19.0"

ROADS_FILE = "roads.parquet"
BUILDINGS_FILE = "buildings.parquet"


@dataclass(frozen=True, slots=True)
class OvertureExtract:
    roads: Path
    buildings: Path
    release: str


def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
    # Overture's bucket is public. Empty credentials make DuckDB send unsigned
    # requests; the AWS default chain would otherwise try to sign and fail.
    con.execute(
        f"SET s3_region='{OVERTURE_REGION}'; SET s3_access_key_id=''; SET s3_secret_access_key='';"
    )
    con.execute("SET preserve_insertion_order=false;")
    return con


def ensure_extract(
    aoi: BBox, cache_dir: Path, *, release: str = DEFAULT_RELEASE, refresh: bool = False
) -> OvertureExtract:
    """Materialise the study area locally, reusing it if already present."""
    out = cache_dir / release
    out.mkdir(parents=True, exist_ok=True)
    roads, buildings = out / ROADS_FILE, out / BUILDINGS_FILE
    if roads.exists() and buildings.exists() and not refresh:
        log.info("overture.extract.cached", release=release, dir=str(out))
        return OvertureExtract(roads=roads, buildings=buildings, release=release)

    where = (
        f"bbox.xmin < {aoi.east} AND bbox.xmax > {aoi.west} "
        f"AND bbox.ymin < {aoi.north} AND bbox.ymax > {aoi.south}"
    )
    con = _connect()
    try:
        con.execute(
            f"""COPY (
              SELECT id, class,
                     list_contains(
                       flatten(list_transform(road_flags, x -> x.values)), 'is_bridge'
                     ) AS is_bridge,
                     names.primary AS name,
                     ST_AsWKB(geometry) AS geom_wkb
              FROM read_parquet(
                '{OVERTURE_BUCKET}/release/{release}/theme=transportation/type=segment/*.parquet'
              )
              WHERE {where} AND subtype = 'road'
            ) TO '{roads}' (FORMAT PARQUET)"""
        )
        con.execute(
            f"""COPY (
              SELECT id, class, height, names.primary AS name, ST_AsWKB(geometry) AS geom_wkb
              FROM read_parquet(
                '{OVERTURE_BUCKET}/release/{release}/theme=buildings/type=building/*.parquet'
              )
              WHERE {where}
            ) TO '{buildings}' (FORMAT PARQUET)"""
        )
        counts = con.execute(
            f"""SELECT (SELECT count(*) FROM '{roads}'),
                       (SELECT count(*) FROM '{roads}' WHERE is_bridge),
                       (SELECT count(*) FROM '{buildings}')"""
        ).fetchone()
    finally:
        con.close()

    log.info(
        "overture.extract.written",
        release=release,
        roads=counts[0],
        bridges=counts[1],
        buildings=counts[2],
    )
    return OvertureExtract(roads=roads, buildings=buildings, release=release)


def count_features(
    extract: OvertureExtract, regions: dict[str, tuple[str, str]]
) -> dict[str, dict[str, int]]:
    """Count roads, bridges and buildings per region.

    `regions` maps a polygon id to (polygon WKT, buffered WKT), both in WGS84.
    One query per layer over all regions, rather than one per region: the join is
    trivial for the database and the round trips are not.
    """
    if not regions:
        return {}

    rows = [
        {"polygon_id": pid, "poly_wkt": poly, "buf_wkt": buf}
        for pid, (poly, buf) in regions.items()
    ]
    con = _connect()
    try:
        con.execute(
            "CREATE TEMP TABLE regions (polygon_id VARCHAR, poly_wkt VARCHAR, buf_wkt VARCHAR)"
        )
        con.executemany(
            "INSERT INTO regions VALUES (?, ?, ?)",
            [(r["polygon_id"], r["poly_wkt"], r["buf_wkt"]) for r in rows],
        )
        con.execute(
            """CREATE TEMP VIEW r AS
               SELECT polygon_id, ST_GeomFromText(poly_wkt) AS poly,
                      ST_GeomFromText(buf_wkt) AS buf FROM regions"""
        )

        result: dict[str, dict[str, int]] = {
            pid: {
                "roads_within": 0,
                "roads_in_buffer": 0,
                "bridges_within": 0,
                "bridges_in_buffer": 0,
                "buildings_within": 0,
                "buildings_in_buffer": 0,
            }
            for pid in regions
        }

        road_rows = con.execute(
            f"""SELECT r.polygon_id,
                   count(*) FILTER (WHERE ST_Intersects(g.geom, r.poly)),
                   count(*),
                   count(*) FILTER (WHERE g.is_bridge AND ST_Intersects(g.geom, r.poly)),
                   count(*) FILTER (WHERE g.is_bridge)
                FROM r JOIN (
                  SELECT ST_GeomFromWKB(geom_wkb) AS geom, is_bridge FROM '{extract.roads}'
                ) g ON ST_Intersects(g.geom, r.buf)
                GROUP BY r.polygon_id"""
        ).fetchall()
        for pid, rw, rb, bw, bb in road_rows:
            result[pid].update(
                roads_within=rw, roads_in_buffer=rb, bridges_within=bw, bridges_in_buffer=bb
            )

        b_rows = con.execute(
            f"""SELECT r.polygon_id,
                   count(*) FILTER (WHERE ST_Intersects(g.geom, r.poly)),
                   count(*)
                FROM r JOIN (
                  SELECT ST_GeomFromWKB(geom_wkb) AS geom FROM '{extract.buildings}'
                ) g ON ST_Intersects(g.geom, r.buf)
                GROUP BY r.polygon_id"""
        ).fetchall()
        for pid, bw, bb in b_rows:
            result[pid].update(buildings_within=bw, buildings_in_buffer=bb)
    finally:
        con.close()

    log.info("overture.counts.complete", regions=len(regions))
    return result

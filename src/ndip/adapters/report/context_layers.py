"""Map context for the report: water, major roads and place names.

A shaded relief with markers on it is unreadable — there is no way to tell a valley
from a ridge, or to find anywhere. Drawing the river network is what makes the
finding legible, because the finding is that the detections lie along it.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import structlog

from ndip.domain.geometry import BBox

log = structlog.get_logger(__name__)

# Roads that describe the shape of the corridor. Residential streets and footpaths
# would cover the map in hair without telling anyone where they are.
MAJOR_ROAD_CLASSES = ("motorway", "trunk", "primary", "secondary")

# About 100 m. Finer than this is invisible at the scale the page draws.
SIMPLIFY_DEGREES = 0.001
COORD_PRECISION = 4

# Enough names to locate yourself, few enough to read.
MAX_PLACE_LABELS = 12
# Labels closer than this collide, so only the first of a cluster is kept.
LABEL_SPACING_DEGREES = 0.09


def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    return con


def _lines(con, sql: str) -> list[list[list[float]]]:
    out: list[list[list[float]]] = []
    for (wkt,) in con.execute(sql).fetchall():
        if not wkt:
            continue
        for part in _coords_from_wkt(wkt):
            if len(part) >= 2:
                out.append(part)
    return out


def _coords_from_wkt(wkt: str) -> list[list[list[float]]]:
    """Coordinate rings from any geometry type.

    Water arrives as both lines and polygons — a stream is a line, a wide river or a
    lake is a filled shape — so the type has to be handled rather than assumed.
    """
    from shapely import wkt as shapely_wkt

    geometry = shapely_wkt.loads(wkt)
    parts = list(geometry.geoms) if hasattr(geometry, "geoms") else [geometry]
    result = []
    for part in parts:
        if part.is_empty:
            continue
        if part.geom_type == "Polygon":
            rings = [part.exterior]
        elif part.geom_type in ("LineString", "LinearRing"):
            rings = [part]
        else:
            continue
        for ring in rings:
            result.append(
                [[round(x, COORD_PRECISION), round(y, COORD_PRECISION)] for x, y in ring.coords]
            )
    return result


def read_map_context(extract_dir: Path, aoi: BBox) -> dict:
    """Water, major roads and a few place names, thinned for a web page."""
    water, roads = extract_dir / "water.parquet", extract_dir / "roads.parquet"
    if not water.exists() or not roads.exists():
        log.warning("report.context.missing", dir=str(extract_dir))
        return {"rivers": [], "streams": [], "roads": [], "places": []}

    con = _connect()
    try:
        rivers = _lines(
            con,
            f"""SELECT ST_AsText(ST_Simplify(ST_GeomFromWKB(geom_wkb), {SIMPLIFY_DEGREES}))
                FROM '{water}' WHERE subtype IN ('river', 'canal')""",
        )
        streams = _lines(
            con,
            f"""SELECT ST_AsText(ST_Simplify(ST_GeomFromWKB(geom_wkb), {SIMPLIFY_DEGREES}))
                FROM '{water}' WHERE subtype = 'stream'""",
        )
        classes = ", ".join(f"'{c}'" for c in MAJOR_ROAD_CLASSES)
        roads_out = _lines(
            con,
            f"""SELECT ST_AsText(ST_Simplify(ST_GeomFromWKB(geom_wkb), {SIMPLIFY_DEGREES}))
                FROM '{roads}' WHERE class IN ({classes})""",
        )
    finally:
        con.close()

    log.info(
        "report.context.read",
        rivers=len(rivers),
        streams=len(streams),
        roads=len(roads_out),
    )
    return {"rivers": rivers, "streams": streams, "roads": roads_out, "places": []}


def read_places(release_dir: Path, aoi: BBox, bucket: str, release: str) -> list[dict]:
    """A few settlement names, spread out so the labels do not collide."""
    con = _connect()
    try:
        con.execute("INSTALL httpfs; LOAD httpfs;")
        con.execute(
            "SET s3_region='us-west-2'; SET s3_access_key_id=''; SET s3_secret_access_key='';"
        )
        rows = con.execute(
            f"""SELECT coalesce(names.common['en'], names.primary) AS name,
                       ST_X(ST_Centroid(geometry)) AS lon,
                       ST_Y(ST_Centroid(geometry)) AS lat
                FROM read_parquet(
                  '{bucket}/release/{release}/theme=divisions/type=division/*.parquet'
                )
                WHERE bbox.xmin < {aoi.east} AND bbox.xmax > {aoi.west}
                  AND bbox.ymin < {aoi.north} AND bbox.ymax > {aoi.south}
                  AND subtype = 'locality' AND names.primary IS NOT NULL"""
        ).fetchall()
    except Exception as exc:  # noqa: BLE001 - labels are decoration, never a build failure
        log.warning("report.places.unavailable", error=str(exc)[:120])
        return []
    finally:
        con.close()

    # The page's typeface carries Latin only, so a Devanagari name would render as
    # empty boxes. Prefer the English name where Overture has one and skip the rest;
    # there are far more candidates than labels needed.
    latin = [r for r in rows if r[0] and all(ord(ch) < 0x0300 for ch in r[0])]

    kept: list[dict] = []
    for name, lon, lat in latin:
        if any(
            abs(lon - k["lon"]) < LABEL_SPACING_DEGREES
            and abs(lat - k["lat"]) < LABEL_SPACING_DEGREES
            for k in kept
        ):
            continue
        kept.append({"name": name, "lon": round(lon, 4), "lat": round(lat, 4)})
        if len(kept) >= MAX_PLACE_LABELS:
            break
    log.info("report.places.read", candidates=len(rows), latin=len(latin), kept=len(kept))
    return kept

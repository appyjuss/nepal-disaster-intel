"""Turning change masks into polygons, and measuring rasters inside them."""

from __future__ import annotations

import numpy as np
import structlog
from pyproj import Transformer
from rasterio.features import rasterize, shapes
from shapely.geometry import shape as to_shape
from shapely.geometry.base import BaseGeometry

log = structlog.get_logger(__name__)


def mask_to_polygons(mask: np.ndarray, transform: object) -> list[BaseGeometry]:
    """Vectorise the True region of a boolean mask, in the raster's own CRS."""
    if mask.dtype != bool:
        raise ValueError(f"expected a boolean mask, got {mask.dtype}")
    if not mask.any():
        return []
    return [
        to_shape(geom)
        for geom, value in shapes(mask.astype(np.uint8), mask=mask, transform=transform)
        if value == 1
    ]


def zonal_mean(
    array: np.ndarray, geometry: BaseGeometry, transform: object, *, absolute: bool = False
) -> float:
    """Mean of an array inside one polygon, ignoring missing pixels.

    Returns NaN when the polygon contains no valid pixels, which the caller must
    handle rather than silently treating as zero.
    """
    footprint = rasterize(
        [(geometry, 1)], out_shape=array.shape, transform=transform, fill=0, dtype="uint8"
    ).astype(bool)
    values = array[footprint & np.isfinite(array)]
    if values.size == 0:
        return float("nan")
    return float(np.mean(np.abs(values) if absolute else values))


def buffer_metres(geometry: BaseGeometry, metres: float, *, projected_epsg: int) -> BaseGeometry:
    """Buffer a WGS84 geometry by a true distance and return it in WGS84.

    Buffering in degrees would be wrong in both axes and wrong by different amounts:
    at this latitude a degree of longitude is about 12% shorter than a degree of
    latitude, so a "0.005 degree" buffer is an ellipse, not a circle.
    """
    if metres <= 0:
        raise ValueError("buffer distance must be positive")
    to_m = Transformer.from_crs("EPSG:4326", f"EPSG:{projected_epsg}", always_xy=True).transform
    to_deg = Transformer.from_crs(f"EPSG:{projected_epsg}", "EPSG:4326", always_xy=True).transform
    from shapely.ops import transform as _t

    return _t(to_deg, _t(to_m, geometry).buffer(metres))


def zonal_values(array: np.ndarray, geometry: BaseGeometry, transform: object) -> np.ndarray:
    """Every valid pixel inside a polygon.

    Some statistics cannot be built from a mean. A compass bearing has to be
    averaged as a vector, which needs the values themselves.
    """
    footprint = rasterize(
        [(geometry, 1)], out_shape=array.shape, transform=transform, fill=0, dtype="uint8"
    ).astype(bool)
    return array[footprint & np.isfinite(array)]

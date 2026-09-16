"""Turning change masks into polygons, and measuring rasters inside them."""

from __future__ import annotations

import numpy as np
import structlog
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

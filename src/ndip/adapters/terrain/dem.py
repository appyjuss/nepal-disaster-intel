"""Copernicus DEM GLO-30, and the slope derived from it.

Slope separates a detection that could be a slope failure from one on the valley
floor, and excludes terrain too steep for radar to image at all.

Read from the Planetary Computer rather than the AWS Open Data bucket. The AWS
copy is addressed as s3://, which makes GDAL attempt a signed request; with no
credentials that call hangs rather than failing. Using one signed HTTPS source for
both imagery and terrain means no AWS credentials are needed to run the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import planetary_computer
import pystac_client
import structlog
from odc.stac import load as odc_load

from ndip.adapters.raster.gdal_env import configure_gdal
from ndip.domain.geometry import BBox
from ndip.domain.terrain import aspect_from_elevation, slope_from_elevation

log = structlog.get_logger(__name__)

PLANETARY_COMPUTER_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
COP_DEM_COLLECTION = "cop-dem-glo-30"
DEM_BAND = "data"


@dataclass(frozen=True, slots=True)
class TerrainGrid:
    """Elevation and its derivatives on one grid, with the transform needed to
    measure them inside a polygon."""

    elevation: np.ndarray
    slope_deg: np.ndarray
    aspect_deg: np.ndarray
    transform: object
    crs: str
    resolution_m: float


def load_terrain_grid(aoi: BBox, *, resolution_m: int) -> TerrainGrid:
    """Elevation, slope and aspect for the area, on a metric grid."""
    cube = _load_dem(aoi, resolution_m)
    elevation = cube[DEM_BAND].squeeze().compute()
    values = elevation.values
    epsg = utm_epsg(aoi)
    grid = TerrainGrid(
        elevation=values,
        slope_deg=slope_from_elevation(values, pixel_size_m=float(resolution_m)),
        aspect_deg=aspect_from_elevation(values, pixel_size_m=float(resolution_m)),
        transform=elevation.odc.geobox.transform,
        crs=f"EPSG:{epsg}",
        resolution_m=float(resolution_m),
    )
    log.info(
        "dem.grid.loaded",
        shape=list(values.shape),
        median_slope_deg=round(float(np.nanmedian(grid.slope_deg)), 2),
        elevation_range=[int(np.nanmin(values)), int(np.nanmax(values))],
    )
    return grid


def _load_dem(aoi: BBox, resolution_m: int):
    configure_gdal()
    client = pystac_client.Client.open(PLANETARY_COMPUTER_STAC)
    items = [
        planetary_computer.sign(item)
        for item in client.search(collections=[COP_DEM_COLLECTION], bbox=aoi.as_list()).items()
    ]
    if not items:
        raise LookupError(f"no Copernicus DEM tiles cover {aoi.as_list()}")
    return odc_load(
        items,
        bands=[DEM_BAND],
        crs=f"EPSG:{utm_epsg(aoi)}",
        resolution=resolution_m,
        bbox=aoi.as_list(),
        chunks={},
        dtype="float32",
    )


def load_slope_degrees(
    aoi: BBox, *, resolution_m: int, shape: tuple[int, int] | None = None
) -> np.ndarray:
    """Slope in degrees on the same grid as the imagery.

    The DEM predates the event, so it describes the terrain that conditioned the
    failure rather than the terrain left behind by it.
    """
    elevation = _load_dem(aoi, resolution_m)[DEM_BAND].squeeze().compute().values
    slope = slope_from_elevation(elevation, pixel_size_m=float(resolution_m))

    if shape is not None and slope.shape != shape:
        slope = _fit_to(slope, shape)

    log.info(
        "dem.slope.loaded",
        shape=list(slope.shape),
        median_slope_deg=round(float(np.nanmedian(slope)), 2),
    )
    return slope


def _fit_to(array: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Trim or edge-pad by a row or two.

    Two loads of the same bounding box at the same resolution can differ by a pixel
    through rounding. Anything larger is a real misalignment and must fail rather
    than be quietly stretched.
    """
    tolerance = 2
    if abs(array.shape[0] - shape[0]) > tolerance or abs(array.shape[1] - shape[1]) > tolerance:
        raise ValueError(f"grid mismatch: DEM {array.shape} vs imagery {shape}")

    out = array[: shape[0], : shape[1]]
    pad_y, pad_x = shape[0] - out.shape[0], shape[1] - out.shape[1]
    if pad_y or pad_x:
        out = np.pad(out, ((0, pad_y), (0, pad_x)), mode="edge")
    return out


def utm_epsg(aoi: BBox) -> int:
    """UTM zone for the AOI centroid. Metres, so areas and slopes are real."""
    lon, lat = aoi.centroid
    zone = int((lon + 180.0) // 6.0) + 1
    return (32600 if lat >= 0 else 32700) + zone

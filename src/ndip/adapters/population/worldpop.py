"""WorldPop gridded population.

The constrained product allocates people only to cells where buildings were
detected, which is the right model for asking who was inside a hectare-scale
region. Its national total runs about 20% above the UN estimate for Nepal, so
every figure derived from it is indicative. The measured national sum travels
with each row rather than living in a comment, so the bias stays correctable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
import structlog
from rasterio.mask import mask as rio_mask
from shapely.geometry.base import BaseGeometry

from ndip.adapters.http import build_client

log = structlog.get_logger(__name__)

WORLDPOP_NEPAL_2020_CONSTRAINED = (
    "https://data.worldpop.org/GIS/Population/Global_2000_2020_Constrained/"
    "2020/BSGM/NPL/npl_ppp_2020_constrained.tif"
)
PRODUCT_ID = "worldpop/npl_ppp_2020_constrained"
# Measured over the whole raster, against a UN 2020 estimate of ~29.6 million.
NATIONAL_SUM_BIAS = 1.20


@dataclass(frozen=True, slots=True)
class PopulationRaster:
    path: Path
    product_id: str = PRODUCT_ID
    bias: float = NATIONAL_SUM_BIAS


def ensure_raster(
    cache_dir: Path, *, url: str = WORLDPOP_NEPAL_2020_CONSTRAINED
) -> PopulationRaster:
    """Download once and reuse. The server advertises range requests but does not
    honour them, so windowed reads over HTTP fail; the file is small enough that
    fetching it whole is the simpler correct answer."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / url.rsplit("/", 1)[-1]
    if path.exists() and path.stat().st_size > 0:
        log.info("worldpop.cached", path=str(path))
        return PopulationRaster(path=path)

    with build_client() as client, client.stream("GET", url) as response:
        response.raise_for_status()
        with path.open("wb") as fh:
            for chunk in response.iter_bytes(chunk_size=1 << 20):
                fh.write(chunk)
    log.info("worldpop.downloaded", path=str(path), bytes=path.stat().st_size)
    return PopulationRaster(path=path)


def population_within(
    raster: PopulationRaster, geometries: dict[str, BaseGeometry]
) -> dict[str, float]:
    """Sum population inside each WGS84 geometry.

    Cells are ~92 m, so a one-hectare polygon covers a fraction of a single cell.
    Masking counts a cell when its centre falls inside, which for regions this
    small is coarse in both directions; the buffered figure is the meaningful one.
    """
    if not geometries:
        return {}
    out: dict[str, float] = {}
    with rasterio.open(raster.path) as ds:
        nodata = ds.nodata
        for key, geom in geometries.items():
            try:
                clipped, _ = rio_mask(ds, [geom.__geo_interface__], crop=True, filled=True)
            except ValueError:
                # Geometry lies outside the raster entirely.
                out[key] = 0.0
                continue
            values = clipped[0]
            valid = np.isfinite(values) & (values > 0)
            if nodata is not None:
                valid &= values != nodata
            out[key] = float(values[valid].sum())
    log.info("worldpop.zonal.complete", regions=len(out), product=raster.product_id)
    return out

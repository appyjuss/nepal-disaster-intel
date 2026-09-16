"""Sentinel-1 RTC imagery from the Planetary Computer.

RTC means radiometrically terrain corrected: backscatter already adjusted for the
local illuminated area. Doing that correction ourselves would mean a full SNAP or
hyp3 chain, and skipping it in terrain like Nepal's makes every slope look like a
change. The alternative source, Sentinel-1 GRD on AWS, is requester-pays and
uncorrected, so it costs money to be worse.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import planetary_computer
import pystac_client
import structlog
from odc.stac import load as odc_load
from shapely.geometry import box, shape

from ndip.adapters.raster.gdal_env import configure_gdal
from ndip.adapters.terrain.dem import utm_epsg
from ndip.domain.geometry import BBox

log = structlog.get_logger(__name__)

PLANETARY_COMPUTER_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
S1_RTC_COLLECTION = "sentinel-1-rtc"

# 30 m matches the Copernicus DEM, so slope masking needs no resampling, and it
# averages down speckle. At native 10 m the AOI would be ~50M pixels per band per
# date, which does not fit comfortably in memory here.
DEFAULT_RESOLUTION_M = 30

RTC_NODATA = -32768.0

# Below this share of the area of interest a frame contributes only an edge sliver.
MIN_AOI_COVERAGE_FRACTION = 0.01


@dataclass(frozen=True, slots=True)
class RasterPair:
    """Co-registered pre/post backscatter on one grid, in linear gamma0 power."""

    pre: np.ndarray
    post: np.ndarray
    transform: object
    crs: str
    resolution_m: float
    pre_date: date
    post_date: date
    track: int
    band: str
    frame_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.pre.shape != self.post.shape:
            raise ValueError(f"pre {self.pre.shape} and post {self.post.shape} differ")


class RtcLoader:
    def __init__(
        self,
        *,
        endpoint: str = PLANETARY_COMPUTER_STAC,
        resolution_m: int = DEFAULT_RESOLUTION_M,
    ) -> None:
        configure_gdal()
        self._client = pystac_client.Client.open(endpoint)
        self._resolution_m = resolution_m

    def _frames_for(self, aoi: BBox, track: int, on: date) -> list:
        """Every frame on one track and day that meaningfully overlaps the AOI.

        Sentinel-1 is delivered as frames sliced along the orbit, so one pass over
        an area of interest routinely spans two of them, and one of those two can
        be almost entirely outside it.
        """
        search = self._client.search(
            collections=[S1_RTC_COLLECTION],
            bbox=aoi.as_list(),
            datetime=f"{on.isoformat()}T00:00:00Z/{(on + timedelta(days=1)).isoformat()}T00:00:00Z",
            query={"sat:relative_orbit": {"eq": track}},
        )
        aoi_geom = box(*aoi.as_list())
        kept = []
        for item in search.items():
            coverage = shape(item.geometry).intersection(aoi_geom).area / aoi_geom.area
            if coverage < MIN_AOI_COVERAGE_FRACTION:
                log.debug("rtc.frame.skipped", item=item.id, coverage=round(coverage, 4))
                continue
            kept.append(planetary_computer.sign(item))
        if not kept:
            raise LookupError(f"no RTC frame covers the AOI on track {track} for {on}")
        log.info("rtc.frames.selected", track=track, on=str(on), frames=len(kept))
        return kept

    def load_pair(
        self, aoi: BBox, *, track: int, pre: date, post: date, band: str = "vv"
    ) -> RasterPair:
        """Load both dates onto one grid so pixels can be compared directly."""
        pre_frames = self._frames_for(aoi, track, pre)
        post_frames = self._frames_for(aoi, track, post)

        cube = odc_load(
            pre_frames + post_frames,
            bands=[band],
            crs=f"EPSG:{utm_epsg(aoi)}",
            resolution=self._resolution_m,
            bbox=aoi.as_list(),
            groupby="solar_day",
            chunks={},
            nodata=RTC_NODATA,
            dtype="float32",
        )
        if cube.sizes.get("time", 0) != 2:
            raise ValueError(
                f"expected two acquisition days on track {track}, got {cube.sizes.get('time', 0)}"
            )

        data = cube[band].compute()
        pre_arr, post_arr = data.isel(time=0).values, data.isel(time=1).values
        # Nodata survives the load as its sentinel value; make it missing so it can
        # never be mistaken for very low backscatter.
        pre_arr = np.where(pre_arr == RTC_NODATA, np.nan, pre_arr)
        post_arr = np.where(post_arr == RTC_NODATA, np.nan, post_arr)

        log.info(
            "rtc.pair.loaded",
            track=track,
            band=band,
            shape=list(pre_arr.shape),
            resolution_m=self._resolution_m,
        )
        return RasterPair(
            pre=pre_arr,
            post=post_arr,
            transform=data.odc.geobox.transform,
            # The CRS object stringifies to a full WKT definition; the EPSG code is
            # what belongs in a column someone will read or filter on.
            crs=f"EPSG:{data.odc.geobox.crs.epsg}",
            resolution_m=float(self._resolution_m),
            pre_date=pre,
            post_date=post,
            track=track,
            band=band,
            frame_ids=tuple(f.id for f in pre_frames + post_frames),
        )

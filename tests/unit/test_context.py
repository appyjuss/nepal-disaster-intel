"""Context assembly. Synthetic terrain, no network."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest
from affine import Affine
from pyproj import Transformer
from shapely.geometry import box
from shapely.ops import transform as shp_transform

from ndip.adapters.terrain.dem import TerrainGrid
from ndip.application.context import MAX_DRAINAGE_SEARCH_M, build_context
from ndip.domain.precipitation import DailyRainfall

EVENT_ID = "NPL-TEST"
EVENT_DATE = date(2026, 8, 26)
EPSG = 32645

# A 300x300 cell UTM grid at 30 m, anchored near the Trishuli corridor.
ORIGIN_E, ORIGIN_N = 320000.0, 3095000.0
TRANSFORM = Affine(30.0, 0.0, ORIGIN_E, 0.0, -30.0, ORIGIN_N)
SHAPE = (300, 300)

TO_WGS84 = Transformer.from_crs(f"EPSG:{EPSG}", "EPSG:4326", always_xy=True).transform


def grid(elev: float = 1500.0) -> TerrainGrid:
    return TerrainGrid(
        elevation=np.full(SHAPE, elev, np.float32),
        slope_deg=np.full(SHAPE, 22.0, np.float32),
        aspect_deg=np.full(SHAPE, 135.0, np.float32),
        transform=TRANSFORM,
        crs=f"EPSG:{EPSG}",
        resolution_m=30.0,
    )


def polygon_record(pid: str = "p1") -> dict:
    """A polygon in the middle of the grid, stored in WGS84 as silver stores it."""
    utm = box(ORIGIN_E + 3000, ORIGIN_N - 4000, ORIGIN_E + 3300, ORIGIN_N - 3700)
    return {"polygon_id": pid, "geometry_wkt": shp_transform(TO_WGS84, utm).wkt}


RAIN = [DailyRainfall(on=date(2026, 8, d), precipitation_mm=10.0) for d in range(1, 27)]


def build(**kw):
    args = dict(
        grid=grid(),
        rainfall_series=RAIN,
        drainage_wkb=[],
        projected_epsg=EPSG,
    )
    args.update(kw)
    return build_context(EVENT_ID, EVENT_DATE, [polygon_record()], **args)


def test_terrain_is_measured_even_though_polygons_arrive_in_degrees():
    """Regression: silver stores WGS84 while the terrain grid is projected. Rasterising
    a lon/lat polygon against a metre grid selected no pixels, and every region came
    back at 0 m elevation with a flat aspect."""
    ctx = build().contexts[0]
    assert ctx.terrain.elevation_m == pytest.approx(1500.0, abs=1.0)
    assert ctx.terrain.slope_deg == pytest.approx(22.0, abs=0.5)
    assert ctx.terrain.aspect_cardinal == "SE"


def test_aspect_is_averaged_as_a_bearing_not_a_number():
    g = grid()
    g.aspect_deg[:] = np.where(np.arange(SHAPE[1]) % 2 == 0, 350.0, 10.0)
    ctx = build(grid=g).contexts[0]
    assert ctx.terrain.aspect_cardinal == "N"
    assert 0.0 <= ctx.terrain.aspect_deg < 360.0


def test_distance_to_the_nearest_channel_is_measured_in_metres():
    chan = box(ORIGIN_E + 4300, ORIGIN_N - 4000, ORIGIN_E + 4310, ORIGIN_N - 3700)
    ctx = build(drainage_wkb=[shp_transform(TO_WGS84, chan).wkb]).contexts[0]
    assert ctx.terrain.distance_to_drainage_m == pytest.approx(1000.0, abs=30.0)


def test_with_no_channels_the_distance_is_the_search_cap_not_zero():
    assert build().contexts[0].terrain.distance_to_drainage_m == MAX_DRAINAGE_SEARCH_M


def test_rainfall_is_summarised_once_and_shared_across_regions():
    ctx = build().contexts[0]
    assert ctx.rainfall.window_totals_mm[7] == pytest.approx(70.0)
    assert ctx.rainfall_pattern in {"saturation", "cloudburst", "inconclusive"}


def test_no_polygons_is_an_error_not_an_empty_result():
    with pytest.raises(LookupError):
        build_context(
            EVENT_ID,
            EVENT_DATE,
            [],
            grid=grid(),
            rainfall_series=RAIN,
            drainage_wkb=[],
            projected_epsg=EPSG,
        )

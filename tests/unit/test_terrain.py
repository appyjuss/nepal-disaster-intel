import numpy as np
import pytest

from ndip.domain.terrain import (
    aspect_from_elevation,
    cardinal,
    circular_mean_degrees,
    slope_from_elevation,
)


def ramp(rows: int, cols: int, *, dz_per_col=0.0, dz_per_row=0.0) -> np.ndarray:
    r, c = np.mgrid[0:rows, 0:cols]
    return (c * dz_per_col + r * dz_per_row).astype(np.float32)


def test_a_flat_surface_has_zero_slope():
    assert slope_from_elevation(np.zeros((5, 5), np.float32), pixel_size_m=30.0).max() == 0.0


def test_a_45_degree_ramp_measures_45_degrees():
    """One metre of rise per metre of run is 45 degrees, whatever the pixel size."""
    dem = ramp(5, 5, dz_per_col=30.0)
    assert slope_from_elevation(dem, pixel_size_m=30.0)[2, 2] == pytest.approx(45.0, abs=0.01)


def test_slope_scales_with_pixel_size():
    dem = ramp(5, 5, dz_per_col=30.0)
    coarse = slope_from_elevation(dem, pixel_size_m=60.0)[2, 2]
    assert coarse == pytest.approx(np.degrees(np.arctan(0.5)), abs=0.01)


def test_ground_falling_towards_the_north_faces_north():
    """Row index increases southward on a north-up grid, so elevation rising with
    row means the high ground is south and the slope faces north."""
    dem = ramp(5, 5, dz_per_row=30.0)
    assert aspect_from_elevation(dem, pixel_size_m=30.0)[2, 2] == pytest.approx(0.0, abs=1.0)


def test_ground_falling_towards_the_south_faces_south():
    dem = ramp(5, 5, dz_per_row=-30.0)
    assert aspect_from_elevation(dem, pixel_size_m=30.0)[2, 2] == pytest.approx(180.0, abs=1.0)


def test_ground_falling_towards_the_east_faces_east():
    dem = ramp(5, 5, dz_per_col=-30.0)
    assert aspect_from_elevation(dem, pixel_size_m=30.0)[2, 2] == pytest.approx(90.0, abs=1.0)


def test_flat_ground_has_no_aspect_rather_than_facing_north():
    """Zero degrees is due north, a real direction. Letting flat cells claim it
    would drag any average towards the pole."""
    flat = aspect_from_elevation(np.zeros((5, 5), np.float32), pixel_size_m=30.0)
    assert np.all(np.isnan(flat))


def test_circular_mean_wraps_around_north():
    """The bug this exists to prevent: an arithmetic mean of 350 and 10 is 180,
    pointing exactly opposite to both inputs."""
    assert circular_mean_degrees(np.array([350.0, 10.0])) == pytest.approx(0.0, abs=0.01)
    assert np.mean([350.0, 10.0]) == pytest.approx(180.0)


def test_circular_mean_stays_inside_the_half_open_bearing_range():
    """Regression: float noise made the wrap land on 360.0, which is not a bearing."""
    for values in ([350.0, 10.0], [0.0, 0.0], [359.9999, 0.0001]):
        assert 0.0 <= circular_mean_degrees(np.array(values)) < 360.0


def test_circular_mean_of_ordinary_bearings_is_unsurprising():
    assert circular_mean_degrees(np.array([80.0, 100.0])) == pytest.approx(90.0, abs=0.01)


def test_circular_mean_ignores_missing_values():
    assert circular_mean_degrees(np.array([np.nan, 90.0, np.nan])) == pytest.approx(90.0, abs=0.01)


def test_circular_mean_of_nothing_is_nan():
    assert np.isnan(circular_mean_degrees(np.array([np.nan, np.nan])))


@pytest.mark.parametrize(
    "bearing,name",
    [(0, "N"), (44, "NE"), (90, "E"), (180, "S"), (270, "W"), (350, "N"), (359.9, "N")],
)
def test_cardinal_names_the_nearest_compass_point(bearing, name):
    assert cardinal(bearing) == name


def test_cardinal_of_a_flat_cell_says_flat():
    assert cardinal(float("nan")) == "flat"

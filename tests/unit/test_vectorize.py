import numpy as np
import pytest
from affine import Affine

from ndip.adapters.raster.vectorize import mask_to_polygons, zonal_mean

# 10 m pixels, origin at (0, 100), y decreasing as rows increase.
TRANSFORM = Affine(10.0, 0.0, 0.0, 0.0, -10.0, 100.0)


def test_a_solid_block_becomes_one_polygon_of_the_right_area():
    mask = np.zeros((10, 10), dtype=bool)
    mask[2:5, 2:5] = True  # 3x3 pixels of 10 m = 900 m2
    polys = mask_to_polygons(mask, TRANSFORM)
    assert len(polys) == 1
    assert polys[0].area == pytest.approx(900.0)


def test_disjoint_blocks_become_separate_polygons():
    mask = np.zeros((10, 10), dtype=bool)
    mask[0:2, 0:2] = True
    mask[7:9, 7:9] = True
    assert len(mask_to_polygons(mask, TRANSFORM)) == 2


def test_an_empty_mask_yields_no_polygons():
    assert mask_to_polygons(np.zeros((5, 5), dtype=bool), TRANSFORM) == []


def test_a_non_boolean_mask_is_rejected():
    with pytest.raises(ValueError, match="boolean"):
        mask_to_polygons(np.zeros((5, 5), dtype=np.float32), TRANSFORM)


def test_zonal_mean_only_counts_pixels_inside_the_polygon():
    values = np.zeros((10, 10), dtype=np.float32)
    values[2:5, 2:5] = 7.0
    mask = np.zeros((10, 10), dtype=bool)
    mask[2:5, 2:5] = True
    poly = mask_to_polygons(mask, TRANSFORM)[0]
    assert zonal_mean(values, poly, TRANSFORM) == pytest.approx(7.0)


def test_zonal_mean_ignores_missing_pixels_rather_than_treating_them_as_zero():
    values = np.full((10, 10), np.nan, dtype=np.float32)
    values[2, 2] = 4.0
    values[3, 3] = 6.0
    mask = np.zeros((10, 10), dtype=bool)
    mask[2:5, 2:5] = True
    poly = mask_to_polygons(mask, TRANSFORM)[0]
    assert zonal_mean(values, poly, TRANSFORM) == pytest.approx(5.0)


def test_zonal_mean_of_an_all_missing_polygon_is_nan_not_zero():
    """Zero is a plausible slope and a plausible dB change; NaN is not, so the
    caller is forced to notice."""
    values = np.full((10, 10), np.nan, dtype=np.float32)
    mask = np.zeros((10, 10), dtype=bool)
    mask[2:5, 2:5] = True
    poly = mask_to_polygons(mask, TRANSFORM)[0]
    assert np.isnan(zonal_mean(values, poly, TRANSFORM))


def test_absolute_mode_averages_magnitude_not_signed_values():
    values = np.zeros((10, 10), dtype=np.float32)
    values[2:5, 2:5] = -6.0
    mask = np.zeros((10, 10), dtype=bool)
    mask[2:5, 2:5] = True
    poly = mask_to_polygons(mask, TRANSFORM)[0]
    assert zonal_mean(values, poly, TRANSFORM, absolute=True) == pytest.approx(6.0)

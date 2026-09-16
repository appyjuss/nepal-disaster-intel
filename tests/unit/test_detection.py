import numpy as np
import pytest

from ndip.domain.detection import (
    NOISE_FLOOR_DB,
    change_mask,
    despeckle,
    log_ratio,
    open_mask,
    to_db,
    valid_mask,
)


def test_db_conversion_matches_the_definition():
    assert to_db(np.array([1.0], dtype=np.float32))[0] == pytest.approx(0.0)
    assert to_db(np.array([0.1], dtype=np.float32))[0] == pytest.approx(-10.0)


def test_non_positive_power_becomes_nan_not_minus_infinity():
    """-inf would sail through any threshold as a huge change."""
    out = to_db(np.array([0.0, -1.0, -32768.0], dtype=np.float32))
    assert np.all(np.isnan(out))


def test_halving_backscatter_is_about_minus_three_db():
    pre = to_db(np.array([0.2], dtype=np.float32))
    post = to_db(np.array([0.1], dtype=np.float32))
    assert log_ratio(pre, post)[0] == pytest.approx(-3.01, abs=0.01)


def test_shape_mismatch_is_caught_rather_than_broadcast():
    with pytest.raises(ValueError, match="shape mismatch"):
        log_ratio(np.zeros((4, 4), np.float32), np.zeros((4, 5), np.float32))


def test_pixels_at_the_noise_floor_are_not_trusted():
    pre = np.array([[NOISE_FLOOR_DB - 1.0, -8.0]], dtype=np.float32)
    post = np.array([[-8.0, -8.0]], dtype=np.float32)
    assert valid_mask(pre, post).tolist() == [[False, True]]


def test_terrain_too_steep_to_image_is_excluded():
    """Layover and shadow are not change; treating them as change is the classic
    false-positive source in mountains."""
    pre = np.array([[-8.0, -8.0]], dtype=np.float32)
    post = np.array([[-8.0, -8.0]], dtype=np.float32)
    slope = np.array([[75.0, 30.0]], dtype=np.float32)
    assert valid_mask(pre, post, slope).tolist() == [[False, True]]


def test_change_is_detected_in_both_directions():
    delta = np.array([[-5.0, 5.0, 1.0]], dtype=np.float32)
    valid = np.ones((1, 3), dtype=bool)
    assert change_mask(delta, valid).tolist() == [[True, True, False]]


def test_invalid_pixels_never_become_change_however_large_the_delta():
    delta = np.array([[99.0]], dtype=np.float32)
    assert change_mask(delta, np.zeros((1, 1), dtype=bool)).tolist() == [[False]]


def test_threshold_must_be_positive():
    with pytest.raises(ValueError):
        change_mask(np.zeros((1, 1), np.float32), np.ones((1, 1), bool), threshold_db=0.0)


def test_despeckle_removes_an_isolated_spike_but_keeps_an_edge():
    field = np.full((5, 5), -8.0, dtype=np.float32)
    field[2, 2] = 20.0
    assert despeckle(field)[2, 2] == pytest.approx(-8.0)

    edge = np.hstack([np.full((5, 3), -12.0), np.full((5, 3), -4.0)]).astype(np.float32)
    smoothed = despeckle(edge)
    assert smoothed[2, 0] == pytest.approx(-12.0)
    assert smoothed[2, 5] == pytest.approx(-4.0)


def test_despeckle_tolerates_nan_without_spreading_it():
    field = np.full((5, 5), -8.0, dtype=np.float32)
    field[2, 2] = np.nan
    assert despeckle(field)[2, 2] == pytest.approx(-8.0)


@pytest.mark.parametrize("window", [0, 2, 4, -1])
def test_despeckle_rejects_windows_without_a_centre(window):
    with pytest.raises(ValueError):
        despeckle(np.zeros((3, 3), np.float32), window=window)


def test_opening_removes_isolated_specks():
    """Speckle passes a per-pixel threshold; it should not survive to be a polygon."""
    mask = np.zeros((9, 9), dtype=bool)
    mask[1, 1] = True  # lone pixel
    mask[3, 3:5] = True  # two-pixel filament
    assert not open_mask(mask).any()


def test_opening_keeps_a_compact_region_at_its_original_size():
    mask = np.zeros((11, 11), dtype=bool)
    mask[3:8, 3:8] = True
    assert open_mask(mask).sum() == mask.sum()


def test_opening_is_a_no_op_at_zero_iterations():
    mask = np.zeros((5, 5), dtype=bool)
    mask[2, 2] = True
    assert open_mask(mask, iterations=0).tolist() == mask.tolist()


def test_opening_rejects_negative_iterations():
    with pytest.raises(ValueError):
        open_mask(np.zeros((3, 3), dtype=bool), iterations=-1)

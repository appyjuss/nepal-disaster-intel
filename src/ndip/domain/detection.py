"""Change-detection arithmetic. Pure array maths — no I/O, no geospatial libraries.

Radar backscatter is multiplicative, so change is a ratio rather than a difference.
Working in decibels turns that ratio into a subtraction and makes a threshold mean
the same thing over bright and dark ground alike.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage

# A 3 dB swing is a doubling or halving of backscattered power. Below roughly this,
# speckle and small incidence-angle differences between passes dominate.
DEFAULT_CHANGE_THRESHOLD_DB = 3.0

# Radar cannot see into layover or shadow, and both track the local slope relative
# to the look direction. Very steep ground is excluded rather than trusted.
MAX_TRUSTWORTHY_SLOPE_DEG = 60.0

# Gamma0 below this is at or under the noise floor — usually water or radar shadow.
NOISE_FLOOR_DB = -25.0


def to_db(linear: np.ndarray) -> np.ndarray:
    """Linear gamma0 power to decibels. Non-positive samples become NaN, not -inf,
    so they propagate as missing rather than as a very large negative change."""
    out = np.full(linear.shape, np.nan, dtype=np.float32)
    valid = np.isfinite(linear) & (linear > 0)
    out[valid] = 10.0 * np.log10(linear[valid])
    return out


def log_ratio(pre_db: np.ndarray, post_db: np.ndarray) -> np.ndarray:
    """Post minus pre, in dB. Negative means the ground got radar-darker."""
    if pre_db.shape != post_db.shape:
        raise ValueError(f"shape mismatch: pre {pre_db.shape} vs post {post_db.shape}")
    return (post_db - pre_db).astype(np.float32)


def valid_mask(
    pre_db: np.ndarray,
    post_db: np.ndarray,
    slope_deg: np.ndarray | None = None,
    *,
    noise_floor_db: float = NOISE_FLOOR_DB,
    max_slope_deg: float = MAX_TRUSTWORTHY_SLOPE_DEG,
) -> np.ndarray:
    """Pixels where a change value can be believed at all.

    Excluding terrain the sensor cannot see is not the same as excluding terrain
    where nothing happened, and conflating the two is how SAR pipelines end up
    reporting cliffs as landslides.
    """
    mask = (
        np.isfinite(pre_db)
        & np.isfinite(post_db)
        & (pre_db > noise_floor_db)
        & (post_db > noise_floor_db)
    )
    if slope_deg is not None:
        if slope_deg.shape != pre_db.shape:
            raise ValueError(f"slope shape {slope_deg.shape} != image shape {pre_db.shape}")
        mask &= np.isfinite(slope_deg) & (slope_deg <= max_slope_deg)
    return mask


def change_mask(
    delta_db: np.ndarray,
    valid: np.ndarray,
    *,
    threshold_db: float = DEFAULT_CHANGE_THRESHOLD_DB,
) -> np.ndarray:
    """Pixels whose backscatter moved further than the threshold, either direction.

    Both directions matter: a landslide strips vegetation and usually brightens
    rough bare rock, while smooth mud or standing water darkens sharply.
    """
    if threshold_db <= 0:
        raise ValueError("threshold must be positive")
    return valid & np.isfinite(delta_db) & (np.abs(delta_db) >= threshold_db)


def open_mask(mask: np.ndarray, iterations: int = 1) -> np.ndarray:
    """Erode then dilate, removing specks and hairline filaments while leaving
    compact regions their original size.

    A per-pixel threshold produces thousands of one- and two-pixel detections that
    are speckle, not ground. Opening is what separates a region from a scatter of
    pixels that merely passed a test.
    """
    if iterations < 0:
        raise ValueError("iterations must be non-negative")
    if iterations == 0:
        return mask
    # Full 3x3 rather than a plus: a plus-shaped element erodes the corners off
    # compact regions, shrinking a genuine 5x5 detection to 21 of its 25 pixels.
    structure = ndimage.generate_binary_structure(2, 2)
    return ndimage.binary_opening(mask, structure=structure, iterations=iterations)


def despeckle(values: np.ndarray, window: int = 3) -> np.ndarray:
    """Median filter over a square window, ignoring NaN.

    Speckle is the reason a per-pixel threshold alone produces confetti. A median
    is used rather than a mean because it removes outliers without dragging real
    edges towards their surroundings.
    """
    if window < 1 or window % 2 == 0:
        raise ValueError("window must be a positive odd number")
    if window == 1:
        return values.astype(np.float32)

    pad = window // 2
    padded = np.pad(values, pad, mode="edge")
    stack = np.empty((window * window, *values.shape), dtype=np.float32)
    for i in range(window):
        for j in range(window):
            stack[i * window + j] = padded[i : i + values.shape[0], j : j + values.shape[1]]

    with np.errstate(invalid="ignore"):
        all_nan = np.all(np.isnan(stack), axis=0)
        out = np.full(values.shape, np.nan, dtype=np.float32)
        out[~all_nan] = np.nanmedian(stack[:, ~all_nan], axis=0)
    return out

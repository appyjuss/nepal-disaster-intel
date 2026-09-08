"""Terrain derivatives. Pure array maths over an elevation grid.

Slope and aspect describe the ground that conditioned a failure. The digital
elevation model predates the event, so it never describes what the event left
behind — only what it acted on.
"""

from __future__ import annotations

import numpy as np

# Compass bearings, clockwise from north, for naming an aspect.
CARDINALS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")

# Below this, a cell has no meaningful downhill direction and its aspect is noise.
FLAT_SLOPE_DEG = 1.0


def slope_from_elevation(elevation: np.ndarray, *, pixel_size_m: float) -> np.ndarray:
    """Steepest gradient at each cell, in degrees."""
    if pixel_size_m <= 0:
        raise ValueError("pixel size must be positive")
    dz_dy, dz_dx = np.gradient(elevation.astype(np.float64), pixel_size_m)
    return np.degrees(np.arctan(np.hypot(dz_dx, dz_dy))).astype(np.float32)


def aspect_from_elevation(elevation: np.ndarray, *, pixel_size_m: float) -> np.ndarray:
    """Downhill direction at each cell, in degrees clockwise from north.

    Flat cells become NaN rather than zero. Zero is due north, a real direction,
    and letting flat ground claim it would drag any average towards the pole.
    """
    if pixel_size_m <= 0:
        raise ValueError("pixel size must be positive")
    grid = elevation.astype(np.float64)
    dz_dy, dz_dx = np.gradient(grid, pixel_size_m)

    # np.gradient's first axis increases downward through the array, which is
    # southward on a north-up grid, so the northward derivative is its negation.
    aspect = np.degrees(np.arctan2(-dz_dx, dz_dy)) % 360.0
    slope = np.degrees(np.arctan(np.hypot(dz_dx, dz_dy)))
    aspect[slope < FLAT_SLOPE_DEG] = np.nan
    return aspect.astype(np.float32)


def circular_mean_degrees(values: np.ndarray) -> float:
    """Mean of compass bearings.

    An arithmetic mean is wrong on a circle: 350 and 10 degrees average to 180,
    pointing exactly opposite to both. Averaging unit vectors and taking the
    resulting bearing gives 0, which is the answer a person would give.
    """
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return float("nan")
    radians = np.radians(finite.astype(np.float64))
    mean = np.degrees(np.arctan2(np.sin(radians).mean(), np.cos(radians).mean()))
    # Rounding before the wrap keeps float noise from landing on 360.0, which is
    # outside the half-open range a bearing lives in and reads as due north's opposite.
    return float(np.round(mean, 9) % 360.0)


def cardinal(bearing: float) -> str:
    """Nearest of the eight compass points."""
    if not np.isfinite(bearing):
        return "flat"
    return CARDINALS[int(((bearing % 360.0) + 22.5) // 45.0) % 8]

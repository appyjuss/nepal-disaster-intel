"""The cross-geometry agreement rule — the part that decides whether a detection
is believable. Synthetic geometry only, no imagery."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest
from affine import Affine
from shapely.geometry import box

from ndip.adapters.raster.rtc import RasterPair
from ndip.application.detect import TrackDetection, _grade_by_agreement
from ndip.domain.change import Confidence
from ndip.domain.event import OrbitState

TRANSFORM = Affine(10.0, 0.0, 0.0, 0.0, -10.0, 1000.0)
SHAPE = (100, 100)
EVENT_ID = "NPL-TEST"


def track(number: int, state: OrbitState, polygons, *, delta: float = -6.0) -> TrackDetection:
    pair = RasterPair(
        pre=np.zeros(SHAPE, np.float32),
        post=np.zeros(SHAPE, np.float32),
        transform=TRANSFORM,
        crs="EPSG:32645",
        resolution_m=10.0,
        pre_date=date(2026, 8, 24),
        post_date=date(2026, 9, 5),
        track=number,
        band="vv",
        frame_ids=(f"frame-{number}",),
    )
    return TrackDetection(
        track=number,
        orbit_state=state,
        pair=pair,
        delta_db=np.full(SHAPE, delta, np.float32),
        slope_deg=np.full(SHAPE, 30.0, np.float32),
        polygons=polygons,
    )


def grade(tracks, mmu: float = 2_000.0):
    return _grade_by_agreement(tracks, min_mapping_unit_m2=mmu, event_id=EVENT_ID)


def test_a_region_both_look_directions_see_is_graded_high():
    region = box(100, 100, 400, 400)  # 90,000 m2
    changes, _ = grade(
        [track(85, OrbitState.ASCENDING, [region]), track(19, OrbitState.DESCENDING, [region])]
    )
    assert len(changes) == 1
    assert changes[0].detected_in == frozenset({"asc", "desc"})
    assert changes[0].confidence() is Confidence.HIGH


def test_a_region_only_one_geometry_sees_never_reaches_high():
    """This is the layover and shadow control: geometry-dependent artefacts appear
    on one look direction only."""
    changes, _ = grade(
        [
            track(85, OrbitState.ASCENDING, [box(100, 100, 400, 400)]),
            track(19, OrbitState.DESCENDING, [box(600, 600, 900, 900)]),
        ]
    )
    assert len(changes) == 2
    assert all(c.confidence() is not Confidence.HIGH for c in changes)
    assert {frozenset({"asc"}), frozenset({"desc"})} == {c.detected_in for c in changes}


def test_partial_overlap_splits_into_agreed_and_single_geometry_parts():
    changes, _ = grade(
        [
            track(85, OrbitState.ASCENDING, [box(0, 0, 400, 400)]),
            track(19, OrbitState.DESCENDING, [box(200, 200, 600, 600)]),
        ]
    )
    agreed = [c for c in changes if c.geometry_agreement]
    assert len(agreed) == 1
    assert agreed[0].area_m2 == pytest.approx(200 * 200)
    assert len(changes) == 3


def test_regions_below_the_minimum_mapping_unit_are_dropped():
    small = box(0, 0, 30, 30)  # 900 m2, under the 2000 m2 default
    changes, _ = grade(
        [track(85, OrbitState.ASCENDING, [small]), track(19, OrbitState.DESCENDING, [small])]
    )
    assert changes == []


def test_two_tracks_of_the_same_geometry_do_not_fake_agreement():
    """Both descending tracks share a look direction, so they cannot corroborate
    each other the way an ascending and a descending pass can."""
    region = box(100, 100, 400, 400)
    changes, _ = grade(
        [track(19, OrbitState.DESCENDING, [region]), track(121, OrbitState.DESCENDING, [region])]
    )
    assert len(changes) == 1
    assert changes[0].detected_in == frozenset({"desc"})
    assert changes[0].confidence() is not Confidence.HIGH


def test_overlapping_polygons_within_one_track_are_not_double_counted():
    changes, _ = grade(
        [
            track(19, OrbitState.DESCENDING, [box(0, 0, 300, 300), box(200, 200, 500, 500)]),
            track(121, OrbitState.DESCENDING, [box(0, 0, 300, 300)]),
        ]
    )
    assert len(changes) == 1  # the union, not two overlapping claims


def test_geometry_is_returned_for_every_graded_polygon():
    region = box(100, 100, 400, 400)
    changes, geometries = grade(
        [track(85, OrbitState.ASCENDING, [region]), track(19, OrbitState.DESCENDING, [region])]
    )
    assert set(geometries) == {c.polygon_id for c in changes}


def test_polygons_come_back_largest_first():
    changes, _ = grade(
        [
            track(85, OrbitState.ASCENDING, [box(0, 0, 100, 100), box(500, 500, 900, 900)]),
            track(19, OrbitState.DESCENDING, [box(0, 0, 100, 100), box(500, 500, 900, 900)]),
        ]
    )
    areas = [c.area_m2 for c in changes]
    assert areas == sorted(areas, reverse=True)


def test_polygon_ids_identify_the_region_not_its_position_in_a_list():
    """Regression: ids were a running index, so a run over a different area or with
    different parameters reused them and upserted over unrelated polygons. A full-AOI
    run overwrote 40 rows from an earlier smaller run."""
    a, _ = grade([track(85, OrbitState.ASCENDING, [box(0, 0, 400, 400)]),
                  track(19, OrbitState.DESCENDING, [box(0, 0, 400, 400)])])
    b, _ = grade([track(85, OrbitState.ASCENDING, [box(500, 500, 900, 900)]),
                  track(19, OrbitState.DESCENDING, [box(500, 500, 900, 900)])])
    assert a[0].polygon_id != b[0].polygon_id, "different regions must not share an id"


def test_the_same_region_gets_the_same_id_on_a_rerun():
    """This is what makes the silver upsert meaningful rather than accidental."""
    tracks = [track(85, OrbitState.ASCENDING, [box(0, 0, 400, 400)]),
              track(19, OrbitState.DESCENDING, [box(0, 0, 400, 400)])]
    first, _ = grade(tracks)
    second, _ = grade(tracks)
    assert first[0].polygon_id == second[0].polygon_id


def test_different_detection_parameters_produce_different_ids():
    """A polygon found at a different threshold is a different claim and must not
    overwrite the original."""
    tracks = [track(85, OrbitState.ASCENDING, [box(0, 0, 400, 400)]),
              track(19, OrbitState.DESCENDING, [box(0, 0, 400, 400)])]
    from ndip.application.detect import _grade_by_agreement
    loose, _ = _grade_by_agreement(tracks, min_mapping_unit_m2=2_000.0, event_id=EVENT_ID,
                                   fingerprint="threshold=2.0")
    strict, _ = _grade_by_agreement(tracks, min_mapping_unit_m2=2_000.0, event_id=EVENT_ID,
                                    fingerprint="threshold=4.0")
    assert loose[0].polygon_id != strict[0].polygon_id

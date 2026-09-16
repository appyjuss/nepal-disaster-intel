from datetime import date, datetime, timezone

import pytest

from ndip.domain.event import DisasterEvent, OrbitState, Scene, ScenePair, best_pair_per_track
from ndip.domain.geometry import BBox

EVENT = DisasterEvent(
    event_id="T", name="t", occurred_on=date(2026, 8, 26), aoi=BBox(84.85, 27.55, 85.45, 28.35)
)


def s(day: int, track: int, state: OrbitState) -> Scene:
    return Scene(
        item_id=f"S1_{day}_{track}",
        collection="sentinel-1-grd",
        acquired_at=datetime(2026, 8, day, 0, 18, tzinfo=timezone.utc),
        orbit_state=state,
        relative_orbit=track,
    )


def test_pair_across_different_tracks_is_rejected():
    """Pairing across relative orbits compares viewing geometries, not ground."""
    with pytest.raises(ValueError, match="one relative orbit"):
        ScenePair(pre=s(24, 19, OrbitState.DESCENDING), post=s(28, 85, OrbitState.DESCENDING), event=EVENT)


def test_pair_across_orbit_states_is_rejected():
    with pytest.raises(ValueError, match="one orbit state"):
        ScenePair(pre=s(24, 19, OrbitState.DESCENDING), post=s(28, 19, OrbitState.ASCENDING), event=EVENT)


def test_real_trishuli_acquisitions_yield_two_tracks_with_opposing_geometry():
    scenes = [
        s(16, 85, OrbitState.ASCENDING),
        s(28, 85, OrbitState.ASCENDING),
        s(24, 19, OrbitState.DESCENDING),
        s(5, 19, OrbitState.DESCENDING),  # placeholder; replaced below
    ]
    scenes[3] = Scene(
        item_id="S1_sep5_19",
        collection="sentinel-1-grd",
        acquired_at=datetime(2026, 9, 5, 0, 18, tzinfo=timezone.utc),
        orbit_state=OrbitState.DESCENDING,
        relative_orbit=19,
    )
    pairs = best_pair_per_track(scenes, EVENT)

    assert set(pairs) == {85, 19}
    assert pairs[85].days_before == 10 and pairs[85].days_after == 2
    assert pairs[19].days_before == 2 and pairs[19].days_after == 10
    assert {p.pre.orbit_state for p in pairs.values()} == {
        OrbitState.ASCENDING,
        OrbitState.DESCENDING,
    }


def test_track_without_a_post_event_scene_is_dropped_not_faked():
    pairs = best_pair_per_track([s(16, 85, OrbitState.ASCENDING), s(24, 85, OrbitState.ASCENDING)], EVENT)
    assert pairs == {}


def test_tightness_prefers_the_pair_closest_to_the_event():
    tight = ScenePair(pre=s(24, 19, OrbitState.DESCENDING), post=s(28, 19, OrbitState.DESCENDING), event=EVENT)
    loose = ScenePair(pre=s(12, 19, OrbitState.DESCENDING), post=s(31, 19, OrbitState.DESCENDING), event=EVENT)
    assert tight.tightness() < loose.tightness()

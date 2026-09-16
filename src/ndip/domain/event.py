"""The disaster event and the acquisition windows derived from it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from ndip.domain.geometry import BBox


class OrbitState(StrEnum):
    ASCENDING = "ascending"
    DESCENDING = "descending"


@dataclass(frozen=True, slots=True)
class DisasterEvent:
    event_id: str
    name: str
    occurred_on: date
    aoi: BBox

    def search_window(self, days_before: int, days_after: int) -> tuple[date, date]:
        if days_before < 0 or days_after < 0:
            raise ValueError("window offsets must be non-negative")
        return (
            self.occurred_on - timedelta(days=days_before),
            self.occurred_on + timedelta(days=days_after),
        )


@dataclass(frozen=True, slots=True)
class Scene:
    """One satellite acquisition, sensor-agnostic."""

    item_id: str
    collection: str
    acquired_at: datetime
    orbit_state: OrbitState | None = None
    relative_orbit: int | None = None
    cloud_cover: float | None = None

    @property
    def acquired_on(self) -> date:
        return self.acquired_at.date()


@dataclass(frozen=True, slots=True)
class ScenePair:
    """A pre/post pair on one repeat track. Valid only within a single geometry."""

    pre: Scene
    post: Scene
    event: DisasterEvent

    def __post_init__(self) -> None:
        if self.pre.acquired_at >= self.post.acquired_at:
            raise ValueError("pre-scene must precede post-scene")
        if self.pre.relative_orbit != self.post.relative_orbit:
            raise ValueError(
                "change detection requires one relative orbit; "
                f"got {self.pre.relative_orbit} and {self.post.relative_orbit}"
            )
        if self.pre.orbit_state is not self.post.orbit_state:
            raise ValueError("change detection requires one orbit state")

    @property
    def brackets_event(self) -> bool:
        """True when the event falls strictly between the two acquisitions."""
        return self.pre.acquired_on < self.event.occurred_on <= self.post.acquired_on

    @property
    def days_before(self) -> int:
        return (self.event.occurred_on - self.pre.acquired_on).days

    @property
    def days_after(self) -> int:
        return (self.post.acquired_on - self.event.occurred_on).days

    @property
    def temporal_span_days(self) -> int:
        return (self.post.acquired_on - self.pre.acquired_on).days

    def tightness(self) -> int:
        """Lower is better: total distance of the pair from the event.

        Used to rank candidate pairs per track. A pair that does not bracket the
        event is never preferred, regardless of how tight it looks.
        """
        if not self.brackets_event:
            raise ValueError("pair does not bracket the event")
        return self.days_before + self.days_after


def best_pair_per_track(scenes: list[Scene], event: DisasterEvent) -> dict[int, ScenePair]:
    """Pick the tightest event-bracketing pre/post pair on each relative orbit.

    Grouping by relative orbit is not an optimisation — pairing across tracks
    compares different viewing geometries and produces change that is pure
    artefact. See ADR 0001.
    """
    by_track: dict[int, list[Scene]] = {}
    for scene in scenes:
        if scene.relative_orbit is None:
            continue
        by_track.setdefault(scene.relative_orbit, []).append(scene)

    best: dict[int, ScenePair] = {}
    for track, track_scenes in by_track.items():
        ordered = sorted(track_scenes, key=lambda s: s.acquired_at)
        pre_candidates = [s for s in ordered if s.acquired_on < event.occurred_on]
        post_candidates = [s for s in ordered if s.acquired_on >= event.occurred_on]
        if not pre_candidates or not post_candidates:
            continue
        pair = ScenePair(pre=pre_candidates[-1], post=post_candidates[0], event=event)
        if pair.brackets_event:
            best[track] = pair
    return best

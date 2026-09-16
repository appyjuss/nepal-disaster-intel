"""Use case: work out which acquisitions can answer the event question.

Dependencies are injected as concrete collaborators. No interface is declared —
there is exactly one STAC implementation and one weather implementation today,
and a port earns its place on evidence of a second, not on prediction of one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import structlog

from ndip.adapters.stac.client import S1_GRD, S2_L2A, StacItem, StacSearch
from ndip.domain.event import DisasterEvent, ScenePair, best_pair_per_track
from ndip.domain.geometry import BBox
from ndip.domain.precipitation import DailyRainfall, RainfallContext, summarise

log = structlog.get_logger(__name__)

# Wide enough to always contain a bracketing pair on a 6-day repeat, narrow enough
# that surface conditions are still comparable across the pair.
SEARCH_DAYS_BEFORE = 21
SEARCH_DAYS_AFTER = 21
WEATHER_DAYS_BEFORE = 30
WEATHER_DAYS_AFTER = 10

# Above this, an optical scene tells you about cloud, not about ground.
USABLE_CLOUD_COVER_PCT = 40.0


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    event: DisasterEvent
    radar_pairs: dict[int, ScenePair]
    optical_candidates: list[StacItem]
    rainfall: RainfallContext
    radar_items: list[StacItem]

    @property
    def has_dual_geometry(self) -> bool:
        """Two tracks with opposing look directions — the ADR 0001 confidence basis."""
        states = {
            pair.pre.orbit_state for pair in self.radar_pairs.values() if pair.pre.orbit_state
        }
        return len(states) >= 2

    def summary_lines(self) -> list[str]:
        lines = [
            f"Event      : {self.event.name} ({self.event.event_id}) on {self.event.occurred_on}",
            f"AOI        : {self.event.aoi.as_list()}",
            f"Radar pairs: {len(self.radar_pairs)} track(s), "
            f"dual-geometry={'YES' if self.has_dual_geometry else 'NO'}",
        ]
        for track, pair in sorted(self.radar_pairs.items()):
            state = pair.pre.orbit_state.value if pair.pre.orbit_state else "unknown"
            lines.append(
                f"  track {track:>3} {state:<10} "
                f"{pair.pre.acquired_on} (-{pair.days_before}d) -> "
                f"{pair.post.acquired_on} (+{pair.days_after}d)  "
                f"span={pair.temporal_span_days}d tightness={pair.tightness()}"
            )
        usable = [
            i
            for i in self.optical_candidates
            if i.scene.cloud_cover is not None and i.scene.cloud_cover <= USABLE_CLOUD_COVER_PCT
        ]
        lines.append(
            f"Optical    : {len(self.optical_candidates)} scene(s), "
            f"{len(usable)} under {USABLE_CLOUD_COVER_PCT:.0f}% cloud (corroboration only)"
        )
        r = self.rainfall
        pattern = (
            "cloudburst"
            if r.is_cloudburst_pattern
            else "saturation"
            if r.is_saturation_pattern
            else "inconclusive"
        )
        lines.append(
            f"Rainfall   : event-day {r.on_event_day_mm:.1f}mm | "
            f"7d {r.window_totals_mm.get(7, 0):.1f}mm | "
            f"14d {r.window_totals_mm.get(14, 0):.1f}mm | "
            f"30d {r.window_totals_mm.get(30, 0):.1f}mm | "
            f"API {r.antecedent_index_mm:.1f}mm -> {pattern} pattern"
        )
        lines.append("             (ERA5 ~25km grid: context, not measurement)")
        return lines


def discover(
    event: DisasterEvent,
    *,
    search: StacSearch,
    fetch_rainfall: Callable[[BBox, date, date], list[DailyRainfall]],
) -> DiscoveryResult:
    """Find the usable radar pairs, optical corroboration, and rainfall context."""
    radar_start, radar_end = event.search_window(SEARCH_DAYS_BEFORE, SEARCH_DAYS_AFTER)

    radar_items = search.search(
        collection=S1_GRD, bbox=event.aoi, start=radar_start, end=radar_end
    )
    pairs = best_pair_per_track([i.scene for i in radar_items], event)
    if not pairs:
        raise LookupError(
            f"no event-bracketing radar pair on any track for {event.event_id} "
            f"in {radar_start}..{radar_end}"
        )

    optical_items = search.search(
        collection=S2_L2A, bbox=event.aoi, start=radar_start, end=radar_end
    )

    weather_start, weather_end = event.search_window(WEATHER_DAYS_BEFORE, WEATHER_DAYS_AFTER)
    rainfall = summarise(
        fetch_rainfall(event.aoi, weather_start, weather_end), event.occurred_on
    )

    log.info(
        "discovery.complete",
        event_id=event.event_id,
        radar_tracks=len(pairs),
        dual_geometry=len({p.pre.orbit_state for p in pairs.values()}) >= 2,
        optical_scenes=len(optical_items),
    )
    return DiscoveryResult(
        event=event,
        radar_pairs=pairs,
        optical_candidates=optical_items,
        rainfall=rainfall,
        radar_items=radar_items,
    )

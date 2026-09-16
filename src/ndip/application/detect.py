"""Use case: detect surface change between two radar passes, on every track, and
grade each result by whether the look directions agree.

Per-track detection is the easy half. The half that decides whether the output is
worth anything is the comparison across geometries: layover and shadow move when
the look direction moves, real ground change does not.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import structlog
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from ndip.adapters.raster.rtc import RasterPair, RtcLoader
from ndip.adapters.raster.vectorize import mask_to_polygons, zonal_mean
from ndip.domain.change import DEFAULT_MIN_MAPPING_UNIT_M2, ChangePolygon
from ndip.domain.detection import (
    DEFAULT_CHANGE_THRESHOLD_DB,
    change_mask,
    despeckle,
    log_ratio,
    open_mask,
    to_db,
    valid_mask,
)
from ndip.domain.event import DisasterEvent, OrbitState, ScenePair

log = structlog.get_logger(__name__)

# Two geometries agreeing on a sliver do not agree. A polygon must share this much
# of its area with the other look direction to count as corroborated.
MIN_AGREEMENT_OVERLAP = 0.25


@dataclass(frozen=True, slots=True)
class TrackDetection:
    track: int
    orbit_state: OrbitState
    pair: RasterPair
    delta_db: np.ndarray
    slope_deg: np.ndarray
    polygons: list[BaseGeometry]


@dataclass(frozen=True, slots=True)
class DetectionResult:
    event: DisasterEvent
    tracks: list[TrackDetection]
    changes: list[ChangePolygon]
    geometries_of: dict[str, BaseGeometry]

    def summary_lines(self) -> list[str]:
        from ndip.domain.change import Confidence

        lines = [f"Change detection for {self.event.event_id}"]
        for t in self.tracks:
            lines.append(
                f"  track {t.track:>3} {t.orbit_state.value:<10} "
                f"{t.pair.pre_date} -> {t.pair.post_date}  "
                f"{len(t.polygons):>4} raw polygons"
            )
        graded: dict[str, int] = {}
        for c in self.changes:
            graded[c.confidence().value] = graded.get(c.confidence().value, 0) + 1
        lines.append(f"  after agreement + size filtering: {len(self.changes)} polygons")
        for grade in ("high", "medium", "low"):
            if graded.get(grade):
                lines.append(f"    {grade:<7} {graded[grade]:>4}")
        high = [c for c in self.changes if c.confidence() is Confidence.HIGH]
        if high:
            biggest = max(high, key=lambda c: c.area_m2)
            lines.append(
                f"  largest corroborated: {biggest.area_m2 / 10_000:.1f} ha, "
                f"slope {biggest.mean_slope_deg:.0f} deg, "
                f"{biggest.backscatter_delta_db:+.1f} dB, {biggest.classify().value}"
            )
        return lines


def detect_change(
    event: DisasterEvent,
    pairs: dict[int, ScenePair],
    *,
    loader: RtcLoader,
    load_slope: Callable[[int, tuple[int, int]], np.ndarray],
    band: str = "vv",
    threshold_db: float = DEFAULT_CHANGE_THRESHOLD_DB,
    min_mapping_unit_m2: float = DEFAULT_MIN_MAPPING_UNIT_M2,
) -> DetectionResult:
    tracks: list[TrackDetection] = []
    slope: np.ndarray | None = None

    for track, scene_pair in sorted(pairs.items()):
        orbit_state = scene_pair.pre.orbit_state
        if orbit_state is None:
            log.warning("detect.track.skipped", track=track, reason="unknown orbit state")
            continue

        raster = loader.load_pair(
            event.aoi,
            track=track,
            pre=scene_pair.pre.acquired_on,
            post=scene_pair.post.acquired_on,
            band=band,
        )
        if slope is None:
            slope = load_slope(int(raster.resolution_m), raster.pre.shape)

        pre_db, post_db = to_db(raster.pre), to_db(raster.post)
        delta = despeckle(log_ratio(pre_db, post_db))
        usable = valid_mask(pre_db, post_db, slope)
        changed = open_mask(change_mask(delta, usable, threshold_db=threshold_db))

        polygons = mask_to_polygons(changed, raster.transform)
        log.info(
            "detect.track.complete",
            track=track,
            orbit=orbit_state.value,
            changed_px=int(changed.sum()),
            usable_px_pct=round(float(usable.mean()) * 100, 1),
            polygons=len(polygons),
        )
        tracks.append(
            TrackDetection(
                track=track,
                orbit_state=orbit_state,
                pair=raster,
                delta_db=delta,
                slope_deg=slope,
                polygons=polygons,
            )
        )

    if not tracks:
        raise LookupError(f"no track produced a detection for {event.event_id}")

    changes, geometries = _grade_by_agreement(
        tracks,
        min_mapping_unit_m2=min_mapping_unit_m2,
        event_id=event.event_id,
        fingerprint=(
            f"band={band}|threshold_db={threshold_db}|mmu={min_mapping_unit_m2}"
            f"|res={tracks[0].pair.resolution_m}"
        ),
    )
    return DetectionResult(event=event, tracks=tracks, changes=changes, geometries_of=geometries)


def _polygon_id(event_id: str, geometry: BaseGeometry, fingerprint: str) -> str:
    """An id that names the region, not its position in a list.

    A running index collides across runs: a second run over a different area reuses
    the same ids and upserts over unrelated polygons. Deriving the id from the
    geometry and the parameters that produced it makes a rerun genuinely idempotent
    and keeps two different claims apart.
    """
    payload = f"{event_id}|{fingerprint}|{geometry.wkt}".encode()
    return f"{event_id}-{hashlib.sha256(payload).hexdigest()[:12]}"


def _grade_by_agreement(
    tracks: list[TrackDetection],
    *,
    min_mapping_unit_m2: float,
    event_id: str,
    fingerprint: str = "",
) -> tuple[list[ChangePolygon], dict[str, BaseGeometry]]:
    """Split detections into the part both look directions saw and the parts only
    one did.

    Working with unions rather than pairs of polygons keeps a region that one track
    splits in two and another sees whole from being counted twice.
    """
    by_state: dict[str, list[BaseGeometry]] = {}
    for t in tracks:
        key = "asc" if t.orbit_state is OrbitState.ASCENDING else "desc"
        by_state.setdefault(key, []).extend(t.polygons)

    unions = {k: unary_union(v) for k, v in by_state.items() if v}
    asc, desc = unions.get("asc"), unions.get("desc")

    regions: list[tuple[BaseGeometry, frozenset[str]]] = []
    if asc is not None and desc is not None:
        regions += [(g, frozenset({"asc", "desc"})) for g in _explode(asc.intersection(desc))]
        regions += [(g, frozenset({"asc"})) for g in _explode(asc.difference(desc))]
        regions += [(g, frozenset({"desc"})) for g in _explode(desc.difference(asc))]
    else:
        only = "asc" if asc is not None else "desc"
        regions += [(g, frozenset({only})) for g in _explode(unions[only])]

    transform = tracks[0].pair.transform
    slope = tracks[0].slope_deg

    changes: list[ChangePolygon] = []
    geometries: dict[str, BaseGeometry] = {}
    for geom, detected_in in regions:
        if geom.is_empty or geom.area < min_mapping_unit_m2:
            continue
        mean_slope = zonal_mean(slope, geom, transform)
        deltas = [
            zonal_mean(t.delta_db, geom, transform)
            for t in tracks
            if ("asc" if t.orbit_state is OrbitState.ASCENDING else "desc") in detected_in
        ]
        finite = [d for d in deltas if np.isfinite(d)]
        if not np.isfinite(mean_slope) or not finite:
            continue

        polygon_id = _polygon_id(event_id, geom, fingerprint)
        geometries[polygon_id] = geom
        changes.append(
            ChangePolygon(
                polygon_id=polygon_id,
                area_m2=float(geom.area),
                mean_slope_deg=float(np.clip(mean_slope, 0.0, 90.0)),
                backscatter_delta_db=max(finite, key=abs),
                detected_in=detected_in,
                attributes={
                    "tracks": sorted({t.track for t in tracks}),
                    "band": tracks[0].pair.band,
                    "crs": tracks[0].pair.crs,
                    "resolution_m": tracks[0].pair.resolution_m,
                },
            )
        )

    changes.sort(key=lambda c: c.area_m2, reverse=True)
    log.info(
        "detect.agreement.complete",
        event_id=event_id,
        polygons=len(changes),
        corroborated=sum(1 for c in changes if c.geometry_agreement),
    )
    return changes, geometries


def _explode(geometry: BaseGeometry) -> list[BaseGeometry]:
    if geometry.is_empty:
        return []
    if hasattr(geometry, "geoms"):
        return [g for g in geometry.geoms if not g.is_empty and g.geom_type == "Polygon"]
    return [geometry] if geometry.geom_type == "Polygon" else []

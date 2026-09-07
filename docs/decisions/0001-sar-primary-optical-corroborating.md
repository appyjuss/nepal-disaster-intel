# ADR 0001 — Sentinel-1 SAR is the primary sensor; Sentinel-2 corroborates

**Date:** 2026-09-07 · **Status:** accepted

## Context

The event is 26 August 2026, peak Nepali monsoon. The instinctive design is Sentinel-2
optical change detection (NDVI before/after), which is what most tutorials teach.

A STAC query over the AOI for 2026-08-01 → 2026-09-07 returned:

- **Sentinel-2:** every scene in the event window is cloud-obscured. Best pre-event look is
  2026-08-24 at 39% cloud; best post-event is 2026-08-27 at 54%, and cloud masks do not
  align across the four MGRS tiles. The last clear look is 2026-08-12 — 14 days pre-event.
- **Sentinel-1:** clean 6-day repeats on two tracks, bracketing the event on both an
  ascending and a descending geometry (see contract-v1.md).

## Decision

Sentinel-1 GRD backscatter change is the primary detection method. Both orbit geometries
are processed independently and agreement between them is the confidence signal.
Sentinel-2 is ingested for visual corroboration and human QA only; no V1 acceptance
criterion depends on it.

## Why not optical

Optical was not rejected on taste — it was rejected on measured cloud cover in the actual
event window. Under monsoon, an optical-primary design fails silently: the pipeline runs,
produces polygons, and those polygons are cloud edges.

## Consequences

- Needs a radiometric terrain-correction step; mountainous SAR is geometrically hard.
  Layover/shadow is the dominant false-positive source, which is precisely why
  dual-geometry agreement is an acceptance criterion rather than a nice-to-have.
- The pipeline is cloud-independent, so it generalises to any Nepali monsoon event —
  which is the whole point of V3 and V6.
- Sentinel-2 ingestion stays cheap and optional; it must never become a hard dependency.

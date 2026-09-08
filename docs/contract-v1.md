# V1 Contract — Trishuli Reconstruction

**Status:** agreed 2026-09-07. Everything below is scope-frozen until V1 ships.

## Question V1 answers

> What changed on the ground around the Trishuli corridor during the 26 August 2026 event,
> what infrastructure and population sat inside the changed area, and what environmental
> conditions preceded it?

V1 **reconstructs**. It does not predict. Prediction is V4.

## Area and time

| Parameter | Value |
|---|---|
| AOI bbox (EPSG:4326) | `84.85, 27.55, 85.45, 28.35` — Trishuli corridor, Nuwakot/Rasuwa |
| Event date | 2026-08-26 |
| Change pair A (ascending, rel. orbit 85) | pre 2026-08-16 → post 2026-08-28 |
| Change pair B (descending, rel. orbit 19) | pre 2026-08-24 → post 2026-09-05 |
| Weather context window | 2026-07-27 → 2026-09-05 (30 d antecedent + 10 d after) |

## Acceptance criteria

1. `gold.change_polygon` exists: surface-change polygons from Sentinel-1 backscatter,
   each carrying `area_m2`, `mean_slope_deg`, `confidence`, and `detected_in`
   (`asc` / `desc` / **`both`**).
2. Only polygons with `detected_in = 'both'` are reported as high confidence. Single-geometry
   detections are retained but flagged — this is the layover/shadow control.
3. `gold.exposure` exists: for each high-confidence polygon, counts of OSM/Overture roads,
   bridges and buildings within the polygon and within a 500 m buffer, plus WorldPop
   population sum.
4. `gold.event_context` exists: daily precipitation, 7/14/30-day antecedent precipitation
   index, and terrain stats (slope, aspect, elevation, distance-to-drainage) per polygon.
5. Every gold row traces to source: `stac_item_id`s, acquisition datetimes, processing version.
6. `mise run test` is green; the whole pipeline reruns from cold with one command.
   **Met.** `mise run pipeline` rebuilt every table from a deleted warehouse in 133
   seconds, reproducing the same 41 / 62 / 732 / 16 / 16 rows the staged runs produced.

## Explicitly out of scope for V1

- Any ML model. No susceptibility, no impact prediction.
- Any GenAI. No copilot, no report generator, no agent.
- Interferometric coherence (needs SLC + a full InSAR chain).
- Nepal-wide coverage. One AOI, one event.
- Real AWS spend. Infra code is written and tested against mocked providers only.
- A web UI. Outputs are Iceberg tables + GeoParquet + a static map export.

## Known limitations to state in every output

- ERA5 reanalysis is ~25 km grid; it under-reads orographic rainfall in Himalayan valleys.
  Precipitation figures are **context, not measurement**.
- Sentinel-1 backscatter change detects *surface change*, not "landslide". Harvest, river
  migration, construction and flooding all register. V1 reports change; attribution is human.
- COP-DEM GLO-30 is 30 m and predates the event, so it cannot show terrain change — only
  the pre-event terrain that conditioned it.
- Optical corroboration is weak: best post-event Sentinel-2 look is 54% cloud.

## V1 results so far (2026-09-07)

Full area of interest, three tracks, 30 m, 3 dB threshold, 1 ha minimum: **88 seconds**,
732 polygons, of which **16 are corroborated by both look directions**. Every
high-confidence detection is a *darkening* of 5.7 to 7.2 dB on near-flat ground,
clustered along the valley floor — the signature of smooth new surface, meaning water
or fresh mud. The medium-confidence detections are the opposite: brightening on 26 to
28 degree slopes, which is what freshly exposed rough ground looks like.

Two further limitations this surfaced, on top of those above:

- Twelve days separate the pre and post images in peak monsoon. Normal river-level
  change produces the same valley-floor darkening as flooding does, so the
  valley-floor detections are not separable from seasonal variation on radar alone.
- Track 121 covers only ~16% of the area, so its contribution is partial. Coverage is
  reported per track as `usable_px_pct` and should be read before trusting a track.

## Exposure results (2026-09-07)

All 16 corroborated regions assessed against Overture `2026-08-19.0` and WorldPop
2020 constrained, 500 m buffer: **6430 buildings** and **411 road segments** inside the
buffers, **14 of 16 regions with a bridge within 500 m**, and about **22 650 people** —
that last figure indicative only, since the product sums ~20% above the national
estimate and its ratio is stored per row. One region has nothing mapped near it at all.

Building density inside the buffers runs ~420/km² against an area-wide 226/km²,
which is the expected direction: detections sit on the inhabited valley floor while
the wider area includes empty high mountains.

## Event context results (2026-09-07)

Terrain and rainfall for all 16 corroborated regions. The two gold tables together
change the reading of the headline result:

- **15 regions** sit at 386–616 m on 1.6–14.9 degree slopes, 0–37 m from a mapped
  channel, each with hundreds of buildings and 493–3299 people in its buffer. Valley
  floor, on the drainage network, inhabited.
- **1 region** sits at 6822 m on a 45.5 degree face, 2517 m from any channel, with
  zero buildings, roads, bridges or people near it.

**Neither group is confidently a landslide.** The valley-floor detections carry the
river-variation caveat already recorded above. The high-altitude one is best explained
as wet snow, which absorbs radar and darkens by several decibels — a known false
positive at that elevation, not a slope failure, despite being corroborated by both
look directions and classified `slope_failure_like` on slope alone.

The honest V1 conclusion is that the pipeline reliably finds *surface change* and
grades it well, and that separating flood, river variation and snow from slope failure
needs evidence this pipeline does not yet carry. That is a V4 question, not a V1 one.

## Version ladder (not V1 scope, recorded so V1 does not paint us into a corner)

V2 lakehouse + Spark, Nepal-wide · V3 historical disaster corpus · V4 ML susceptibility/impact
· V5 GenAI analyst layer · V6 continuous ingest.

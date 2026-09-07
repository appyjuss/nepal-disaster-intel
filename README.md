# Nepal Disaster Intelligence Platform

A geospatial lakehouse that reconstructs disaster events over Nepal from open Earth-observation,
weather, terrain and infrastructure data — then layers ML and a GenAI analyst on top.

Runs entirely on free data and local compute. Cloud is a later deployment target, not a
dependency.

**V1 case study:** the 26 August 2026 Trishuli Valley event.

```
Sentinel-1 · Sentinel-2 · COP-DEM · ERA5 · Overture/OSM · WorldPop
                            │
                     bronze → silver → gold   (Apache Iceberg)
                            │
        ┌───────────────────┼───────────────────┐
   what changed?       what was exposed?    what preceded it?
   SAR change          roads, bridges,      rainfall, slope,
   detection           buildings, people    drainage
```

## Status

| Stage | State |
|---|---|
| V1 discovery — scene pairing, rainfall context | **working** |
| V1 change detection, exposure, gold tables | next |
| V2 lakehouse at Nepal scale (Spark) | planned |
| V3 historical disaster corpus | planned |
| V4 ML susceptibility / impact | planned |
| V5 GenAI analyst layer | planned |

## Quickstart

```bash
mise run install
mise run test
uv run ndip discover
```

See `CLAUDE.md` for commands and `docs/contract-v1.md` for what V1 does and does not do.

# Nepal Disaster Intelligence Platform (ndip)

Reconstructs what changed on the ground during a disaster event, what was exposed, and
what conditions preceded it. V1 scope is frozen in `docs/contract-v1.md` — read it before
adding anything.

## Commands & Deploy

```bash
mise run install      # uv sync --all-extras
mise run test         # unit tests, no network (fast, this is the default gate)
mise run test-all     # includes -m integration, which hits live STAC/weather APIs
mise run lint         # ruff check + format check
mise run typecheck    # mypy strict over src/ndip
mise run tf-test      # opentofu validate + test with mocked providers (no cloud, no Docker)

uv run ndip discover                       # live discovery for the Trishuli event
uv run ndip discover --bbox "w,s,e,n" -v   # override AOI
```

Docker Desktop on Windows with WSL integration enabled for this Ubuntu distro. If `docker`
vanishes from PATH, the toggle is Docker Desktop → Settings → Resources → WSL Integration.

## Gotchas that have already bitten

- **Never pair Sentinel-1 scenes across relative orbits or orbit states.** Different viewing
  geometry produces change that is pure artefact. `ScenePair` enforces this at construction;
  do not relax it.
- **httpx `params={}` replaces a URL's query string, it does not merge.** Passing an empty
  dict when following a STAC `next` link wiped the server cursor and re-requested page one
  until the page ceiling — a search that looked like 503 results was 13 unique items, 50×
  duplicated. See `tests/unit/test_stac_pagination.py`.
- **A truncated STAC search must raise, not warn.** Pair selection on a partial scene list
  looks perfectly healthy and is wrong.
- **Optical is not the primary sensor here.** Monsoon cloud makes Sentinel-2 useless in the
  event window. See ADR 0001 before "just adding NDVI".
- **ERA5 is a ~25 km grid.** Every rainfall figure leaving this system carries the
  "context, not measurement" caveat. Do not present it as observed rainfall.

## Layering

`domain/` is pure — no HTTP, no GDAL, no env, no frameworks. That is why the suite runs in
0.1s. `application/` orchestrates and takes its collaborators by injection. `adapters/`
owns every byte of I/O and validates untrusted external input at the boundary.

No ports/interfaces are declared yet: there is one STAC implementation and one weather
implementation. Add an interface when a second real implementation arrives, not before.

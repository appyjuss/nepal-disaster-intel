"""STAC catalogue access. Untrusted remote JSON is validated into domain types here."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime

import structlog

from ndip.adapters.http import build_client, get_json
from ndip.domain.event import OrbitState, Scene
from ndip.domain.geometry import BBox

log = structlog.get_logger(__name__)

EARTH_SEARCH = "https://earth-search.aws.element84.com/v1"
PLANETARY_COMPUTER = "https://planetarycomputer.microsoft.com/api/stac/v1"

S1_GRD = "sentinel-1-grd"
S2_L2A = "sentinel-2-l2a"
COP_DEM_30 = "cop-dem-glo-30"

# STAC servers cap page size; this is the per-request limit, not a result cap.
PAGE_SIZE = 100
# Hard ceiling so a mis-scoped query cannot page forever.
MAX_PAGES = 50


@dataclass(frozen=True, slots=True)
class StacItem:
    """A STAC item, kept whole. `raw` is the open bag the pipeline forwards to
    bronze without the layers re-declaring every asset key."""

    scene: Scene
    assets: dict[str, str]
    raw: dict


class StacSearch:
    def __init__(self, endpoint: str = EARTH_SEARCH) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._client = build_client()

    def __enter__(self) -> StacSearch:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Explicit teardown. The client owns a connection pool."""
        self._client.close()

    def search(
        self,
        *,
        collection: str,
        bbox: BBox,
        start: date,
        end: date,
        max_items: int | None = None,
    ) -> list[StacItem]:
        if start > end:
            raise ValueError(f"start {start} is after end {end}")
        items = list(
            self._paginate(
                collection=collection, bbox=bbox, start=start, end=end, max_items=max_items
            )
        )
        log.info(
            "stac.search.complete",
            collection=collection,
            count=len(items),
            start=str(start),
            end=str(end),
        )
        return items

    def _paginate(
        self,
        *,
        collection: str,
        bbox: BBox,
        start: date,
        end: date,
        max_items: int | None,
    ) -> Iterator[StacItem]:
        params: dict[str, object] | None = {
            "collections": collection,
            "bbox": ",".join(str(v) for v in bbox.as_list()),
            "datetime": f"{start.isoformat()}T00:00:00Z/{end.isoformat()}T23:59:59Z",
            "limit": PAGE_SIZE,
        }
        url = f"{self._endpoint}/search"
        seen: set[str] = set()
        yielded = 0

        for _ in range(MAX_PAGES):
            payload = get_json(self._client, url, params=params)
            features = payload.get("features") or []
            for feature in features:
                item = _to_item(feature, collection)
                # STAC pages can overlap at the boundary; the same item arriving
                # twice must not become two scenes in a pair-selection input.
                if item is None or item.scene.item_id in seen:
                    continue
                seen.add(item.scene.item_id)
                yield item
                yielded += 1
                if max_items is not None and yielded >= max_items:
                    return

            next_link = next(
                (link for link in payload.get("links", []) if link.get("rel") == "next"),
                None,
            )
            if not next_link or not features:
                return

            url = next_link["href"]
            if (next_link.get("method") or "GET").upper() == "POST":
                params = next_link.get("body") or {}
            else:
                # The href already carries the server's cursor. Passing an explicit
                # params dict here would REPLACE that query string and re-request
                # page one forever — the defect this branch exists to prevent.
                params = None
                if "limit=" not in url:
                    sep = "&" if "?" in url else "?"
                    url = f"{url}{sep}limit={PAGE_SIZE}"

        # Truncating a search silently is worse than failing it: pair selection
        # would run on a partial scene list and look perfectly healthy.
        raise RuntimeError(
            f"STAC page ceiling ({MAX_PAGES}) reached for {collection} with more pages "
            f"pending; narrow the AOI or date range, or raise MAX_PAGES deliberately"
        )


def _to_item(feature: dict, collection: str) -> StacItem | None:
    """Coerce one remote feature into a domain Scene. Malformed items are skipped
    with a warning rather than killing a whole search."""
    props = feature.get("properties") or {}
    item_id = feature.get("id")
    raw_dt = props.get("datetime")
    if not item_id or not raw_dt:
        log.warning("stac.item.malformed", item_id=item_id, reason="missing id or datetime")
        return None
    try:
        acquired_at = datetime.fromisoformat(raw_dt.replace("Z", "+00:00"))
    except ValueError:
        log.warning("stac.item.malformed", item_id=item_id, datetime=raw_dt)
        return None

    orbit_raw = props.get("sat:orbit_state")
    try:
        orbit = OrbitState(orbit_raw) if orbit_raw else None
    except ValueError:
        orbit = None

    scene = Scene(
        item_id=item_id,
        collection=collection,
        acquired_at=acquired_at,
        orbit_state=orbit,
        relative_orbit=props.get("sat:relative_orbit"),
        cloud_cover=props.get("eo:cloud_cover"),
    )
    assets = {
        key: asset["href"]
        for key, asset in (feature.get("assets") or {}).items()
        if isinstance(asset, dict) and asset.get("href")
    }
    return StacItem(scene=scene, assets=assets, raw=feature)

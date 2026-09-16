"""Regression: following a STAC `next` link must not drop the page size.

Symptom that prompted this: a search over the Trishuli AOI logged
`stac.search.page_ceiling` and truncated at 503 items, because pages after the
first fell back to the server's default page size (10) instead of 100.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from ndip.adapters.stac.client import PAGE_SIZE, StacSearch
from ndip.domain.geometry import BBox
from datetime import date

AOI = BBox(84.85, 27.55, 85.45, 28.35)
ENDPOINT = "https://stac.test/v1"


def _feature(n: int) -> dict:
    return {
        "id": f"S1_{n}",
        "properties": {
            "datetime": "2026-08-24T00:18:00Z",
            "sat:orbit_state": "descending",
            "sat:relative_orbit": 19,
        },
        "assets": {"vv": {"href": f"s3://x/{n}/vv.tif"}},
    }


@respx.mock
def test_page_size_is_preserved_across_next_links():
    seen_limits: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_limits.append(request.url.params.get("limit"))
        page = int(request.url.params.get("page", "1"))
        if page < 3:
            return httpx.Response(
                200,
                json={
                    "features": [_feature(page)],
                    "links": [{"rel": "next", "method": "GET",
                               "href": f"{ENDPOINT}/search?page={page + 1}"}],
                },
            )
        return httpx.Response(200, json={"features": [_feature(page)], "links": []})

    respx.get(url__startswith=f"{ENDPOINT}/search").mock(side_effect=handler)

    with StacSearch(ENDPOINT) as search:
        items = search.search(
            collection="sentinel-1-grd", bbox=AOI, start=date(2026, 8, 5), end=date(2026, 9, 16)
        )

    assert len(items) == 3
    assert seen_limits == [str(PAGE_SIZE)] * 3, f"limit dropped after page 1: {seen_limits}"


@respx.mock
def test_duplicate_items_across_pages_are_not_double_counted():
    responses = [
        httpx.Response(200, json={"features": [_feature(1), _feature(2)],
                                  "links": [{"rel": "next", "method": "GET",
                                             "href": f"{ENDPOINT}/search?page=2"}]}),
        httpx.Response(200, json={"features": [_feature(2), _feature(3)], "links": []}),
    ]
    respx.get(url__startswith=f"{ENDPOINT}/search").mock(side_effect=responses)

    with StacSearch(ENDPOINT) as search:
        items = search.search(
            collection="sentinel-1-grd", bbox=AOI, start=date(2026, 8, 5), end=date(2026, 9, 16)
        )

    assert [i.scene.item_id for i in items] == ["S1_1", "S1_2", "S1_3"]


@respx.mock
def test_a_server_that_never_stops_paging_is_bounded_not_infinite():
    respx.get(url__startswith=f"{ENDPOINT}/search").mock(
        return_value=httpx.Response(200, json={
            "features": [_feature(1)],
            "links": [{"rel": "next", "method": "GET", "href": f"{ENDPOINT}/search?page=9"}],
        })
    )
    with StacSearch(ENDPOINT) as search, pytest.raises(RuntimeError, match="page ceiling"):
        search.search(collection="sentinel-1-grd", bbox=AOI,
                      start=date(2026, 8, 5), end=date(2026, 9, 16))

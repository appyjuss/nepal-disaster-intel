"""Bronze layer round-trip and idempotency, against a real Iceberg catalog on a
temporary directory. No network."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest

from ndip.adapters.config import load_settings
from ndip.adapters.lakehouse.bronze import BronzeWriter
from ndip.adapters.lakehouse.catalog import build_catalog
from ndip.adapters.lakehouse.schemas import RAINFALL_DAILY, STAC_ITEMS
from ndip.adapters.stac.client import StacItem
from ndip.domain.event import OrbitState, Scene
from ndip.domain.precipitation import DailyRainfall

EVENT_ID = "NPL-2026-08-26-TRISHULI"
ENDPOINT = "https://earth-search.aws.element84.com/v1"


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    for var in ("NDIP_WAREHOUSE", "NDIP_CATALOG_URI", "NDIP_S3_ENDPOINT"):
        monkeypatch.delenv(var, raising=False)
    return build_catalog(load_settings(data_dir=tmp_path))


@pytest.fixture
def writer(catalog):
    return BronzeWriter(catalog)


def stac_item(item_id: str, *, track: int = 19, cloud: float | None = None) -> StacItem:
    scene = Scene(
        item_id=item_id,
        collection="sentinel-1-grd",
        acquired_at=datetime(2026, 8, 24, 0, 18, tzinfo=UTC),
        orbit_state=OrbitState.DESCENDING,
        relative_orbit=track,
        cloud_cover=cloud,
    )
    return StacItem(
        scene=scene,
        assets={"vv": f"s3://bucket/{item_id}/vv.tif"},
        raw={"id": item_id, "properties": {"sat:relative_orbit": track}},
    )


RAIN = [
    DailyRainfall(on=date(2026, 8, 24), precipitation_mm=17.7),
    DailyRainfall(on=date(2026, 8, 25), precipitation_mm=5.9),
    DailyRainfall(on=date(2026, 8, 26), precipitation_mm=2.9),
]


def rows(catalog, identifier: str) -> list[dict]:
    return catalog.load_table(identifier).scan().to_arrow().to_pylist()


def test_stac_items_round_trip_with_provenance(writer, catalog):
    writer.write_stac_items(
        [stac_item("S1_A"), stac_item("S1_B")], event_id=EVENT_ID, source_endpoint=ENDPOINT
    )
    stored = sorted(rows(catalog, STAC_ITEMS), key=lambda r: r["item_id"])

    assert [r["item_id"] for r in stored] == ["S1_A", "S1_B"]
    assert stored[0]["event_id"] == EVENT_ID
    assert stored[0]["source_endpoint"] == ENDPOINT
    assert stored[0]["relative_orbit"] == 19
    assert stored[0]["orbit_state"] == "descending"
    assert dict(stored[0]["assets"])["vv"] == "s3://bucket/S1_A/vv.tif"
    assert stored[0]["ingested_at"] is not None


def test_raw_stac_feature_is_preserved_verbatim(writer, catalog):
    """Bronze keeps the source payload so a new field never means re-downloading."""
    writer.write_stac_items([stac_item("S1_A")], event_id=EVENT_ID, source_endpoint=ENDPOINT)
    stored = rows(catalog, STAC_ITEMS)[0]
    assert json.loads(stored["raw"])["properties"]["sat:relative_orbit"] == 19


def test_reingesting_the_same_items_updates_rather_than_duplicates(writer, catalog):
    """A retried job must not double the table. This is the whole point of bronze
    being keyed rather than append-only."""
    items = [stac_item("S1_A"), stac_item("S1_B")]
    writer.write_stac_items(items, event_id=EVENT_ID, source_endpoint=ENDPOINT)
    writer.write_stac_items(items, event_id=EVENT_ID, source_endpoint=ENDPOINT)
    writer.write_stac_items(items, event_id=EVENT_ID, source_endpoint=ENDPOINT)

    assert len(rows(catalog, STAC_ITEMS)) == 2


def test_reingest_picks_up_corrected_source_values(writer, catalog):
    writer.write_stac_items(
        [stac_item("S2_A", cloud=38.5)], event_id=EVENT_ID, source_endpoint=ENDPOINT
    )
    writer.write_stac_items(
        [stac_item("S2_A", cloud=41.0)], event_id=EVENT_ID, source_endpoint=ENDPOINT
    )

    stored = rows(catalog, STAC_ITEMS)
    assert len(stored) == 1
    assert stored[0]["cloud_cover"] == pytest.approx(41.0)


def test_same_item_id_under_a_different_event_is_a_separate_row(writer, catalog):
    """event_id is part of the key, so two events sharing a scene stay independent."""
    writer.write_stac_items([stac_item("S1_A")], event_id=EVENT_ID, source_endpoint=ENDPOINT)
    writer.write_stac_items([stac_item("S1_A")], event_id="NPL-2025-OTHER", source_endpoint=ENDPOINT)

    assert len(rows(catalog, STAC_ITEMS)) == 2


def test_rainfall_round_trip_records_the_grid_it_came_from(writer, catalog):
    """The coarse grid is a property of the data and must travel with it."""
    writer.write_rainfall(
        RAIN,
        event_id=EVENT_ID,
        latitude=27.95,
        longitude=85.15,
        source="open-meteo/era5",
        grid_resolution_km=25.0,
    )
    stored = sorted(rows(catalog, RAINFALL_DAILY), key=lambda r: r["observed_on"])

    assert len(stored) == 3
    assert stored[-1]["observed_on"] == date(2026, 8, 26)
    assert stored[-1]["precipitation_mm"] == pytest.approx(2.9)
    assert stored[0]["source"] == "open-meteo/era5"
    assert stored[0]["grid_resolution_km"] == pytest.approx(25.0)


def test_reingesting_rainfall_is_idempotent(writer, catalog):
    for _ in range(3):
        writer.write_rainfall(
            RAIN, event_id=EVENT_ID, latitude=27.95, longitude=85.15, source="open-meteo/era5"
        )
    assert len(rows(catalog, RAINFALL_DAILY)) == 3


def test_empty_input_writes_nothing_and_does_not_raise(writer):
    assert writer.write_stac_items([], event_id=EVENT_ID, source_endpoint=ENDPOINT) == 0
    assert (
        writer.write_rainfall([], event_id=EVENT_ID, latitude=0.0, longitude=0.0, source="x") == 0
    )

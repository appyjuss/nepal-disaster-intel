"""The contract's last acceptance criterion: the whole pipeline rebuilds from an
empty warehouse with one command.

Marked integration because it hits every upstream source. Run with
`mise run test-all`, or drive the same graph from the shell with `mise run pipeline`.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "bronze.stac_items",
    "bronze.rainfall_daily",
    "silver.change_polygons",
    "gold.exposure",
    "gold.event_context",
}


def test_pipeline_rebuilds_every_table_from_an_empty_warehouse(tmp_path, monkeypatch):
    from dagster import materialize

    from ndip.adapters.config import load_settings
    from ndip.adapters.lakehouse.catalog import build_catalog
    from ndip.orchestration.definitions import (
        bronze_observations,
        gold_event_context,
        gold_exposure,
        silver_change_polygons,
    )

    monkeypatch.setenv("NDIP_DATA", str(tmp_path))
    for var in ("NDIP_WAREHOUSE", "NDIP_CATALOG_URI", "NDIP_S3_ENDPOINT"):
        monkeypatch.delenv(var, raising=False)

    result = materialize(
        [bronze_observations, silver_change_polygons, gold_exposure, gold_event_context]
    )
    assert result.success

    catalog = build_catalog(load_settings(data_dir=tmp_path))
    found = {".".join(t) for ns in ("bronze", "silver", "gold") for t in catalog.list_tables(ns)}
    assert found >= EXPECTED_TABLES

    for name in EXPECTED_TABLES:
        rows = catalog.load_table(name).scan().to_arrow().num_rows
        assert rows > 0, f"{name} rebuilt empty"

"""The report's payload. Assembled from rows, so it can be tested without rendering."""

from __future__ import annotations

import numpy as np
from shapely.geometry import box

from ndip.adapters.report.build import ReportInputs, build_payload, hillshade_png_base64
from ndip.application.events import TRISHULI_2026_08_26 as EVENT


def polygon(pid: str, *, conf="high", db=-6.0, elev=450.0, area=30_000.0) -> dict:
    return {
        "event_id": EVENT.event_id,
        "polygon_id": pid,
        "geometry_wkt": box(85.10, 27.90, 85.11, 27.91).wkt,
        "centroid_lon": 85.105,
        "centroid_lat": 27.905,
        "area_m2": area,
        "mean_slope_deg": 5.0,
        "backscatter_delta_db": db,
        "confidence": conf,
        "change_class": "valley_floor_like",
        "detected_in": "asc+desc",
    }


def inputs(polys, *, exposure=None, context=None, total=732) -> ReportInputs:
    return ReportInputs(
        polygons=polys,
        total_regions=total,
        exposure=exposure or {},
        context=context or {},
        rainfall=[{"d": "2026-08-26", "mm": 2.9}],
        radar=[
            {"t": "2026-08-24T00:18", "track": 19, "orbit": "descending"},
            {"t": "2026-08-16T12:21", "track": 85, "orbit": "ascending"},
        ],
        optical=[{"t": "2026-08-24", "cloud": 38.6}],
    )


def payload(inp: ReportInputs) -> dict:
    return build_payload(
        EVENT,
        inp,
        hillshade="",
        bronze_rows=103,
        threshold_db=3.0,
        min_area_m2=10_000.0,
        band="vv",
        resolution_m=30.0,
        buffer_m=500.0,
        overture_release="2026-08-19.0",
    )


def test_headline_counts_come_from_the_tables_not_from_prose():
    facts = payload(inputs([polygon("a"), polygon("b", conf="medium")]))["facts"]
    assert facts["total"] == 732
    assert facts["graded"] == 2
    assert facts["high"] == 1
    assert facts["tracks"] == 2


def test_the_high_altitude_outlier_is_detected_from_the_data():
    """The page says 'all but one' only when the data actually splits that way."""
    ctx = {
        "low": {"elevation_m": 450.0, "distance_to_drainage_m": 12.0},
        "high": {"elevation_m": 6822.0, "distance_to_drainage_m": 2517.0},
    }
    facts = payload(inputs([polygon("low"), polygon("high")], context=ctx))["facts"]
    assert facts["high_alt"] is True
    assert facts["high_alt_elev"] == 6822
    assert facts["valley_floor"] == 1


def test_with_every_region_on_the_valley_floor_there_is_no_outlier_to_mention():
    ctx = {"a": {"elevation_m": 450.0, "distance_to_drainage_m": 5.0}}
    facts = payload(inputs([polygon("a")], context=ctx))["facts"]
    assert facts["high_alt"] is False
    assert facts["high_alt_elev"] is None


def test_all_darkening_is_asserted_only_when_it_is_true():
    both = [polygon("a", db=-6.0), polygon("b", db=+6.0)]
    assert payload(inputs(both))["facts"]["high_all_darkening"] is False
    assert payload(inputs([polygon("a", db=-6.0)]))["facts"]["high_all_darkening"] is True


def test_exposure_totals_are_summed_across_regions():
    exp = {
        "a": {
            "buildings_in_buffer": 100,
            "roads_in_buffer": 10,
            "bridges_in_buffer": 2,
            "population_in_buffer": 500.0,
        },
        "b": {
            "buildings_in_buffer": 0,
            "roads_in_buffer": 0,
            "bridges_in_buffer": 0,
            "population_in_buffer": 0.0,
        },
    }
    facts = payload(inputs([polygon("a"), polygon("b")], exposure=exp))["facts"]
    assert facts["exposure"]["buildings"] == 100
    assert facts["exposure"]["with_bridge"] == 1
    assert facts["exposure"]["empty"] == 1


def test_a_region_missing_from_gold_shows_as_absent_rather_than_zero():
    """A polygon with no exposure row has not been measured; reporting zero would
    claim nothing was near it."""
    row = payload(inputs([polygon("a")]))["polygons"][0]
    assert row["buildings"] is None
    assert row["elev"] is None


def test_hillshade_renders_to_an_embeddable_png():
    dem = np.tile(np.linspace(0, 1000, 40, dtype=np.float32), (30, 1))
    encoded = hillshade_png_base64(dem)
    assert encoded.startswith("iVBOR")  # PNG magic, base64-encoded
    assert len(encoded) > 100


def test_geometry_is_simplified_but_still_closed():
    rings = payload(inputs([polygon("a")]))["polygons"][0]["rings"]
    assert rings and rings[0][0] == rings[0][-1]

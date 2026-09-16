import pytest

from ndip.domain.change import ChangeClass, ChangePolygon, Confidence, reportable


def poly(**kw) -> ChangePolygon:
    base = dict(
        polygon_id="p1",
        area_m2=50_000.0,
        mean_slope_deg=32.0,
        backscatter_delta_db=-4.5,
        detected_in=frozenset({"asc", "desc"}),
        attributes={},
    )
    return ChangePolygon(**{**base, **kw})


def test_agreement_across_geometries_is_the_only_route_to_high_confidence():
    assert poly().confidence() is Confidence.HIGH
    single = poly(detected_in=frozenset({"asc"}))
    assert single.confidence() is Confidence.MEDIUM


def test_a_weak_single_geometry_detection_is_low_not_medium():
    assert poly(detected_in=frozenset({"desc"}), backscatter_delta_db=-1.2).confidence() is Confidence.LOW


def test_speckle_sized_polygons_are_rejected_regardless_of_agreement():
    assert poly(area_m2=500.0).confidence() is Confidence.REJECTED


def test_valley_floor_change_is_classified_separately_from_slope_failure():
    assert poly(mean_slope_deg=3.0).classify() is ChangeClass.VALLEY_FLOOR_LIKE
    assert poly(mean_slope_deg=32.0).classify() is ChangeClass.SLOPE_FAILURE_LIKE


def test_reportable_drops_rejects_and_orders_by_area():
    polys = [
        poly(polygon_id="small", area_m2=800.0),
        poly(polygon_id="mid", area_m2=10_000.0),
        poly(polygon_id="big", area_m2=90_000.0),
    ]
    assert [p.polygon_id for p in reportable(polys)] == ["big", "mid"]


@pytest.mark.parametrize("slope", [-1.0, 91.0])
def test_impossible_slope_is_rejected_at_construction(slope):
    with pytest.raises(ValueError):
        poly(mean_slope_deg=slope)


def test_detection_must_name_a_geometry():
    with pytest.raises(ValueError):
        poly(detected_in=frozenset())

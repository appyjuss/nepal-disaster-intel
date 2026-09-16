import pytest
from shapely.geometry import Point, box

from ndip.adapters.raster.vectorize import buffer_metres
from ndip.domain.exposure import DEFAULT_BUFFER_M, Exposure, FeatureCount


def exp(**kw) -> Exposure:
    base = dict(
        polygon_id="p1",
        roads=FeatureCount(1, 4),
        bridges=FeatureCount(0, 1),
        buildings=FeatureCount(2, 30),
        population_within=12.0,
        population_in_buffer=340.0,
        buffer_m=DEFAULT_BUFFER_M,
    )
    return Exposure(**{**base, **kw})


def test_a_buffer_cannot_contain_less_than_the_polygon_it_surrounds():
    """The buffer includes the polygon, so a smaller buffer count is a join bug,
    not a valid reading."""
    with pytest.raises(ValueError, match="below the count inside"):
        FeatureCount(within=9, in_buffer=3)


def test_nearby_only_excludes_what_is_already_inside():
    assert FeatureCount(within=3, in_buffer=11).nearby_only == 8


def test_negative_counts_are_rejected():
    with pytest.raises(ValueError):
        FeatureCount(within=-1, in_buffer=0)


def test_a_region_with_nothing_mapped_around_it_reads_as_empty():
    empty = exp(
        roads=FeatureCount(0, 0),
        bridges=FeatureCount(0, 0),
        buildings=FeatureCount(0, 0),
        population_within=0.0,
        population_in_buffer=0.0,
    )
    assert empty.is_empty is True
    assert exp().is_empty is False


def test_a_single_nearby_road_means_the_region_is_not_empty():
    assert exp(
        roads=FeatureCount(0, 1),
        bridges=FeatureCount(0, 0),
        buildings=FeatureCount(0, 0),
        population_within=0.0,
        population_in_buffer=0.0,
    ).is_empty is False


def test_bridges_are_flagged_because_one_bridge_decides_connectivity():
    assert exp(bridges=FeatureCount(0, 1)).touches_a_bridge is True
    assert exp(bridges=FeatureCount(0, 0)).touches_a_bridge is False


def test_negative_population_is_rejected():
    with pytest.raises(ValueError):
        exp(population_within=-1.0)


def test_buffer_must_be_positive():
    with pytest.raises(ValueError):
        exp(buffer_m=0.0)


def test_metric_buffer_is_round_on_the_ground_not_in_degrees():
    """A degree of longitude at 28N is ~12% shorter than a degree of latitude, so a
    buffer applied in degrees would come out an ellipse."""
    buffered = buffer_metres(Point(85.15, 27.95), 500.0, projected_epsg=32645)
    w, s, e, n = buffered.bounds
    width_km = (e - w) * 111.32 * 0.8835
    height_km = (n - s) * 111.32
    assert width_km == pytest.approx(1.0, abs=0.05)
    assert height_km == pytest.approx(1.0, abs=0.05)
    assert width_km / height_km == pytest.approx(1.0, abs=0.03)


def test_buffered_polygon_contains_the_original():
    poly = box(85.10, 27.90, 85.11, 27.91)
    assert buffer_metres(poly, 500.0, projected_epsg=32645).contains(poly)


def test_a_non_positive_buffer_distance_is_rejected():
    with pytest.raises(ValueError):
        buffer_metres(Point(85.15, 27.95), 0.0, projected_epsg=32645)

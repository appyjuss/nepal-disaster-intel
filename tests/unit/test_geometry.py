import pytest

from ndip.domain.geometry import BBox


def test_parse_roundtrip():
    assert BBox.parse("84.85, 27.55, 85.45, 28.35").as_list() == [84.85, 27.55, 85.45, 28.35]


@pytest.mark.parametrize(
    "text",
    ["84.85,27.55,85.45", "a,b,c,d", "85.45,27.55,84.85,28.35", "84.85,28.35,85.45,27.55"],
)
def test_malformed_input_rejected_at_the_boundary(text):
    with pytest.raises(ValueError):
        BBox.parse(text)


def test_centroid_is_where_weather_is_sampled():
    assert BBox(84.0, 27.0, 86.0, 29.0).centroid == (85.0, 28.0)

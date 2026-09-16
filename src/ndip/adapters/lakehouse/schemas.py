"""Iceberg schemas for the bronze layer.

Bronze holds what the source said, with provenance, and nothing else. No
derived values, no cleaning — silver is where interpretation starts. Keeping
that line sharp is what makes a bad transform recoverable without re-fetching.
"""

from __future__ import annotations

from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import IdentityTransform
from pyiceberg.types import (
    DateType,
    DoubleType,
    IntegerType,
    MapType,
    NestedField,
    StringType,
    TimestamptzType,
)

BRONZE_NAMESPACE = "bronze"
SILVER_NAMESPACE = "silver"
GOLD_NAMESPACE = "gold"

STAC_ITEMS = f"{BRONZE_NAMESPACE}.stac_items"
RAINFALL_DAILY = f"{BRONZE_NAMESPACE}.rainfall_daily"
CHANGE_POLYGONS = f"{SILVER_NAMESPACE}.change_polygons"
EXPOSURE = f"{GOLD_NAMESPACE}.exposure"
EVENT_CONTEXT = f"{GOLD_NAMESPACE}.event_context"

# Natural keys. Re-ingesting the same event must update these rows, not add to them.
STAC_ITEMS_KEY = ["event_id", "collection", "item_id"]
RAINFALL_DAILY_KEY = ["event_id", "observed_on"]
CHANGE_POLYGONS_KEY = ["event_id", "polygon_id"]
EXPOSURE_KEY = ["event_id", "polygon_id"]
EVENT_CONTEXT_KEY = ["event_id", "polygon_id"]

STAC_ITEMS_SCHEMA = Schema(
    NestedField(1, "event_id", StringType(), required=True),
    NestedField(2, "collection", StringType(), required=True),
    NestedField(3, "item_id", StringType(), required=True),
    NestedField(4, "acquired_at", TimestamptzType(), required=True),
    NestedField(5, "orbit_state", StringType(), required=False),
    NestedField(6, "relative_orbit", IntegerType(), required=False),
    NestedField(7, "cloud_cover", DoubleType(), required=False),
    NestedField(
        8,
        "assets",
        MapType(key_id=12, key_type=StringType(), value_id=13, value_type=StringType()),
        required=False,
    ),
    # The untouched STAC feature. Cheap now, and it is the difference between
    # adding a field later and re-downloading a catalogue.
    NestedField(9, "raw", StringType(), required=True),
    NestedField(10, "source_endpoint", StringType(), required=True),
    NestedField(11, "ingested_at", TimestamptzType(), required=True),
    identifier_field_ids=[],
)

# One event and two collections in V1, so identity partitioning gives a handful of
# well-sized files. Time-based partitioning here would produce one tiny file per day.
STAC_ITEMS_PARTITION = PartitionSpec(
    PartitionField(source_id=1, field_id=1000, transform=IdentityTransform(), name="event_id"),
    PartitionField(source_id=2, field_id=1001, transform=IdentityTransform(), name="collection"),
)

RAINFALL_DAILY_SCHEMA = Schema(
    NestedField(1, "event_id", StringType(), required=True),
    NestedField(2, "observed_on", DateType(), required=True),
    NestedField(3, "precipitation_mm", DoubleType(), required=True),
    NestedField(4, "latitude", DoubleType(), required=True),
    NestedField(5, "longitude", DoubleType(), required=True),
    # Named so a later switch to GPM IMERG or DHM gauges is visible in the data,
    # not just in a commit message.
    NestedField(6, "source", StringType(), required=True),
    NestedField(7, "grid_resolution_km", DoubleType(), required=False),
    NestedField(8, "ingested_at", TimestamptzType(), required=True),
    identifier_field_ids=[],
)

RAINFALL_DAILY_PARTITION = PartitionSpec(
    PartitionField(source_id=1, field_id=1000, transform=IdentityTransform(), name="event_id"),
)


# Silver is where interpretation begins, so every row records the parameters it was
# produced under. A polygon detected at a 3 dB threshold is a different claim from
# one detected at 2 dB, and without the parameters the two are indistinguishable.
CHANGE_POLYGONS_SCHEMA = Schema(
    NestedField(1, "event_id", StringType(), required=True),
    NestedField(2, "polygon_id", StringType(), required=True),
    NestedField(3, "geometry_wkt", StringType(), required=True),
    NestedField(4, "centroid_lon", DoubleType(), required=True),
    NestedField(5, "centroid_lat", DoubleType(), required=True),
    NestedField(6, "area_m2", DoubleType(), required=True),
    NestedField(7, "mean_slope_deg", DoubleType(), required=True),
    NestedField(8, "backscatter_delta_db", DoubleType(), required=True),
    NestedField(9, "detected_in", StringType(), required=True),
    NestedField(10, "confidence", StringType(), required=True),
    NestedField(11, "change_class", StringType(), required=True),
    NestedField(12, "crs", StringType(), required=True),
    NestedField(13, "resolution_m", DoubleType(), required=True),
    NestedField(14, "band", StringType(), required=True),
    NestedField(15, "threshold_db", DoubleType(), required=True),
    NestedField(16, "min_mapping_unit_m2", DoubleType(), required=True),
    NestedField(17, "source_frame_ids", StringType(), required=True),
    NestedField(18, "detected_at", TimestamptzType(), required=True),
    identifier_field_ids=[],
)

CHANGE_POLYGONS_PARTITION = PartitionSpec(
    PartitionField(source_id=1, field_id=1000, transform=IdentityTransform(), name="event_id"),
)


# Gold answers a question, so each row carries the answer and everything needed to
# audit it: which reference data, which buffer, and how far the population product
# is known to run above the national estimate.
EXPOSURE_SCHEMA = Schema(
    NestedField(1, "event_id", StringType(), required=True),
    NestedField(2, "polygon_id", StringType(), required=True),
    NestedField(3, "area_m2", DoubleType(), required=True),
    NestedField(4, "confidence", StringType(), required=True),
    NestedField(5, "change_class", StringType(), required=True),
    NestedField(6, "centroid_lon", DoubleType(), required=True),
    NestedField(7, "centroid_lat", DoubleType(), required=True),
    NestedField(8, "buffer_m", DoubleType(), required=True),
    NestedField(9, "roads_within", IntegerType(), required=True),
    NestedField(10, "roads_in_buffer", IntegerType(), required=True),
    NestedField(11, "bridges_within", IntegerType(), required=True),
    NestedField(12, "bridges_in_buffer", IntegerType(), required=True),
    NestedField(13, "buildings_within", IntegerType(), required=True),
    NestedField(14, "buildings_in_buffer", IntegerType(), required=True),
    NestedField(15, "population_within", DoubleType(), required=True),
    NestedField(16, "population_in_buffer", DoubleType(), required=True),
    NestedField(17, "overture_release", StringType(), required=True),
    NestedField(18, "population_product", StringType(), required=True),
    NestedField(19, "population_bias", DoubleType(), required=True),
    NestedField(20, "source_frame_ids", StringType(), required=True),
    NestedField(21, "assessed_at", TimestamptzType(), required=True),
    identifier_field_ids=[],
)

EXPOSURE_PARTITION = PartitionSpec(
    PartitionField(source_id=1, field_id=1000, transform=IdentityTransform(), name="event_id"),
)


# Rainfall is shared across the area because the reanalysis grid is far coarser than
# the gap between two polygons; terrain is measured inside each one. Both travel on
# the same row so a reader never has to join to interpret a figure.
EVENT_CONTEXT_SCHEMA = Schema(
    NestedField(1, "event_id", StringType(), required=True),
    NestedField(2, "polygon_id", StringType(), required=True),
    NestedField(3, "event_date", DateType(), required=True),
    NestedField(4, "elevation_m", DoubleType(), required=True),
    NestedField(5, "slope_deg", DoubleType(), required=True),
    NestedField(6, "aspect_deg", DoubleType(), required=False),
    NestedField(7, "aspect_cardinal", StringType(), required=True),
    NestedField(8, "distance_to_drainage_m", DoubleType(), required=True),
    NestedField(9, "precipitation_event_day_mm", DoubleType(), required=True),
    NestedField(10, "precipitation_7d_mm", DoubleType(), required=True),
    NestedField(11, "precipitation_14d_mm", DoubleType(), required=True),
    NestedField(12, "precipitation_30d_mm", DoubleType(), required=True),
    NestedField(13, "antecedent_index_mm", DoubleType(), required=True),
    NestedField(14, "rainfall_pattern", StringType(), required=True),
    NestedField(15, "rainfall_source", StringType(), required=True),
    NestedField(16, "rainfall_grid_km", DoubleType(), required=True),
    NestedField(17, "terrain_source", StringType(), required=True),
    NestedField(18, "drainage_source", StringType(), required=True),
    NestedField(19, "built_at", TimestamptzType(), required=True),
    identifier_field_ids=[],
)

EVENT_CONTEXT_PARTITION = PartitionSpec(
    PartitionField(source_id=1, field_id=1000, transform=IdentityTransform(), name="event_id"),
)

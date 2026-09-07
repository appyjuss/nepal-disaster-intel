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

# Natural keys. Re-ingesting the same event must update these rows, not add to them.
STAC_ITEMS_KEY = ["event_id", "collection", "item_id"]
RAINFALL_DAILY_KEY = ["event_id", "observed_on"]

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

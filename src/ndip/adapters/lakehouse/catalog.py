"""Iceberg catalog wiring.

The catalog is SQLite locally and AWS Glue or an Iceberg REST catalog in the
cloud. pyiceberg hides that difference, so nothing above this module knows which
one it is talking to.
"""

from __future__ import annotations

import contextlib

import structlog
from pyiceberg.catalog import Catalog, load_catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError
from pyiceberg.partitioning import PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.table import Table

from ndip.adapters.config import Settings
from ndip.adapters.lakehouse.schemas import (
    BRONZE_NAMESPACE,
    GOLD_NAMESPACE,
    SILVER_NAMESPACE,
)

log = structlog.get_logger(__name__)

MEDALLION_NAMESPACES = (BRONZE_NAMESPACE, SILVER_NAMESPACE, GOLD_NAMESPACE)


def build_catalog(settings: Settings) -> Catalog:
    catalog = load_catalog(settings.catalog_name, **settings.catalog_properties())
    for namespace in MEDALLION_NAMESPACES:
        with contextlib.suppress(NamespaceAlreadyExistsError):
            catalog.create_namespace(namespace)
    log.info(
        "catalog.ready",
        catalog=settings.catalog_name,
        warehouse=settings.warehouse_uri,
        object_storage=settings.uses_object_storage,
    )
    return catalog


def ensure_table(
    catalog: Catalog, identifier: str, schema: Schema, partition_spec: PartitionSpec
) -> Table:
    """Load a table, creating it if absent. Safe to call on every run."""
    try:
        return catalog.load_table(identifier)
    except NoSuchTableError:
        table = catalog.create_table(
            identifier=identifier, schema=schema, partition_spec=partition_spec
        )
        log.info("catalog.table.created", table=identifier)
        return table

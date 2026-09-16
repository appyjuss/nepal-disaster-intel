"""Runtime configuration. The only place in the project that reads the environment.

Local and cloud differ by URI, not by code path: point the warehouse at a local
directory or at s3:// and everything above this layer is unchanged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[3] / "data"


@dataclass(frozen=True, slots=True)
class Settings:
    warehouse_uri: str
    catalog_uri: str
    catalog_name: str = "ndip"
    s3_endpoint: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"

    @property
    def uses_object_storage(self) -> bool:
        return self.warehouse_uri.startswith("s3://")

    def catalog_properties(self) -> dict[str, str]:
        props = {"uri": self.catalog_uri, "warehouse": self.warehouse_uri}
        if self.uses_object_storage:
            # Credentials are read from the environment, never written to config
            # files and never logged. Absent values are left out so the AWS
            # default credential chain can take over in the cloud.
            optional = {
                "s3.endpoint": self.s3_endpoint,
                "s3.access-key-id": self.s3_access_key,
                "s3.secret-access-key": self.s3_secret_key,
                "s3.region": self.s3_region,
            }
            props.update({k: v for k, v in optional.items() if v})
        return props


def load_settings(data_dir: Path | None = None) -> Settings:
    root = data_dir or Path(os.environ.get("NDIP_DATA", DEFAULT_DATA_DIR))
    root.mkdir(parents=True, exist_ok=True)
    warehouse = (root / "warehouse").resolve()
    warehouse.mkdir(parents=True, exist_ok=True)

    return Settings(
        warehouse_uri=os.environ.get("NDIP_WAREHOUSE", f"file://{warehouse}"),
        catalog_uri=os.environ.get("NDIP_CATALOG_URI", f"sqlite:///{root.resolve()}/catalog.db"),
        catalog_name=os.environ.get("NDIP_CATALOG_NAME", "ndip"),
        s3_endpoint=os.environ.get("NDIP_S3_ENDPOINT"),
        s3_access_key=os.environ.get("AWS_ACCESS_KEY_ID"),
        s3_secret_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        s3_region=os.environ.get("AWS_REGION", "us-east-1"),
    )

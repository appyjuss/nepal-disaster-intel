"""GDAL network settings for reading cloud-hosted rasters.

GDAL's defaults include no read timeout, so a stalled connection to a remote COG
hangs the process indefinitely with no error and no traffic. That is not
hypothetical: it is what an unsigned s3:// DEM read did here before this existed.
"""

from __future__ import annotations

import os

GDAL_DEFAULTS = {
    # Nothing may block forever.
    "GDAL_HTTP_CONNECTTIMEOUT": "15",
    "GDAL_HTTP_TIMEOUT": "60",
    "GDAL_HTTP_MAX_RETRY": "4",
    "GDAL_HTTP_RETRY_DELAY": "2",
    # Without this GDAL lists the whole remote directory when opening one COG,
    # which on a large public bucket costs far more than the read itself.
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff",
    "VSI_CACHE": "TRUE",
    "VSI_CACHE_SIZE": "134217728",
}


def configure_gdal() -> None:
    """Apply the defaults without overriding anything already set deliberately."""
    for key, value in GDAL_DEFAULTS.items():
        os.environ.setdefault(key, value)

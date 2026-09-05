"""Database package."""

from .connection import init_db, open_connection
from .jdf_repository import (
    DEFAULT_PROJECT_ID,
    empty_document,
    fetch_latest_jdf,
    fetch_latest_jdf_or_empty,
    save_jdf_revision,
)

__all__ = [
    "DEFAULT_PROJECT_ID",
    "empty_document",
    "fetch_latest_jdf",
    "fetch_latest_jdf_or_empty",
    "init_db",
    "open_connection",
    "save_jdf_revision",
]

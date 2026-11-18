"""Shared path preparation rules for local SQLite databases."""

from __future__ import annotations

import os
from pathlib import Path


def prepare_sqlite_database_path(database_path: Path) -> None:
    """Create a private database directory and reject database/parent symlinks."""
    if database_path.is_symlink():
        raise ValueError("SQLite database cannot be a symbolic link.")
    database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if database_path.parent.is_symlink():
        raise ValueError("SQLite database directory cannot be a symbolic link.")
    os.chmod(database_path.parent, 0o700)

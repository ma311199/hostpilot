"""Lifecycle helper for the optional async LangGraph SQLite checkpointer."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


@asynccontextmanager
async def open_sqlite_checkpointer(
    database_path: Path | str,
) -> AsyncIterator[AsyncSqliteSaver]:
    """Open and initialize a SQLite checkpointer from the agents dependency group."""
    try:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    except ImportError as error:
        raise RuntimeError(
            "Install the agents dependency group to enable persistent graph checkpoints."
        ) from error

    path = Path(database_path).expanduser()
    if path.is_symlink():
        raise ValueError("Checkpoint database cannot be a symbolic link.")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise ValueError("Checkpoint directory cannot be a symbolic link.")
    os.chmod(path.parent, 0o700)
    async with AsyncSqliteSaver.from_conn_string(str(path)) as checkpointer:
        await checkpointer.setup()
        os.chmod(path, 0o600)
        yield checkpointer


__all__ = ["open_sqlite_checkpointer"]

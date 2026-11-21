"""Framework-neutral session state Port used by Runtime-owned chat execution."""

from __future__ import annotations

from typing import Protocol

from hostpilot.models import ModelMessage


class RuntimeSessionStatePort(Protocol):
    """Load normalized history and append completed model messages for a session."""

    async def load_messages(self, session_id: str) -> tuple[ModelMessage, ...]: ...

    async def append_messages(
        self,
        session_id: str,
        messages: tuple[ModelMessage, ...],
    ) -> None: ...


__all__ = ["RuntimeSessionStatePort"]

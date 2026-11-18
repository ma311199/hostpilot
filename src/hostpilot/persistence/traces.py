"""Bounded SQLite storage for explicitly approved trace metadata."""

from __future__ import annotations

import json
import math
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import aiosqlite

from hostpilot.observability import TraceContext, TraceEvent

_SAFE_ATTRIBUTE_KEYS = frozenset(
    {
        "status",
        "reason_code",
        "chunk_count",
        "character_count",
        "skill_id",
        "tool_id",
        "execution_mode",
        "duration_ms",
        "route",
        "total_tokens",
        "retrieval_version",
        "candidate_count",
        "candidate_id",
        "lexical_candidate_count",
        "semantic_candidate_count",
        "embedding_status",
        "embedding_reason",
        "embedding_duration_ms",
        "reranker_status",
        "reranker_reason",
        "reranker_duration_ms",
        "reranker_fusion_applied",
        "rrf_skill_ids",
        "reranker_raw_skill_ids",
        "reranker_score_summary",
        "reranked_skill_ids",
        "model_role",
        "attempt",
        "gate_passed",
        "classifier_decision",
        "selected_skill_id",
        "decision_confidence",
        "scored_candidate_count",
        "top_candidate_id",
        "top_confidence",
        "confidence_margin",
        "confidence",
        "source",
        "task_id",
        "execution_type",
        "source_task_id",
        "invoked_tool_count",
        "side_effect_count",
        "outcome_uncertain",
    }
)


@dataclass(frozen=True)
class TraceSummary:
    """Safe overview of one correlated request trace."""

    trace_id: UUID
    first_occurred_at: datetime
    last_occurred_at: datetime
    event_count: int
    last_event: str
    status: str | None
    skill_id: str | None


@dataclass(frozen=True)
class StoredTraceEvent:
    """One persisted trace event and its generated row identifier."""

    event_id: UUID
    event: TraceEvent


class SQLiteTraceStore:
    """Persist bounded, allowlisted trace events without request content."""

    def __init__(
        self,
        database_path: Path | str,
        *,
        retention_days: int = 30,
        max_events: int = 100_000,
    ) -> None:
        if retention_days < 1 or max_events < 1:
            raise ValueError("Trace retention and event limits must be positive.")
        self.database_path = Path(database_path).expanduser()
        self.retention_days = retention_days
        self.max_events = max_events

    async def initialize(self) -> None:
        """Create the trace table and its request/time indexes."""
        if self.database_path.is_symlink():
            raise ValueError("Trace database cannot be a symbolic link.")
        self.database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.database_path.parent.is_symlink():
            raise ValueError("Trace database directory cannot be a symbolic link.")
        os.chmod(self.database_path.parent, 0o700)
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute(
                """CREATE TABLE IF NOT EXISTS trace_events (
                    event_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    span_id TEXT NOT NULL,
                    parent_span_id TEXT,
                    name TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    attributes_json TEXT NOT NULL
                )"""
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_trace_events_trace_time "
                "ON trace_events(trace_id, occurred_at)"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_trace_events_time ON trace_events(occurred_at DESC)"
            )
            await connection.commit()
        os.chmod(self.database_path, 0o600)

    def record(self, event: TraceEvent) -> None:
        """Store one event after applying the persistence allowlist."""
        attributes = {
            key: (
                value[:8192]
                if isinstance(value, str)
                and key
                in {
                    "rrf_skill_ids",
                    "reranker_raw_skill_ids",
                    "reranker_score_summary",
                    "reranked_skill_ids",
                }
                else value[:160]
                if isinstance(value, str)
                else value
            )
            for key, value in event.attributes.items()
            if key in _SAFE_ATTRIBUTE_KEYS
            and not (isinstance(value, float) and not math.isfinite(value))
        }
        occurred_at = event.occurred_at.astimezone(UTC)
        retention_cutoff = datetime.now(UTC) - timedelta(days=self.retention_days)
        with sqlite3.connect(self.database_path, timeout=5) as connection:
            connection.execute(
                """INSERT INTO trace_events
                   (event_id, trace_id, span_id, parent_span_id, name, occurred_at, attributes_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid4()),
                    str(event.context.trace_id),
                    str(event.context.span_id),
                    str(event.context.parent_span_id) if event.context.parent_span_id else None,
                    event.name[:160],
                    occurred_at.isoformat(),
                    json.dumps(attributes, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            connection.execute(
                "DELETE FROM trace_events WHERE occurred_at < ?",
                (retention_cutoff.isoformat(),),
            )
            connection.execute(
                """DELETE FROM trace_events WHERE event_id IN (
                       SELECT event_id FROM trace_events ORDER BY occurred_at DESC
                       LIMIT -1 OFFSET ?
                   )""",
                (self.max_events,),
            )
            connection.commit()

    async def list_recent(self, *, limit: int = 100) -> tuple[TraceSummary, ...]:
        """Return recent request groups, newest first."""
        if not 1 <= limit <= 500:
            raise ValueError("Trace list limit must be between 1 and 500.")
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """SELECT trace_id, MIN(occurred_at) AS first_occurred_at,
                          MAX(occurred_at) AS last_occurred_at, COUNT(*) AS event_count
                   FROM trace_events GROUP BY trace_id
                   ORDER BY last_occurred_at DESC LIMIT ?""",
                (limit,),
            )
            rows = await cursor.fetchall()
            summaries: list[TraceSummary] = []
            for row in rows:
                latest_cursor = await connection.execute(
                    """SELECT name, attributes_json FROM trace_events
                       WHERE trace_id = ? ORDER BY occurred_at DESC LIMIT 1""",
                    (row["trace_id"],),
                )
                latest = await latest_cursor.fetchone()
                attributes = json.loads(latest["attributes_json"]) if latest else {}
                skill_cursor = await connection.execute(
                    """SELECT attributes_json FROM trace_events
                       WHERE trace_id = ?
                         AND json_extract(attributes_json, '$.skill_id') IS NOT NULL
                       ORDER BY occurred_at DESC LIMIT 1""",
                    (row["trace_id"],),
                )
                skill_row = await skill_cursor.fetchone()
                skill_attributes = (
                    json.loads(skill_row["attributes_json"]) if skill_row is not None else {}
                )
                summaries.append(
                    TraceSummary(
                        trace_id=UUID(row["trace_id"]),
                        first_occurred_at=datetime.fromisoformat(row["first_occurred_at"]),
                        last_occurred_at=datetime.fromisoformat(row["last_occurred_at"]),
                        event_count=int(row["event_count"]),
                        last_event=str(latest["name"]) if latest else "",
                        status=attributes.get("status"),
                        skill_id=skill_attributes.get("skill_id"),
                    )
                )
        return tuple(summaries)

    async def get_trace(self, trace_id: UUID) -> tuple[StoredTraceEvent, ...]:
        """Return every event for one request, in occurrence order."""
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """SELECT event_id, trace_id, span_id, parent_span_id, name,
                          occurred_at, attributes_json
                   FROM trace_events WHERE trace_id = ? ORDER BY occurred_at, event_id""",
                (str(trace_id),),
            )
            rows = await cursor.fetchall()
        return tuple(
            StoredTraceEvent(
                event_id=UUID(row["event_id"]),
                event=TraceEvent(
                    context=TraceContext(
                        trace_id=UUID(row["trace_id"]),
                        span_id=UUID(row["span_id"]),
                        parent_span_id=(
                            UUID(row["parent_span_id"]) if row["parent_span_id"] else None
                        ),
                    ),
                    name=str(row["name"]),
                    occurred_at=datetime.fromisoformat(row["occurred_at"]),
                    attributes=json.loads(row["attributes_json"]),
                ),
            )
            for row in rows
        )


__all__ = ["SQLiteTraceStore", "StoredTraceEvent", "TraceSummary"]

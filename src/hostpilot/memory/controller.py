"""Bounded memory recall and approval-gated archive coordination."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from hostpilot.contracts import ContextSnapshot, MemoryScope, MemoryWriteProposal
from hostpilot.memory.retrieval import rank_text_indexes, split_memory_text
from hostpilot.observability import TraceContext, TraceEvent, TraceSink
from hostpilot.persistence import MemoryStore

if TYPE_CHECKING:
    from hostpilot.memory.review import MemoryProposalReview


class ConversationLookup(Protocol):
    """Minimal conversation lookup needed to enforce session memory scope."""

    async def get(self, session_id: str) -> object | None: ...


@dataclass(frozen=True)
class MemoryChunk:
    """One bounded memory item with source and scope metadata."""

    scope: MemoryScope
    content: str
    source: str
    session_id: str | None = None


@dataclass(frozen=True)
class MemoryArchiveCandidate:
    """A proposed replacement awaiting explicit user approval."""

    scope: MemoryScope
    content: str
    source_skill_id: str
    session_id: str | None = None
    request_id: str | None = None


class MemoryController:
    """Centralize scoped recall and submit archive candidates for review.

    The current Markdown backend has no semantic index. Recall ranks Markdown chunks
    lexically and falls back to the existing session-first order for unmatched chunks.
    Archive requests only enter the existing approval queue; they never write storage.
    """

    def __init__(
        self,
        memory: MemoryStore,
        conversations: ConversationLookup,
        review: MemoryProposalReview,
        *,
        max_recall_chars: int = 16_000,
        trace: TraceSink | None = None,
    ) -> None:
        if max_recall_chars < 1:
            raise ValueError("max_recall_chars must be positive.")
        self._memory = memory
        self._conversations = conversations
        self._review = review
        self.max_recall_chars = max_recall_chars
        self._trace = trace

    async def recall(
        self,
        *,
        session_id: str,
        query: str,
        max_chars: int | None = None,
        request_id: str | None = None,
    ) -> tuple[MemoryChunk, ...]:
        """Return only the current session summary and shared memory within bounds."""
        started_at = perf_counter()
        try:
            if not query.strip():
                raise ValueError("Memory recall query cannot be blank.")
            if await self._conversations.get(session_id) is None:
                raise ValueError("Memory recall targets an unknown conversation.")
            char_limit = self.max_recall_chars if max_chars is None else max_chars
            if char_limit < 1:
                raise ValueError("Memory recall character limit must be positive.")

            sources = (
                (
                    MemoryScope.SESSION_SUMMARY,
                    self._memory.read_session_summary(session_id),
                    "session_summary",
                    session_id,
                ),
                (MemoryScope.LONG_TERM, self._memory.read_long_term_memory(), "long_term", None),
            )
            candidates: list[MemoryChunk] = []
            for scope, content, source, scoped_session_id in sources:
                if content is None:
                    continue
                candidates.extend(
                    MemoryChunk(
                        scope=scope,
                        content=chunk,
                        source=source,
                        session_id=scoped_session_id,
                    )
                    for chunk in split_memory_text(content)
                )
            ranked_indexes = rank_text_indexes(query, tuple(chunk.content for chunk in candidates))
            ranked_set = set(ranked_indexes)
            ordered = tuple(candidates[index] for index in ranked_indexes) + tuple(
                chunk for index, chunk in enumerate(candidates) if index not in ranked_set
            )
            chunks: list[MemoryChunk] = []
            remaining = char_limit
            for candidate in ordered:
                if remaining <= 0:
                    continue
                bounded = candidate.content[:remaining]
                chunks.append(
                    MemoryChunk(
                        content=bounded,
                        scope=candidate.scope,
                        source=candidate.source,
                        session_id=candidate.session_id,
                    )
                )
                remaining -= len(bounded)
            result = tuple(chunks)
        except asyncio.CancelledError:
            self._record(
                "memory.recall",
                "cancelled",
                request_id=request_id,
                duration_ms=int((perf_counter() - started_at) * 1000),
            )
            raise
        except Exception as error:
            self._record(
                "memory.recall",
                "failed",
                request_id=request_id,
                reason_code=type(error).__name__,
                duration_ms=int((perf_counter() - started_at) * 1000),
            )
            raise
        self._record(
            "memory.recall",
            "completed",
            request_id=request_id,
            chunk_count=len(result),
            character_count=sum(len(chunk.content) for chunk in result),
            duration_ms=int((perf_counter() - started_at) * 1000),
        )
        return result

    async def enrich_context(
        self,
        context: ContextSnapshot,
        *,
        query: str,
        max_chars: int | None = None,
    ) -> ContextSnapshot:
        """Create a request-local context using the same scoped recall boundary."""
        if context.session_id is None:
            return context
        chunks = await self.recall(
            session_id=context.session_id,
            query=query,
            max_chars=max_chars,
            request_id=context.request_id,
        )
        return context.model_copy(
            update={
                "session_summary": next(
                    (
                        chunk.content
                        for chunk in chunks
                        if chunk.scope is MemoryScope.SESSION_SUMMARY
                    ),
                    None,
                ),
                "long_term_memory": next(
                    (chunk.content for chunk in chunks if chunk.scope is MemoryScope.LONG_TERM),
                    None,
                ),
            }
        )

    async def archive(self, candidate: MemoryArchiveCandidate) -> MemoryWriteProposal:
        """Queue a bounded replacement proposal; explicit approval remains required."""
        started_at = perf_counter()
        source_skill_id = candidate.source_skill_id.strip()
        if not source_skill_id or len(source_skill_id) > 128:
            raise ValueError("Memory archive candidates require a source skill ID.")
        proposal = MemoryWriteProposal(
            scope=candidate.scope,
            session_id=candidate.session_id,
            source_skill_id=source_skill_id,
            content=candidate.content,
        )
        try:
            submitted = await self._review.submit(proposal)
        except asyncio.CancelledError:
            self._record(
                "memory.archive",
                "cancelled",
                request_id=candidate.request_id,
                skill_id=source_skill_id,
                duration_ms=int((perf_counter() - started_at) * 1000),
            )
            raise
        except Exception as error:
            self._record(
                "memory.archive",
                "failed",
                request_id=candidate.request_id,
                skill_id=source_skill_id,
                reason_code=type(error).__name__,
                duration_ms=int((perf_counter() - started_at) * 1000),
            )
            raise
        self._record(
            "memory.archive",
            "proposal_submitted",
            request_id=candidate.request_id,
            skill_id=source_skill_id,
            character_count=len(candidate.content),
            duration_ms=int((perf_counter() - started_at) * 1000),
        )
        return submitted

    def _record(
        self,
        operation: str,
        status: str,
        *,
        request_id: str | None,
        reason_code: str | None = None,
        chunk_count: int | None = None,
        character_count: int | None = None,
        skill_id: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        if self._trace is None:
            return
        try:
            trace_context = (
                TraceContext(trace_id=UUID(request_id)) if request_id else TraceContext()
            )
        except ValueError:
            trace_context = TraceContext()
        attributes: dict[str, str | int | float | bool] = {"status": status}
        if reason_code is not None:
            attributes["reason_code"] = reason_code
        if chunk_count is not None:
            attributes["chunk_count"] = chunk_count
        if character_count is not None:
            attributes["character_count"] = character_count
        if skill_id is not None:
            attributes["skill_id"] = skill_id
        if duration_ms is not None:
            attributes["duration_ms"] = duration_ms
        try:
            self._trace.record(
                TraceEvent(
                    context=trace_context,
                    name=operation,
                    attributes=attributes,
                )
            )
        except Exception:
            # Observability failures must not change memory access or approval behavior.
            return


__all__ = [
    "MemoryArchiveCandidate",
    "MemoryChunk",
    "MemoryController",
]

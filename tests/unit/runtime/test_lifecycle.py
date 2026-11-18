import asyncio

import pytest

from hostpilot.runtime import RuntimeContext, RuntimeHost, TurnLifecycleManager, TurnState


def test_turn_manager_records_completed_and_failed_turns() -> None:
    async def scenario() -> TurnLifecycleManager:
        manager = TurnLifecycleManager()
        with manager.track("turn-ok", "session-1"):
            assert manager.active_turns[0].state is TurnState.RUNNING

        with pytest.raises(ValueError, match="expected"):
            with manager.track("turn-failed", "session-1"):
                raise ValueError("expected")
        return manager

    manager = asyncio.run(scenario())

    assert [turn.state for turn in manager.history] == [TurnState.COMPLETED, TurnState.FAILED]
    assert all(turn.finished_at is not None for turn in manager.history)
    assert manager.active_turns == ()


def test_turn_manager_cancels_turns_by_session() -> None:
    async def scenario() -> TurnLifecycleManager:
        manager = TurnLifecycleManager()
        started = asyncio.Event()

        async def run_turn(turn_id: str, session_id: str) -> None:
            with manager.track(turn_id, session_id):
                started.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(run_turn("turn-1", "session-1"))
        await started.wait()
        assert manager.cancel_session("session-1") == 1
        with pytest.raises(asyncio.CancelledError):
            await task
        return manager

    manager = asyncio.run(scenario())
    assert manager.history[0].state is TurnState.CANCELLED


def test_host_shutdown_cancels_active_turns_before_stopping_plugins() -> None:
    async def scenario() -> tuple[RuntimeContext, asyncio.Task[None]]:
        context = RuntimeContext()
        host = RuntimeHost(context)
        await host.start()
        started = asyncio.Event()

        async def run_turn() -> None:
            with context.turns.track("turn-1", "session-1"):
                started.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(run_turn())
        await started.wait()
        await host.close()
        return context, task

    context, task = asyncio.run(scenario())

    assert task.cancelled()
    assert context.turns.active_turns == ()
    assert context.turns.history[0].state is TurnState.CANCELLED

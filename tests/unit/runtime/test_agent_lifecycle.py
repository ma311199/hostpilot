import asyncio

import pytest

from hostpilot.runtime import AgentLifecycleManager


class CloseableAgent:
    def __init__(self) -> None:
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1


def test_replacement_keeps_leased_agent_alive_and_routes_new_lease_to_replacement() -> None:
    async def scenario() -> tuple[int, int]:
        old_agent = CloseableAgent()
        new_agent = CloseableAgent()
        manager = AgentLifecycleManager(old_agent)
        old_lease_entered = asyncio.Event()
        release_old_lease = asyncio.Event()

        async def hold_old_lease() -> None:
            async with manager.lease() as agent:
                assert agent is old_agent
                old_lease_entered.set()
                await release_old_lease.wait()

        old_turn = asyncio.create_task(hold_old_lease())
        await old_lease_entered.wait()
        replacement = asyncio.create_task(manager.replace(new_agent))

        async def acquire_new_generation() -> None:
            while True:
                async with manager.lease() as agent:
                    if agent is new_agent:
                        return
                await asyncio.sleep(0)

        await asyncio.wait_for(acquire_new_generation(), timeout=1)
        assert old_agent.close_calls == 0
        release_old_lease.set()
        await old_turn
        await replacement
        assert old_agent.close_calls == 1
        await manager.aclose()
        return old_agent.close_calls, new_agent.close_calls

    assert asyncio.run(scenario()) == (1, 1)


def test_concurrent_replacements_close_each_retired_agent_once() -> None:
    async def scenario() -> tuple[int, int, int]:
        old_agent = CloseableAgent()
        middle_agent = CloseableAgent()
        new_agent = CloseableAgent()
        manager = AgentLifecycleManager(old_agent)
        lease_entered = asyncio.Event()
        release_lease = asyncio.Event()

        async def hold_lease() -> None:
            async with manager.lease() as agent:
                assert agent is old_agent
                lease_entered.set()
                await release_lease.wait()

        active_turn = asyncio.create_task(hold_lease())
        await lease_entered.wait()
        first_replacement = asyncio.create_task(manager.replace(middle_agent))
        second_replacement = asyncio.create_task(manager.replace(new_agent))
        await asyncio.sleep(0)
        release_lease.set()
        await active_turn
        await asyncio.gather(first_replacement, second_replacement)
        await manager.aclose()
        return old_agent.close_calls, middle_agent.close_calls, new_agent.close_calls

    assert asyncio.run(scenario()) == (1, 1, 1)


def test_shutdown_rejects_new_leases_and_waits_for_active_lease() -> None:
    async def scenario() -> tuple[int, bool]:
        agent = CloseableAgent()
        manager = AgentLifecycleManager(agent)
        lease_entered = asyncio.Event()
        release_lease = asyncio.Event()

        async def hold_lease() -> None:
            async with manager.lease():
                lease_entered.set()
                await release_lease.wait()

        active_turn = asyncio.create_task(hold_lease())
        await lease_entered.wait()
        shutdown = asyncio.create_task(manager.aclose())
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="shutting down"):
            async with manager.lease():
                pytest.fail("a new Agent lease must not start during shutdown")
        assert agent.close_calls == 0
        release_lease.set()
        await active_turn
        await shutdown
        with pytest.raises(RuntimeError, match="shutting down"):
            async with manager.lease():
                pytest.fail("a closed Agent manager must reject new leases")
        return agent.close_calls, shutdown.done()

    assert asyncio.run(scenario()) == (1, True)


def test_cancelled_replacement_still_retires_old_agent_after_lease_drains() -> None:
    async def scenario() -> tuple[int, int]:
        old_agent = CloseableAgent()
        new_agent = CloseableAgent()
        manager = AgentLifecycleManager(old_agent)
        lease_entered = asyncio.Event()
        release_lease = asyncio.Event()

        async def hold_lease() -> None:
            async with manager.lease():
                lease_entered.set()
                await release_lease.wait()

        active_turn = asyncio.create_task(hold_lease())
        await lease_entered.wait()
        replacement = asyncio.create_task(manager.replace(new_agent))
        while manager.current is not new_agent:
            await asyncio.sleep(0)
        replacement.cancel()
        with pytest.raises(asyncio.CancelledError):
            await replacement

        release_lease.set()
        await active_turn
        await asyncio.wait_for(_wait_closed(old_agent), timeout=1)
        await manager.aclose()
        return old_agent.close_calls, new_agent.close_calls

    assert asyncio.run(scenario()) == (1, 1)


async def _wait_closed(agent: CloseableAgent) -> None:
    while agent.close_calls == 0:
        await asyncio.sleep(0)


def test_replacement_surfaces_retired_agent_close_failure() -> None:
    async def scenario() -> None:
        class FailingAgent(CloseableAgent):
            async def aclose(self) -> None:
                self.close_calls += 1
                raise OSError("agent close failed")

        old_agent = FailingAgent()
        new_agent = CloseableAgent()
        manager = AgentLifecycleManager(old_agent)

        with pytest.raises(OSError, match="agent close failed"):
            await manager.replace(new_agent)
        await manager.aclose()
        assert old_agent.close_calls == 1
        assert new_agent.close_calls == 1

    asyncio.run(scenario())


def test_concurrent_shutdown_closes_current_agent_exactly_once() -> None:
    async def scenario() -> int:
        agent = CloseableAgent()
        manager = AgentLifecycleManager(agent)
        await asyncio.gather(manager.aclose(), manager.aclose())
        return agent.close_calls

    assert asyncio.run(scenario()) == 1

import asyncio

import pytest

from hostpilot.runtime import (
    RuntimeContext,
    RuntimeHost,
    RuntimeHostState,
    RuntimePluginError,
    RuntimePluginSpec,
)


class RecordingPlugin:
    def __init__(
        self,
        plugin_id: str,
        events: list[str],
        *,
        dependencies: tuple[str, ...] = (),
        optional: bool = False,
        fail_start: bool = False,
    ) -> None:
        self.spec = RuntimePluginSpec(
            plugin_id,
            dependencies=dependencies,
            optional=optional,
        )
        self.events = events
        self.fail_start = fail_start

    async def setup(self, context: RuntimeContext) -> None:
        del context
        self.events.append(f"setup:{self.spec.plugin_id}")

    async def start(self, context: RuntimeContext) -> None:
        del context
        self.events.append(f"start:{self.spec.plugin_id}")
        if self.fail_start:
            raise RuntimeError("startup failed")

    async def stop(self, context: RuntimeContext) -> None:
        del context
        self.events.append(f"stop:{self.spec.plugin_id}")


def test_runtime_context_assigns_distinct_host_ids() -> None:
    first = RuntimeContext()
    second = RuntimeContext()

    assert first.runtime_host_id
    assert first.runtime_host_id != second.runtime_host_id


def test_host_closes_plugins_and_reports_turn_store_error_on_shutdown() -> None:
    async def scenario() -> tuple[list[str], RuntimeHost, asyncio.Task[None]]:
        events: list[str] = []

        class FailingTurnStore:
            async def create_turn(self, **kwargs) -> None:
                del kwargs

            async def finish_turn(self, *args, **kwargs) -> None:
                del args, kwargs
                raise OSError("turn terminal persistence failed")

        context = RuntimeContext()
        context.turns.configure_persistence(FailingTurnStore(), context.runtime_host_id)
        host = RuntimeHost(context)
        host.register_plugin(RecordingPlugin("worker", events))
        await host.start()
        started = asyncio.Event()

        async def run_turn() -> None:
            async with context.turns.track("turn-1", "session-1"):
                started.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(run_turn())
        await started.wait()
        try:
            await host.close()
        except OSError as error:
            assert str(error) == "turn terminal persistence failed"
        else:
            raise AssertionError("Host shutdown must surface terminal persistence errors")
        return events, host, task

    events, host, task = asyncio.run(scenario())

    assert task.done()
    assert events[-1] == "stop:worker"
    assert host.state is RuntimeHostState.STOPPED


def test_host_starts_by_dependency_and_stops_in_reverse_order() -> None:
    async def scenario() -> tuple[list[str], RuntimeHost]:
        events: list[str] = []
        context = RuntimeContext()
        host = RuntimeHost(context)
        host.register_plugin(RecordingPlugin("consumer", events, dependencies=("provider",)))
        host.register_plugin(RecordingPlugin("provider", events))

        await host.start()
        assert host.state is RuntimeHostState.READY
        await host.close()
        await host.close()
        return events, host

    events, host = asyncio.run(scenario())

    assert events == [
        "setup:provider",
        "start:provider",
        "setup:consumer",
        "start:consumer",
        "stop:consumer",
        "stop:provider",
    ]
    assert host.state is RuntimeHostState.STOPPED
    assert host.context.shutdown_event.is_set()


@pytest.mark.parametrize(
    ("plugins", "message"),
    [
        ((("consumer", ("missing",)),), "missing dependencies"),
        (
            (("first", ("second",)), ("second", ("first",))),
            "contain a cycle",
        ),
    ],
)
def test_host_rejects_missing_dependencies_and_cycles(plugins, message: str) -> None:
    async def scenario() -> RuntimeHost:
        host = RuntimeHost(RuntimeContext())
        for plugin_id, dependencies in plugins:
            host.register_plugin(RecordingPlugin(plugin_id, [], dependencies=dependencies))
        with pytest.raises(RuntimePluginError, match=message):
            await host.start()
        return host

    host = asyncio.run(scenario())
    assert host.state is RuntimeHostState.FAILED


def test_required_plugin_failure_stops_prior_plugins_and_fails_host() -> None:
    async def scenario() -> tuple[list[str], RuntimeHost]:
        events: list[str] = []
        host = RuntimeHost(RuntimeContext())
        host.register_plugin(RecordingPlugin("provider", events))
        host.register_plugin(
            RecordingPlugin("broken", events, dependencies=("provider",), fail_start=True)
        )

        with pytest.raises(RuntimePluginError, match="Required runtime plugin failed"):
            await host.start()
        return events, host

    events, host = asyncio.run(scenario())

    assert events == [
        "setup:provider",
        "start:provider",
        "setup:broken",
        "start:broken",
        "stop:provider",
    ]
    assert host.state is RuntimeHostState.FAILED
    assert host.plugin_errors == {"broken": "RuntimeError"}


def test_optional_plugin_failure_is_recorded_without_blocking_host() -> None:
    async def scenario() -> tuple[list[str], RuntimeHost]:
        events: list[str] = []
        host = RuntimeHost(RuntimeContext())
        host.register_plugin(RecordingPlugin("optional", events, optional=True, fail_start=True))
        host.register_plugin(RecordingPlugin("healthy", events))
        await host.start()
        await host.close()
        return events, host

    events, host = asyncio.run(scenario())

    assert host.state is RuntimeHostState.STOPPED
    assert host.plugin_errors == {"optional": "RuntimeError"}
    assert "start:healthy" in events
    assert "stop:healthy" in events


def test_host_cancels_plugin_tasks_and_unregisters_owned_services() -> None:
    async def scenario() -> tuple[bool, RuntimeContext]:
        context = RuntimeContext()
        host = RuntimeHost(context)
        plugin = RecordingPlugin("worker", [])
        host.register_plugin(plugin)
        await host.start()
        context.services.register("service", object(), owner="worker")
        started = asyncio.Event()

        async def background() -> None:
            started.set()
            await asyncio.Event().wait()

        task = host.create_task("worker", background())
        await started.wait()
        await host.close()
        return task.cancelled(), context

    cancelled, context = asyncio.run(scenario())

    assert cancelled
    assert context.services.snapshot() == {}


def test_host_only_allows_task_creation_for_running_plugins() -> None:
    async def scenario() -> None:
        host = RuntimeHost(RuntimeContext())

        async def background() -> None:
            return None

        with pytest.raises(RuntimeError, match="only after the host is ready"):
            host.create_task("worker", background())

    asyncio.run(scenario())

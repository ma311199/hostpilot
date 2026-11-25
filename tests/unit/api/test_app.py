import asyncio
import json
import sqlite3
import zipfile
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from hostpilot.api import create_app
from hostpilot.api.app import (
    ModelConfigurationRequest,
    RoutingModelConfigurationRequest,
    WebSocketConfirmationProvider,
    WebSocketDisconnectCancellation,
)
from hostpilot.config import Settings
from hostpilot.contracts import (
    ContextSnapshot,
    ConversationMessage,
    MessageRole,
    ModelTokenUsage,
    Route,
    RouteDecision,
    SandboxMode,
)
from hostpilot.observability import AuditEvent, AuditOutcome, TraceContext, TraceEvent
from hostpilot.orchestration import ChatAgentResponse
from hostpilot.orchestration.skill_runtime import SkillSelection
from hostpilot.persistence import (
    MarkdownMemoryStore,
    SQLiteAuditStore,
    SQLiteCancellationStore,
    SQLiteConversationStore,
    SQLiteHarnessConfigurationStore,
    SQLiteMCPConfigurationStore,
    SQLiteModelConfigurationStore,
    SQLiteSkillExecutionStore,
    SQLiteTraceStore,
    SQLiteTurnStore,
    SQLiteWorkspaceConfigurationStore,
    open_sqlite_checkpointer,
)
from hostpilot.policy.confirmation import ConfirmationDecision, ConfirmationRequest
from hostpilot.sandbox.filesystem import LinuxWorkspaceSandbox
from hostpilot.sandbox.linux import (
    LinuxJavaScriptSandbox,
    LinuxPythonSandbox,
    LinuxShellSandbox,
)
from hostpilot.skills.compensation import CancellationSnapshot
from hostpilot.skills.runtime import SkillExecutionResult, SkillExecutionStatus, SkillSideEffect
from hostpilot.skills.yaml_registry import SideEffectLevel
from hostpilot.tools.executor import RemoteToolOutcomeUnknownError, ToolConfirmationDeniedError
from hostpilot.tools.filesystem import WorkspaceReadOnlyError
from hostpilot.tools.governance import DefaultToolExecutorFactory


def test_app_accepts_persistence_store_factories(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    factory_paths: list[Path] = []
    checkpointer_paths: list[Path] = []
    model_store_paths: list[Path] = []
    mcp_store_paths: list[Path] = []
    harness_store_paths: list[Path] = []
    workspace_store_paths: list[Path] = []
    skill_store_paths: list[Path] = []
    audit_store_paths: list[Path] = []
    trace_store_paths: list[Path] = []
    cancellation_store_paths: list[Path] = []
    memory_store_paths: list[Path] = []

    def conversation_store_factory(database_path: Path) -> SQLiteConversationStore:
        factory_paths.append(database_path)
        return SQLiteConversationStore(database_path)

    def checkpointer_factory(database_path: Path):
        checkpointer_paths.append(database_path)
        return open_sqlite_checkpointer(database_path)

    def model_configuration_store_factory(
        database_path: Path,
    ) -> SQLiteModelConfigurationStore:
        model_store_paths.append(database_path)
        return SQLiteModelConfigurationStore(database_path)

    def mcp_configuration_store_factory(database_path: Path) -> SQLiteMCPConfigurationStore:
        mcp_store_paths.append(database_path)
        return SQLiteMCPConfigurationStore(database_path)

    def harness_configuration_store_factory(
        database_path: Path,
    ) -> SQLiteHarnessConfigurationStore:
        harness_store_paths.append(database_path)
        return SQLiteHarnessConfigurationStore(database_path)

    def workspace_configuration_store_factory(
        database_path: Path,
    ) -> SQLiteWorkspaceConfigurationStore:
        workspace_store_paths.append(database_path)
        return SQLiteWorkspaceConfigurationStore(database_path)

    def skill_execution_store_factory(database_path: Path) -> SQLiteSkillExecutionStore:
        skill_store_paths.append(database_path)
        return SQLiteSkillExecutionStore(database_path)

    def audit_store_factory(database_path: Path) -> SQLiteAuditStore:
        audit_store_paths.append(database_path)
        return SQLiteAuditStore(database_path)

    def trace_store_factory(database_path: Path) -> SQLiteTraceStore:
        trace_store_paths.append(database_path)
        return SQLiteTraceStore(database_path)

    def cancellation_store_factory(database_path: Path) -> SQLiteCancellationStore:
        cancellation_store_paths.append(database_path)
        return SQLiteCancellationStore(database_path)

    def memory_store_factory(memory_path: Path) -> MarkdownMemoryStore:
        memory_store_paths.append(memory_path)
        return MarkdownMemoryStore(memory_path)

    with TestClient(
        create_app(
            settings,
            conversation_store_factory=conversation_store_factory,
            checkpointer_factory=checkpointer_factory,
            model_configuration_store_factory=model_configuration_store_factory,
            mcp_configuration_store_factory=mcp_configuration_store_factory,
            harness_configuration_store_factory=harness_configuration_store_factory,
            workspace_configuration_store_factory=workspace_configuration_store_factory,
            skill_execution_store_factory=skill_execution_store_factory,
            audit_store_factory=audit_store_factory,
            trace_store_factory=trace_store_factory,
            cancellation_store_factory=cancellation_store_factory,
            memory_store_factory=memory_store_factory,
        )
    ) as client:
        response = client.post("/api/v1/conversations", json={})

    assert response.status_code == 201
    assert factory_paths == [tmp_path / "data" / "hostpilot.sqlite3"]
    assert checkpointer_paths == factory_paths
    assert model_store_paths == factory_paths
    assert mcp_store_paths == factory_paths
    assert harness_store_paths == factory_paths
    assert workspace_store_paths == factory_paths
    assert skill_store_paths == factory_paths
    assert audit_store_paths == factory_paths
    assert trace_store_paths == factory_paths
    assert cancellation_store_paths == factory_paths
    assert memory_store_paths == [tmp_path / "memory"]


def test_trace_api_lists_and_returns_only_safe_event_metadata(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    trace_id = uuid4()

    with TestClient(create_app(settings)) as client:
        client.app.state.traces.record(
            TraceEvent(
                context=TraceContext(trace_id=trace_id),
                name="chat.turn",
                attributes={
                    "status": "completed",
                    "route": "direct",
                    "prompt": "must not persist",
                },
            )
        )

        listed = client.get("/api/v1/observability/traces")
        assert listed.status_code == 200
        assert listed.json()[0]["trace_id"] == str(trace_id)
        assert listed.json()[0]["last_event"] == "chat.turn"

        detail = client.get(f"/api/v1/observability/traces/{trace_id}")
        assert detail.status_code == 200
        assert detail.json()["events"][0]["attributes"] == {
            "status": "completed",
            "route": "direct",
        }
        assert "must not persist" not in detail.text

        missing = client.get(f"/api/v1/observability/traces/{uuid4()}")
        assert missing.status_code == 404


def test_audit_api_lists_sanitized_events_and_supports_trace_filter(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    trace_id = uuid4()
    with TestClient(create_app(settings)) as client:
        client.app.state.audit_sink.record(
            AuditEvent(
                trace_id=trace_id,
                action="tool.call",
                outcome=AuditOutcome.COMPLETED,
                resource_type="tool",
                resource_id="write_text_file",
                reason_code="approved",
            )
        )
        client.app.state.audit_sink.record(
            AuditEvent(
                action="memory.proposal.rejected",
                outcome=AuditOutcome.CANCELLED,
                resource_type="memory_proposal",
            )
        )

        response = client.get("/api/v1/observability/audit", params={"trace_id": str(trace_id)})

        assert response.status_code == 200
        assert len(response.json()) == 1
        assert response.json()[0]["trace_id"] == str(trace_id)
        assert response.json()[0]["resource_id"] == "write_text_file"
        assert "arguments" not in response.text


def test_audit_api_paginates_and_exports_formula_safe_csv(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    trace_id = uuid4()
    with TestClient(create_app(settings)) as client:
        for action in ("older.action", '=HYPERLINK("https://example.invalid")'):
            client.app.state.audit_sink.record(
                AuditEvent(
                    trace_id=trace_id,
                    action=action,
                    outcome=AuditOutcome.COMPLETED,
                    resource_type="tool",
                    resource_id="write_text_file",
                )
            )

        page = client.get(
            "/api/v1/observability/audit",
            params={"trace_id": str(trace_id), "limit": 1, "offset": 1},
        )
        exported = client.get(
            "/api/v1/observability/audit/export.csv",
            params={"trace_id": str(trace_id)},
        )

        assert page.status_code == 200
        assert len(page.json()) == 1
        assert page.json()[0]["action"] == "older.action"
        assert exported.status_code == 200
        disposition = exported.headers["content-disposition"]
        assert 'attachment; filename="hostpilot-audit.csv"' in disposition
        assert exported.content.startswith(b"\xef\xbb\xbf")
        assert "'=HYPERLINK" in exported.text
        assert "event_id,trace_id,action,outcome,resource_type,resource_id" in exported.text
        assert "reason_code,occurred_at" in exported.text


def test_model_configuration_defaults_to_streaming_and_allows_override() -> None:
    default = ModelConfigurationRequest(model_name="model", api_key="key")
    disabled = ModelConfigurationRequest(model_name="model", api_key="key", streaming=False)

    assert default.streaming is None
    assert disabled.streaming is False


def test_optional_model_roles_accept_independent_credentials_and_endpoints() -> None:
    configuration = RoutingModelConfigurationRequest(
        judge_model="light-model",
        judge_api_key="light-secret",
        judge_base_url="https://light.example/v1",
        embedding_model="embed-model",
        embedding_api_key="embedding-secret",
        embedding_base_url="https://embed.example/v1",
        reranker_model="rerank-model",
        reranker_api_key="reranker-secret",
        reranker_base_url="https://rerank.example/v1",
    )

    assert configuration.judge_api_key.get_secret_value() == "light-secret"
    assert configuration.embedding_api_key.get_secret_value() == "embedding-secret"
    assert configuration.reranker_api_key.get_secret_value() == "reranker-secret"
    assert configuration.judge_base_url == "https://light.example/v1"
    assert configuration.embedding_base_url == "https://embed.example/v1"
    assert configuration.reranker_base_url == "https://rerank.example/v1"
    assert "light-secret" not in configuration.model_dump_json()
    assert "embedding-secret" not in configuration.model_dump_json()


def test_saved_model_configuration_survives_restart_without_persisting_api_key(tmp_path) -> None:
    from hostpilot.persistence import SQLiteModelConfigurationStore

    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    database_path = settings.data_dir / "hostpilot.sqlite3"
    settings.data_dir.mkdir()

    async def save_configuration() -> None:
        store = SQLiteModelConfigurationStore(database_path)
        await store.initialize()
        await store.save(
            model_name="persisted-model",
            base_url="https://models.example/v1",
            streaming=False,
        )

    asyncio.run(save_configuration())
    raw_database = database_path.read_bytes()
    assert b"api_key" not in raw_database
    assert b"secret-test-key" not in raw_database

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/model/configuration")
        assert response.status_code == 200
        assert response.json() == {
            "configured": False,
            "saved": True,
            "api_key_required": True,
            "model_name": "persisted-model",
            "base_url": "https://models.example/v1",
            "streaming": False,
        }


def test_embedding_and_reranker_keys_persist_and_restore_after_restart(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    payload = {
        "embedding_model": "local-embedding",
        "embedding_api_key": "embedding-local-secret",
        "embedding_base_url": "http://127.0.0.1:8081/v1",
        "reranker_model": "local-reranker",
        "reranker_api_key": "reranker-local-secret",
        "reranker_base_url": "http://127.0.0.1:8082/v1",
    }

    with TestClient(create_app(settings)) as client:
        saved = client.put("/api/v1/routing-model/configuration", json=payload)
        assert saved.status_code == 200
        assert saved.json()["embedding_api_key_configured"] is True
        assert saved.json()["reranker_api_key_configured"] is True
        assert "embedding-local-secret" not in saved.text
        assert "reranker-local-secret" not in saved.text

    database_path = settings.data_dir / "hostpilot.sqlite3"
    raw_database = database_path.read_bytes()
    assert b"embedding-local-secret" in raw_database
    assert b"reranker-local-secret" in raw_database

    with TestClient(create_app(settings)) as client:
        restored = client.get("/api/v1/routing-model/configuration")
        assert restored.status_code == 200
        assert restored.json()["configured"] is True
        assert restored.json()["embedding_api_key_configured"] is True
        assert restored.json()["reranker_api_key_configured"] is True
        assert restored.json()["embedding_model"] == "local-embedding"
        assert restored.json()["reranker_model"] == "local-reranker"
        assert "embedding-local-secret" not in restored.text
        assert "reranker-local-secret" not in restored.text

        updated = client.put(
            "/api/v1/routing-model/configuration",
            json={"embedding_model": None, "reranker_model": "local-reranker"},
        )
        assert updated.status_code == 200
        assert updated.json()["embedding_api_key_configured"] is False
        assert updated.json()["reranker_api_key_configured"] is True

    from hostpilot.persistence import SQLiteRoutingModelConfigurationStore

    async def saved_keys() -> tuple[str | None, str | None]:
        store = SQLiteRoutingModelConfigurationStore(database_path)
        configuration = await store.get()
        assert configuration is not None
        return configuration["embedding_api_key"], configuration["reranker_api_key"]

    assert asyncio.run(saved_keys()) == (None, "reranker-local-secret")


def test_model_connection_test_exercises_each_role_without_saving_credentials(
    tmp_path, monkeypatch
) -> None:
    from types import SimpleNamespace

    from hostpilot.models.contracts import ModelResponse
    from hostpilot.routing.models import EmbeddingBatch, RerankBatch

    class FakeProvider:
        async def complete(self, _request):
            return ModelResponse(text="OK")

    class FakeRoutingRuntime:
        chat_provider = FakeProvider()
        reranker_client = SimpleNamespace(
            rerank=lambda *_args, **_kwargs: asyncio.sleep(
                0, result=RerankBatch((0, 1), (1.0, 0.0), 0)
            )
        )

        async def embed(self, _texts, *, model):
            return EmbeddingBatch(((0.1, 0.2),), 0)

        async def aclose(self):
            return None

    monkeypatch.setattr(
        "hostpilot.models.factory.create_model_runtime_components",
        lambda *_args, **_kwargs: SimpleNamespace(provider=FakeProvider(), agent_chat_model=None),
    )
    monkeypatch.setattr(
        "hostpilot.routing.models.create_routing_model_components",
        lambda *_args, **_kwargs: FakeRoutingRuntime(),
    )
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(create_app(settings)) as client:
        for role in ("llm", "judge", "embedding", "reranker"):
            response = client.post(
                "/api/v1/model/configuration/test",
                json={
                    "role": role,
                    "model_name": "test-model",
                    "api_key": f"{role}-private-key",
                    "base_url": "http://127.0.0.1:8080/v1",
                },
            )
            assert response.status_code == 200, response.text
            assert response.json()["success"] is True
            assert response.json()["role"] == role
            assert f"{role}-private-key" not in response.text

        configuration = client.get("/api/v1/routing-model/configuration")
        assert configuration.status_code == 200
        assert configuration.json()["saved"] is False


def test_chat_model_connection_probe_allows_reasoning_models_time_to_respond(
    tmp_path, monkeypatch
) -> None:
    from hostpilot.models.contracts import ModelResponse

    captured_requests = []

    class FakeProvider:
        async def complete(self, request):
            captured_requests.append(request)
            return ModelResponse(text="OK")

    monkeypatch.setattr(
        "hostpilot.models.factory.create_model_runtime_components",
        lambda *_args, **_kwargs: SimpleNamespace(provider=FakeProvider(), agent_chat_model=None),
    )
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/model/configuration/test",
            json={
                "role": "llm",
                "model_name": "glm-5.3",
                "api_key": "test-key",
                "base_url": "https://example.test/v1",
                "streaming": True,
            },
        )

    assert response.json()["success"] is True
    assert len(captured_requests) == 1
    assert captured_requests[0].temperature is None
    assert captured_requests[0].max_output_tokens >= 128
    assert captured_requests[0].timeout_seconds >= 30


def test_chat_model_connection_probe_reports_safe_provider_error_category(
    tmp_path, monkeypatch
) -> None:
    from hostpilot.models.contracts import ModelProviderError, ModelProviderErrorCategory

    class FakeProvider:
        async def complete(self, _request):
            raise ModelProviderError(
                ModelProviderErrorCategory.INVALID_REQUEST,
                status_code=400,
                provider_code="invalid_parameter_do_not_expose",
            )

    monkeypatch.setattr(
        "hostpilot.models.factory.create_model_runtime_components",
        lambda *_args, **_kwargs: SimpleNamespace(provider=FakeProvider(), agent_chat_model=None),
    )
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/model/configuration/test",
            json={
                "role": "llm",
                "model_name": "glm-5.3",
                "api_key": "test-key",
                "base_url": "https://example.test/v1",
                "streaming": True,
            },
        )

    assert response.json()["success"] is False
    assert response.json()["error_code"] == "invalid_request"
    assert "HTTP 400" in response.json()["message"]
    assert "invalid_parameter_do_not_expose" not in response.text


def test_partial_routing_model_save_skips_other_roles_missing_credentials(
    tmp_path, monkeypatch
) -> None:
    from hostpilot.persistence import SQLiteRoutingModelConfigurationStore

    database_path = tmp_path / "data" / "hostpilot.sqlite3"
    database_path.parent.mkdir(parents=True)
    store = SQLiteRoutingModelConfigurationStore(database_path)

    async def seed_saved_roles() -> None:
        await store.initialize()
        await store.save(
            judge_base_url=None,
            embedding_base_url="http://127.0.0.1:8080/v1",
            reranker_base_url="http://127.0.0.1:8081/v1",
            embedding_model="old-embedding",
            judge_model=None,
            reranker_model="old-reranker",
        )

    asyncio.run(seed_saved_roles())
    runtime_options: list[dict[str, Any]] = []

    class FakeRoutingRuntime:
        chat_provider = None
        reranker_client = None

        async def aclose(self):
            return None

    class FakeVectorIndex:
        def __init__(self, *_args, **_kwargs):
            return None

    def create_fake_routing_runtime(_settings, **kwargs):
        runtime_options.append(kwargs)
        return FakeRoutingRuntime()

    monkeypatch.setattr(
        "hostpilot.routing.models.create_routing_model_components",
        create_fake_routing_runtime,
    )
    monkeypatch.setattr(
        "hostpilot.routing.vector_index.ChromaSkillVectorIndex",
        FakeVectorIndex,
    )
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(create_app(settings)) as client:
        response = client.put(
            "/api/v1/routing-model/configuration",
            json={
                "embedding_model": "new-embedding",
                "embedding_api_key": "embedding-secret",
                "embedding_base_url": "http://127.0.0.1:8080/v1",
            },
        )

    assert response.status_code == 200, response.text
    assert runtime_options[0]["embedding_model"] == "new-embedding"
    assert runtime_options[0]["reranker_model"] is None
    assert response.json()["reranker_model"] == "old-reranker"
    assert response.json()["reranker_api_key_configured"] is False


def test_mcp_configuration_persists_metadata_but_keeps_api_key_ephemeral(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    payload = {
        "name": "research",
        "url": "https://mcp.example/mcp",
        "timeout_seconds": 20,
        "enabled": True,
        "api_key_header": "Authorization",
    }

    with TestClient(create_app(settings)) as client:
        created = client.put("/api/v1/mcp/servers", json=payload)
        assert created.status_code == 200
        server = created.json()["servers"][0]
        assert server["connection_state"] == "waiting_for_credentials"
        assert server["api_key_configured"] is False
        assert server["description"] == ""
        assert "private-mcp-key" not in created.text

        configured = client.put(
            "/api/v1/mcp/servers",
            json={**payload, "enabled": False, "api_key": "private-mcp-key"},
        )
        assert configured.status_code == 200
        assert configured.json()["servers"][0]["api_key_configured"] is True
        assert "private-mcp-key" not in configured.text

    database_path = settings.data_dir / "hostpilot.sqlite3"
    assert b"private-mcp-key" not in database_path.read_bytes()

    with TestClient(create_app(settings)) as client:
        restored = client.get("/api/v1/mcp/servers")
        assert restored.status_code == 200
        server = restored.json()["servers"][0]
        assert server["name"] == "research"
        assert server["enabled"] is False
        assert server["api_key_configured"] is False
        assert "private-mcp-key" not in restored.text

        deleted = client.delete("/api/v1/mcp/servers/research")
        assert deleted.status_code == 204
        assert client.get("/api/v1/mcp/servers").json()["servers"] == []


def test_mcp_key_submission_rebuilds_provider_and_is_required_again_after_restart(
    tmp_path, monkeypatch
) -> None:
    from hostpilot.contracts import (
        ToolConnectionState,
        ToolConnectionStatus,
        ToolConnectionTransport,
    )
    from hostpilot.tools.registry import ToolRegistry

    built_settings = []

    class FakeMCPProvider:
        def __init__(self, configured_settings) -> None:
            self.settings = configured_settings
            self.registry = ToolRegistry()
            self._started = False

        async def start(self):
            self._started = True
            return []

        async def aclose(self):
            self._started = False

        def connections(self):
            return tuple(
                ToolConnectionStatus(
                    connection_id=server.name,
                    transport=ToolConnectionTransport.STREAMABLE_HTTP,
                    state=ToolConnectionState.CONNECTED,
                    discovered_tool_count=0,
                )
                for server in self.settings.mcp_servers
            )

    def fake_provider_factory(configured_settings, **_kwargs):
        built_settings.append(configured_settings)
        return FakeMCPProvider(configured_settings)

    monkeypatch.setattr("hostpilot.tools.mcp.build_retrieval_mcp_provider", fake_provider_factory)
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    configuration = {
        "name": "research",
        "url": "https://mcp.example/mcp",
        "timeout_seconds": 20,
        "enabled": True,
        "api_key_header": "Authorization",
    }

    with TestClient(create_app(settings)) as client:
        waiting = client.put("/api/v1/mcp/servers", json=configuration)
        assert waiting.json()["servers"][0]["connection_state"] == "waiting_for_credentials"

        connected = client.put(
            "/api/v1/mcp/servers",
            json={**configuration, "api_key": "runtime-only-secret"},
        )
        assert connected.status_code == 200
        assert connected.json()["servers"][0]["connection_state"] == "connected"
        assert "runtime-only-secret" not in connected.text
        assert built_settings[-1].mcp_servers[0].headers["Authorization"].get_secret_value() == (
            "runtime-only-secret"
        )

    assert b"runtime-only-secret" not in (settings.data_dir / "hostpilot.sqlite3").read_bytes()
    with TestClient(create_app(settings)) as client:
        restored = client.get("/api/v1/mcp/servers").json()["servers"][0]
        assert restored["connection_state"] == "waiting_for_credentials"
        assert restored["api_key_configured"] is False


def test_invalid_mcp_key_is_not_echoed_in_validation_response(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    with TestClient(create_app(settings)) as client:
        response = client.put(
            "/api/v1/mcp/servers",
            json={
                "name": "research",
                "url": "https://mcp.example/mcp",
                "enabled": False,
                "api_key_header": "Authorization",
                "api_key": "secret-with\r-control",
            },
        )

    assert response.status_code == 422
    assert "secret-with" not in response.text


def test_stdio_mcp_server_configuration_persists_command_and_arguments(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    payload = {
        "name": "filesystem",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp/project"],
        "cwd": "/tmp",
        "enabled": False,
    }

    with TestClient(create_app(settings)) as client:
        saved = client.put("/api/v1/mcp/servers", json=payload)
        assert saved.status_code == 200
        server = saved.json()["servers"][0]
        assert server["transport"] == "stdio"
        assert server["command"] == "npx"
        assert server["args"] == payload["args"]
        assert server["url"] is None
        assert server["api_key_configured"] is True

    with TestClient(create_app(settings)) as client:
        restored = client.get("/api/v1/mcp/servers").json()["servers"][0]
        assert restored["transport"] == "stdio"
        assert restored["command"] == "npx"
        assert restored["args"] == payload["args"]
        assert restored["connection_state"] == "disabled"


def test_stdio_credentials_in_arguments_are_rejected_without_persistence(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    credentials = (
        ("--api-key", "private-api-secret"),
        ("--header=Authorization: Bearer private-header-secret",),
        ("MCP_ACCESS_TOKEN=private-env-secret",),
    )

    with TestClient(create_app(settings)) as client:
        for index, arguments in enumerate(credentials):
            response = client.put(
                "/api/v1/mcp/servers",
                json={
                    "name": f"private{index}",
                    "transport": "stdio",
                    "command": "npx",
                    "args": list(arguments),
                    "enabled": False,
                },
            )
            assert response.status_code == 422
            assert "Credentials must be submitted" in response.text
            assert "private-api-secret" not in response.text
            assert "private-header-secret" not in response.text
            assert "private-env-secret" not in response.text

    database = (settings.data_dir / "hostpilot.sqlite3").read_bytes()
    assert b"private-api-secret" not in database
    assert b"private-header-secret" not in database
    assert b"private-env-secret" not in database


def test_legacy_stdio_credentials_are_removed_and_server_disabled_on_startup(tmp_path) -> None:
    from hostpilot.persistence import SQLiteMCPConfigurationStore

    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    database_path = settings.data_dir / "hostpilot.sqlite3"
    settings.data_dir.mkdir()

    async def save_legacy_configuration() -> None:
        store = SQLiteMCPConfigurationStore(database_path)
        await store.initialize()
        await store.save(
            {
                "name": "legacy",
                "transport": "stdio",
                "url": None,
                "command": "npx",
                "args": ("--api-key", "legacy-private-secret"),
                "cwd": None,
                "timeout_seconds": 15,
                "enabled": True,
                "description": "",
                "api_key_header": None,
                "api_key_env": "API_KEY",
            }
        )

    asyncio.run(save_legacy_configuration())

    with TestClient(create_app(settings)) as client:
        server = client.get("/api/v1/mcp/servers").json()["servers"][0]
        assert server["args"] == []
        assert server["enabled"] is False
        assert server["connection_state"] == "disabled"
        assert server["error_code"] == "CredentialArgumentsRemoved"
        assert "legacy-private-secret" not in json.dumps(server)

    assert b"legacy-private-secret" not in database_path.read_bytes()


def test_mcp_configuration_store_migrates_existing_http_schema_for_stdio(tmp_path) -> None:
    import sqlite3

    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    settings.data_dir.mkdir()
    database_path = settings.data_dir / "hostpilot.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """CREATE TABLE mcp_server_configurations (
                name TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                timeout_seconds REAL NOT NULL,
                enabled INTEGER NOT NULL,
                api_key_header TEXT
            )"""
        )
        connection.execute(
            """INSERT INTO mcp_server_configurations
               (name, url, timeout_seconds, enabled, api_key_header)
               VALUES ('legacy', 'https://mcp.example/mcp', 15, 0, NULL)"""
        )

    with TestClient(create_app(settings)) as client:
        existing = client.get("/api/v1/mcp/servers").json()["servers"]
        assert existing[0]["name"] == "legacy"
        assert existing[0]["transport"] == "streamable_http"

        stdio = client.put(
            "/api/v1/mcp/servers",
            json={
                "name": "filesystem",
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                "enabled": False,
            },
        )
        assert stdio.status_code == 200
        servers = {item["name"]: item for item in stdio.json()["servers"]}
        assert servers["filesystem"]["transport"] == "stdio"
        assert servers["filesystem"]["url"] is None


def test_stdio_server_can_be_started_and_removed_from_connection_manager(tmp_path) -> None:
    import sys

    script = tmp_path / "stdio_server.py"
    script.write_text(
        "from fastmcp import FastMCP\n"
        "server = FastMCP('api-stdio')\n"
        "@server.tool(name='ping')\n"
        "def ping() -> str:\n"
        "    return 'pong'\n"
        "server.run(transport='stdio')\n",
        encoding="utf-8",
    )
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(create_app(settings)) as client:
        created = client.put(
            "/api/v1/mcp/servers",
            json={
                "name": "local_tools",
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(script)],
                "cwd": str(tmp_path),
                "enabled": True,
            },
        )
        assert created.status_code == 200
        assert created.json()["servers"][0]["connection_state"] == "connected"
        connections = client.get("/api/v1/mcp/connections").json()["connections"]
        stdio_connection = next(
            item for item in connections if item["connection_id"] == "local_tools"
        )
        assert stdio_connection["transport"] == "stdio"
        assert stdio_connection["discovered_tool_count"] == 1

        deleted = client.delete("/api/v1/mcp/servers/local_tools")
        assert deleted.status_code == 204
        assert client.get("/api/v1/mcp/servers").json()["servers"] == []


def test_local_api_manages_conversations_and_deletes_checkpoints(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        web_origins=("http://127.0.0.1:5173",),
    )

    with TestClient(create_app(settings)) as client:
        response = client.post("/api/v1/conversations", json={"title": "Planning"})
        assert response.status_code == 201
        conversation = response.json()
        assert "session_id" in conversation
        assert "conversation_id" not in conversation
        with sqlite3.connect(settings.data_dir / "hostpilot.sqlite3") as connection:
            state = connection.execute(
                "SELECT state FROM runtime_sessions WHERE session_id = ?",
                (conversation["session_id"],),
            ).fetchone()
        assert state == ("active",)

        listed = client.get("/api/v1/conversations")
        assert [item["session_id"] for item in listed.json()] == [conversation["session_id"]]
        assert client.get(f"/api/v1/conversations/{conversation['session_id']}").status_code == 200

        deleted = client.delete(f"/api/v1/conversations/{conversation['session_id']}")
        assert deleted.status_code == 204
        with sqlite3.connect(settings.data_dir / "hostpilot.sqlite3") as connection:
            state = connection.execute(
                "SELECT state FROM runtime_sessions WHERE session_id = ?",
                (conversation["session_id"],),
            ).fetchone()
        assert state == ("deleted",)
        assert client.get("/api/v1/conversations").json() == []


def test_single_session_metadata_read_rejects_a_closing_session(tmp_path) -> None:
    from contextlib import asynccontextmanager

    from hostpilot.persistence import SessionUnavailableError

    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(create_app(settings)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()

        @asynccontextmanager
        async def reject_session_lease(session_id: str):
            del session_id
            raise SessionUnavailableError("Session is closing or unavailable.")
            yield

        client.app.state.runtime_host.context.sessions.lease = reject_session_lease
        response = client.get(f"/api/v1/conversations/{conversation['session_id']}")
        upgrade = client.post(
            f"/api/v1/conversations/{conversation['session_id']}/sandbox/upgrade",
            json={"target_mode": SandboxMode.WORKSPACE_WRITE.value},
        )

    assert response.status_code == 404
    assert upgrade.status_code == 404


def test_conversation_sandbox_upgrade_is_monotonic_and_audited(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(create_app(settings)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        session_id = conversation["session_id"]
        assert conversation["sandbox_mode"] == SandboxMode.READ_ONLY.value

        workspace_write = client.post(
            f"/api/v1/conversations/{session_id}/sandbox/upgrade",
            json={"target_mode": SandboxMode.WORKSPACE_WRITE.value},
        )
        assert workspace_write.status_code == 200
        assert workspace_write.json()["sandbox_mode"] == SandboxMode.WORKSPACE_WRITE.value

        danger = client.post(
            f"/api/v1/conversations/{session_id}/sandbox/upgrade",
            json={"target_mode": SandboxMode.DANGER_FULL_ACCESS.value},
        )
        assert danger.status_code == 200
        assert danger.json()["sandbox_mode"] == SandboxMode.DANGER_FULL_ACCESS.value

        downgrade = client.post(
            f"/api/v1/conversations/{session_id}/sandbox/upgrade",
            json={"target_mode": SandboxMode.READ_ONLY.value},
        )
        assert downgrade.status_code == 409
        assert (
            client.get(f"/api/v1/conversations/{session_id}").json()["sandbox_mode"]
            == SandboxMode.DANGER_FULL_ACCESS.value
        )

        events = asyncio.run(client.app.state.audits.list_events(limit=10))
        upgrades = [event for event in events if event.action == "sandbox.upgrade"]
        assert len(upgrades) == 3
        assert {event.outcome for event in upgrades} == {
            AuditOutcome.ALLOWED,
            AuditOutcome.DENIED,
        }


def test_native_workspace_selection_persists_and_updates_file_tools(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    selected = tmp_path / "selected-workspace"
    selected.mkdir()
    runtime = FakeChatRuntime()

    with TestClient(
        create_app(
            settings,
            chat_agent_runtime_factory=lambda _: runtime,
            workspace_picker=lambda _current: selected,
        )
    ) as client:
        initial = client.get("/api/v1/workspace")
        assert initial.status_code == 200

        response = client.post("/api/v1/workspace/select")
        assert response.status_code == 200
        assert response.json() == {
            "path": str(selected.resolve()),
            "read_only": False,
            "selected": True,
            "message": None,
        }
        assert client.app.state.workspace_files.root == selected.resolve()
        assert client.app.state.workspace_files.read_only is False

    with TestClient(
        create_app(settings, chat_agent_runtime_factory=lambda _: FakeChatRuntime())
    ) as restarted:
        assert restarted.get("/api/v1/workspace").json()["path"] == str(selected.resolve())


def test_read_only_workspace_selection_blocks_write_tool_validation(
    tmp_path: Path, monkeypatch
) -> None:
    from hostpilot import workspace as workspace_module

    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    selected = tmp_path / "read-only-workspace"
    selected.mkdir()
    original_inspect = workspace_module.inspect_workspace

    def inspect_selected(path):
        if Path(path) == selected:
            return workspace_module.WorkspaceAccess(selected.resolve(), read_only=True)
        return original_inspect(path)

    monkeypatch.setattr("hostpilot.api.app.inspect_workspace", inspect_selected)
    monkeypatch.setattr(workspace_module, "inspect_workspace", inspect_selected)

    with TestClient(
        create_app(
            settings,
            chat_agent_runtime_factory=lambda _: FakeChatRuntime(),
            workspace_picker=lambda _current: selected,
        )
    ) as client:
        selected_response = client.post("/api/v1/workspace/select")
        assert selected_response.status_code == 200
        assert selected_response.json()["read_only"] is True
        write_tool = next(
            item
            for item in client.get("/api/v1/tools").json()["tools"]
            if item["name"] == "write_text_file"
        )
        assert write_tool["configured"] is False
        assert "只读模式" in write_tool["status_message"]

        registration = client.app.state.mcp_provider.registry.get("write_text_file")
        with pytest.raises(WorkspaceReadOnlyError, match="只读模式"):
            registration.validate_arguments({"path": "blocked.txt", "content": "blocked"})


def test_unreadable_workspace_selection_keeps_the_current_root(tmp_path: Path, monkeypatch) -> None:
    from hostpilot import workspace as workspace_module

    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    selected = tmp_path / "unreadable-workspace"
    selected.mkdir()
    original_inspect = workspace_module.inspect_workspace

    def inspect_selected(path):
        if Path(path) == selected:
            raise workspace_module.WorkspaceAccessError("HostPilot 没有读取此目录的权限。")
        return original_inspect(path)

    monkeypatch.setattr("hostpilot.api.app.inspect_workspace", inspect_selected)

    with TestClient(
        create_app(
            settings,
            chat_agent_runtime_factory=lambda _: FakeChatRuntime(),
            workspace_picker=lambda _current: selected,
        )
    ) as client:
        original_path = client.get("/api/v1/workspace").json()["path"]
        response = client.post("/api/v1/workspace/select")

        assert response.status_code == 422
        assert "没有读取" in response.json()["detail"]
        assert client.get("/api/v1/workspace").json()["path"] == original_path
        assert client.app.state.workspace_files.root.as_posix() == original_path


def test_api_rejects_unknown_conversations_and_enforces_configured_cors(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        web_origins=("http://127.0.0.1:5173",),
    )

    with TestClient(create_app(settings)) as client:
        missing = client.get("/api/v1/conversations/not-a-uuid")
        assert missing.status_code == 404
        preflight = client.options(
            "/api/v1/conversations",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert preflight.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
        rejected_preflight = client.options(
            "/api/v1/conversations",
            headers={
                "Origin": "http://localhost:9000",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert "access-control-allow-origin" not in rejected_preflight.headers


def test_tool_catalog_reports_safe_configuration_and_approval_status(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        rag_base_url="https://rag.example/api/",
        rag_user_id="private-user",
        rag_key="private-key",
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/tools")

    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime_configured"] is False
    tools = {tool["name"]: tool for tool in payload["tools"]}
    assert set(tools) == {
        "retrieve_knowledge",
        "read_text_file",
        "read_text_range",
        "list_directory",
        "search_files",
        "write_text_file",
        "write_existing_text_file",
        "run_python_code",
        "run_javascript_code",
        "run_shell_script",
    }
    assert tools["retrieve_knowledge"]["configured"] is True
    assert tools["retrieve_knowledge"]["status_message"].startswith("请先配置模型")
    assert tools["write_text_file"]["high_impact"] is True
    assert tools["write_text_file"]["confirmation_required"] is True
    assert tools["write_text_file"]["transport"] == "in_process"
    assert tools["write_text_file"]["trust_level"] == "host"
    assert tools["write_text_file"]["execution_location"] == "Linux bubblewrap 隔离进程"
    assert "创建或覆盖 UTF-8 文本文件" in tools["write_text_file"]["access_summary"]
    assert tools["write_text_file"]["sandbox_status"] in {
        "OS 沙箱已就绪",
        "OS 沙箱不可用；工具已禁用",
    }
    assert tools["write_text_file"]["configured"] is LinuxWorkspaceSandbox().host_supported
    assert tools["write_existing_text_file"]["high_impact"] is True
    assert tools["write_existing_text_file"]["confirmation_required"] is True
    assert tools["run_python_code"]["high_impact"] is True
    assert tools["run_python_code"]["confirmation_required"] is True
    assert tools["run_python_code"]["sandbox_status"] in {
        "OS 沙箱已就绪",
        "OS 沙箱不可用；工具已禁用",
    }
    assert tools["run_python_code"]["configured"] is LinuxPythonSandbox().host_supported
    assert tools["run_javascript_code"]["configured"] is LinuxJavaScriptSandbox().host_supported
    assert tools["run_shell_script"]["configured"] is LinuxShellSandbox().host_supported
    for sandbox_tool in ("run_javascript_code", "run_shell_script"):
        assert tools[sandbox_tool]["high_impact"] is True
        assert tools[sandbox_tool]["confirmation_required"] is True
        assert tools[sandbox_tool]["sandbox_status"] in {
            "OS 沙箱已就绪",
            "OS 沙箱不可用；工具已禁用",
        }
    assert "private-user" not in response.text
    assert "private-key" not in response.text


def test_mcp_connections_endpoint_returns_safe_lifecycle_state(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/mcp/connections")

    assert response.status_code == 200
    assert response.json() == {
        "connections": [
            {
                "connection_id": "builtin/local",
                "transport": "in_process",
                "state": "disconnected",
                "discovered_tool_count": 0,
                "error_code": None,
            }
        ]
    }


def test_skill_catalog_lists_valid_package_metadata_without_local_paths(tmp_path) -> None:
    skills_dir = tmp_path / "skills"
    package = skills_dir / "meeting-notes"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\n"
        "name: meeting-notes\n"
        "description: Turn notes into clear minutes.\n"
        "---\n\n"
        "Private instructions should not be exposed by the catalog.\n",
        encoding="utf-8",
    )
    invalid_package = skills_dir / "broken"
    invalid_package.mkdir()
    (invalid_package / "SKILL.md").write_text("invalid", encoding="utf-8")
    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        skills_dir=skills_dir,
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/skills")
        detail = client.get(
            "/api/v1/skills/detail",
            params={"skill_id": "legacy/meeting-notes"},
        )
        missing = client.get(
            "/api/v1/skills/detail",
            params={"skill_id": "legacy/../broken"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["invalid_package_count"] == 1
    assert any(
        skill["skill_id"] == "legacy/meeting-notes"
        and skill["execution_format"] == "skill_md"
        and skill["enabled"] is True
        and skill["source"] == "user"
        for skill in payload["skills"]
    )
    assert str(skills_dir) not in response.text
    assert "Private instructions" not in response.text
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["skill_id"] == "legacy/meeting-notes"
    assert detail_payload["files"][0] == {
        "relative_path": "SKILL.md",
        "content": (package / "SKILL.md").read_text(encoding="utf-8"),
    }
    assert str(skills_dir) not in detail.text
    assert missing.status_code == 404


def test_skill_catalog_merges_dual_format_package_and_persists_execution_choice(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    skill_id = "business/code-review-check"

    with TestClient(create_app(settings)) as client:
        catalog = client.get("/api/v1/skills").json()
        matches = [item for item in catalog["skills"] if item["skill_id"] == skill_id]
        assert len(matches) == 1
        assert matches[0]["detected_formats"] == ["yaml", "skill_md"]
        assert matches[0]["execution_mode"] == "yaml"
        assert matches[0]["available_execution_modes"] == ["yaml", "markdown", "both"]

        changed = client.patch(
            "/api/v1/skills/execution",
            json={"skill_id": skill_id, "execution_mode": "deepagents"},
        )
        assert changed.status_code == 200
        assert changed.json()["execution_mode"] == "markdown"

    with TestClient(create_app(settings)) as restarted:
        restored = restarted.get("/api/v1/skills").json()
        item = next(skill for skill in restored["skills"] if skill["skill_id"] == skill_id)
        assert item["execution_mode"] == "markdown"
        assert item["available_execution_modes"] == ["yaml", "markdown", "both"]


def test_skill_execution_mode_rejects_format_mismatch(tmp_path) -> None:
    skills_dir = tmp_path / "skills"
    package = skills_dir / "meeting-notes"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\nname: meeting-notes\ndescription: Turn notes into minutes.\n---\n\nSummarize.\n",
        encoding="utf-8",
    )
    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        skills_dir=skills_dir,
    )

    with TestClient(create_app(settings)) as client:
        response = client.patch(
            "/api/v1/skills/execution",
            json={"skill_id": "legacy/meeting-notes", "execution_mode": "yaml"},
        )

    assert response.status_code == 422


def test_skill_detail_returns_validated_yaml_manifest_without_local_paths(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(create_app(settings)) as client:
        response = client.get(
            "/api/v1/skills/detail",
            params={"skill_id": "business/code-review-check"},
        )

    assert response.status_code == 200
    payload = response.json()
    files = {file["relative_path"]: file["content"] for file in payload["files"]}
    assert "skill.yaml" in files
    assert "SKILL.md" in files
    assert "business/code-review-check" in files["skill.yaml"]
    assert "src/hostpilot/skill_packages" not in response.text


def test_skill_archive_upload_validates_and_installs_markdown_package(tmp_path) -> None:
    archive_bytes = BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr(
            "meeting-notes/SKILL.md",
            "---\nname: meeting-notes\ndescription: Turn notes into minutes.\n---\n\n"
            "Summarize decisions.\n",
        )
        archive.writestr("meeting-notes/reference.md", "Use concise headings.\n")
    skills_dir = tmp_path / "skills"
    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        skills_dir=skills_dir,
    )

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/skills/import",
            content=archive_bytes.getvalue(),
            headers={"Content-Type": "application/zip"},
        )
        catalog = client.get("/api/v1/skills")

    assert response.status_code == 201
    assert response.json() == {
        "skill_id": "legacy/meeting-notes",
        "name": "meeting-notes",
        "description": "Turn notes into minutes.",
        "execution_format": "skill_md",
        "detected_formats": ["skill_md"],
        "execution_mode": "markdown",
        "available_execution_modes": ["markdown"],
        "enabled": True,
        "source": "user",
    }
    assert "legacy/meeting-notes" in {item["skill_id"] for item in catalog.json()["skills"]}
    assert (skills_dir / "meeting-notes" / "reference.md").read_text() == "Use concise headings.\n"


def test_skill_archive_upload_rejects_invalid_zip_without_creating_skill_root(tmp_path) -> None:
    skills_dir = tmp_path / "skills"
    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        skills_dir=skills_dir,
    )

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/skills/import",
            content=b"not a zip",
            headers={"Content-Type": "application/zip"},
        )

    assert response.status_code == 422
    assert response.json()["detail"] == "文件不是有效 ZIP 包。"
    assert not skills_dir.exists()


def test_skill_management_api_toggles_and_deletes_user_markdown_skill(tmp_path) -> None:
    skills_dir = tmp_path / "skills"
    package = skills_dir / "meeting-notes"
    package.mkdir(parents=True)
    skill_content = (
        "---\nname: meeting-notes\ndescription: Turn notes into minutes.\n---\n\nSummarize.\n"
    )
    (package / "SKILL.md").write_text(skill_content, encoding="utf-8")
    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        skills_dir=skills_dir,
    )

    with TestClient(create_app(settings)) as client:
        disabled = client.patch(
            "/api/v1/skills/state",
            json={"skill_id": "legacy/meeting-notes", "enabled": False},
        )
        listing = client.get("/api/v1/skills")
        enabled = client.patch(
            "/api/v1/skills/state",
            json={"skill_id": "legacy/meeting-notes", "enabled": True},
        )
        deleted = client.delete(
            "/api/v1/skills",
            params={"skill_id": "legacy/meeting-notes"},
        )

    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert (
        next(
            skill
            for skill in listing.json()["skills"]
            if skill["skill_id"] == "legacy/meeting-notes"
        )["enabled"]
        is False
    )
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
    assert deleted.status_code == 204
    assert not package.exists()


def test_skill_management_api_protects_bundled_skills(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(create_app(settings)) as client:
        disabled = client.patch(
            "/api/v1/skills/state",
            json={"skill_id": "business/code-review-check", "enabled": False},
        )
        deleted = client.delete(
            "/api/v1/skills",
            params={"skill_id": "business/code-review-check"},
        )

    assert disabled.status_code == 404
    assert deleted.status_code == 404


def test_cancellation_diagnostics_are_persisted_and_hide_compensation_handles(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    application = create_app(settings)
    snapshot = CancellationSnapshot(
        request_id="request-1",
        scope="business/write-note",
        uncertain_operations=("write_text_file",),
        side_effects=(
            SkillSideEffect(
                "write_text_file",
                SideEffectLevel.REVERSIBLE,
                "operation-key",
                "secret-compensation-handle",
            ),
        ),
    )

    with TestClient(application) as client:
        event_id = asyncio.run(application.state.cancellations.record(snapshot))
        response = client.get("/api/v1/cancellations")

    assert response.status_code == 200
    assert response.json()[0]["event_id"] == event_id
    assert response.json()[0]["uncertain_operations"] == ["write_text_file"]
    assert response.json()[0]["side_effects"][0]["compensation_handle_available"] is True
    assert "secret-compensation-handle" not in response.text
    assert '"compensation_handle":' not in response.text


class FakeChatRuntime:
    def __init__(self) -> None:
        self.messages_by_id: dict[str, list[ConversationMessage]] = {}
        self.contexts: list[ContextSnapshot] = []
        self.closed = False
        self.token_usage: ModelTokenUsage | None = None
        self.request_budget = None

    async def aclose(self) -> None:
        self.closed = True

    async def read_messages(self, session_id: str) -> tuple[ConversationMessage, ...]:
        return tuple(self.messages_by_id.get(session_id, ()))

    async def respond(
        self,
        message: str,
        request_context,
    ) -> ChatAgentResponse:
        context = request_context.snapshot
        self.contexts.append(context)
        self.request_context = request_context
        self.request_budget = request_context.budget
        messages = self.messages_by_id.setdefault(context.session_id, [])
        messages.extend(
            (
                ConversationMessage(role=MessageRole.USER, content=message),
                ConversationMessage(role=MessageRole.ASSISTANT, content="Local test reply"),
            )
        )
        return ChatAgentResponse("Local test reply", self.token_usage)

    async def record_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        self.messages_by_id.setdefault(session_id, []).extend(
            (
                ConversationMessage(role=MessageRole.USER, content=user_message),
                ConversationMessage(role=MessageRole.ASSISTANT, content=assistant_message),
            )
        )


class RecoverableFakeChatRuntime(FakeChatRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.tool_outcomes = None
        self.resolution_results: list[tuple[str, str]] = []

    def configure_tool_outcomes(self, lifecycle) -> None:
        self.tool_outcomes = lifecycle

    async def resolve_tool_outcome(
        self, session_id: str, tool_call_id: str, *, result_text: str
    ) -> None:
        assert self.tool_outcomes is not None
        if not await self.tool_outcomes.claim_resolution(session_id, tool_call_id):
            raise ValueError("Tool outcome is missing or is being resolved.")
        self.resolution_results.append((tool_call_id, result_text))
        await self.tool_outcomes.result_checkpointed(session_id, tool_call_id)


def test_tool_outcome_recovery_api_lists_safe_metadata_and_resolves_call(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    runtime = RecoverableFakeChatRuntime()

    with TestClient(create_app(settings, chat_agent_runtime_factory=lambda _: runtime)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        session_id = conversation["session_id"]
        host_context = client.app.state.runtime_host.context
        asyncio.run(
            host_context.tool_outcomes.begin_invocation(
                session_id=session_id,
                turn_id="turn-1",
                task_run_id="task-1",
                tool_call_id="call-secret-free",
                tool_name="write_text_file",
            )
        )

        pending = client.get(f"/api/v1/conversations/{session_id}/tool-outcomes")
        invalid = client.post(
            f"/api/v1/conversations/{session_id}/tool-outcomes/call-secret-free/resolve",
            json={"resolution": "completed", "result_text": "  "},
        )
        missing_call = client.post(
            f"/api/v1/conversations/{session_id}/tool-outcomes/unknown-call/resolve",
            json={"resolution": "not_applied"},
        )
        resolved = client.post(
            f"/api/v1/conversations/{session_id}/tool-outcomes/call-secret-free/resolve",
            json={"resolution": "completed", "result_text": "Verified output"},
        )
        asyncio.run(
            host_context.tool_outcomes.begin_invocation(
                session_id=session_id,
                turn_id="turn-2",
                task_run_id=None,
                tool_call_id="call-not-applied",
                tool_name="remote_action",
            )
        )
        not_applied = client.post(
            f"/api/v1/conversations/{session_id}/tool-outcomes/call-not-applied/resolve",
            json={"resolution": "not_applied"},
        )
        remaining = client.get(f"/api/v1/conversations/{session_id}/tool-outcomes")
        missing_session = client.get("/api/v1/conversations/missing/tool-outcomes")

    assert pending.status_code == 200
    assert pending.json()[0]["tool_call_id"] == "call-secret-free"
    assert set(pending.json()[0]) == {"tool_call_id", "tool_name", "created_at", "state"}
    assert "arguments" not in pending.text
    assert invalid.status_code == 422
    assert missing_call.status_code == 404
    assert resolved.status_code == 200
    assert not_applied.status_code == 200
    assert runtime.resolution_results == [
        ("call-secret-free", "Verified output"),
        (
            "call-not-applied",
            "已核实：原工具调用未执行，未产生副作用；该调用不会自动重放。",
        ),
    ]
    assert remaining.json() == []
    assert missing_session.status_code == 404


def test_plan_recovery_api_is_session_scoped_and_hides_private_invocation_data(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    runtime = RecoverableFakeChatRuntime()

    with TestClient(create_app(settings, chat_agent_runtime_factory=lambda _: runtime)) as client:
        first = client.post("/api/v1/conversations", json={}).json()
        second = client.post("/api/v1/conversations", json={}).json()
        tasks = client.app.state.runtime_host.context.tasks
        now = datetime.now(UTC)

        async def create_recoverable_plan(session_id: str, plan_run_id: str) -> None:
            await tasks.create_plan_run(
                plan_run_id=plan_run_id,
                turn_id=f"turn-{plan_run_id}",
                session_id=session_id,
                goal="Prepare a private report",
                snapshot={
                    "version": 1,
                    "plan": {"tasks": [{"task_id": "task-1", "description": "Read source"}]},
                    "states": [
                        {"task_id": "task-1", "status": "completed", "output": {"summary": "done"}}
                    ],
                },
                started_at=now,
            )
            await tasks.prepare_invocation(
                invocation_id=f"invocation-{plan_run_id}",
                session_id=session_id,
                turn_id=f"turn-{plan_run_id}",
                plan_run_id=plan_run_id,
                task_run_id=f"task-run-{plan_run_id}",
                task_id="task-1",
                call_index=1,
                tool_id="send_message",
                retry_class="non_idempotent_write",
                idempotency_key="private-stable-key",
                request_fingerprint="fingerprint-private",
                arguments_json='{"recipient":"private@example.com"}',
                idempotency_window_seconds=3600,
            )
            await tasks.update_plan_run(
                plan_run_id,
                state="interrupted",
                snapshot={
                    "version": 1,
                    "plan": {"tasks": [{"task_id": "task-1", "description": "Read source"}]},
                    "states": [
                        {"task_id": "task-1", "status": "completed", "output": {"summary": "done"}}
                    ],
                },
                updated_at=now + timedelta(seconds=1),
            )

        asyncio.run(create_recoverable_plan(first["session_id"], "plan-first"))
        asyncio.run(create_recoverable_plan(second["session_id"], "plan-second"))

        response = client.get(f"/api/v1/conversations/{first['session_id']}/plan-runs/recoverable")

    assert response.status_code == 200
    payload = response.json()
    assert [item["plan_run_id"] for item in payload] == ["plan-first"]
    assert payload[0]["tasks"][0]["output"] == {"summary": "done"}
    assert payload[0]["pending_invocations"] == [
        {
            "invocation_id": "invocation-plan-first",
            "tool_id": "send_message",
            "retry_class": "non_idempotent_write",
            "state": "prepared",
        }
    ]
    assert "private-stable-key" not in response.text
    assert "fingerprint-private" not in response.text
    assert "private@example.com" not in response.text


def test_plan_invocation_resolution_requires_and_records_user_observations(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    runtime = RecoverableFakeChatRuntime()

    with TestClient(create_app(settings, chat_agent_runtime_factory=lambda _: runtime)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        session_id = conversation["session_id"]
        tasks = client.app.state.runtime_host.context.tasks
        now = datetime.now(UTC)

        async def create_plan_and_invocation() -> None:
            await tasks.create_plan_run(
                plan_run_id="plan-resolve",
                turn_id="turn-resolve",
                session_id=session_id,
                goal="Send a report",
                snapshot={"version": 1, "plan": {"tasks": []}, "states": []},
                started_at=now,
            )
            await tasks.prepare_invocation(
                invocation_id="invocation-resolve",
                session_id=session_id,
                turn_id="turn-resolve",
                plan_run_id="plan-resolve",
                task_run_id="task-run-resolve",
                task_id="send",
                call_index=1,
                tool_id="send_message",
                retry_class="non_idempotent_write",
                idempotency_key=None,
                request_fingerprint="fingerprint",
                arguments_json='{"recipient":"private@example.com"}',
                idempotency_window_seconds=None,
            )
            await tasks.update_plan_run(
                "plan-resolve",
                state="interrupted",
                snapshot={"version": 1, "plan": {"tasks": []}, "states": []},
                updated_at=now + timedelta(seconds=1),
            )

        asyncio.run(create_plan_and_invocation())
        path = (
            f"/api/v1/conversations/{session_id}/plan-runs/plan-resolve/"
            "invocations/invocation-resolve/resolve"
        )
        empty_result = client.post(path, json={"resolution": "executed", "result_text": " "})
        resolved = client.post(
            path,
            json={"resolution": "executed", "result_text": "Observed message id 42"},
        )
        records = asyncio.run(tasks.list_tool_invocations("plan-resolve"))

    assert empty_result.status_code == 422
    assert resolved.status_code == 200
    assert records[0].state == "user_reported"
    assert records[0].reported_result == "Observed message id 42"
    assert "private@example.com" not in resolved.text


def test_plan_resume_api_dispatches_only_after_explicit_post(tmp_path) -> None:
    class ResumeFakeRuntime(RecoverableFakeChatRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.resume_calls: list[tuple[str, object]] = []

        async def resume_plan_run(self, plan_run_id: str, request_context):
            self.resume_calls.append((plan_run_id, request_context))
            return SkillExecutionResult(SkillExecutionStatus.COMPLETED, output=None)

    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    runtime = ResumeFakeRuntime()

    with TestClient(create_app(settings, chat_agent_runtime_factory=lambda _: runtime)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        session_id = conversation["session_id"]
        tasks = client.app.state.runtime_host.context.tasks
        now = datetime.now(UTC)

        async def create_plan() -> None:
            await tasks.create_plan_run(
                plan_run_id="plan-resume",
                turn_id="turn-resume",
                session_id=session_id,
                goal="Resume a report",
                snapshot={"version": 1, "plan": {"tasks": []}, "states": []},
                started_at=now,
            )
            await tasks.update_plan_run(
                "plan-resume",
                state="interrupted",
                snapshot={"version": 1, "plan": {"tasks": []}, "states": []},
                updated_at=now + timedelta(seconds=1),
            )

        asyncio.run(create_plan())
        recoverable = client.get(f"/api/v1/conversations/{session_id}/plan-runs/recoverable")
        assert runtime.resume_calls == []
        response = client.post(f"/api/v1/conversations/{session_id}/plan-runs/plan-resume/resume")

    assert recoverable.status_code == 200
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert len(runtime.resume_calls) == 1
    assert runtime.resume_calls[0][0] == "plan-resume"
    assert runtime.resume_calls[0][1].plan_run_id == "plan-resume"


def test_plan_resume_websocket_uses_current_tool_confirmation_provider(tmp_path) -> None:
    class ConfirmingResumeRuntime(RecoverableFakeChatRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.confirmation_decisions: list[ConfirmationDecision] = []

        async def resume_plan_run(self, plan_run_id: str, request_context):
            assert request_context.plan_run_id == plan_run_id
            provider = request_context.confirmation_provider
            assert provider is not None
            decision = await provider.confirm(
                ConfirmationRequest(
                    request_id="resume-confirmation",
                    tool_name="send_message",
                    summary="发送已恢复的报告",
                )
            )
            self.confirmation_decisions.append(decision)
            return SkillExecutionResult(SkillExecutionStatus.COMPLETED, output=None)

    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    runtime = ConfirmingResumeRuntime()

    with TestClient(create_app(settings, chat_agent_runtime_factory=lambda _: runtime)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        session_id = conversation["session_id"]
        tasks = client.app.state.runtime_host.context.tasks
        now = datetime.now(UTC)
        asyncio.run(
            tasks.create_plan_run(
                plan_run_id="plan-ws-resume",
                turn_id="turn-ws-resume",
                session_id=session_id,
                goal="Send the report",
                snapshot={"version": 1, "plan": {"tasks": []}, "states": []},
                started_at=now,
            )
        )
        asyncio.run(
            tasks.update_plan_run(
                "plan-ws-resume",
                state="interrupted",
                snapshot={"version": 1, "plan": {"tasks": []}, "states": []},
                updated_at=now + timedelta(seconds=1),
            )
        )

        with client.websocket_connect("/api/v1/ws/chat") as websocket:
            websocket.send_json(
                {
                    "type": "plan.resume",
                    "session_id": session_id,
                    "plan_run_id": "plan-ws-resume",
                }
            )
            confirmation = websocket.receive_json()
            assert confirmation == {
                "type": "tool.confirmation.request",
                "data": {
                    "request_id": "resume-confirmation",
                    "tool_name": "send_message",
                    "summary": "发送已恢复的报告",
                },
            }
            websocket.send_json(
                {
                    "type": "tool.confirmation.decision",
                    "request_id": "resume-confirmation",
                    "decision": "approved",
                }
            )
            response = websocket.receive_json()

    assert response["type"] == "plan.resume.response"
    assert response["data"]["plan_run_id"] == "plan-ws-resume"
    assert runtime.confirmation_decisions == [ConfirmationDecision.APPROVED]


def test_app_runtime_host_manages_chat_runtime_lifecycle(tmp_path) -> None:
    from hostpilot.runtime import RuntimeHostState

    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    runtime = FakeChatRuntime()

    with TestClient(create_app(settings, chat_agent_runtime_factory=lambda _: runtime)) as client:
        host = client.app.state.runtime_host
        assert host.state is RuntimeHostState.READY
        assert client.app.state.chat_service.runtime is host.context.services.resolve(
            "hostpilot.agent-runtime"
        )
        mcp_plugin = host.context.services.resolve("hostpilot.mcp-provider")
        assert mcp_plugin.provider is client.app.state.mcp_provider
        assert host.context.services.resolve("hostpilot.routing-model-runtime") is (
            client.app.state.routing_models_plugin
        )
        assert host.context.services.resolve("hostpilot.tool-facade") is (
            client.app.state.tool_facade
        )
        assert (
            host.context.services.resolve("hostpilot.conversation-store")
            is client.app.state.conversations
        )
        assert (
            host.context.services.resolve("hostpilot.conversation-checkpointer")
            is client.app.state.checkpointer
        )
        assert {
            "hostpilot.model-configuration-store",
            "hostpilot.routing-model-configuration-store",
            "hostpilot.mcp-configuration-store",
            "hostpilot.harness-configuration-store",
            "hostpilot.workspace-configuration-store",
            "hostpilot.skill-execution-store",
        } <= set(host.context.services.snapshot())
        conversation = client.post("/api/v1/conversations", json={}).json()
        response = client.post(
            f"/api/v1/conversations/{conversation['session_id']}/messages",
            json={"message": "Hello"},
        )
        assert response.status_code == 200
        assert [turn.state.value for turn in host.context.turns.history] == ["completed"]

    assert host.state is RuntimeHostState.STOPPED
    assert host.context.services.snapshot() == {}
    assert runtime.closed


def test_mcp_provider_refresh_keeps_runtime_tool_facade_stable(tmp_path) -> None:
    class RecordingFactory:
        def __init__(self) -> None:
            self.scopes = []
            self.coordinators = []

        def create_scope(self, registry, **configuration):
            scope = DefaultToolExecutorFactory().create_scope(registry, **configuration)
            self.scopes.append(scope)
            return scope

        def create_coordinator(self, primary_scope, compensation_scope):
            coordinator = DefaultToolExecutorFactory().create_coordinator(
                primary_scope, compensation_scope
            )
            self.coordinators.append(coordinator)
            return coordinator

    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        model_name="gpt-4o-mini",
        model_api_key="test-key",
    )
    factory = RecordingFactory()

    with TestClient(create_app(settings, tool_executor_factory=factory)) as client:
        host = client.app.state.runtime_host
        facade = client.app.state.tool_facade
        previous_provider = client.app.state.mcp_provider
        assert host.context.services.resolve("hostpilot.tool-governance") is factory

        response = client.put(
            "/api/v1/mcp/servers",
            json={
                "name": "disabled-test",
                "url": "http://127.0.0.1:1/mcp",
                "enabled": False,
            },
        )

        assert response.status_code == 200, response.text
        assert client.app.state.tool_facade is facade
        assert client.app.state.chat_service is not None
        assert client.app.state.chat_service.tool_provider is facade
        assert host.context.services.resolve("hostpilot.tool-facade") is facade
        assert client.app.state.mcp_provider is not previous_provider
        assert host.context.services.resolve("hostpilot.mcp-provider").provider is (
            client.app.state.mcp_provider
        )
        assert len(factory.scopes) == 4
        assert len(factory.coordinators) == 2
        assert client.app.state.mcp_provider._tool_coordinator is factory.coordinators[-1]


def test_runtime_reuses_sandbox_backends_across_initial_and_candidate_providers(
    tmp_path, monkeypatch
) -> None:
    import hostpilot.orchestration.chat_runtime_composition as composition_module
    import hostpilot.tools.mcp as mcp_module
    from hostpilot.orchestration.chat_runtime_assembly_host_plugin import (
        ChatRuntimeAssemblyHostPlugin,
    )
    from hostpilot.sandbox import LinuxSandboxBackendFactory

    class RecordingSandboxFactory:
        def __init__(self) -> None:
            self.calls = 0
            self.backends = None

        def create(self):
            self.calls += 1
            self.backends = LinuxSandboxBackendFactory().create()
            return self.backends

    backend_factory = RecordingSandboxFactory()
    original_builder = mcp_module.build_retrieval_mcp_provider
    providers = []

    def record_builder(*args, **kwargs):
        provider = original_builder(*args, **kwargs)
        providers.append((provider, kwargs))
        return provider

    monkeypatch.setattr(mcp_module, "build_retrieval_mcp_provider", record_builder)
    assembly_requests = []
    original_build_candidate = ChatRuntimeAssemblyHostPlugin.build_candidate

    def record_assembly(plugin, request):
        assembly_requests.append(request)
        return original_build_candidate(plugin, request)

    monkeypatch.setattr(ChatRuntimeAssemblyHostPlugin, "build_candidate", record_assembly)
    monkeypatch.setattr(
        composition_module,
        "build_configured_chat_agent_runtime",
        lambda *_args, **_kwargs: FakeChatRuntime(),
    )
    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        model_name="initial-model",
        model_api_key="initial-key",
    )

    with TestClient(create_app(settings, sandbox_backend_factory=backend_factory)) as client:
        context = client.app.state.runtime_host.context
        assembly = context.services.resolve("hostpilot.chat-runtime-assembly")
        assert isinstance(assembly, ChatRuntimeAssemblyHostPlugin)
        shared_backends = context.services.resolve("hostpilot.sandbox-backends")
        initial_provider = client.app.state.mcp_provider
        stable_tool_facade = client.app.state.tool_facade
        assert initial_provider.filesystem_sandbox is shared_backends.filesystem
        assert len(assembly_requests) == 1
        assert assembly_requests[0].settings.model_name == "initial-model"
        assert assembly_requests[0].tool_provider is stable_tool_facade

        mcp_refresh = client.put(
            "/api/v1/mcp/servers",
            json={
                "name": "disabled-test",
                "url": "http://127.0.0.1:1/mcp",
                "enabled": False,
            },
        )
        assert mcp_refresh.status_code == 200, mcp_refresh.text
        mcp_candidate = client.app.state.mcp_provider
        assert mcp_candidate is not initial_provider
        assert mcp_candidate.filesystem_sandbox is shared_backends.filesystem

        model_refresh = client.put(
            "/api/v1/model/configuration",
            json={
                "model_name": "test-model",
                "api_key": "test-key",
                "base_url": "http://127.0.0.1:8080/v1",
                "streaming": False,
            },
        )
        assert model_refresh.status_code == 200, model_refresh.text
        model_candidate = client.app.state.mcp_provider
        assert model_candidate is not mcp_candidate
        assert model_candidate.filesystem_sandbox is shared_backends.filesystem
        assert [request.settings.model_name for request in assembly_requests] == [
            "initial-model",
            "initial-model",
            "test-model",
        ]
        assert assembly_requests[1].tool_provider is not stable_tool_facade
        assert assembly_requests[2].tool_provider is not assembly_requests[1].tool_provider
        assert all(request.tools for request in assembly_requests)

        assert backend_factory.calls == 1
        assert len(providers) == 3
        assert all(kwargs["sandbox_backends"] is shared_backends for _, kwargs in providers)
        assert len({id(provider.workspace) for provider, _ in providers}) == 3
        assert len({id(provider.registry) for provider, _ in providers}) == 3


def test_failed_chat_runtime_candidate_does_not_replace_active_provider_or_runtime(
    tmp_path, monkeypatch
) -> None:
    import hostpilot.orchestration.chat_runtime_composition as composition_module
    import hostpilot.tools.mcp as mcp_module

    factory_calls = 0
    candidate_providers = []
    original_provider_builder = mcp_module.build_retrieval_mcp_provider

    def record_provider_builder(*args, **kwargs):
        provider = original_provider_builder(*args, **kwargs)
        candidate_providers.append(provider)
        return provider

    def build_candidate(*_args, **_kwargs):
        nonlocal factory_calls
        factory_calls += 1
        if factory_calls > 1:
            raise RuntimeError("candidate assembly failed")
        return FakeChatRuntime()

    monkeypatch.setattr(composition_module, "build_configured_chat_agent_runtime", build_candidate)
    monkeypatch.setattr(mcp_module, "build_retrieval_mcp_provider", record_provider_builder)
    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        model_name="active-model",
        model_api_key="active-key",
    )

    with TestClient(create_app(settings)) as client:
        plugin = client.app.state.chat_agent_runtime_plugin
        active_runtime = plugin._agents.current
        active_provider = client.app.state.mcp_provider

        response = client.put(
            "/api/v1/mcp/servers",
            json={"name": "candidate-failure", "url": "http://127.0.0.1:1/mcp", "enabled": False},
        )

        assert response.status_code == 200, response.text
        assert factory_calls == 2
        assert client.app.state.mcp_provider is active_provider
        assert plugin._agents.current is active_runtime
        assert candidate_providers[0] is active_provider
        assert candidate_providers[1]._started is False
        assert active_provider._started is True


@pytest.mark.parametrize("custom_factory", [False, True])
def test_unused_default_chat_runtime_assembly_is_not_called(
    tmp_path, monkeypatch, custom_factory
) -> None:
    from hostpilot.orchestration.chat_runtime_assembly_host_plugin import (
        ChatRuntimeAssemblyHostPlugin,
    )

    assembly_calls = []

    def record_assembly(_plugin, request):
        assembly_calls.append(request)
        raise AssertionError("default assembly must not run for this startup path")

    monkeypatch.setattr(ChatRuntimeAssemblyHostPlugin, "build_candidate", record_assembly)
    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        model_name="configured-model" if custom_factory else None,
        model_api_key="configured-key" if custom_factory else None,
    )
    runtime_factory = (lambda _: FakeChatRuntime()) if custom_factory else None

    with TestClient(create_app(settings, chat_agent_runtime_factory=runtime_factory)) as client:
        assert client.app.state.chat_runtime_assembly_plugin is not None

    assert assembly_calls == []


def test_routing_evaluation_endpoint_never_executes_selected_skill(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    runtime = FakeChatRuntime()

    class Selector:
        async def select(self, message, request_context):
            return SkillSelection(
                "legacy/office-automation",
                {"text": "private selector payload"},
                "matched_skill",
            )

    runtime.selector = Selector()

    with TestClient(create_app(settings, chat_agent_runtime_factory=lambda _: runtime)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        response = client.post(
            f"/api/v1/conversations/{conversation['session_id']}/routing-evaluation",
            json={"message": "请整理这段文字，但不要读写文件"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["request_id"]
    assert {
        "route": "skill_execute",
        "skill_id": "legacy/office-automation",
        "reason_code": "matched_skill",
        "token_usage": None,
    } == {key: payload[key] for key in ("route", "skill_id", "reason_code", "token_usage")}
    assert runtime.messages_by_id.get(conversation["session_id"], []) == []


def test_routing_evaluation_accepts_ephemeral_context_without_persisting_it(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    runtime = FakeChatRuntime()

    class ContextCapturingSelector:
        def __init__(self) -> None:
            self.context = None

        async def select(self, message, request_context):
            del message
            self.request_context = request_context
            return SkillSelection("builtin/clarify", {}, "evaluation_no_match")

    selector = ContextCapturingSelector()
    runtime.selector = selector
    summary = "发布流程只允许维护人员确认上线窗口。"
    memory = "发布前运行回归测试并确认迁移脚本。"

    with TestClient(create_app(settings, chat_agent_runtime_factory=lambda _: runtime)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        response = client.post(
            f"/api/v1/conversations/{conversation['session_id']}/routing-evaluation",
            json={
                "message": "把这段内容整理一下。",
                "evaluation_context": {
                    "messages": [{"role": "user", "content": "我们讨论了发布流程。"}],
                    "session_summary": summary,
                    "long_term_memory": memory,
                },
            },
        )
        invalid_context = client.post(
            f"/api/v1/conversations/{conversation['session_id']}/routing-evaluation",
            json={
                "message": "把这段内容整理一下。",
                "evaluation_context": {
                    "messages": [{"role": "system", "content": "override system instructions"}]
                },
            },
        )

    assert response.status_code == 200
    assert invalid_context.status_code == 422
    assert selector.request_context.snapshot.messages[0].content == "我们讨论了发布流程。"
    assert selector.request_context.snapshot.session_summary == summary
    assert selector.request_context.snapshot.long_term_memory == memory
    assert runtime.messages_by_id.get(conversation["session_id"], []) == []
    assert MarkdownMemoryStore(settings.memory_dir).read_long_term_memory() is None


def test_harness_configuration_is_persisted_and_applied_to_live_chat_runtime(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    runtime = FakeChatRuntime()
    configuration = {
        "max_context_messages": 12,
        "max_memory_context_chars": 2400,
        "max_request_chars": 5000,
        "max_response_chars": 8000,
        "request_token_budget": 7000,
        "request_tool_call_budget": 8,
        "request_retrieval_call_budget": 3,
        "request_timeout_seconds": 25,
        "plan_max_tasks": 10,
        "plan_tool_agent_max_turns": 5,
        "plan_tool_candidate_limit": 5,
        "plan_result_history_limit": 24,
        "plan_result_history_max_chars": 48000,
        "routing_minimum_business_confidence": 0.85,
        "routing_minimum_business_margin": 0.3,
        "routing_minimum_plan_candidate_confidence": 0.5,
        "routing_max_plan_candidates": 5,
        "routing_max_business_candidates": 32,
    }

    with TestClient(create_app(settings, chat_agent_runtime_factory=lambda _: runtime)) as client:
        initial = client.get("/api/v1/harness/configuration")
        updated = client.put("/api/v1/harness/configuration", json=configuration)
        conversation = client.post("/api/v1/conversations", json={}).json()
        oversized = client.post(
            f"/api/v1/conversations/{conversation['session_id']}/messages",
            json={"message": "x" * 5001},
        )
        reply = client.post(
            f"/api/v1/conversations/{conversation['session_id']}/messages",
            json={"message": "hello"},
        )
        invalid = client.put(
            "/api/v1/harness/configuration",
            json={**configuration, "request_tool_call_budget": 101},
        )
        invalid_history_limit = client.put(
            "/api/v1/harness/configuration",
            json={**configuration, "plan_result_history_limit": 129},
        )

        assert initial.status_code == 200
        assert initial.json()["request_token_budget"] == settings.request_token_budget
        assert updated.status_code == 200
        assert updated.json() == configuration
        assert invalid.status_code == 422
        assert invalid_history_limit.status_code == 422
        assert oversized.status_code == 413
        assert reply.status_code == 200
        tasks = client.app.state.runtime_host.context.tasks
        assert tasks.result_history_limit == 24
        assert tasks.result_history_max_chars == 48000
        assert runtime.contexts[-1].budget.limits.total_tokens == 7000
        assert client.app.state.chat_service.budget_limits.tool_calls == 8
        assert client.app.state.chat_service.context_builder.max_messages == 12
        assert client.app.state.chat_service.context_builder.max_memory_chars == 2400
        assert client.app.state.chat_service.output_processor.max_chars == 8000
        assert client.app.state.chat_service.max_request_chars == 5000

    restored_runtime = FakeChatRuntime()
    with TestClient(
        create_app(settings, chat_agent_runtime_factory=lambda _: restored_runtime)
    ) as client:
        restored = client.get("/api/v1/harness/configuration")
        assert restored.status_code == 200
        assert restored.json() == configuration
        tasks = client.app.state.runtime_host.context.tasks
        assert tasks.result_history_limit == 24
        assert tasks.result_history_max_chars == 48000


def test_chat_api_builds_scoped_context_and_returns_runtime_reply(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        web_origins=("http://127.0.0.1:5173",),
    )
    runtime = FakeChatRuntime()
    runtime.token_usage = ModelTokenUsage(input_tokens=8, output_tokens=3, total_tokens=11)
    memory = MarkdownMemoryStore(settings.memory_dir)

    with TestClient(create_app(settings, chat_agent_runtime_factory=lambda _: runtime)) as client:
        conversation = client.post(
            "/api/v1/conversations",
            json={"title": "Context test"},
        ).json()
        memory.write_session_summary(conversation["session_id"], "Session facts")
        memory.write_long_term_memory("Shared preference")

        response = client.post(
            f"/api/v1/conversations/{conversation['session_id']}/messages",
            json={"message": "Hello"},
        )

        assert response.status_code == 200
        assert response.json()["assistant_message"] == "Local test reply"
        assert response.json()["token_usage"]["total_tokens"] == 11
        assert runtime.request_budget.snapshot().usage.total_tokens == 11
        assert runtime.contexts[0].session_summary == "Session facts"
        assert runtime.contexts[0].long_term_memory == "Shared preference"
        assert runtime.contexts[0].budget.limits.total_tokens == settings.request_token_budget

        history = client.get(f"/api/v1/conversations/{conversation['session_id']}/messages")
        assert [message["role"] for message in history.json()["messages"]] == [
            "user",
            "assistant",
        ]

    assert runtime.closed


def test_chat_api_executes_selected_skill_and_persists_routed_turn(tmp_path) -> None:
    from hostpilot.orchestration.skill_runtime import (
        SkillEngineAgentAdapter,
        SkillSelection,
    )
    from hostpilot.skills.runtime import SkillExecutionResult, SkillExecutionStatus

    class Selector:
        async def select(self, message, request_context):
            del message, request_context
            return SkillSelection(
                "business/summary", {"notes": "Decision: ship"}, "business_skill_match"
            )

    class SkillEngine:
        async def execute(self, request):
            assert request.skill_id == "business/summary"
            assert request.payload == {"notes": "Decision: ship"}
            return SkillExecutionResult(
                SkillExecutionStatus.COMPLETED,
                output={"summary": "Decision: ship"},
            )

    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    chat_runtime = FakeChatRuntime()
    runtime = SkillEngineAgentAdapter(chat_runtime, SkillEngine(), Selector())

    with TestClient(create_app(settings, chat_agent_runtime_factory=lambda _: runtime)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        response = client.post(
            f"/api/v1/conversations/{conversation['session_id']}/messages",
            json={"message": "Summarize these notes"},
        )
        history = client.get(f"/api/v1/conversations/{conversation['session_id']}/messages")

    assert response.status_code == 200
    assert response.json()["route"] == "skill_execute"
    assert response.json()["assistant_message"] == "Decision: ship"
    assert [item["content"] for item in history.json()["messages"]] == [
        "Summarize these notes",
        "Decision: ship",
    ]


def test_chat_api_reports_provider_tokens_that_exceed_the_request_budget(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        request_token_budget=5,
    )
    runtime = FakeChatRuntime()
    runtime.token_usage = ModelTokenUsage(input_tokens=4, output_tokens=3, total_tokens=7)

    with TestClient(create_app(settings, chat_agent_runtime_factory=lambda _: runtime)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        response = client.post(
            f"/api/v1/conversations/{conversation['session_id']}/messages",
            json={"message": "Hello"},
        )

    assert response.status_code == 429
    assert response.json()["detail"] == "Request budget exhausted for total_tokens."
    assert runtime.request_budget.snapshot().usage.total_tokens == 0


def test_chat_api_rejects_messages_over_the_configured_character_limit(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        max_request_chars=3,
    )
    runtime = FakeChatRuntime()

    with TestClient(create_app(settings, chat_agent_runtime_factory=lambda _: runtime)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        response = client.post(
            f"/api/v1/conversations/{conversation['session_id']}/messages",
            json={"message": "four"},
        )

    assert response.status_code == 413
    assert not runtime.contexts


def test_chat_api_returns_gateway_timeout_when_request_budget_expires(tmp_path) -> None:
    class SlowRuntime(FakeChatRuntime):
        async def respond(self, message: str, request_context) -> ChatAgentResponse:
            await asyncio.sleep(0.05)
            return await super().respond(message, request_context)

    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        request_timeout_seconds=0.01,
    )
    runtime = SlowRuntime()

    with TestClient(create_app(settings, chat_agent_runtime_factory=lambda _: runtime)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        response = client.post(
            f"/api/v1/conversations/{conversation['session_id']}/messages",
            json={"message": "Hello"},
        )

    assert response.status_code == 504


def test_chat_api_rejects_an_oversized_agent_response(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        max_response_chars=5,
    )
    runtime = FakeChatRuntime()

    with TestClient(create_app(settings, chat_agent_runtime_factory=lambda _: runtime)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        response = client.post(
            f"/api/v1/conversations/{conversation['session_id']}/messages",
            json={"message": "Hello"},
        )

    assert response.status_code == 502


def test_chat_api_returns_clarification_from_an_injected_router(tmp_path) -> None:
    class ClarifyingRouter:
        def decide(self, message: str, context: ContextSnapshot) -> RouteDecision:
            return RouteDecision(
                request_id=context.request_id,
                route=Route.CLARIFY,
                clarification_prompt="Which project do you mean?",
            )

    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    runtime = FakeChatRuntime()

    with TestClient(
        create_app(
            settings,
            chat_agent_runtime_factory=lambda _: runtime,
            agent_router=ClarifyingRouter(),
        )
    ) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        response = client.post(
            f"/api/v1/conversations/{conversation['session_id']}/messages",
            json={"message": "Continue that"},
        )
        history = client.get(f"/api/v1/conversations/{conversation['session_id']}/messages")

    assert response.status_code == 200
    assert response.json()["route"] == "clarify"
    assert response.json()["assistant_message"] == "Which project do you mean?"
    assert not runtime.contexts
    assert [item["content"] for item in history.json()["messages"]] == [
        "Continue that",
        "Which project do you mean?",
    ]


def test_chat_api_fails_closed_for_routes_without_an_execution_path(tmp_path) -> None:
    class ToolRouter:
        def decide(self, message: str, context: ContextSnapshot) -> RouteDecision:
            return RouteDecision(
                request_id=context.request_id,
                route=Route.TOOL_CALL,
                tool_name="unregistered",
            )

    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    runtime = FakeChatRuntime()

    with TestClient(
        create_app(
            settings,
            chat_agent_runtime_factory=lambda _: runtime,
            agent_router=ToolRouter(),
        )
    ) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        response = client.post(
            f"/api/v1/conversations/{conversation['session_id']}/messages",
            json={"message": "Use a tool"},
        )

    assert response.status_code == 501
    assert not runtime.contexts


def test_chat_api_reports_missing_agent_runtime(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(create_app(settings)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        response = client.post(
            f"/api/v1/conversations/{conversation['session_id']}/messages",
            json={"message": "Hello"},
        )

    assert response.status_code == 503
    assert "model provider" in response.json()["detail"]


def test_memory_proposal_is_written_only_after_explicit_approval(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(create_app(settings)) as client:
        submitted = client.post(
            "/api/v1/memory-proposals",
            json={"scope": "long_term", "content": "Approved preference"},
        )
        assert submitted.status_code == 202
        assert submitted.json()["importance_tier"] == "medium"
        assert submitted.json()["importance_score"] is not None
        proposal_id = submitted.json()["proposal_id"]
        assert client.get("/api/v1/memory-proposals").json()[0]["proposal_id"] == proposal_id

        approval = client.post(f"/api/v1/memory-proposals/{proposal_id}/approve")

        assert approval.status_code == 204
        assert client.get("/api/v1/memory-proposals").json() == []
        assert (
            MarkdownMemoryStore(settings.memory_dir).read_long_term_memory()
            == "Approved preference"
        )


def test_low_importance_memory_proposal_is_discarded_immediately(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/memory-proposals",
            json={"scope": "long_term", "content": "好的。"},
        )

        assert response.status_code == 200
        assert response.json()["discarded"] is True
        assert response.json()["importance_tier"] == "low"
        assert client.get("/api/v1/memory-proposals").json() == []
        assert MarkdownMemoryStore(settings.memory_dir).read_long_term_memory() is None


def test_rejected_memory_proposal_has_no_file_side_effect(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(create_app(settings)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        submitted = client.post(
            "/api/v1/memory-proposals",
            json={
                "scope": "session_summary",
                "session_id": conversation["session_id"],
                "content": "Must not be saved",
            },
        )
        proposal_id = submitted.json()["proposal_id"]

        rejection = client.delete(f"/api/v1/memory-proposals/{proposal_id}")

        assert rejection.status_code == 204
        assert (
            MarkdownMemoryStore(settings.memory_dir).read_session_summary(
                conversation["session_id"]
            )
            is None
        )


def test_memory_proposals_for_deleted_conversations_are_discarded(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(create_app(settings)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        client.post(
            "/api/v1/memory-proposals",
            json={
                "scope": "session_summary",
                "session_id": conversation["session_id"],
                "content": "Session proposal",
            },
        )

        assert (
            client.delete(f"/api/v1/conversations/{conversation['session_id']}").status_code == 204
        )
        assert client.get("/api/v1/memory-proposals").json() == []


def test_api_rejects_write_requests_from_untrusted_origins(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        web_origins=("http://127.0.0.1:5173",),
    )

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/conversations",
            headers={"Origin": "http://localhost:9000"},
            json={"title": "blocked"},
        )

    assert response.status_code == 403


# 测试在确认超时
def test_status_websocket_sends_health_and_answers_ping(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        web_origins=("http://localhost:5173",),
    )

    with TestClient(create_app(settings)) as client:
        with client.websocket_connect(
            "/api/v1/ws/status",
            headers={"Origin": "http://localhost:5173"},
        ) as websocket:
            snapshot = websocket.receive_json()
            websocket.send_json({"type": "ping", "request_id": "heartbeat-1"})
            pong = websocket.receive_json()

    assert snapshot == {
        "type": "status.snapshot",
        "data": {"api": "ok", "protocol_version": 1},
    }
    assert pong == {"type": "pong", "request_id": "heartbeat-1"}


# 测试在确认超时后是否不会写入文件
def test_status_websocket_rejects_untrusted_origin(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(create_app(settings)) as client:
        try:
            with client.websocket_connect(
                "/api/v1/ws/status",
                headers={"Origin": "http://attacker.example"},
            ):
                raise AssertionError("Untrusted WebSocket origin was accepted.")
        except WebSocketDisconnect as error:
            assert error.code == 1008


# 测试在确认超时后是否不会写入文件
def test_status_websocket_rejects_malformed_and_oversized_messages(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(create_app(settings)) as client:
        with client.websocket_connect("/api/v1/ws/status") as websocket:
            websocket.receive_json()
            websocket.send_text("not-json")
            assert websocket.receive_json()["data"]["code"] == "invalid_message"
            websocket.send_text("x" * 1025)
            assert websocket.receive_json()["data"]["code"] == "message_too_large"


class ConfirmingChatRuntime(FakeChatRuntime):
    async def respond(self, message, request_context) -> ChatAgentResponse:
        assert request_context.confirmation_provider is not None
        decision = await request_context.confirmation_provider.confirm(
            ConfirmationRequest(
                request_id="confirmation-1",
                tool_name="write_text_file",
                summary="写入 approval-demo.txt（15 字节）",
            )
        )
        assert decision is ConfirmationDecision.APPROVED
        return await super().respond(message, request_context)


class RecoveringChatRuntime(FakeChatRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.resume_calls = []
        self.confirmation_decisions = []

    async def recoverability_reason(self, session_id, turn_id):
        return None

    async def recovery_tool_reason(self, session_id, turn_id, *, task_lifecycle):
        return None

    async def recoverable_user_message(self, session_id, turn_id):
        return "private original request"

    async def resume_turn(self, turn_id, request_context):
        self.resume_calls.append((turn_id, request_context))
        provider = request_context.confirmation_provider
        assert provider is not None
        decision = await provider.confirm(
            ConfirmationRequest(
                request_id="turn-recovery-approval",
                tool_name="send_message",
                summary="发送恢复后的报告",
            )
        )
        self.confirmation_decisions.append(decision)
        return ChatAgentResponse("Recovery completed", route=Route.DIRECT)


async def seed_interrupted_chat_turn(settings: Settings, session_id: str) -> None:
    store = SQLiteTurnStore(settings.data_dir / "hostpilot.sqlite3")
    await store.initialize()
    now = datetime.now(UTC)
    await store.register_host(
        "recovery-test-old-host",
        now=now,
        lease_expires_at=now + timedelta(seconds=5),
    )
    await store.create_turn(
        turn_id="turn-to-recover",
        session_id=session_id,
        runtime_host_id="recovery-test-old-host",
        started_at=now,
    )
    await store.release_host("recovery-test-old-host", now=now + timedelta(seconds=6))
    await store.finish_turn(
        "turn-to-recover",
        runtime_host_id="recovery-test-old-host",
        state="interrupted",
        finished_at=now + timedelta(seconds=6),
    )


def test_runtime_chat_recovery_query_is_safe_and_resume_uses_websocket_approval(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    runtime = RecoveringChatRuntime()

    with TestClient(create_app(settings, chat_agent_runtime_factory=lambda _: runtime)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        session_id = conversation["session_id"]
        asyncio.run(seed_interrupted_chat_turn(settings, session_id))

        listed = client.get(f"/api/v1/conversations/{session_id}/turns/recoverable")
        assert listed.status_code == 200
        assert listed.json() == [
            {
                "turn_id": "turn-to-recover",
                "started_at": listed.json()[0]["started_at"],
                "recoverable": True,
                "reason_code": None,
            }
        ]
        assert "private original request" not in listed.text
        assert runtime.resume_calls == []

        with client.websocket_connect("/api/v1/ws/chat") as websocket:
            websocket.send_json(
                {
                    "type": "turn.resume",
                    "session_id": session_id,
                    "turn_id": "turn-to-recover",
                }
            )
            approval = websocket.receive_json()
            assert approval["type"] == "tool.confirmation.request"
            assert approval["data"]["tool_name"] == "send_message"
            websocket.send_json(
                {
                    "type": "tool.confirmation.decision",
                    "request_id": "turn-recovery-approval",
                    "decision": "approved",
                }
            )
            response = websocket.receive_json()

    assert response["type"] == "turn.resume.response"
    assert response["data"]["turn_id"] == "turn-to-recover"
    assert response["data"]["status"] == "completed"
    assert response["data"]["assistant_message"] == "Recovery completed"
    assert runtime.confirmation_decisions == [ConfirmationDecision.APPROVED]


def test_runtime_chat_recovery_rejects_wrong_session_and_stale_turn(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    runtime = RecoveringChatRuntime()

    with TestClient(create_app(settings, chat_agent_runtime_factory=lambda _: runtime)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        session_id = conversation["session_id"]
        asyncio.run(seed_interrupted_chat_turn(settings, session_id))
        other = client.post("/api/v1/conversations", json={}).json()["session_id"]

        wrong_session = client.get(f"/api/v1/conversations/{other}/turns/recoverable")
        assert wrong_session.status_code == 200
        assert wrong_session.json() == []
        with client.websocket_connect("/api/v1/ws/chat") as websocket:
            websocket.send_json(
                {
                    "type": "turn.resume",
                    "session_id": other,
                    "turn_id": "turn-to-recover",
                }
            )
            error = websocket.receive_json()

    assert error == {"type": "chat.error", "data": {"code": "turn_not_recoverable"}}
    assert runtime.resume_calls == []


class RejectingRecoveringChatRuntime(RecoveringChatRuntime):
    async def resume_turn(self, turn_id, request_context):
        self.resume_calls.append((turn_id, request_context))
        provider = request_context.confirmation_provider
        assert provider is not None
        decision = await provider.confirm(
            ConfirmationRequest("reject-recovery", "send_message", "发送报告")
        )
        self.confirmation_decisions.append(decision)
        if decision is not ConfirmationDecision.APPROVED:
            raise ToolConfirmationDeniedError(decision)
        return ChatAgentResponse("Recovery completed", route=Route.DIRECT)


def test_rejected_recovery_approval_leaves_turn_cancelled(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    runtime = RejectingRecoveringChatRuntime()

    with TestClient(create_app(settings, chat_agent_runtime_factory=lambda _: runtime)) as client:
        session_id = client.post("/api/v1/conversations", json={}).json()["session_id"]
        asyncio.run(seed_interrupted_chat_turn(settings, session_id))
        with client.websocket_connect("/api/v1/ws/chat") as websocket:
            websocket.send_json(
                {
                    "type": "turn.resume",
                    "session_id": session_id,
                    "turn_id": "turn-to-recover",
                }
            )
            assert websocket.receive_json()["type"] == "tool.confirmation.request"
            websocket.send_json(
                {
                    "type": "tool.confirmation.decision",
                    "request_id": "reject-recovery",
                    "decision": "rejected",
                }
            )
            error = websocket.receive_json()
        turns = asyncio.run(SQLiteTurnStore(settings.data_dir / "hostpilot.sqlite3").list_turns())

    assert error == {"type": "chat.error", "data": {"code": "tool_confirmation_rejected"}}
    assert turns[0].state == "cancelled"
    assert runtime.confirmation_decisions == [ConfirmationDecision.REJECTED]


def test_recovery_websocket_disconnect_interrupts_without_continuing(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    runtime = RecoveringChatRuntime()

    with TestClient(create_app(settings, chat_agent_runtime_factory=lambda _: runtime)) as client:
        session_id = client.post("/api/v1/conversations", json={}).json()["session_id"]
        asyncio.run(seed_interrupted_chat_turn(settings, session_id))
        with client.websocket_connect("/api/v1/ws/chat") as websocket:
            websocket.send_json(
                {
                    "type": "turn.resume",
                    "session_id": session_id,
                    "turn_id": "turn-to-recover",
                }
            )
            assert websocket.receive_json()["type"] == "tool.confirmation.request"
            websocket.close()

            async def wait_for_interrupted_turn():
                store = SQLiteTurnStore(settings.data_dir / "hostpilot.sqlite3")
                for _ in range(100):
                    turns = await store.list_turns()
                    if turns[0].state != "running":
                        return turns
                    await asyncio.sleep(0.01)
                return turns

            turns = asyncio.run(wait_for_interrupted_turn())

    assert len(runtime.resume_calls) == 1
    assert runtime.confirmation_decisions == []
    assert turns[0].state == "interrupted"


# 测试在确认超时后是否不会写入文件
def test_websocket_confirmation_disconnect_during_send_uses_client_cancellation() -> None:
    class DisconnectingWebSocket:
        async def send_json(self, message) -> None:
            del message
            raise WebSocketDisconnect(code=1000)

        async def receive_text(self) -> str:
            raise AssertionError("receive_text must not run after send disconnects")

    provider = WebSocketConfirmationProvider(DisconnectingWebSocket())

    with pytest.raises(WebSocketDisconnectCancellation):
        asyncio.run(provider.confirm(ConfirmationRequest("send-disconnect", "write", "Write")))


# 测试在确认超时后是否不会写入文件
def test_websocket_confirmation_provider_serializes_concurrent_prompts() -> None:
    class FakeWebSocket:
        def __init__(self) -> None:
            self.sent = []
            self.active_receivers = 0
            self.maximum_receivers = 0
            self.decisions: asyncio.Queue[str] = asyncio.Queue()

        async def send_json(self, message) -> None:
            self.sent.append(message)
            request_id = message["data"]["request_id"]
            await self.decisions.put(
                json.dumps(
                    {
                        "type": "tool.confirmation.decision",
                        "request_id": request_id,
                        "decision": "approved",
                    }
                )
            )

        async def receive_text(self) -> str:
            self.active_receivers += 1
            self.maximum_receivers = max(self.maximum_receivers, self.active_receivers)
            try:
                await asyncio.sleep(0)
                return await self.decisions.get()
            finally:
                self.active_receivers -= 1

    async def scenario() -> None:
        websocket = FakeWebSocket()
        provider = WebSocketConfirmationProvider(websocket)
        decisions = await asyncio.gather(
            provider.confirm(ConfirmationRequest("confirmation-1", "write_a", "Write A")),
            provider.confirm(ConfirmationRequest("confirmation-2", "write_b", "Write B")),
        )

        assert decisions == [ConfirmationDecision.APPROVED, ConfirmationDecision.APPROVED]
        assert [message["data"]["request_id"] for message in websocket.sent] == [
            "confirmation-1",
            "confirmation-2",
        ]
        assert websocket.maximum_receivers == 1

    asyncio.run(scenario())


# 测试在确认超时后是否不会写入文件
def test_chat_websocket_waits_for_explicit_tool_confirmation(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    runtime = ConfirmingChatRuntime()

    with TestClient(create_app(settings, chat_agent_runtime_factory=lambda _: runtime)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        with client.websocket_connect("/api/v1/ws/chat") as websocket:
            websocket.send_json(
                {
                    "type": "chat.send",
                    "session_id": conversation["session_id"],
                    "message": "Write an approval demo",
                }
            )
            confirmation = websocket.receive_json()
            assert confirmation == {
                "type": "tool.confirmation.request",
                "data": {
                    "request_id": "confirmation-1",
                    "tool_name": "write_text_file",
                    "summary": "写入 approval-demo.txt（15 字节）",
                },
            }
            websocket.send_json(
                {
                    "type": "tool.confirmation.decision",
                    "request_id": "confirmation-1",
                    "decision": "approved",
                }
            )
            response = websocket.receive_json()

    assert response["type"] == "chat.response"
    assert response["data"]["assistant_message"] == "Local test reply"
    assert "session_id" in response["data"]
    assert "conversation_id" not in response["data"]
    assert runtime.contexts


def test_chat_websocket_rejects_obsolete_conversation_id_field(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(create_app(settings)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        with client.websocket_connect("/api/v1/ws/chat") as websocket:
            websocket.send_json(
                {
                    "type": "chat.send",
                    "conversation_id": conversation["session_id"],
                    "message": "hello",
                }
            )
            response = websocket.receive_json()

    assert response == {"type": "chat.error", "data": {"code": "invalid_message"}}


def test_chat_websocket_reports_unknown_remote_tool_outcome(tmp_path) -> None:
    class RemoteTimeoutRuntime(FakeChatRuntime):
        async def respond(
            self,
            message,
            request_context,
        ) -> ChatAgentResponse:
            del message, request_context
            raise RemoteToolOutcomeUnknownError(
                "Remote MCP call timed out; the remote operation outcome is unknown."
            )

    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    runtime = RemoteTimeoutRuntime()

    with TestClient(create_app(settings, chat_agent_runtime_factory=lambda _: runtime)) as client:
        http_conversation = client.post("/api/v1/conversations", json={}).json()
        http_response = client.post(
            f"/api/v1/conversations/{http_conversation['session_id']}/messages",
            json={"message": "Run a remote action"},
        )
        assert http_response.status_code == 504
        assert "outcome is unknown" in http_response.json()["detail"]

        websocket_conversation = client.post("/api/v1/conversations", json={}).json()
        with client.websocket_connect("/api/v1/ws/chat") as websocket:
            websocket.send_json(
                {
                    "type": "chat.send",
                    "session_id": websocket_conversation["session_id"],
                    "message": "Run a remote action",
                }
            )
            response = websocket.receive_json()

    assert response == {
        "type": "chat.error",
        "data": {"code": "remote_tool_outcome_unknown"},
    }


#
class MCPWriteRuntime(FakeChatRuntime):
    """Exercise the real MCP provider from a deterministic model-runtime substitute."""

    def __init__(self, provider) -> None:
        super().__init__()
        self.provider = provider

    async def respond(self, message, request_context) -> ChatAgentResponse:
        assert request_context.budget is not None
        assert self.provider.workspace.active_sandbox_mode is request_context.sandbox_mode
        if not self.provider._started:
            await self.provider.start()
        result = await self.provider.call_tool(
            "write_text_file",
            {"path": "approval-demo.txt", "content": "private content"},
            budget=request_context.budget,
            confirmation_provider=request_context.confirmation_provider,
        )
        context = request_context.snapshot
        self.contexts.append(context)
        self.messages_by_id.setdefault(context.session_id, []).extend(
            (
                ConversationMessage(role=MessageRole.USER, content=message),
                ConversationMessage(role=MessageRole.ASSISTANT, content=result),
            )
        )
        return ChatAgentResponse(result)

    async def aclose(self) -> None:
        self.closed = True


def _bind_mcp_runtime_to_host_provider(client, runtime: MCPWriteRuntime, workspace) -> None:
    assert not client.app.state.workspace_files.read_only
    runtime.provider = client.app.state.mcp_provider
    assert runtime.provider.workspace is client.app.state.workspace_files
    client.app.state.chat_service.tool_provider = client.app.state.tool_facade


def _create_app_with_mcp_runtime(settings, runtime: MCPWriteRuntime, workspace):
    class WorkspaceStore(SQLiteWorkspaceConfigurationStore):
        async def initialize(self) -> None:
            await super().initialize()
            await self.save(str(workspace))

    return create_app(
        settings,
        chat_agent_runtime_factory=lambda _: runtime,
        workspace_configuration_store_factory=WorkspaceStore,
    )


def test_chat_sandbox_mode_is_isolated_between_conversations(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = MCPWriteRuntime(None)

    with TestClient(_create_app_with_mcp_runtime(settings, runtime, workspace)) as client:
        _bind_mcp_runtime_to_host_provider(client, runtime, workspace)
        read_only = client.post("/api/v1/conversations", json={}).json()
        writable = client.post("/api/v1/conversations", json={}).json()
        upgraded = client.post(
            f"/api/v1/conversations/{writable['session_id']}/sandbox/upgrade",
            json={"target_mode": SandboxMode.WORKSPACE_WRITE.value},
        )
        assert upgraded.status_code == 200

        with client.websocket_connect("/api/v1/ws/chat") as websocket:
            websocket.send_json(
                {
                    "type": "chat.send",
                    "session_id": read_only["session_id"],
                    "message": "Write the approval demo",
                }
            )
            assert websocket.receive_json() == {
                "type": "chat.error",
                "data": {"code": "tool_denied"},
            }
            assert not (workspace / "approval-demo.txt").exists()

            websocket.send_json(
                {
                    "type": "chat.send",
                    "session_id": writable["session_id"],
                    "message": "Write the approval demo",
                }
            )
            confirmation = websocket.receive_json()
            assert confirmation["type"] == "tool.confirmation.request"
            websocket.send_json(
                {
                    "type": "tool.confirmation.decision",
                    "request_id": confirmation["data"]["request_id"],
                    "decision": "approved",
                }
            )
            result = websocket.receive_json()
            assert result["type"] == "chat.response", result

        assert (workspace / "approval-demo.txt").read_text(encoding="utf-8") == "private content"
        assert (
            client.get(f"/api/v1/conversations/{read_only['session_id']}").json()["sandbox_mode"]
            == SandboxMode.READ_ONLY.value
        )


def test_chat_websocket_disconnect_clears_writable_sandbox_mode(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = MCPWriteRuntime(None)

    with TestClient(_create_app_with_mcp_runtime(settings, runtime, workspace)) as client:
        _bind_mcp_runtime_to_host_provider(client, runtime, workspace)
        writable = client.post("/api/v1/conversations", json={}).json()
        read_only = client.post("/api/v1/conversations", json={}).json()
        upgraded = client.post(
            f"/api/v1/conversations/{writable['session_id']}/sandbox/upgrade",
            json={"target_mode": SandboxMode.WORKSPACE_WRITE.value},
        )
        assert upgraded.status_code == 200

        with client.websocket_connect("/api/v1/ws/chat") as websocket:
            websocket.send_json(
                {
                    "type": "chat.send",
                    "session_id": writable["session_id"],
                    "message": "Write the approval demo",
                }
            )
            confirmation = websocket.receive_json()
            assert confirmation["type"] == "tool.confirmation.request"
            websocket.close()

        with client.websocket_connect("/api/v1/ws/chat") as websocket:
            websocket.send_json(
                {
                    "type": "chat.send",
                    "session_id": read_only["session_id"],
                    "message": "Write the approval demo",
                }
            )
            response = websocket.receive_json()

        assert response == {"type": "chat.error", "data": {"code": "tool_denied"}}
        assert not (workspace / "approval-demo.txt").exists()


# 测试在确认超时后是否不会写入文件
def test_chat_websocket_mcp_write_requires_browser_approval(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    runtime = MCPWriteRuntime(None)

    with TestClient(_create_app_with_mcp_runtime(settings, runtime, tmp_path)) as client:
        _bind_mcp_runtime_to_host_provider(client, runtime, tmp_path)
        conversation = client.post("/api/v1/conversations", json={}).json()
        upgraded = client.post(
            f"/api/v1/conversations/{conversation['session_id']}/sandbox/upgrade",
            json={"target_mode": SandboxMode.WORKSPACE_WRITE.value},
        )
        assert upgraded.status_code == 200
        with client.websocket_connect("/api/v1/ws/chat") as websocket:
            websocket.send_json(
                {
                    "type": "chat.send",
                    "session_id": conversation["session_id"],
                    "message": "Write the approval demo",
                }
            )
            confirmation = websocket.receive_json()
            assert confirmation["type"] == "tool.confirmation.request"
            assert confirmation["data"]["tool_name"] == "write_text_file"
            assert "private content" not in confirmation["data"]["summary"]
            websocket.send_json(
                {
                    "type": "tool.confirmation.decision",
                    "request_id": confirmation["data"]["request_id"],
                    "decision": "approved",
                }
            )
            response = websocket.receive_json()

    assert response["type"] == "chat.response"
    assert (tmp_path / "approval-demo.txt").read_text(encoding="utf-8") == "private content"


# 测试在确认被拒绝或取消后是否不会写入文件
def test_chat_websocket_mcp_write_rejection_and_cancellation_do_not_write(tmp_path) -> None:
    for decision in ("rejected", "cancelled"):
        settings = Settings(
            data_dir=tmp_path / decision / "data",
            memory_dir=tmp_path / decision / "memory",
        )
        workspace = tmp_path / decision / "workspace"
        workspace.mkdir(parents=True)
        runtime = MCPWriteRuntime(None)

        with TestClient(_create_app_with_mcp_runtime(settings, runtime, workspace)) as client:
            _bind_mcp_runtime_to_host_provider(client, runtime, workspace)
            conversation = client.post("/api/v1/conversations", json={}).json()
            upgraded = client.post(
                f"/api/v1/conversations/{conversation['session_id']}/sandbox/upgrade",
                json={"target_mode": SandboxMode.WORKSPACE_WRITE.value},
            )
            assert upgraded.status_code == 200
            with client.websocket_connect("/api/v1/ws/chat") as websocket:
                websocket.send_json(
                    {
                        "type": "chat.send",
                        "session_id": conversation["session_id"],
                        "message": "Write the approval demo",
                    }
                )
                confirmation = websocket.receive_json()
                websocket.send_json(
                    {
                        "type": "tool.confirmation.decision",
                        "request_id": confirmation["data"]["request_id"],
                        "decision": decision,
                    }
                )
                response = websocket.receive_json()

        assert response == {
            "type": "chat.error",
            "data": {"code": f"tool_confirmation_{decision}"},
        }
        assert not (workspace / "approval-demo.txt").exists()


# 测试在确认超时后是否不会写入文件
def test_chat_websocket_mcp_write_confirmation_timeout_does_not_write(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = MCPWriteRuntime(None)

    with (
        patch("hostpilot.api.app.CHAT_CONFIRMATION_TIMEOUT_SECONDS", 0.01),
        TestClient(_create_app_with_mcp_runtime(settings, runtime, workspace)) as client,
    ):
        _bind_mcp_runtime_to_host_provider(client, runtime, workspace)
        conversation = client.post("/api/v1/conversations", json={}).json()
        upgraded = client.post(
            f"/api/v1/conversations/{conversation['session_id']}/sandbox/upgrade",
            json={"target_mode": SandboxMode.WORKSPACE_WRITE.value},
        )
        assert upgraded.status_code == 200
        with client.websocket_connect("/api/v1/ws/chat") as websocket:
            websocket.send_json(
                {
                    "type": "chat.send",
                    "session_id": conversation["session_id"],
                    "message": "Write the approval demo",
                }
            )
            confirmation = websocket.receive_json()
            assert confirmation["type"] == "tool.confirmation.request"
            response = websocket.receive_json()

    assert response == {
        "type": "chat.error",
        "data": {"code": "tool_confirmation_timed_out"},
    }
    assert not (workspace / "approval-demo.txt").exists()


def test_mcp_server_description_round_trips_and_enforces_maximum_length(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    payload = {
        "name": "research",
        "url": "https://mcp.example/mcp",
        "description": "Searches the internal research collection.",
    }

    with TestClient(create_app(settings)) as client:
        saved = client.put("/api/v1/mcp/servers", json=payload)
        assert saved.status_code == 200
        assert saved.json()["servers"][0]["description"] == payload["description"]

        restored = client.get("/api/v1/mcp/servers")
        assert restored.status_code == 200
        assert restored.json()["servers"][0]["description"] == payload["description"]

        oversized = client.put(
            "/api/v1/mcp/servers",
            json={**payload, "description": "x" * 501},
        )
        assert oversized.status_code == 422

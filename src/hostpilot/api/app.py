"""Local FastAPI application for managing conversation sessions."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import (
    APIRouter,
    FastAPI,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

from hostpilot.api.schemas import (
    ChatRequest,
    ChatResponse,
    ConversationHistoryResponse,
)
from hostpilot.budget import BudgetExceededError
from hostpilot.config import Settings, load_settings
from hostpilot.context import ContextBuilder, ConversationNotFoundError
from hostpilot.contracts import (
    BudgetLimits,
    ConversationSummary,
    MemoryScope,
    MemoryWriteProposal,
    ToolType,
)
from hostpilot.memory import MemoryWriteDenied, MemoryWriteService
from hostpilot.memory.review import (
    DuplicatePendingProposal,
    MemoryProposalReview,
    PendingProposalNotFound,
)
from hostpilot.observability import StructuredAuditSink
from hostpilot.orchestration import AgentRuntime, ChatService, create_configured_runtime
from hostpilot.orchestration.chat import RequestTooLargeError, UnsupportedRouteError
from hostpilot.output import OutputProcessor, OutputValidationError
from hostpilot.persistence import (
    MarkdownMemoryStore,
    SQLiteConversationStore,
    open_sqlite_checkpointer,
)
from hostpilot.policy.confirmation import ConfirmationDecision, ConfirmationRequest
from hostpilot.routing import CommandRouter, Router
from hostpilot.tools.executor import (
    AsyncConfirmationProvider,
    ToolConfirmationDeniedError,
    ToolDeniedError,
    ToolExecutionError,
)

router = APIRouter(prefix="/api/v1")
CHAT_CONFIRMATION_TIMEOUT_SECONDS = 60


class StatusPing(BaseModel):
    """Bounded client heartbeat message for the dashboard WebSocket."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(pattern="^ping$")
    request_id: str = Field(min_length=1, max_length=128)


class ChatSocketRequest(BaseModel):
    """One bounded chat operation accepted over the chat WebSocket."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(pattern="^chat.send$")
    conversation_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=100_000)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message cannot be blank.")
        return value


class ToolConfirmationDecisionRequest(BaseModel):
    """One explicit response to an outstanding high-impact tool proposal."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(pattern="^tool.confirmation.decision$")
    request_id: str = Field(min_length=1, max_length=64)
    decision: ConfirmationDecision


class WebSocketConfirmationProvider(AsyncConfirmationProvider):
    """Pause a chat turn and ask its connected browser to approve a tool call."""

    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket

    async def confirm(self, request: ConfirmationRequest) -> ConfirmationDecision:
        """Send a safe summary and wait for its matching approval decision."""
        await self.websocket.send_json(
            {
                "type": "tool.confirmation.request",
                "data": {
                    "request_id": request.request_id,
                    "tool_name": request.tool_name,
                    "summary": request.summary,
                },
            }
        )
        try:
            async with asyncio.timeout(CHAT_CONFIRMATION_TIMEOUT_SECONDS):
                while True:
                    raw_message = await self.websocket.receive_text()
                    if len(raw_message.encode("utf-8")) > 1024:
                        continue
                    try:
                        response = ToolConfirmationDecisionRequest.model_validate_json(raw_message)
                    except (ValidationError, ValueError, json.JSONDecodeError):
                        continue
                    if response.request_id == request.request_id:
                        return response.decision
        except TimeoutError:
            return ConfirmationDecision.TIMED_OUT
        except WebSocketDisconnect:
            return ConfirmationDecision.CANCELLED


class CreateConversationRequest(BaseModel):
    """Validated fields accepted when opening a new conversation."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)


class ModelConfigurationRequest(BaseModel):
    """OpenAI-compatible model credentials submitted from the local settings page."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    model_name: str = Field(min_length=1, max_length=200)
    api_key: SecretStr = Field(min_length=1, max_length=8192)
    base_url: str | None = Field(default=None, max_length=2048)

    @field_validator("api_key")
    @classmethod
    def api_key_must_not_be_blank(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value().strip()
        if not secret:
            raise ValueError("API key cannot be blank.")
        return SecretStr(secret)

    @field_validator("base_url")
    @classmethod
    def base_url_must_be_http(cls, value: str | None) -> str | None:
        if value is not None and value and not value.startswith(("http://", "https://")):
            raise ValueError("Base URL must start with http:// or https://.")
        return value or None


class ModelConfigurationStatus(BaseModel):
    """Safe model runtime status that never includes its API key."""

    configured: bool
    model_name: str | None = None
    base_url: str | None = None


class ToolCatalogItem(BaseModel):
    """Safe public information about one locally registered tool."""

    name: str
    description: str
    tool_type: ToolType
    configured: bool
    high_impact: bool
    confirmation_required: bool
    status_message: str


class ToolCatalogResponse(BaseModel):
    """Tool availability without exposing schemas, handlers, or credentials."""

    runtime_configured: bool
    tools: tuple[ToolCatalogItem, ...]


@router.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Return a lightweight liveness response."""
    return {"status": "ok"}


@router.get("/model/configuration", response_model=ModelConfigurationStatus, tags=["model"])
async def get_model_configuration(request: Request) -> ModelConfigurationStatus:
    """Report whether a model runtime is available without revealing secrets."""
    get_status = cast(
        Callable[[], ModelConfigurationStatus],
        request.app.state.get_model_configuration_status,
    )
    return get_status()


@router.put("/model/configuration", response_model=ModelConfigurationStatus, tags=["model"])
async def configure_model(
    body: ModelConfigurationRequest,
    request: Request,
) -> ModelConfigurationStatus:
    """Create or replace the in-memory OpenAI-compatible model runtime."""
    configure = cast(
        Callable[[ModelConfigurationRequest], Awaitable[ModelConfigurationStatus]],
        request.app.state.configure_model,
    )
    try:
        return await configure(body)
    except (ValueError, RuntimeError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not configure this model provider.",
        ) from error


@router.get("/tools", response_model=ToolCatalogResponse, tags=["tools"])
async def list_tools(request: Request) -> ToolCatalogResponse:
    """List local tool descriptions and safe configuration/approval status."""
    app_settings: Settings = request.app.state.settings
    provider = request.app.state.mcp_provider
    runtime_configured = request.app.state.chat_service is not None
    rag_configured = all(
        (app_settings.rag_base_url, app_settings.rag_user_id, app_settings.rag_key)
    )
    tools = []
    for descriptor in provider.registry.descriptors():
        configured = descriptor.name != "retrieve_knowledge" or rag_configured
        if not configured:
            status_message = "需要在后端配置 RAG 服务后才能检索。"
        elif not runtime_configured:
            status_message = "请先配置模型运行时，Agent 才能调用此工具。"
        else:
            status_message = "已配置，可由 Agent 按需调用。"
        tools.append(
            ToolCatalogItem(
                name=descriptor.name,
                description=descriptor.description,
                tool_type=descriptor.tool_type,
                configured=configured,
                high_impact=descriptor.high_impact,
                confirmation_required=descriptor.high_impact,
                status_message=status_message,
            )
        )
    return ToolCatalogResponse(runtime_configured=runtime_configured, tools=tuple(tools))


@router.websocket("/ws/status")
async def status_websocket(websocket: WebSocket) -> None:
    """Push initial backend health and answer bounded client heartbeats."""
    app_settings: Settings = websocket.scope["app"].state.settings
    if not _websocket_origin_allowed(websocket, app_settings.web_origins):
        await websocket.close(code=1008, reason="WebSocket origin is not allowed.")
        return

    await websocket.accept()
    await websocket.send_json(
        {"type": "status.snapshot", "data": {"api": "ok", "protocol_version": 1}}
    )
    try:
        while True:
            raw_message = await websocket.receive_text()
            if len(raw_message.encode("utf-8")) > 1024:
                await websocket.send_json({"type": "error", "data": {"code": "message_too_large"}})
                continue
            try:
                ping = StatusPing.model_validate_json(raw_message)
            except (ValidationError, ValueError, json.JSONDecodeError):
                await websocket.send_json({"type": "error", "data": {"code": "invalid_message"}})
                continue
            await websocket.send_json({"type": "pong", "request_id": ping.request_id})
    except WebSocketDisconnect:
        return


@router.websocket("/ws/chat")
async def chat_websocket(websocket: WebSocket) -> None:
    """Receive chat messages and return completed Agent turns over WebSocket."""
    app_settings: Settings = websocket.scope["app"].state.settings
    if not _websocket_origin_allowed(websocket, app_settings.web_origins):
        await websocket.close(code=1008, reason="WebSocket origin is not allowed.")
        return

    await websocket.accept()
    try:
        while True:
            raw_message = await websocket.receive_text()
            if len(raw_message.encode("utf-8")) > app_settings.max_request_chars * 4 + 1024:
                await websocket.send_json(
                    {"type": "chat.error", "data": {"code": "message_too_large"}}
                )
                continue
            try:
                body = ChatSocketRequest.model_validate_json(raw_message)
            except (ValidationError, ValueError, json.JSONDecodeError):
                await websocket.send_json(
                    {"type": "chat.error", "data": {"code": "invalid_message"}}
                )
                continue

            chat_service: ChatService | None = websocket.scope["app"].state.chat_service
            if chat_service is None:
                await websocket.send_json(
                    {"type": "chat.error", "data": {"code": "runtime_unavailable"}}
                )
                continue
            try:
                async with websocket.scope["app"].state.model_runtime_lock:
                    result = await chat_service.send(
                        body.conversation_id,
                        body.message,
                        confirmation_provider=WebSocketConfirmationProvider(websocket),
                    )
            except RequestTooLargeError:
                code = "message_too_large"
            except (ConversationNotFoundError, ValueError):
                code = "conversation_not_found"
            except BudgetExceededError:
                code = "budget_exceeded"
            except ToolConfirmationDeniedError as error:
                code = f"tool_confirmation_{error.decision.value}"
            except ToolDeniedError:
                code = "tool_denied"
            except ToolExecutionError:
                code = "tool_failed"
            except TimeoutError:
                code = "request_timeout"
            except (OutputValidationError, UnsupportedRouteError):
                code = "request_failed"
            else:
                await websocket.send_json(
                    {
                        "type": "chat.response",
                        "data": ChatResponse(
                            conversation_id=result.conversation_id,
                            request_id=result.request_id,
                            assistant_message=result.assistant_message,
                            route=result.route,
                            clarification_prompt=result.clarification_prompt,
                            token_usage=result.token_usage,
                        ).model_dump(mode="json"),
                    }
                )
                continue
            await websocket.send_json({"type": "chat.error", "data": {"code": code}})
    except WebSocketDisconnect:
        return


@router.post(
    "/conversations",
    response_model=ConversationSummary,
    status_code=status.HTTP_201_CREATED,
    tags=["conversations"],
)
async def create_conversation(
    body: CreateConversationRequest,
    request: Request,
) -> ConversationSummary:
    """Create an isolated conversation ID for the local user."""
    store: SQLiteConversationStore = request.app.state.conversations
    return await store.create(body.title)


@router.get(
    "/conversations",
    response_model=tuple[ConversationSummary, ...],
    tags=["conversations"],
)
async def list_conversations(request: Request) -> tuple[ConversationSummary, ...]:
    """List local conversation metadata without loading graph state."""
    store: SQLiteConversationStore = request.app.state.conversations
    return await store.list()


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationSummary,
    tags=["conversations"],
)
async def get_conversation(
    conversation_id: str,
    request: Request,
) -> ConversationSummary:
    """Return metadata for one conversation."""
    store: SQLiteConversationStore = request.app.state.conversations
    try:
        conversation = await store.get(conversation_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Conversation not found.") from error
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return conversation


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["conversations"],
)
async def delete_conversation(conversation_id: str, request: Request) -> Response:
    """Delete a conversation, its graph checkpoints, and its session summary."""
    store: SQLiteConversationStore = request.app.state.conversations
    memory: MarkdownMemoryStore = request.app.state.memory
    checkpointer = request.app.state.checkpointer
    try:
        conversation = await store.get(conversation_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Conversation not found.") from error
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    await checkpointer.adelete_thread(conversation.conversation_id)
    await store.delete(conversation.conversation_id)
    memory.delete_session_summary(conversation.conversation_id)
    await request.app.state.memory_review.remove_for_conversation(conversation.conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/memory-proposals",
    response_model=tuple[MemoryWriteProposal, ...],
    tags=["memory"],
)
async def list_memory_proposals(request: Request) -> tuple[MemoryWriteProposal, ...]:
    """Return memory changes awaiting explicit user approval."""
    review: MemoryProposalReview = request.app.state.memory_review
    return await review.list_pending()


@router.post(
    "/memory-proposals",
    response_model=MemoryWriteProposal,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["memory"],
)
async def submit_memory_proposal(
    proposal: MemoryWriteProposal,
    request: Request,
) -> MemoryWriteProposal:
    """Queue a memory proposal without changing any Markdown file."""
    review: MemoryProposalReview = request.app.state.memory_review
    if (
        proposal.scope is MemoryScope.SESSION_SUMMARY
        and proposal.conversation_id is not None
        and await request.app.state.conversations.get(proposal.conversation_id) is None
    ):
        raise HTTPException(status_code=404, detail="Conversation not found.")
    try:
        return await review.submit(proposal)
    except DuplicatePendingProposal as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except MemoryWriteDenied as error:
        raise HTTPException(status_code=413, detail=str(error)) from error


@router.post(
    "/memory-proposals/{proposal_id}/approve",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["memory"],
)
async def approve_memory_proposal(proposal_id: str, request: Request) -> Response:
    """Apply a queued memory change after this explicit approval request."""
    review: MemoryProposalReview = request.app.state.memory_review
    try:
        await review.approve(proposal_id)
    except PendingProposalNotFound as error:
        raise HTTPException(status_code=404, detail="Memory proposal not found.") from error
    except MemoryWriteDenied as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/memory-proposals/{proposal_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["memory"],
)
async def reject_memory_proposal(proposal_id: str, request: Request) -> Response:
    """Discard a pending memory proposal without writing it."""
    review: MemoryProposalReview = request.app.state.memory_review
    try:
        await review.reject(proposal_id)
    except PendingProposalNotFound as error:
        raise HTTPException(status_code=404, detail="Memory proposal not found.") from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=ChatResponse,
    tags=["chat"],
)
async def send_message(
    conversation_id: str,
    body: ChatRequest,
    request: Request,
) -> ChatResponse:
    """Submit one message to the configured Agent runtime."""
    chat_service: ChatService | None = request.app.state.chat_service
    if chat_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent runtime is not configured; select a model provider first.",
        )
    try:
        async with request.app.state.model_runtime_lock:
            result = await chat_service.send(conversation_id, body.message)
    except RequestTooLargeError as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except OutputValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Agent response failed output validation.",
        ) from error
    except (ConversationNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Conversation not found.") from error
    except BudgetExceededError as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Request budget exhausted for {error.resource}.",
        ) from error
    except ToolConfirmationDeniedError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Tool operation was not approved ({error.decision.value}).",
        ) from error
    except ToolDeniedError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Agent tool call was denied by policy or request budget.",
        ) from error
    except ToolExecutionError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Agent tool execution failed verification or execution.",
        ) from error
    except TimeoutError as error:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Request exceeded its configured time budget.",
        ) from error
    except UnsupportedRouteError as error:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"Route '{error}' is not available yet.",
        ) from error
    return ChatResponse(
        conversation_id=result.conversation_id,
        request_id=result.request_id,
        assistant_message=result.assistant_message,
        route=result.route,
        clarification_prompt=result.clarification_prompt,
        token_usage=result.token_usage,
    )


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=ConversationHistoryResponse,
    tags=["chat"],
)
async def get_messages(
    conversation_id: str,
    request: Request,
) -> ConversationHistoryResponse:
    """Return persisted messages for one conversation."""
    chat_service: ChatService | None = request.app.state.chat_service
    if chat_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent runtime is not configured; select a model provider first.",
        )
    try:
        async with request.app.state.model_runtime_lock:
            conversation, messages = await chat_service.history(conversation_id)
    except (ConversationNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Conversation not found.") from error
    return ConversationHistoryResponse(conversation=conversation, messages=messages)


def create_app(
    settings: Settings | None = None,
    *,
    runtime_factory: Callable[[Any], AgentRuntime] | None = None,
    agent_router: Router | None = None,
) -> FastAPI:
    """Build the local API and open persistent stores during application lifespan."""
    app_settings = settings or load_settings()
    database_path = app_settings.data_dir / "hostpilot.sqlite3"
    conversation_store = SQLiteConversationStore(database_path)
    memory_store = MarkdownMemoryStore(app_settings.memory_dir)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        await conversation_store.initialize()
        async with open_sqlite_checkpointer(database_path) as checkpointer:
            application.state.conversations = conversation_store
            application.state.memory = memory_store
            application.state.memory_review = MemoryProposalReview(
                MemoryWriteService(memory_store, conversation_store),
                audit=StructuredAuditSink(),
            )
            application.state.checkpointer = checkpointer
            from hostpilot.tools.mcp import build_retrieval_mcp_provider

            mcp_provider: Any = build_retrieval_mcp_provider(app_settings)
            application.state.mcp_provider = mcp_provider
            runtime: AgentRuntime | None = None
            chat_service: ChatService | None = None
            runtime_lock = asyncio.Lock()
            application.state.model_runtime_lock = runtime_lock

            def make_chat_service(
                agent_runtime: AgentRuntime,
                tool_provider: Any | None,
            ) -> ChatService:
                return ChatService(
                    conversation_store,
                    ContextBuilder(
                        conversation_store,
                        memory_store,
                        agent_runtime,
                        max_messages=app_settings.max_context_messages,
                        max_memory_chars=app_settings.max_memory_context_chars,
                    ),
                    agent_runtime,
                    budget_limits=BudgetLimits(
                        total_tokens=app_settings.request_token_budget,
                        tool_calls=app_settings.request_tool_call_budget,
                        retrieval_calls=app_settings.request_retrieval_call_budget,
                        timeout_seconds=app_settings.request_timeout_seconds,
                    ),
                    max_request_chars=app_settings.max_request_chars,
                    router=agent_router or CommandRouter(),
                    tool_provider=tool_provider,
                    output_processor=OutputProcessor(max_chars=app_settings.max_response_chars),
                )

            def model_configuration_status() -> ModelConfigurationStatus:
                return ModelConfigurationStatus(
                    configured=runtime is not None,
                    model_name=app_settings.model_name if runtime is not None else None,
                    base_url=app_settings.model_base_url if runtime is not None else None,
                )

            async def configure_model_runtime(
                configuration: ModelConfigurationRequest,
            ) -> ModelConfigurationStatus:
                nonlocal app_settings, chat_service, mcp_provider, runtime
                if runtime_factory is not None:
                    raise RuntimeError(
                        "Model runtime is managed by the configured runtime factory."
                    )

                candidate_settings = app_settings.model_copy(
                    update={
                        "model_name": configuration.model_name,
                        "model_api_key": configuration.api_key,
                        "model_base_url": configuration.base_url,
                    }
                )
                candidate_provider = build_retrieval_mcp_provider(candidate_settings)
                try:
                    candidate_tools = await candidate_provider.start()
                    candidate_runtime = create_configured_runtime(
                        candidate_settings,
                        checkpointer=checkpointer,
                        tools=candidate_tools,
                    )
                except Exception:
                    await candidate_provider.aclose()
                    raise

                async with runtime_lock:
                    previous_runtime = runtime
                    previous_provider = mcp_provider
                    runtime = candidate_runtime
                    mcp_provider = candidate_provider
                    application.state.mcp_provider = candidate_provider
                    app_settings = candidate_settings
                    application.state.settings = candidate_settings
                    if chat_service is None:
                        chat_service = make_chat_service(candidate_runtime, candidate_provider)
                    else:
                        chat_service.runtime = candidate_runtime
                        chat_service.context_builder.runtime = candidate_runtime
                        chat_service.tool_provider = candidate_provider
                    application.state.chat_service = chat_service
                    if previous_runtime is not None:
                        close_runtime = getattr(previous_runtime, "aclose", None)
                        if close_runtime is not None:
                            await close_runtime()
                    if previous_provider is not None:
                        await previous_provider.aclose()
                return model_configuration_status()

            application.state.get_model_configuration_status = model_configuration_status
            application.state.configure_model = configure_model_runtime
            try:
                if runtime_factory is not None:
                    runtime = runtime_factory(checkpointer)
                elif app_settings.model_name:
                    mcp_tools = await mcp_provider.start()
                    runtime = create_configured_runtime(
                        app_settings, checkpointer=checkpointer, tools=mcp_tools
                    )

                if runtime is not None:
                    chat_service = make_chat_service(
                        runtime,
                        mcp_provider if mcp_provider._started else None,
                    )
                application.state.chat_service = chat_service
                yield
            finally:
                if runtime is not None:
                    close_runtime = getattr(runtime, "aclose", None)
                    if close_runtime is not None:
                        await close_runtime()
                if mcp_provider is not None:
                    await mcp_provider.aclose()

    app = FastAPI(title="HostPilot API", version="1", lifespan=lifespan)
    app.state.settings = app_settings
    if app_settings.web_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(app_settings.web_origins),
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Content-Type"],
            allow_credentials=False,
        )

    @app.middleware("http")
    async def reject_untrusted_write_origins(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin is not None:
                host = request.headers.get("host", "")
                same_origin = f"{request.url.scheme}://{host}"
                if origin != same_origin and origin not in app_settings.web_origins:
                    return Response(
                        content="Request Origin is not allowed.",
                        status_code=status.HTTP_403_FORBIDDEN,
                    )
        return await call_next(request)

    app.include_router(router)
    return app


def _websocket_origin_allowed(websocket: WebSocket, allowed_origins: tuple[str, ...]) -> bool:
    origin = websocket.headers.get("origin")
    if origin is None:
        return True
    scheme = "https" if websocket.url.scheme == "wss" else "http"
    same_origin = f"{scheme}://{websocket.headers.get('host', '')}"
    return origin == same_origin or origin in allowed_origins


__all__ = ["CreateConversationRequest", "create_app", "router"]

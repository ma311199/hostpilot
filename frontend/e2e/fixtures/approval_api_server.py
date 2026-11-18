"""Run an isolated API with a deterministic runtime for browser approval tests."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from hostpilot.api.app import create_app
from hostpilot.budget import RequestBudget
from hostpilot.config import load_settings
from hostpilot.contracts import ContextSnapshot, ConversationMessage, MessageRole
from hostpilot.orchestration.runtime import AgentResponse
from hostpilot.tools.executor import AsyncConfirmationProvider
from hostpilot.tools.mcp import MCPToolProvider, build_retrieval_mcp_provider


class ApprovalRuntime:
    """Pause on a governed workspace write and persist only an approved reply."""

    def __init__(self, provider: MCPToolProvider) -> None:
        self.provider = provider
        self.messages_by_id: dict[str, list[ConversationMessage]] = {}
        self.request_sequence = 0
        self.provider_started = False

    async def read_messages(self, conversation_id: str) -> tuple[ConversationMessage, ...]:
        return tuple(self.messages_by_id.get(conversation_id, ()))

    async def respond(
        self,
        message: str,
        context: ContextSnapshot,
        *,
        budget: RequestBudget | None = None,
        confirmation_provider: AsyncConfirmationProvider | None = None,
    ) -> AgentResponse:
        if budget is None or confirmation_provider is None:
            raise RuntimeError("The request budget and browser approval provider are required.")
        self.request_sequence += 1
        if not self.provider_started:
            await self.provider.start()
            self.provider_started = True
        if message.startswith("Run the sandbox approval check"):
            output = (
                "sandbox-rejected-output"
                if "rejected" in message
                else "sandbox-approved-output"
            )
            result = await self.provider.call_tool(
                "run_python_code",
                {"code": f"print('{output}')"},
                budget=budget,
                confirmation_provider=confirmation_provider,
            )
            self.messages_by_id.setdefault(context.conversation_id, []).extend(
                (
                    ConversationMessage(role=MessageRole.USER, content=message),
                    ConversationMessage(role=MessageRole.ASSISTANT, content=result),
                )
            )
            return AgentResponse(result)
        path = f"approval-e2e-{self.request_sequence}.txt"
        result = await self.provider.call_tool(
            "write_text_file",
            {"path": path, "content": f"isolated approval content {self.request_sequence}"},
            budget=budget,
            confirmation_provider=confirmation_provider,
        )
        self.messages_by_id.setdefault(context.conversation_id, []).extend(
            (
                ConversationMessage(role=MessageRole.USER, content=message),
                ConversationMessage(role=MessageRole.ASSISTANT, content=result),
            )
        )
        return AgentResponse(result)

    async def aclose(self) -> None:
        await self.provider.aclose()

    async def record_turn(
        self,
        _conversation_id: str,
        _user_message: str,
        _assistant_message: str,
    ) -> None:
        return None


def main() -> None:
    """Start FastAPI with temporary storage and a deterministic chat runtime."""
    settings = load_settings()
    workspace = Path(os.environ["HOSTPILOT_E2E_WORKSPACE"])
    provider = build_retrieval_mcp_provider(settings, workspace_root=workspace)
    app = create_app(
        settings,
        runtime_factory=lambda _checkpointer: ApprovalRuntime(provider),
        workspace_picker=lambda _current: workspace,
    )
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()

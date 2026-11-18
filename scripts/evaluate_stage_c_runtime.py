"""Exercise the real model selector and Skill Runtime with isolated tool stubs."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from hostpilot.budget import RequestBudget
from hostpilot.config import Settings
from hostpilot.contracts import (
    BudgetContext,
    BudgetLimits,
    ContextSnapshot,
    ExecutionEnvironment,
    TimeoutProfile,
    ToolDescriptor,
    ToolType,
    TrustLevel,
)
from hostpilot.evaluation.fixtures import write_fixture_skills
from hostpilot.evaluation.model_config import model_configuration_from_environment
from hostpilot.models import create_model_runtime
from hostpilot.observability import AuditEvent
from hostpilot.orchestration.runtime import AgentResponse
from hostpilot.orchestration.skill_runtime import SkillRuntimeAgentAdapter, SkillSelection
from hostpilot.policy import PolicyEngine
from hostpilot.policy.confirmation import ConfirmationDecision, ConfirmationRequest
from hostpilot.routing.skill_selector import ModelProviderSkillSelector
from hostpilot.skills.llm_actions import register_llm_completion_action
from hostpilot.skills.model_plan import (
    LLMSkillAssessor,
    ModelPlanFallback,
    build_model_plan_executor,
)
from hostpilot.skills.model_provider_adapter import ProviderSingleTurnLLM
from hostpilot.skills.runtime import (
    BuiltinExecutorRegistry,
    SkillExecutionRequest,
    SkillExecutionResult,
    SkillRuntime,
    StepActionCall,
    StepActionRegistration,
    StepActionRegistry,
    StepActionResult,
    register_core_builtin_skills,
)
from hostpilot.skills.yaml_registry import SideEffectLevel, YamlSkillRegistry
from hostpilot.tools.executor import ToolDeniedError, ToolExecutor
from hostpilot.tools.filesystem import WorkspaceFiles
from hostpilot.tools.registry import ToolRegistration, ToolRegistry

DATASET_PATH = (
    Path(__file__).resolve().parents[1] / "docs/evaluations/stage-c-model-evaluation-v2.yaml"
)
DEFAULT_CASES = ("skill-meeting-001", "plan-001", "safety-004")


class RecordingSelector:
    def __init__(self, selector: ModelProviderSkillSelector) -> None:
        self.selector = selector
        self.selection: SkillSelection | None = None

    async def select(self, *args: Any, **kwargs: Any) -> SkillSelection | None:
        self.selection = await self.selector.select(*args, **kwargs)
        return self.selection


class StubChatRuntime:
    def __init__(self) -> None:
        self.turns: list[tuple[str, str, str]] = []

    async def read_messages(self, conversation_id: str) -> tuple[Any, ...]:
        del conversation_id
        return ()

    async def respond(self, *args: Any, **kwargs: Any) -> AgentResponse:
        del args, kwargs
        raise RuntimeError("Selector-only Runtime fallback was not expected in this harness.")

    async def record_turn(self, conversation_id: str, user_message: str, reply: str) -> None:
        self.turns.append((conversation_id, user_message, reply))


class RecordingSkillRuntime(SkillRuntime):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.last_result: SkillExecutionResult | None = None
        self.last_exception: dict[str, Any] | None = None

    async def execute(self, request: SkillExecutionRequest) -> SkillExecutionResult:
        try:
            result = await super().execute(request)
        except Exception as error:
            self.last_exception = {
                "error_code": type(error).__name__,
                "frames": [
                    {
                        "file": Path(frame.filename).name,
                        "function": frame.name,
                        "line": frame.lineno,
                    }
                    for frame in traceback.extract_tb(error.__traceback__)[-8:]
                ],
            }
            raise
        self.last_result = result
        return result


class RecordingPlanner:
    def __init__(self, planner: Any) -> None:
        self.planner = planner
        self.drafts: list[list[dict[str, Any]]] = []

    async def create_plan(self, request: Any, blackboard: Any) -> Any:
        plan = await self.planner.create_plan(request, blackboard)
        self._record(plan)
        return plan

    async def replan(self, request: Any, reason: str, blackboard: Any) -> Any:
        plan = await self.planner.replan(request, reason, blackboard)
        self._record(plan)
        return plan

    def _record(self, plan: Any) -> None:
        self.drafts.append(
            [
                {
                    "task_id": task.task_id,
                    "description": task.description,
                    "execution_type": task.execution_type.value,
                    "depends_on": list(task.depends_on),
                    "expected_output": task.expected_output,
                    "input": dict(task.input or {}),
                    "tool_id": (task.input or {}).get("tool_id"),
                    "skill_id": task.skill_id,
                }
                for task in plan.tasks
            ]
        )


class RecordingPlanTaskExecutor:
    def __init__(self, executor: Any) -> None:
        self.executor = executor
        self.results: list[dict[str, Any]] = []
        self.capability_selections: list[dict[str, Any]] = []
        select_capability = getattr(executor, "_select_capability", None)
        if callable(select_capability):

            async def record_capability_selection(call: Any, execution_type: Any) -> Any:
                try:
                    selection = await select_capability(call, execution_type)
                except Exception as error:
                    self.capability_selections.append(
                        {
                            "task_id": call.task.task_id,
                            "execution_type": execution_type.value,
                            "error_code": type(error).__name__,
                            "error_frames": [
                                {
                                    "file": Path(frame.filename).name,
                                    "function": frame.name,
                                    "line": frame.lineno,
                                }
                                for frame in traceback.extract_tb(error.__traceback__)[-5:]
                            ],
                        }
                    )
                    raise
                capability_id, arguments = selection
                self.capability_selections.append(
                    {
                        "task_id": call.task.task_id,
                        "execution_type": execution_type.value,
                        "selected_capability_id": capability_id,
                        "payload_keys": sorted(arguments) if isinstance(arguments, Mapping) else [],
                    }
                )
                return selection

            executor._select_capability = record_capability_selection

    async def execute_task(self, call: Any) -> Any:
        task = call.task
        try:
            result = await self.executor.execute_task(call)
        except Exception as error:
            self.results.append(
                {
                    "task_id": task.task_id,
                    "description": task.description,
                    "execution_type": task.execution_type.value,
                    "expected_output": task.expected_output,
                    "tool_id": (task.input or {}).get("tool_id"),
                    "input": dict(task.input or {}),
                    "error_code": type(error).__name__,
                    "error_message": str(error),
                    "error_traceback": traceback.format_exception(
                        type(error), error, error.__traceback__
                    ),
                }
            )
            raise
        self.results.append(
            {
                "task_id": task.task_id,
                "description": task.description,
                "execution_type": task.execution_type.value,
                "expected_output": task.expected_output,
                "tool_id": (task.input or {}).get("tool_id"),
                "input": dict(task.input or {}),
                "succeeded": result.succeeded,
                "error_code": result.error_code,
                "invoked_tool_ids": list(result.invoked_tool_ids),
            }
        )
        return result


class RecordingPlanVerifier:
    def __init__(self, verifier: Any) -> None:
        self.verifier = verifier
        self.decisions: list[dict[str, Any]] = []

    async def verify(self, request: Any, blackboard: Any) -> Any:
        decision = await self.verifier.verify(request, blackboard)
        self.decisions.append({"accepted": decision.accepted, "reason_code": decision.reason_code})
        return decision


class StubConfirmationProvider:
    """Approve only an in-memory stub operation; no real resource is changed."""

    def __init__(self) -> None:
        self.requests: list[ConfirmationRequest] = []

    async def confirm(self, request: ConfirmationRequest) -> ConfirmationDecision:
        self.requests.append(request)
        return ConfirmationDecision.APPROVED


class StubGovernedTools:
    """Run test tool calls through ToolExecutor while handlers remain in memory."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace = WorkspaceFiles(workspace_root)
        self.registry = ToolRegistry()
        self.audit_events: list[AuditEvent] = []
        self.calls: list[dict[str, Any]] = []
        self.handler_calls: list[str] = []
        self.read_values: dict[str, str] = {
            "README.md": (
                "# HostPilot\n\n"
                "HostPilot provides conversation-based task routing, YAML Skill execution, "
                "and a LangGraph-backed orchestrator. The current repository includes "
                "model configuration, direct answers, clarification, Skill selection, "
                "and governed workspace file tools.\n"
            ),
            "ROADMAP.md": (
                "# Roadmap\n\n"
                "## In progress\n"
                "- Stage C: validate real-model routing and Plan Runtime behavior with "
                "isolated tool stubs.\n"
                "- Add representative business Skills to the production registry.\n\n"
                "## Not yet complete\n"
                "- End-to-end verification of parallel Plans with governed tool calls.\n"
                "- Persistent model-provider abstraction shared across model features.\n"
            ),
            "docs/roadmap.md": (
                "# Roadmap\n\n"
                "## In progress\n"
                "- Stage C: validate real-model routing and Plan Runtime behavior with "
                "isolated tool stubs.\n"
                "- Add representative business Skills to the production registry.\n\n"
                "## Not yet complete\n"
                "- End-to-end verification of parallel Plans with governed tool calls.\n"
                "- Persistent model-provider abstraction shared across model features.\n"
            ),
            "requirements.md": (
                "# Requirements\n\n"
                "The system must route requests to direct-answer, clarification, Skills, "
                "or Plan execution. Tool calls must pass local validation and policy; "
                "high-impact writes require explicit confirmation.\n"
            ),
            "docs/README.md": (
                "# Documentation\n\n"
                "See the [root README](../README.md) and the [missing guide](guide.md).\n"
            ),
            "docs/guide.md": "# Guide\n\nSee [requirements](../requirements.md).\n",
            "src/hostpilot/orchestration/chat.py": (
                "# Chat orchestration fixture\n\n"
                "ChatService builds a request budget, resolves context, selects a route, "
                "and records the completed turn.\n"
            ),
            "src/hostpilot/routing/skill_selector.py": (
                "# Skill selector fixture\n\n"
                "The selector retrieves candidates with BM25/RRF, applies model classification, "
                "and routes business Skill failures to Plan.\n"
            ),
            "src/hostpilot/skills/model_plan.py": (
                "# Plan runtime fixture\n\n"
                "The Plan runtime validates tasks, retrieves bounded tool candidates, runs a "
                "multi-turn ReAct task agent, requests confirmation for writes, and verifies "
                "the final blackboard.\n"
            ),
        }
        self.search_values: dict[str, dict[str, Any]] = {
            "*": {
                "matches": [
                    "README.md",
                    "ROADMAP.md",
                    "requirements.md",
                    "docs/README.md",
                    "docs/guide.md",
                    "src/hostpilot/orchestration/chat.py",
                    "src/hostpilot/routing/skill_selector.py",
                    "src/hostpilot/skills/model_plan.py",
                ],
                "scanned_entries": 8,
                "truncated": False,
            },
            "*.md": {
                "matches": [
                    "README.md",
                    "ROADMAP.md",
                    "requirements.md",
                    "docs/README.md",
                    "docs/guide.md",
                ],
                "scanned_entries": 5,
                "truncated": False,
            },
            "requirements*": {
                "matches": ["requirements.md"],
                "scanned_entries": 1,
                "truncated": False,
            },
            "*.py": {
                "matches": [
                    "src/hostpilot/orchestration/chat.py",
                    "src/hostpilot/routing/skill_selector.py",
                    "src/hostpilot/skills/model_plan.py",
                ],
                "scanned_entries": 3,
                "truncated": False,
            },
        }
        self.proposed_writes: list[dict[str, str]] = []
        self.executor = ToolExecutor(self.registry, PolicyEngine(), audit=self.audit_events.append)
        self._register_tools()

    def _register_tools(self) -> None:
        self.registry.register(
            ToolRegistration(
                descriptor=ToolDescriptor(
                    name="read_text_file",
                    description="Read a workspace text file from the synthetic fixture.",
                    access_summary="读取隔离评测工作区中的 UTF-8 文本文件；path 为相对路径。",
                    input_schema={
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                        "additionalProperties": False,
                    },
                    tool_type=ToolType.ACTION,
                    trust_level=TrustLevel.HOST,
                    execution_env=ExecutionEnvironment.HOST,
                    timeout_profile=TimeoutProfile(timeout_seconds=5),
                ),
                validate_arguments=self._validate_read,
                handler=self._read,
                verify_result=self._verify_read,
            )
        )
        self.registry.register(
            ToolRegistration(
                descriptor=ToolDescriptor(
                    name="search_files",
                    description="Search workspace file names with a bounded glob.",
                    access_summary=(
                        "按文件名 glob 搜索隔离评测工作区中的文件；不读取内容，最多返回 100 项。"
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string"},
                            "path": {"type": "string"},
                            "max_results": {"type": "integer", "minimum": 1, "maximum": 100},
                            "max_depth": {"type": "integer", "minimum": 0, "maximum": 8},
                        },
                        "required": ["pattern"],
                        "additionalProperties": False,
                    },
                    tool_type=ToolType.ACTION,
                    trust_level=TrustLevel.HOST,
                    execution_env=ExecutionEnvironment.HOST,
                    timeout_profile=TimeoutProfile(timeout_seconds=5),
                ),
                validate_arguments=self._validate_search,
                handler=self._search,
                verify_result=self._verify_search,
            )
        )
        self.registry.register(
            ToolRegistration(
                descriptor=ToolDescriptor(
                    name="write_text_file",
                    description="Propose a workspace text write; the stub never writes a file.",
                    access_summary=(
                        "在隔离评测工作区内提出 UTF-8 文本文件写入提案；path 指定相对路径，"
                        "content 指定文本内容；只记录提案，不会落盘。"
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["path", "content"],
                        "additionalProperties": False,
                    },
                    tool_type=ToolType.ACTION,
                    trust_level=TrustLevel.HOST,
                    execution_env=ExecutionEnvironment.HOST,
                    timeout_profile=TimeoutProfile(timeout_seconds=5),
                    high_impact=True,
                ),
                validate_arguments=self._validate_write,
                handler=self._write_stub,
                verify_result=self._verify_write,
                confirmation_summary=lambda args: f"stub write to {args['path']}",
            )
        )

    def _validate_read(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if set(arguments) != {"path"} or not isinstance(arguments.get("path"), str):
            raise ValueError("read_text_file accepts one path.")
        path = str(arguments["path"]).strip()
        self.workspace.validate_read_path(path)
        return {"path": path}

    def _validate_write(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if set(arguments) != {"path", "content"}:
            raise ValueError("write_text_file accepts path and content.")
        path, content = arguments.get("path"), arguments.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            raise ValueError("path and content must be strings.")
        normalized = path.strip()
        self.workspace.validate_write_path(normalized)
        if len(content.encode("utf-8")) > self.workspace.max_file_bytes:
            raise ValueError("content exceeds the fixture size limit.")
        return {"path": normalized, "content": content}

    def _validate_search(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        allowed = {"pattern", "path", "max_results", "max_depth"}
        if set(arguments) - allowed:
            raise ValueError("search_files received unsupported arguments.")
        pattern = arguments.get("pattern")
        path = arguments.get("path", "")
        max_results = arguments.get("max_results", 50)
        max_depth = arguments.get("max_depth", 6)
        if not isinstance(pattern, str) or not pattern.strip() or "/" in pattern or "\\" in pattern:
            raise ValueError("pattern must be a bounded file-name glob.")
        if not isinstance(path, str) or len(path) > 1024:
            raise ValueError("path must not exceed 1024 characters.")
        if (
            not isinstance(max_results, int)
            or isinstance(max_results, bool)
            or not 1 <= max_results <= 100
        ):
            raise ValueError("max_results must be between 1 and 100.")
        if not isinstance(max_depth, int) or isinstance(max_depth, bool) or not 0 <= max_depth <= 8:
            raise ValueError("max_depth must be between 0 and 8.")
        self.workspace.validate_directory_path(path)
        return {
            "pattern": pattern.strip(),
            "path": path,
            "max_results": max_results,
            "max_depth": max_depth,
        }

    async def _read(self, arguments: Mapping[str, Any]) -> str:
        path = str(arguments["path"])
        self.handler_calls.append("read_text_file")
        return self.read_values.get(path, f"Synthetic fixture content for {path}.")

    async def _write_stub(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        path, content = str(arguments["path"]), str(arguments["content"])
        self.handler_calls.append("write_text_file")
        self.proposed_writes.append({"path": path, "content": content})
        return {"ok": True, "path": path, "stub_only": True}

    async def _search(self, arguments: Mapping[str, Any]) -> str:
        self.handler_calls.append("search_files")
        pattern = str(arguments["pattern"]).strip().casefold()
        result = self.search_values.get(pattern)
        if result is None and pattern in {"*.yaml", "*.yml", "*.y*ml"}:
            result = self.search_values.get("*.yaml")
        if result is None and pattern in {"*.sql", "*.ddl"}:
            result = self.search_values.get("*.sql")
        if result is None and pattern.endswith(".md"):
            result = self.search_values["*.md"]
        if result is None and pattern.endswith(".py"):
            result = self.search_values["*.py"]
        if result is None and pattern.startswith("requirements"):
            result = self.search_values["requirements*"]
        if result is None and pattern in {"*", "**"}:
            result = self.search_values["*"]
        if result is None:
            result = {"matches": [], "scanned_entries": 0, "truncated": False}
        return json.dumps(result, ensure_ascii=False)

    @staticmethod
    def _verify_read(result: Any, _: Mapping[str, Any]) -> None:
        if not isinstance(result, str):
            raise ValueError("The read stub must return text.")

    @staticmethod
    def _verify_write(result: Any, arguments: Mapping[str, Any]) -> None:
        if not isinstance(result, Mapping) or result.get("ok") is not True:
            raise ValueError("The write stub did not accept the proposal.")
        if result.get("path") != arguments.get("path"):
            raise ValueError("The write stub returned a different path.")

    @staticmethod
    def _verify_search(result: Any, _: Mapping[str, Any]) -> None:
        if not isinstance(result, str):
            raise ValueError("The search stub must return JSON text.")

    async def call_tool(
        self,
        tool_id: str,
        arguments: Mapping[str, Any],
        *,
        budget: RequestBudget,
        confirmation_provider: StubConfirmationProvider | None = None,
    ) -> Any:
        self.calls.append(
            {
                "tool_id": tool_id,
                "path": arguments.get("path"),
                "pattern": arguments.get("pattern"),
                "handler_ran": False,
            }
        )
        return await self.executor.execute(
            tool_id,
            arguments,
            budget=budget,
            confirmation_provider=confirmation_provider,
        )

    def descriptors(self) -> dict[str, ToolDescriptor]:
        return {descriptor.name: descriptor for descriptor in self.registry.descriptors()}


def _add_plan_006_api_contract_fixtures(tools: StubGovernedTools) -> None:
    """Provide case-local synthetic contracts so plan-006 has comparable evidence."""
    contract_paths = [
        "services/customer-api/openapi.yaml",
        "services/account-api/openapi.yaml",
    ]
    tools.read_values.update(
        {
            contract_paths[0]: (
                "openapi: 3.0.3\n"
                "info:\n  title: Customer API\n  version: 1.0.0\n"
                "paths:\n  /v1/customers/{customerId}:\n"
                "    get:\n      parameters:\n"
                "        - name: customerId\n          in: path\n          required: true\n"
                "          schema: {type: string}\n"
                "      responses:\n        '200':\n"
                "          description: Customer\n          content:\n"
                "            application/json:\n              schema:\n"
                "                type: object\n"
                "                required: [customerId, email, status]\n"
                "                properties:\n                  customerId: {type: string}\n"
                "                  email: {type: string, format: email}\n"
                "                  status: {type: string, enum: [active, suspended]}\n"
            ),
            contract_paths[1]: (
                "openapi: 3.0.3\n"
                "info:\n  title: Account API\n  version: 1.0.0\n"
                "paths:\n  /v1/customers/{customerId}:\n"
                "    get:\n      parameters:\n"
                "        - name: customerId\n          in: path\n          required: true\n"
                "          schema: {type: string}\n"
                "      responses:\n        '200':\n"
                "          description: Customer account\n          content:\n"
                "            application/json:\n              schema:\n"
                "                type: object\n"
                "                required: [customerId, email, status]\n"
                "                properties:\n                  customerId: {type: string}\n"
                "                  email: {type: string, format: email}\n"
                "                  status: {type: string, enum: [active, disabled]}\n"
            ),
        }
    )
    all_files = dict(tools.search_values["*"])
    all_files["matches"] = [*all_files["matches"], *contract_paths]
    all_files["scanned_entries"] += len(contract_paths)
    tools.search_values["*"] = all_files
    tools.search_values["*.yaml"] = {
        "matches": contract_paths,
        "scanned_entries": len(contract_paths),
        "truncated": False,
    }


def _add_plan_007_database_fixtures(tools: StubGovernedTools) -> None:
    """Provide case-local schema and migration evidence for the migration planning case."""
    fixture_paths = ["db/schema.sql", "db/migrations/001_add_order_status.sql"]
    tools.read_values.update(
        {
            fixture_paths[0]: (
                "CREATE TABLE customers (\n"
                "    id UUID PRIMARY KEY,\n"
                "    email TEXT NOT NULL UNIQUE\n"
                ");\n\n"
                "CREATE TABLE orders (\n"
                "    id UUID PRIMARY KEY,\n"
                "    customer_id UUID NOT NULL REFERENCES customers(id),\n"
                "    total_cents INTEGER NOT NULL CHECK (total_cents >= 0)\n"
                ");\n\n"
                "CREATE INDEX idx_orders_customer_id ON orders(customer_id);\n"
            ),
            fixture_paths[1]: (
                "-- Existing migration convention: numbered SQL files.\n"
                "ALTER TABLE orders ADD COLUMN status TEXT NOT NULL DEFAULT 'pending';\n"
            ),
        }
    )
    all_files = dict(tools.search_values["*"])
    all_files["matches"] = [*all_files["matches"], *fixture_paths]
    all_files["scanned_entries"] += len(fixture_paths)
    tools.search_values["*"] = all_files
    tools.search_values["*.sql"] = {
        "matches": fixture_paths,
        "scanned_entries": len(fixture_paths),
        "truncated": False,
    }


def _add_plan_008_secret_management_fixtures(tools: StubGovernedTools) -> None:
    """Provide case-local policy docs and intentionally unsafe config code for review."""
    markdown_path = "docs/security/secrets.md"
    python_path = "src/hostpilot/config/settings.py"
    tools.read_values.update(
        {
            markdown_path: (
                "# Secret handling policy\n\n"
                "- Load API keys from the HOSTPILOT_API_KEY environment variable.\n"
                "- Keep credentials in process memory; do not persist them to files or SQLite.\n"
                "- Never log credential values. Redact secret-bearing configuration fields.\n"
                "- Rotate exposed credentials and use a secret manager in production.\n"
            ),
            python_path: (
                "import logging\n\n"
                "logger = logging.getLogger(__name__)\n"
                'MODEL_API_KEY = "fixture-placeholder"\n\n'
                "def load_api_key() -> str:\n"
                '    logger.info("Using model API key %s", MODEL_API_KEY)\n'
                "    return MODEL_API_KEY\n"
            ),
        }
    )
    all_files = dict(tools.search_values["*"])
    all_files["matches"] = [*all_files["matches"], markdown_path, python_path]
    all_files["scanned_entries"] += 2
    tools.search_values["*"] = all_files
    markdown_files = dict(tools.search_values["*.md"])
    markdown_files["matches"] = [*markdown_files["matches"], markdown_path]
    markdown_files["scanned_entries"] += 1
    tools.search_values["*.md"] = markdown_files
    python_files = dict(tools.search_values["*.py"])
    python_files["matches"] = [*python_files["matches"], python_path]
    python_files["scanned_entries"] += 1
    tools.search_values["*.py"] = python_files


def _active_app_model() -> dict[str, Any]:
    request = urllib.request.Request(
        "http://127.0.0.1:8000/api/v1/model/configuration",
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) and payload.get("configured") else {}


def _model_configuration() -> tuple[str, str | None, str, bool]:
    active = _active_app_model()
    return model_configuration_from_environment(active, interactive=sys.stdin.isatty())


def _register_stub_skill_action(actions: StepActionRegistry) -> None:
    async def summarize(call: StepActionCall) -> StepActionResult:
        source = next(
            (str(value) for value in call.inputs.values() if isinstance(value, str)),
            "No supplied content.",
        )
        output = {
            "result": (
                f"Synthetic {call.skill_id.rsplit('/', 1)[-1]} result based only on supplied text: "
                f"{source[:1200]}"
            )
        }
        return StepActionResult(output=output)

    actions.register(
        StepActionRegistration(
            action="evaluation.noop",
            side_effect_level=SideEffectLevel.READ_ONLY,
            handler=summarize,
        )
    )


async def _run_case(
    model: Any,
    case: dict[str, Any],
    fixture_skills: list[dict[str, Any]],
    *,
    show_response: bool = False,
    token_budget: int = 20_000,
    tool_call_budget: int = 8,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="hostpilot-runtime-eval-") as temp_dir:
        registry_root = Path(temp_dir) / "skills"
        registry_root.mkdir()
        code_review_id = "business/code-review-check"
        write_fixture_skills(
            registry_root,
            fixture_skills,
            skip_skill_ids=frozenset({code_review_id}),
        )
        packaged_skill_root = Path(__file__).resolve().parents[1] / "src/hostpilot/skill_packages"
        registry = YamlSkillRegistry(registry_root, additional_roots=(packaged_skill_root,))
        stub_tools = StubGovernedTools(Path(temp_dir))
        if case["case_id"] == "plan-006":
            _add_plan_006_api_contract_fixtures(stub_tools)
        elif case["case_id"] == "plan-007":
            _add_plan_007_database_fixtures(stub_tools)
        elif case["case_id"] == "plan-008":
            _add_plan_008_secret_management_fixtures(stub_tools)
        actions = StepActionRegistry()
        _register_stub_skill_action(actions)
        register_llm_completion_action(actions, model)
        builtins = BuiltinExecutorRegistry()
        assessor = LLMSkillAssessor(model)
        skill_runtime = RecordingSkillRuntime(registry, actions, builtins, assessor, assessor)
        plan_executor = build_model_plan_executor(
            model,
            skills=registry,
            skill_runtime=skill_runtime,
            tool_provider=stub_tools,
            tool_descriptors=stub_tools.descriptors(),
            max_replans=1,
        )
        recording_planner = RecordingPlanner(plan_executor.planner)
        recording_tasks = RecordingPlanTaskExecutor(plan_executor.task_executor)
        recording_verifier = RecordingPlanVerifier(plan_executor.verifier)
        plan_executor.planner = recording_planner
        plan_executor.replanner = recording_planner
        plan_executor.task_executor = recording_tasks
        plan_executor.verifier = recording_verifier
        skill_runtime.plan_fallback = ModelPlanFallback(plan_executor)
        register_core_builtin_skills(
            builtins,
            llm=ProviderSingleTurnLLM(model),
            plan_executor=plan_executor,
        )
        selector = RecordingSelector(ModelProviderSkillSelector(registry, model))
        chat_runtime = StubChatRuntime()
        adapter = SkillRuntimeAgentAdapter(chat_runtime, skill_runtime, selector)
        confirmation = StubConfirmationProvider()
        budget = RequestBudget.start(
            BudgetContext(
                limits=BudgetLimits(
                    total_tokens=token_budget,
                    tool_calls=tool_call_budget,
                    retrieval_calls=0,
                    timeout_seconds=180,
                )
            )
        )
        context = ContextSnapshot(
            request_id=str(uuid.uuid4()),
            conversation_id=str(uuid.uuid4()),
        )
        started_at = time.monotonic()
        try:
            response = await adapter.respond(
                case["prompt"],
                context,
                budget=budget,
                confirmation_provider=confirmation,
            )
            error_code = None
        except Exception as error:  # Report an isolated evaluation failure without hiding its type.
            response = None
            error_code = type(error).__name__
            error_message = str(error)
            error_traceback = traceback.format_exception(type(error), error, error.__traceback__)
        else:
            error_message = None
            error_traceback = None
        elapsed = time.monotonic() - started_at
        selection = selector.selection
        runtime_result = skill_runtime.last_result
        expected = case["expected"]
        actual_skill = selection.skill_id if selection is not None else None
        actual_route = response.route.value if response is not None and response.route else None
        expected_route = expected.get("route")
        expected_skill = expected.get("skill_id")
        result = {
            "case_id": case["case_id"],
            "expected_route": expected_route,
            "actual_route": actual_route,
            "expected_skill_id": expected_skill,
            "actual_skill_id": actual_skill,
            "route_match": expected_route is None or actual_route == expected_route,
            "skill_match": expected_skill is None or actual_skill == expected_skill,
            "reason_code": selection.reason_code if selection else None,
            "runtime_result": _runtime_result_summary(runtime_result),
            "skill_runtime_exception": skill_runtime.last_exception,
            "plan_drafts": recording_planner.drafts,
            "plan_task_results": recording_tasks.results,
            "capability_selections": recording_tasks.capability_selections,
            "tool_agent_diagnostics": list(
                getattr(recording_tasks.executor, "tool_agent_diagnostics", ())
            ),
            "plan_verifications": recording_verifier.decisions,
            "error_code": error_code,
            "error_message": error_message,
            "error_traceback": error_traceback,
            "stub_tool_attempts": [
                {
                    "tool_id": item["tool_id"],
                    "path": item["path"],
                    "pattern": item.get("pattern"),
                }
                for item in stub_tools.calls
            ],
            "stub_handlers_run": len(stub_tools.handler_calls),
            "stub_writes_proposed": len(stub_tools.proposed_writes),
            "stub_write_proposals": [
                {
                    "path": item["path"],
                    "content_bytes": len(item["content"].encode("utf-8")),
                    "content_present": bool(item["content"].strip()),
                }
                for item in stub_tools.proposed_writes
            ],
            "confirmation_requests": len(confirmation.requests),
            "denied_audit_events": [
                event.reason_code
                for event in stub_tools.audit_events
                if event.outcome.value == "denied"
            ],
            "provider_reported_tokens": budget.snapshot().usage.total_tokens,
            "latency_seconds": round(elapsed, 3),
            "response_present": response is not None,
            "chat_turns_recorded": len(chat_runtime.turns),
        }
        if show_response:
            result["_assistant_response"] = response.content if response is not None else None
        return result


def _runtime_result_summary(result: SkillExecutionResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    output = result.output
    snapshot = result.snapshot
    return {
        "status": result.status.value,
        "clarification_prompt": result.clarification_prompt,
        "output_keys": sorted(output) if isinstance(output, Mapping) else [],
        "error_code": output.get("error_code") if isinstance(output, Mapping) else None,
        "failure_code": snapshot.failure_code if snapshot else None,
        "admission": (
            {
                "disposition": snapshot.admission.disposition.value,
                "reason_code": snapshot.admission.reason_code,
                "message": snapshot.admission.message,
            }
            if snapshot and snapshot.admission
            else None
        ),
        "result_assessment": (
            {
                "disposition": snapshot.result_assessment.disposition.value,
                "reason_code": snapshot.result_assessment.reason_code,
                "message": snapshot.result_assessment.message,
            }
            if snapshot and snapshot.result_assessment
            else None
        ),
        "step_states": (
            [
                {"step_id": step.step_id, "status": step.status, "error_code": step.error_code}
                for step in snapshot.step_states
            ]
            if snapshot
            else []
        ),
    }


def _new_budget() -> RequestBudget:
    return RequestBudget.start(
        BudgetContext(
            limits=BudgetLimits(
                total_tokens=500,
                tool_calls=4,
                retrieval_calls=0,
                timeout_seconds=30,
            )
        )
    )


async def _run_tool_boundary_probes() -> dict[str, Any]:
    """Verify path denial, confirmation, read-only calls, and stub-only writes without an LLM."""
    with tempfile.TemporaryDirectory(prefix="hostpilot-tool-boundary-") as temp_dir:
        root = Path(temp_dir)

        outside = StubGovernedTools(root)
        outside_confirmation = StubConfirmationProvider()
        try:
            await outside.call_tool(
                "write_text_file",
                {"path": "/etc/hosts", "content": "evaluation-probe"},
                budget=_new_budget(),
                confirmation_provider=outside_confirmation,
            )
            outside_denied = False
        except ToolDeniedError:
            outside_denied = True
        outside_reason = outside.audit_events[-1].reason_code if outside.audit_events else None

        unapproved = StubGovernedTools(root)
        try:
            await unapproved.call_tool(
                "write_text_file",
                {"path": "unapproved.txt", "content": "probe"},
                budget=_new_budget(),
            )
            unapproved_denied = False
        except ToolDeniedError:
            unapproved_denied = True
        unapproved_reason = (
            unapproved.audit_events[-1].reason_code if unapproved.audit_events else None
        )

        approved = StubGovernedTools(root)
        confirmation = StubConfirmationProvider()
        await approved.call_tool(
            "write_text_file",
            {"path": "approved-stub.txt", "content": "probe"},
            budget=_new_budget(),
            confirmation_provider=confirmation,
        )
        approved_file_created = (root / "approved-stub.txt").exists()

        read_stub = StubGovernedTools(root)
        read_result = await read_stub.call_tool(
            "read_text_file",
            {"path": "README.md"},
            budget=_new_budget(),
        )
        report = {
            "model_calls": 0,
            "real_tool_side_effects": 0,
            "outside_workspace_write": {
                "denied": outside_denied,
                "audit_reason": outside_reason,
                "handler_runs": len(outside.handler_calls),
                "confirmation_requests": len(outside_confirmation.requests),
            },
            "write_without_approval": {
                "denied": unapproved_denied,
                "audit_reason": unapproved_reason,
                "handler_runs": len(unapproved.handler_calls),
            },
            "approved_write_stub": {
                "approval_requests": len(confirmation.requests),
                "handler_runs": len(approved.handler_calls),
                "proposals_recorded": len(approved.proposed_writes),
                "file_created_on_disk": approved_file_created,
            },
            "read_stub": {
                "handler_runs": len(read_stub.handler_calls),
                "returned_fixture_text": isinstance(read_result, str),
                "real_file_read": False,
            },
        }
        report["passed"] = bool(
            outside_denied
            and outside_reason == "invalid_arguments"
            and not outside.handler_calls
            and not outside_confirmation.requests
            and unapproved_denied
            and unapproved_reason == "confirmation_provider_unavailable"
            and not unapproved.handler_calls
            and len(confirmation.requests) == 1
            and len(approved.handler_calls) == 1
            and len(approved.proposed_writes) == 1
            and not approved_file_created
            and len(read_stub.handler_calls) == 1
            and isinstance(read_result, str)
        )
        return report


async def _run(args: argparse.Namespace) -> int:
    if args.token_budget < 1:
        raise SystemExit("--token-budget must be positive")
    if args.tool_call_budget < 1:
        raise SystemExit("--tool-call-budget must be positive")
    tool_boundaries = await _run_tool_boundary_probes()
    if args.static_only:
        report = {"tool_boundaries": tool_boundaries}
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            args.output.write_text(encoded + "\n", encoding="utf-8")
        print(json.dumps(tool_boundaries, ensure_ascii=False))
        if args.output:
            print(f"Report written to: {args.output}")
        return 0 if tool_boundaries["passed"] else 1

    dataset = yaml.safe_load(args.dataset.read_text(encoding="utf-8"))
    selected_ids = set(args.case_id or DEFAULT_CASES)
    cases = [case for case in dataset["cases"] if case["case_id"] in selected_ids]
    missing = selected_ids - {case["case_id"] for case in cases}
    if missing:
        raise SystemExit(f"Unknown case IDs: {', '.join(sorted(missing))}")

    model_name, base_url, api_key, streaming = _model_configuration()
    settings = Settings(
        model_name=model_name,
        model_api_key=api_key,
        model_base_url=base_url,
        model_streaming=streaming,
    )
    model = create_model_runtime(settings).provider

    results = []
    for case in cases:
        result = await _run_case(
            model,
            case,
            dataset["fixture_skills"],
            show_response=args.show_response,
            token_budget=args.token_budget,
            tool_call_budget=args.tool_call_budget,
        )
        assistant_response = result.pop("_assistant_response", None)
        if args.show_response:
            print(f"\n--- {case['case_id']} assistant response (terminal only) ---")
            print(assistant_response or "(No assistant response)")
            print("--- end response ---\n")
        results.append(result)
    report = {
        "dataset_id": dataset["dataset_id"],
        "dataset_version": dataset["version"],
        "model": model_name,
        "streaming": streaming,
        "token_budget": args.token_budget,
        "tool_call_budget": args.tool_call_budget,
        "mode": "selector_and_skill_runtime_with_governed_in_memory_tool_stubs",
        "real_tool_side_effects": 0,
        "deterministic_tool_boundaries": tool_boundaries,
        "cases": results,
    }
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(f"Model: {model_name}; streaming={streaming}; real tool side effects=0")
    for result in results:
        print(
            f"{result['case_id']} | {result['expected_route']} -> {result['actual_route']} | "
            f"skill={result['actual_skill_id']} | tools={result['stub_tool_attempts']} | "
            f"denials={result['denied_audit_events']} | error={result['error_code']}"
        )
    if args.output:
        print(f"Report written to: {args.output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DATASET_PATH,
        help="Evaluation dataset YAML (defaults to v2).",
    )
    parser.add_argument(
        "--static-only",
        action="store_true",
        help="Run ToolExecutor boundary probes without configuring a model or spending tokens.",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        help="Case ID to run; repeat to select multiple cases. Defaults to three safe scenarios.",
    )
    parser.add_argument(
        "--show-response",
        action="store_true",
        help="Print the assistant response in the terminal; it is not added to the JSON report.",
    )
    parser.add_argument(
        "--token-budget",
        type=int,
        default=20_000,
        help="Per-case model token budget for the isolated runtime (default: 20000).",
    )
    parser.add_argument(
        "--tool-call-budget",
        type=int,
        default=8,
        help="Per-case governed tool-call budget for the isolated runtime (default: 8).",
    )
    parser.add_argument("--output", type=Path, help="Optional JSON report path.")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

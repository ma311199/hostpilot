"""Evaluate builtin.runtime-chat with a real model and isolated governed tools."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from pydantic import SecretStr

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
from hostpilot.evaluation.model_config import model_configuration_from_environment
from hostpilot.observability import AuditEvent
from hostpilot.orchestration.runtime import create_configured_chat_agent_runtime
from hostpilot.persistence import open_sqlite_checkpointer
from hostpilot.policy import PolicyEngine
from hostpilot.runtime import RequestContext
from hostpilot.tools.executor import ToolExecutor
from hostpilot.tools.facade import ToolFacade
from hostpilot.tools.registry import (
    ToolHandler,
    ToolRegistration,
    ToolRegistry,
    ToolResultVerifier,
)

DEFAULT_CASES = ("direct-answer", "read-tool")
CASES: dict[str, dict[str, Any]] = {
    "direct-answer": {
        "prompt": "12 乘以 8 等于多少？只回答结果。",
        "expected_text": "96",
        "expected_tool": None,
    },
    "read-tool": {
        "prompt": (
            "请调用 read_text_file 读取 about.txt，依据文件内容用中文总结项目名称。"
            "必须先读取文件，不能猜测。"
        ),
        "expected_text": "hostpilot",
        "expected_tool": {"name": "read_text_file", "path": "about.txt"},
    },
    "skill-clarify": {
        "prompt": "帮我做一次代码评审。",
        "expected_text": "代码或 diff",
        "expected_tool": None,
        "expected_route": "clarify",
        "expected_skill_id": "business/code-review-check",
    },
    "skill-code-review": {
        "prompt": ("检查这段代码的安全问题：`query = 'SELECT * FROM users WHERE name=' + name`。"),
        "expected_text": "SQL injection",
        "expected_tool": None,
        "expected_route": "skill_execute",
        "expected_skill_id": "business/code-review-check",
    },
    "plan-two-reads": {
        "prompt": (
            "请分步骤完成：先读取 about.txt，再读取 status.txt；"
            "比较两份内容中的项目名称和当前工作主题，"
            "最后用两点总结。必须调用 read_text_file 读取这两个文件，不要猜测。"
        ),
        "expected_text": "Runtime-native",
        "expected_tool": None,
        "expected_tools": [
            {"name": "read_text_file", "path": "about.txt"},
            {"name": "read_text_file", "path": "status.txt"},
        ],
        "expected_route": "plan_execute",
        "expected_skill_id": "builtin/orchestrator_plan",
    },
    "recovery-after-tool": {
        "prompt": (
            "请调用 read_text_file 读取 about.txt，然后只依据读取结果用中文回答项目名称。"
            "不要猜测，也不要调用其他工具。"
        ),
        "expected_text": "hostpilot",
        "expected_tool": {"name": "read_text_file", "path": "about.txt"},
    },
    "recovery-after-process-restart": {
        "prompt": (
            "请调用 read_text_file 读取 about.txt，然后只依据读取结果用中文回答项目名称。"
            "不要猜测，也不要调用其他工具。"
        ),
        "expected_text": "hostpilot",
        "expected_tool": {"name": "read_text_file", "path": "about.txt"},
    },
}


class RecordingSelector:
    """Capture the Skill selector result while preserving the production call path."""

    def __init__(self, selector: Any) -> None:
        self.selector = selector
        self.selection: Any | None = None

    async def select(self, *args: Any, **kwargs: Any) -> Any:
        self.selection = await self.selector.select(*args, **kwargs)
        return self.selection


class FailOnceAfterToolModel:
    """Inject a single model failure after the isolated tool handler completed."""

    def __init__(self, model: Any, tools: RuntimeChatStubTools) -> None:
        self.model = model
        self.tools = tools
        self.failure_injected = False

    async def complete(self, request_context: RequestContext, request: Any) -> Any:
        if not self.failure_injected and self.tools.handler_runs == 1:
            self.failure_injected = True
            raise RuntimeError("evaluation_injected_model_failure_after_tool")
        return await self.model.complete(request_context, request)


class RuntimeChatStubTools:
    """Govern tool calls through ToolExecutor while keeping all data in memory."""

    def __init__(self) -> None:
        self.registry = ToolRegistry()
        self.audit_events: list[AuditEvent] = []
        self.calls: list[dict[str, Any]] = []
        self.handler_runs = 0
        self.read_values = {
            "about.txt": "Project: HostPilot. In-memory evaluation fixture.",
            "status.txt": "Current topic: Runtime-native migration validation. In-memory fixture.",
        }
        self.executor = ToolExecutor(self.registry, PolicyEngine(), audit=self.audit_events.append)
        self.registry.register(
            ToolRegistration(
                descriptor=ToolDescriptor(
                    name="read_text_file",
                    description=(
                        "Read an allowlisted text fixture from the isolated evaluation workspace."
                    ),
                    access_summary="读取隔离评测内存中的 about.txt 或 status.txt，不访问磁盘。",
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
                handler=cast(ToolHandler, self._read),
                verify_result=cast(ToolResultVerifier, self._verify_read),
            )
        )

    @staticmethod
    def _validate_read(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        path = arguments.get("path")
        if set(arguments) != {"path"} or path not in {"about.txt", "status.txt"}:
            raise ValueError("Only about.txt and status.txt fixtures are available.")
        return {"path": path}

    async def _read(self, arguments: Mapping[str, Any]) -> str:
        self.handler_runs += 1
        return self.read_values[str(arguments["path"])]

    @staticmethod
    def _verify_read(result: Any, _: Mapping[str, Any]) -> None:
        if not isinstance(result, str):
            raise ValueError("The in-memory fixture must return text.")

    async def call_tool(
        self,
        tool_id: str,
        arguments: Mapping[str, Any],
        *,
        budget: RequestBudget,
        confirmation_provider: Any | None = None,
    ) -> str:
        self.calls.append({"name": tool_id, "arguments": dict(arguments)})
        result = await self.executor.execute(
            tool_id,
            arguments,
            budget=budget,
            confirmation_provider=confirmation_provider,
        )
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)


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
    return model_configuration_from_environment(_active_app_model(), interactive=sys.stdin.isatty())


def _recovery_worker_command(
    script_path: Path,
    *,
    phase: str,
    state_dir: Path,
    session_id: str,
    token_budget: int,
    tool_call_budget: int,
) -> list[str]:
    """Build worker argv without accepting credentials as command-line data."""
    return [
        sys.executable,
        str(script_path),
        "--_recovery-worker-phase",
        phase,
        "--_recovery-state-dir",
        str(state_dir),
        "--_recovery-session-id",
        session_id,
        "--token-budget",
        str(token_budget),
        "--tool-call-budget",
        str(tool_call_budget),
    ]


def _recovery_worker_environment(
    model_name: str,
    base_url: str | None,
    api_key: str,
    streaming: bool,
) -> dict[str, str]:
    """Pass model configuration to child workers without writing credentials to disk."""
    environment = os.environ.copy()
    environment["HOSTPILOT_MODEL_NAME"] = model_name
    environment["HOSTPILOT_MODEL_API_KEY"] = api_key
    environment["HOSTPILOT_MODEL_STREAMING"] = str(streaming).lower()
    if base_url is None:
        environment.pop("HOSTPILOT_MODEL_BASE_URL", None)
    else:
        environment["HOSTPILOT_MODEL_BASE_URL"] = base_url
    return environment


def _aggregate_process_recovery(prepare: dict[str, Any], resume: dict[str, Any]) -> dict[str, Any]:
    """Judge both worker phases and their shared session/tool execution evidence."""
    same_session_id = isinstance(prepare.get("session_id"), str) and prepare.get(
        "session_id"
    ) == resume.get("session_id")
    fresh_process = (
        isinstance(prepare.get("process_id"), int)
        and isinstance(resume.get("process_id"), int)
        and prepare.get("process_id") != resume.get("process_id")
    )
    tool_calls = [*prepare.get("tool_calls", []), *resume.get("tool_calls", [])]
    total_handler_runs = int(prepare.get("handler_runs", 0)) + int(resume.get("handler_runs", 0))
    response_text = str(resume.get("response_text") or "")
    expected_tool = {"name": "read_text_file", "arguments": {"path": "about.txt"}}
    expected_tools_match = tool_calls == [expected_tool]
    passed = (
        prepare.get("phase") == "prepare"
        and prepare.get("failure_injected") is True
        and prepare.get("checkpoint_has_tool_result") is True
        and prepare.get("error_code") == "evaluation_injected_model_failure_after_tool"
        and resume.get("phase") == "resume"
        and fresh_process
        and resume.get("checkpoint_reopened") is True
        and resume.get("checkpoint_has_tool_result") is True
        and resume.get("error_code") is None
        and resume.get("actual_route") == "direct"
        and "hostpilot" in response_text.casefold()
        and same_session_id
        and expected_tools_match
        and total_handler_runs == 1
    )
    return {
        "phase_results": {"prepare": prepare, "resume": resume},
        "session_id": prepare.get("session_id"),
        "same_session_id": same_session_id,
        "fresh_process_boundary": fresh_process,
        "tool_calls": tool_calls,
        "tool_match": expected_tools_match,
        "expected_response_contains": "hostpilot",
        "response_match": "hostpilot" in response_text.casefold(),
        "response_present": bool(response_text),
        "assistant_response": response_text or None,
        "total_handler_runs": total_handler_runs,
        "no_duplicate_tool_execution": total_handler_runs == 1,
        "token_usage": int(prepare.get("token_usage", 0)) + int(resume.get("token_usage", 0)),
        "tool_call_usage": int(prepare.get("tool_call_usage", 0))
        + int(resume.get("tool_call_usage", 0)),
        "latency_seconds": 0.0,
        "expected_route": "direct",
        "actual_route": resume.get("actual_route"),
        "route_match": resume.get("actual_route") == "direct",
        "error_code": None if passed else "process_recovery_assertion_failed",
        "passed": passed,
    }


async def _run_recovery_worker(args: argparse.Namespace) -> int:
    """Run one isolated process-recovery phase and write its non-secret result."""
    model_name, base_url, api_key, streaming = _model_configuration()
    root = args._recovery_state_dir
    settings = Settings(
        model_name=model_name,
        model_api_key=SecretStr(api_key),
        model_base_url=base_url,
        model_streaming=streaming,
        chat_runtime_plugin="builtin.runtime-chat",
        data_dir=root / "data",
        memory_dir=root / "memory",
        skills_dir=root / "skills",
    )
    skills_dir = settings.skills_dir or root / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    tools = RuntimeChatStubTools()
    budget = RequestBudget.start(
        BudgetContext(
            limits=BudgetLimits(
                total_tokens=args.token_budget,
                tool_calls=args.tool_call_budget,
                retrieval_calls=0,
                timeout_seconds=180,
            )
        )
    )
    request_context = RequestContext(
        snapshot=ContextSnapshot(
            request_id=str(uuid.uuid4()), session_id=args._recovery_session_id
        ),
        budget=budget,
    )
    phase_result: dict[str, Any] = {
        "phase": args._recovery_worker_phase,
        "session_id": args._recovery_session_id,
        "process_id": os.getpid(),
        "tool_calls": [],
        "handler_runs": 0,
        "token_usage": 0,
        "tool_call_usage": 0,
        "error_code": None,
    }
    async with open_sqlite_checkpointer(root / "checkpoints.sqlite") as checkpointer:
        adapter = create_configured_chat_agent_runtime(
            settings,
            checkpointer=checkpointer,
            tools=(),
            tool_provider=ToolFacade(tools),
        )
        chat_adapter = cast(Any, adapter)
        runtime = chat_adapter.chat_agent_runtime
        try:
            if args._recovery_worker_phase == "prepare":
                original_model = runtime.execution_loop.model
                failing_model = FailOnceAfterToolModel(original_model, tools)
                runtime.execution_loop.model = failing_model
                try:
                    await runtime.respond(
                        CASES["recovery-after-process-restart"]["prompt"], request_context
                    )
                except Exception as error:
                    phase_result["error_code"] = str(error)
                finally:
                    runtime.execution_loop.model = original_model
                messages = await runtime.session_state.load_messages(args._recovery_session_id)
                phase_result.update(
                    {
                        "failure_injected": failing_model.failure_injected,
                        "checkpoint_has_tool_result": any(
                            getattr(message.role, "value", message.role) == "tool"
                            and bool(message.content)
                            for message in messages
                        ),
                    }
                )
            elif args._recovery_worker_phase == "resume":
                messages = await runtime.session_state.load_messages(args._recovery_session_id)
                phase_result["checkpoint_reopened"] = True
                phase_result["checkpoint_has_tool_result"] = any(
                    getattr(message.role, "value", message.role) == "tool" and bool(message.content)
                    for message in messages
                )
                response = await runtime.respond(
                    CASES["recovery-after-process-restart"]["prompt"], request_context
                )
                phase_result["response_text"] = response.content
                phase_result["actual_route"] = response.route.value if response.route else None
            else:
                raise ValueError("Unknown recovery worker phase.")
            phase_result["tool_calls"] = list(tools.calls)
            phase_result["handler_runs"] = tools.handler_runs
            phase_result["token_usage"] = budget.snapshot().usage.total_tokens
            phase_result["tool_call_usage"] = budget.snapshot().usage.tool_calls
        finally:
            skill_runtime = getattr(runtime, "skill_runtime", None)
            close = getattr(skill_runtime, "aclose", None)
            if callable(close):
                await close()
    result_path = root / f"{args._recovery_worker_phase}.json"
    result_path.write_text(json.dumps(phase_result, ensure_ascii=False), encoding="utf-8")
    return 0


def _run_process_recovery_evaluation(
    args: argparse.Namespace,
    model_configuration: tuple[str, str | None, str, bool],
) -> int:
    """Run prepare and resume phases in separate Python processes."""
    model_name, base_url, api_key, streaming = model_configuration
    started_at = time.monotonic()
    session_id = str(uuid.uuid4())
    prepare: dict[str, Any] = {}
    resume: dict[str, Any] = {}
    worker_error: str | None = None
    with tempfile.TemporaryDirectory(prefix="hostpilot-runtime-chat-restart-") as temp_dir:
        state_dir = Path(temp_dir)
        environment = _recovery_worker_environment(model_name, base_url, api_key, streaming)
        script_path = Path(__file__).resolve()
        for phase in ("prepare", "resume"):
            command = _recovery_worker_command(
                script_path,
                phase=phase,
                state_dir=state_dir,
                session_id=session_id,
                token_budget=args.token_budget,
                tool_call_budget=args.tool_call_budget,
            )
            completed = subprocess.run(command, env=environment, capture_output=True, text=True)
            result_path = state_dir / f"{phase}.json"
            if completed.returncode != 0 or not result_path.is_file():
                worker_error = f"{phase}_worker_exit_{completed.returncode}"
                break
            phase_result = json.loads(result_path.read_text(encoding="utf-8"))
            if phase == "prepare":
                prepare = phase_result
            else:
                resume = phase_result
    result = _aggregate_process_recovery(prepare, resume)
    result["latency_seconds"] = round(time.monotonic() - started_at, 3)
    if worker_error is not None:
        result["error_code"] = worker_error
        result["passed"] = False
    if args.show_response:
        print("\n--- recovery-after-process-restart assistant response ---")
        print(result.get("assistant_response") or "(No assistant response)")
        print("--- end response ---\n")
    report = {
        "runtime_plugin": "builtin.runtime-chat",
        "model": model_name,
        "streaming": streaming,
        "mode": "real_model_runtime_loop_with_two_process_sqlite_recovery",
        "real_tool_side_effects": 0,
        "token_budget_per_process": args.token_budget,
        "tool_call_budget_per_process": args.tool_call_budget,
        "cases": [
            {
                **{
                    key: value
                    for key, value in result.items()
                    if key not in {"assistant_response", "phase_results"}
                },
                "phase_results": {
                    phase: {key: value for key, value in details.items() if key != "response_text"}
                    for phase, details in result["phase_results"].items()
                },
            }
        ],
    }
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(f"Runtime plugin: builtin.runtime-chat; model={model_name}; real tool side effects=0")
    print(
        f"recovery-after-process-restart | passed={result['passed']} | "
        f"route={result['actual_route']} | tokens={result['token_usage']} | "
        f"tools={result['tool_calls']} | handler_runs={result['total_handler_runs']} | "
        f"error={result['error_code']}"
    )
    if args.output:
        print(f"Report written to: {args.output}")
    return 0 if result["passed"] else 1


async def _run_case(
    runtime: Any,
    chat_adapter: Any,
    selector: RecordingSelector,
    tools: RuntimeChatStubTools,
    case_id: str,
    *,
    token_budget: int,
    tool_call_budget: int,
    show_response: bool,
) -> dict[str, Any]:
    case = CASES[case_id]
    session_id = str(uuid.uuid4())
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
    request_context = RequestContext(
        snapshot=ContextSnapshot(request_id=str(uuid.uuid4()), session_id=session_id),
        budget=budget,
    )
    started_at = time.monotonic()
    response_text: str | None = None
    error_code: str | None = None
    budget_usages = [budget.snapshot().usage]
    recovery_result: dict[str, Any] | None = None
    selector.selection = None
    if case_id == "recovery-after-tool":
        original_model_port = runtime.execution_loop.model
        failing_model_port = FailOnceAfterToolModel(original_model_port, tools)
        runtime.execution_loop.model = failing_model_port
        first_error: str | None = None
        try:
            try:
                response = await runtime.respond(case["prompt"], request_context)
                response_text = response.content
            except Exception as error:
                first_error = str(error)
            calls_after_failure = len(tools.calls)
            handlers_after_failure = tools.handler_runs
            budget_usages[0] = budget.snapshot().usage
            retry_error: str | None = None
            if failing_model_port.failure_injected and handlers_after_failure == 1:
                retry_budget = RequestBudget.start(
                    BudgetContext(
                        limits=BudgetLimits(
                            total_tokens=token_budget,
                            tool_calls=tool_call_budget,
                            retrieval_calls=0,
                            timeout_seconds=180,
                        )
                    )
                )
                retry_context = RequestContext(
                    snapshot=ContextSnapshot(request_id=str(uuid.uuid4()), session_id=session_id),
                    budget=retry_budget,
                )
                try:
                    response = await runtime.respond(case["prompt"], retry_context)
                    response_text = response.content
                except Exception as error:
                    retry_error = type(error).__name__
                budget_usages.append(retry_budget.snapshot().usage)
            else:
                retry_error = "injected_failure_not_reached_after_tool_completion"
            recovery_result = {
                "failure_injected": failing_model_port.failure_injected,
                "first_error": first_error,
                "tool_calls_after_failure": calls_after_failure,
                "handler_runs_after_failure": handlers_after_failure,
                "tool_calls_after_retry": len(tools.calls),
                "handler_runs_after_retry": tools.handler_runs,
                "retry_error": retry_error,
                "no_duplicate_tool_execution": (
                    failing_model_port.failure_injected
                    and first_error == "evaluation_injected_model_failure_after_tool"
                    and handlers_after_failure == 1
                    and tools.handler_runs == 1
                ),
            }
            error_code = retry_error
        finally:
            runtime.execution_loop.model = original_model_port
    else:
        try:
            selector.selection = None
            if case_id.startswith(("skill-", "plan-")):
                response = await chat_adapter.respond(case["prompt"], request_context)
            else:
                response = await runtime.respond(case["prompt"], request_context)
            response_text = response.content
        except Exception as error:  # Keep a per-case failure report and continue selected cases.
            error_code = type(error).__name__
        budget_usages[0] = budget.snapshot().usage

    expected_tool = case["expected_tool"]
    expected_tools = case.get("expected_tools")
    selection = selector.selection
    actual_route = response.route.value if response_text and response.route else None
    actual_skill_id = selection.skill_id if selection is not None else None
    if expected_tools is not None:
        expected_tool_pairs = {(tool["name"], tool["path"]) for tool in expected_tools}
        actual_tool_pairs = {(call["name"], call["arguments"].get("path")) for call in tools.calls}
        tool_match = (
            len(tools.calls) == len(expected_tools) and actual_tool_pairs == expected_tool_pairs
        )
    elif expected_tool is None:
        tool_match = not tools.calls
    else:
        tool_match = (
            len(tools.calls) == 1
            and tools.calls[0]["name"] == expected_tool["name"]
            and tools.calls[0]["arguments"].get("path") == expected_tool["path"]
        )
    normalized_response = (response_text or "").casefold()
    text_match = case["expected_text"].casefold() in normalized_response
    result = {
        "case_id": case_id,
        "expected_tool": expected_tool,
        "expected_tools": expected_tools,
        "tool_calls": list(tools.calls),
        "tool_match": tool_match,
        "expected_response_contains": case["expected_text"],
        "response_match": text_match,
        "expected_route": case.get("expected_route"),
        "actual_route": actual_route,
        "route_match": (
            case.get("expected_route") is None or actual_route == case["expected_route"]
        ),
        "expected_skill_id": case.get("expected_skill_id"),
        "actual_skill_id": actual_skill_id,
        "skill_match": (
            case.get("expected_skill_id") is None or actual_skill_id == case["expected_skill_id"]
        ),
        "response_present": response_text is not None,
        "error_code": error_code,
        "token_usage": sum(usage.total_tokens for usage in budget_usages),
        "tool_call_usage": sum(usage.tool_calls for usage in budget_usages),
        "handler_runs": tools.handler_runs,
        "latency_seconds": round(time.monotonic() - started_at, 3),
        "passed": (
            error_code is None
            and text_match
            and tool_match
            and (case.get("expected_route") is None or actual_route == case["expected_route"])
            and (
                case.get("expected_skill_id") is None
                or actual_skill_id == case["expected_skill_id"]
            )
            and (
                case_id != "recovery-after-tool"
                or bool(
                    recovery_result
                    and recovery_result["no_duplicate_tool_execution"]
                    and response_text is not None
                )
            )
        ),
    }
    if recovery_result is not None:
        result["recovery"] = recovery_result
    if show_response:
        result["assistant_response"] = response_text
    return result


async def _run(args: argparse.Namespace) -> int:
    if args._recovery_worker_phase is not None:
        if args._recovery_state_dir is None or args._recovery_session_id is None:
            raise SystemExit("Recovery worker state directory and session ID are required.")
        return await _run_recovery_worker(args)
    if args.token_budget < 1 or args.tool_call_budget < 0:
        raise SystemExit("Token budget must be positive and tool-call budget cannot be negative.")
    selected = tuple(args.case_id or DEFAULT_CASES)
    unknown = set(selected) - CASES.keys()
    if unknown:
        raise SystemExit(f"Unknown case IDs: {', '.join(sorted(unknown))}")
    model_name, base_url, api_key, streaming = _model_configuration()
    if "recovery-after-process-restart" in selected:
        if selected != ("recovery-after-process-restart",):
            raise SystemExit("recovery-after-process-restart must run as a standalone case.")
        return _run_process_recovery_evaluation(args, (model_name, base_url, api_key, streaming))
    results: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="hostpilot-runtime-chat-eval-") as temp_dir:
        root = Path(temp_dir)
        settings = Settings(
            model_name=model_name,
            model_api_key=SecretStr(api_key),
            model_base_url=base_url,
            model_streaming=streaming,
            chat_runtime_plugin="builtin.runtime-chat",
            data_dir=root / "data",
            memory_dir=root / "memory",
            skills_dir=root / "skills",
        )
        skills_dir = settings.skills_dir or root / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        stub_tools = RuntimeChatStubTools()
        async with open_sqlite_checkpointer(root / "checkpoints.sqlite") as checkpointer:
            adapter = create_configured_chat_agent_runtime(
                settings,
                checkpointer=checkpointer,
                tools=(),
                tool_provider=ToolFacade(stub_tools),
            )
            chat_adapter = cast(Any, adapter)
            runtime = chat_adapter.chat_agent_runtime
            selector = RecordingSelector(chat_adapter.selector)
            chat_adapter.selector = selector
            try:
                for case_id in selected:
                    # Each case receives a clean, per-case tool record.
                    stub_tools.calls.clear()
                    stub_tools.handler_runs = 0
                    result = await _run_case(
                        runtime,
                        chat_adapter,
                        selector,
                        stub_tools,
                        case_id,
                        token_budget=args.token_budget,
                        tool_call_budget=args.tool_call_budget,
                        show_response=args.show_response,
                    )
                    if args.show_response:
                        print(f"\n--- {case_id} assistant response ---")
                        print(result.get("assistant_response") or "(No assistant response)")
                        print("--- end response ---\n")
                    results.append(result)
            finally:
                skill_runtime = getattr(runtime, "skill_runtime", None)
                close = getattr(skill_runtime, "aclose", None)
                if callable(close):
                    await close()

    report = {
        "runtime_plugin": "builtin.runtime-chat",
        "model": model_name,
        "streaming": streaming,
        "mode": "real_model_runtime_loop_with_in_memory_governed_tool_stub",
        "real_tool_side_effects": 0,
        "token_budget": args.token_budget,
        "tool_call_budget": args.tool_call_budget,
        "cases": results,
    }
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(f"Runtime plugin: builtin.runtime-chat; model={model_name}; real tool side effects=0")
    for result in results:
        print(
            f"{result['case_id']} | passed={result['passed']} | "
            f"route={result['actual_route']} | skill={result['actual_skill_id']} | "
            f"tokens={result['token_usage']} | tools={result['tool_calls']} | "
            f"error={result['error_code']}"
        )
    if args.output:
        print(f"Report written to: {args.output}")
    return 0 if all(result["passed"] for result in results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-id", action="append", help="Case ID; repeat to select multiple.")
    parser.add_argument("--show-response", action="store_true")
    parser.add_argument("--token-budget", type=int, default=20_000)
    parser.add_argument("--tool-call-budget", type=int, default=8)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--_recovery-worker-phase",
        choices=("prepare", "resume"),
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--_recovery-state-dir", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--_recovery-session-id", help=argparse.SUPPRESS)
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

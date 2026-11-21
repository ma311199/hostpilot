from __future__ import annotations

import asyncio
import json
import runpy
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from hostpilot.budget import RequestBudget
from hostpilot.contracts import BudgetContext, BudgetLimits
from hostpilot.tools.executor import ToolDeniedError

_SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts/evaluate_runtime_chat.py"
_SCRIPT = runpy.run_path(str(_SCRIPT_PATH))
RuntimeChatStubTools = _SCRIPT["RuntimeChatStubTools"]
FailOnceAfterToolModel = _SCRIPT["FailOnceAfterToolModel"]
EVALUATION_CASES = _SCRIPT["CASES"]
aggregate_process_recovery = _SCRIPT["_aggregate_process_recovery"]
recovery_worker_command = _SCRIPT["_recovery_worker_command"]
recovery_worker_environment = _SCRIPT["_recovery_worker_environment"]
run_process_recovery_evaluation = _SCRIPT["_run_process_recovery_evaluation"]


def test_runtime_chat_evaluation_read_tool_uses_only_the_fixture() -> None:
    async def scenario() -> None:
        tools = RuntimeChatStubTools()
        budget = RequestBudget.start(
            BudgetContext(
                limits=BudgetLimits(
                    total_tokens=1000,
                    tool_calls=1,
                    retrieval_calls=0,
                    timeout_seconds=30,
                )
            )
        )

        result = await tools.call_tool("read_text_file", {"path": "about.txt"}, budget=budget)

        assert "Project: HostPilot" in result
        assert budget.snapshot().usage.tool_calls == 1
        assert len(tools.audit_events) == 1

    asyncio.run(scenario())


def test_runtime_chat_evaluation_exposes_the_status_fixture_for_plan_reads() -> None:
    async def scenario() -> None:
        tools = RuntimeChatStubTools()
        budget = RequestBudget.start(
            BudgetContext(
                limits=BudgetLimits(
                    total_tokens=1000,
                    tool_calls=1,
                    retrieval_calls=0,
                    timeout_seconds=30,
                )
            )
        )

        result = await tools.call_tool("read_text_file", {"path": "status.txt"}, budget=budget)

        assert "Runtime-native migration validation" in result
        assert tools.handler_runs == 1

    asyncio.run(scenario())


def test_runtime_chat_evaluation_read_tool_rejects_paths_outside_fixture() -> None:
    async def scenario() -> None:
        tools = RuntimeChatStubTools()
        budget = RequestBudget.start(
            BudgetContext(
                limits=BudgetLimits(
                    total_tokens=1000,
                    tool_calls=1,
                    retrieval_calls=0,
                    timeout_seconds=30,
                )
            )
        )

        with pytest.raises(ToolDeniedError):
            await tools.call_tool("read_text_file", {"path": "../README.md"}, budget=budget)

        assert budget.snapshot().usage.tool_calls == 0
        assert len(tools.audit_events) == 1

    asyncio.run(scenario())


def test_recovery_fault_injection_occurs_only_after_tool_completion() -> None:
    class Model:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, request_context, request):
            del request_context, request
            self.calls += 1
            return "model response"

    class Tools:
        handler_runs = 0

    async def scenario() -> None:
        model = Model()
        tools = Tools()
        failing_model = FailOnceAfterToolModel(model, tools)

        assert await failing_model.complete(None, None) == "model response"
        tools.handler_runs = 1
        with pytest.raises(RuntimeError, match="evaluation_injected_model_failure_after_tool"):
            await failing_model.complete(None, None)
        assert await failing_model.complete(None, None) == "model response"
        assert model.calls == 2

    asyncio.run(scenario())


def test_runtime_chat_evaluation_includes_a_business_skill_execution_case() -> None:
    case = EVALUATION_CASES["skill-code-review"]

    assert case["expected_route"] == "skill_execute"
    assert case["expected_skill_id"] == "business/code-review-check"
    assert "SELECT * FROM users" in case["prompt"]


def test_runtime_chat_evaluation_includes_a_read_only_plan_case() -> None:
    case = EVALUATION_CASES["plan-two-reads"]

    assert case["expected_route"] == "plan_execute"
    assert case["expected_skill_id"] == "builtin/orchestrator_plan"
    assert {tool["path"] for tool in case["expected_tools"]} == {"about.txt", "status.txt"}


def test_runtime_chat_evaluation_includes_process_restart_recovery_case() -> None:
    case = EVALUATION_CASES["recovery-after-process-restart"]

    assert case["expected_text"] == "hostpilot"
    assert case["expected_tool"] == {"name": "read_text_file", "path": "about.txt"}


def test_process_recovery_aggregator_requires_fresh_process_and_single_handler_run() -> None:
    assert aggregate_process_recovery is not None
    session_id = "session-1"
    prepare = {
        "phase": "prepare",
        "session_id": session_id,
        "process_id": 101,
        "failure_injected": True,
        "checkpoint_has_tool_result": True,
        "tool_calls": [{"name": "read_text_file", "arguments": {"path": "about.txt"}}],
        "handler_runs": 1,
        "token_usage": 100,
        "error_code": "evaluation_injected_model_failure_after_tool",
    }
    resume = {
        "phase": "resume",
        "session_id": session_id,
        "process_id": 102,
        "checkpoint_reopened": True,
        "checkpoint_has_tool_result": True,
        "response_text": "HostPilot",
        "actual_route": "direct",
        "tool_calls": [],
        "handler_runs": 0,
        "token_usage": 50,
        "error_code": None,
    }

    result = aggregate_process_recovery(prepare, resume)

    assert result["passed"] is True
    assert result["total_handler_runs"] == 1
    assert result["token_usage"] == 150
    assert result["same_session_id"] is True


def test_process_recovery_aggregator_rejects_duplicate_handler_execution() -> None:
    assert aggregate_process_recovery is not None
    prepare = {
        "phase": "prepare",
        "session_id": "session-1",
        "process_id": 101,
        "failure_injected": True,
        "checkpoint_has_tool_result": True,
        "tool_calls": [{"name": "read_text_file", "arguments": {"path": "about.txt"}}],
        "handler_runs": 1,
        "token_usage": 100,
        "error_code": "evaluation_injected_model_failure_after_tool",
    }
    resume = {
        "phase": "resume",
        "session_id": "session-1",
        "process_id": 102,
        "checkpoint_reopened": True,
        "checkpoint_has_tool_result": True,
        "response_text": "HostPilot",
        "actual_route": "direct",
        "tool_calls": [{"name": "read_text_file", "arguments": {"path": "about.txt"}}],
        "handler_runs": 1,
        "token_usage": 50,
        "error_code": None,
    }

    result = aggregate_process_recovery(prepare, resume)

    assert result["passed"] is False
    assert result["total_handler_runs"] == 2


def test_recovery_worker_command_never_receives_the_model_key() -> None:
    api_key = "test-secret-value"

    command = recovery_worker_command(
        Path("evaluate_runtime_chat.py"),
        phase="prepare",
        state_dir=Path("/tmp/runtime-recovery"),
        session_id="session-1",
        token_budget=1000,
        tool_call_budget=2,
    )
    environment = recovery_worker_environment("glm-5.3", None, api_key, True)

    assert api_key not in command
    assert environment["HOSTPILOT_MODEL_API_KEY"] == api_key


def test_process_recovery_report_does_not_include_credentials() -> None:
    assert aggregate_process_recovery is not None
    secret = "test-secret-value"
    prepare = {
        "phase": "prepare",
        "session_id": "session-1",
        "process_id": 101,
        "failure_injected": True,
        "checkpoint_has_tool_result": True,
        "tool_calls": [{"name": "read_text_file", "arguments": {"path": "about.txt"}}],
        "handler_runs": 1,
        "token_usage": 100,
        "error_code": "evaluation_injected_model_failure_after_tool",
    }
    resume = {
        "phase": "resume",
        "session_id": "session-1",
        "process_id": 102,
        "checkpoint_reopened": True,
        "checkpoint_has_tool_result": True,
        "response_text": "HostPilot",
        "actual_route": "direct",
        "tool_calls": [],
        "handler_runs": 0,
        "token_usage": 50,
        "error_code": None,
    }

    result = aggregate_process_recovery(prepare, resume)

    assert secret not in str(result)


def test_process_recovery_runner_uses_two_workers_and_keeps_key_out_of_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_phases: list[str] = []
    worker_sessions: list[str] = []
    worker_state_dirs: list[str] = []
    api_key = "test-secret-value"

    def fake_run(command, *, env, capture_output, text):
        assert capture_output is True
        assert text is True
        assert env["HOSTPILOT_MODEL_API_KEY"] == api_key
        assert api_key not in command
        phase = command[command.index("--_recovery-worker-phase") + 1]
        state_dir = Path(command[command.index("--_recovery-state-dir") + 1])
        session_id = command[command.index("--_recovery-session-id") + 1]
        worker_phases.append(phase)
        worker_sessions.append(session_id)
        worker_state_dirs.append(str(state_dir))
        phase_result = {
            "phase": phase,
            "session_id": session_id,
            "process_id": 101 if phase == "prepare" else 102,
            "tool_calls": (
                [{"name": "read_text_file", "arguments": {"path": "about.txt"}}]
                if phase == "prepare"
                else []
            ),
            "handler_runs": 1 if phase == "prepare" else 0,
            "token_usage": 100 if phase == "prepare" else 50,
            "tool_call_usage": 1 if phase == "prepare" else 0,
            "error_code": (
                "evaluation_injected_model_failure_after_tool" if phase == "prepare" else None
            ),
            "failure_injected": phase == "prepare",
            "checkpoint_has_tool_result": True,
            "checkpoint_reopened": phase == "resume",
            "response_text": "HostPilot" if phase == "resume" else None,
            "actual_route": "direct" if phase == "resume" else None,
        }
        (state_dir / f"{phase}.json").write_text(json.dumps(phase_result), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(_SCRIPT["subprocess"], "run", fake_run)
    args = Namespace(
        token_budget=2000,
        tool_call_budget=2,
        show_response=False,
        output=tmp_path / "recovery-report.json",
    )

    exit_code = run_process_recovery_evaluation(args, ("glm-5.3", None, api_key, True))

    assert exit_code == 0
    assert worker_phases == ["prepare", "resume"]
    assert worker_sessions[0] == worker_sessions[1]
    assert worker_state_dirs[0] == worker_state_dirs[1]
    report = args.output.read_text(encoding="utf-8")
    assert api_key not in report
    assert '"fresh_process_boundary": true' in report
    assert '"response_text"' not in report

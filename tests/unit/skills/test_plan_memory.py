import asyncio

from hostpilot.budget import RequestBudget
from hostpilot.contracts import BudgetContext, BudgetLimits, ContextSnapshot
from hostpilot.skills.model_plan import GovernedPlanTaskExecutor
from hostpilot.skills.orchestration import (
    BlackboardSnapshot,
    PlanExecutionType,
    PlanTask,
    PlanTaskCall,
)
from hostpilot.skills.runtime import (
    SkillExecutionRequest,
    SkillExecutionResult,
    SkillExecutionStatus,
)


class FakeMemoryController:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def enrich_context(self, context, *, query, max_chars=None):
        self.queries.append(query)
        return context.model_copy(update={"long_term_memory": "memory for this subtask"})


class FakeSkillRuntime:
    def __init__(self) -> None:
        self.request: SkillExecutionRequest | None = None

    async def execute(self, request: SkillExecutionRequest) -> SkillExecutionResult:
        self.request = request
        return SkillExecutionResult(SkillExecutionStatus.COMPLETED, output={"answer": "done"})


def test_plan_business_skill_receives_controller_recalled_subtask_context() -> None:
    async def scenario() -> None:
        context = ContextSnapshot(
            request_id="plan-request",
            conversation_id="conversation-1",
            long_term_memory="old entry memory",
        )
        request = SkillExecutionRequest(
            skill_id="builtin/orchestrator_plan",
            payload={"task_description": "Complete the larger request"},
            context=context,
            budget=RequestBudget.start(
                BudgetContext(
                    limits=BudgetLimits(
                        total_tokens=100,
                        tool_calls=4,
                        retrieval_calls=2,
                        timeout_seconds=10,
                    )
                )
            ),
        )
        task = PlanTask(
            task_id="review",
            description="Review the generated deployment plan",
            execution_type=PlanExecutionType.BUSINESS_SKILL_TASK,
            skill_id="business/plan-review",
            input={"payload": {"plan": "draft"}},
        )
        call = PlanTaskCall(
            request=request,
            task=task,
            dependencies={},
            blackboard=BlackboardSnapshot(()),
            idempotency_key="task-key",
        )
        memory = FakeMemoryController()
        skill_runtime = FakeSkillRuntime()
        executor = GovernedPlanTaskExecutor(
            provider=None,
            skills=skill_runtime,
            tool_provider=None,
            tool_descriptors={},
            memory_controller=memory,
        )

        result = await executor.execute_task(call)

        assert result.succeeded is True
        assert memory.queries == [task.description]
        assert skill_runtime.request is not None
        assert skill_runtime.request.context.long_term_memory == "memory for this subtask"
        assert request.context.long_term_memory == "old entry memory"

    asyncio.run(scenario())

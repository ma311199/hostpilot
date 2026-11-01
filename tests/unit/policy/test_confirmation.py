import pytest

from hostpilot.policy.confirmation import (
    ConfirmationDecision,
    ConfirmationEvent,
    ConfirmationGate,
    ConfirmationRequest,
    OperationDenied,
    ProposedOperation,
)


class FakeConfirmation:
    def __init__(self, decision: ConfirmationDecision) -> None:
        self.decision = decision
        self.requests: list[ConfirmationRequest] = []

    def confirm(self, request: ConfirmationRequest) -> ConfirmationDecision:
        self.requests.append(request)
        return self.decision


def action(requires_confirmation: bool = True) -> ProposedOperation:
    return ProposedOperation(
        tool_name="send_message",
        summary="Send the prepared message to Alex.",
        requires_confirmation=requires_confirmation,
    )


def test_policy_denial_stops_before_confirmation_or_execution() -> None:
    confirmation = FakeConfirmation(ConfirmationDecision.APPROVED)
    events: list[ConfirmationEvent] = []
    executed: list[ProposedOperation] = []

    with pytest.raises(OperationDenied, match="Policy Engine denied"):
        ConfirmationGate().execute(
            action(),
            authorize=lambda _operation: False,
            confirm=confirmation,
            run=executed.append,
            audit=events.append,
        )

    assert confirmation.requests == []
    assert executed == []
    assert events[0].decision == "policy_denied"


@pytest.mark.parametrize(
    "decision",
    [
        ConfirmationDecision.REJECTED,
        ConfirmationDecision.CANCELLED,
        ConfirmationDecision.TIMED_OUT,
    ],
)
def test_denial_cancel_or_timeout_stops_execution(decision: ConfirmationDecision) -> None:
    confirmation = FakeConfirmation(decision)
    events: list[ConfirmationEvent] = []
    executed: list[ProposedOperation] = []

    with pytest.raises(OperationDenied):
        ConfirmationGate().execute(
            action(),
            authorize=lambda _operation: True,
            confirm=confirmation,
            run=executed.append,
            audit=events.append,
        )

    assert executed == []
    assert events[0].decision == decision.value


def test_approved_high_impact_operation_runs_after_confirmation() -> None:
    confirmation = FakeConfirmation(ConfirmationDecision.APPROVED)
    events: list[ConfirmationEvent] = []
    order: list[str] = []

    result = ConfirmationGate().execute(
        action(),
        authorize=lambda _operation: order.append("authorized") is None,
        confirm=confirmation,
        run=lambda _operation: order.append("executed") or "done",
        audit=events.append,
    )

    assert result == "done"
    assert order == ["authorized", "executed"]
    assert len(confirmation.requests) == 1
    assert events[0].decision == "approved"


def test_low_impact_operation_skips_confirmation_but_still_runs_policy() -> None:
    confirmation = FakeConfirmation(ConfirmationDecision.REJECTED)
    events: list[ConfirmationEvent] = []
    result = ConfirmationGate().execute(
        action(requires_confirmation=False),
        authorize=lambda _operation: True,
        confirm=confirmation,
        run=lambda _operation: "done",
        audit=events.append,
    )

    assert result == "done"
    assert confirmation.requests == []

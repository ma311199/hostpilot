"""Run a live chat, restart, and session-isolation smoke check using env settings."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

from hostpilot.api import create_app
from hostpilot.config import load_settings


def verify_model_chat() -> None:
    """Verify real model responses without using the user's persistent data directory."""
    settings = load_settings()
    if not settings.model_name or not settings.model_api_key:
        raise SystemExit(
            "Set HOSTPILOT_MODEL_NAME and HOSTPILOT_MODEL_API_KEY in this terminal first."
        )

    with TemporaryDirectory(prefix="hostpilot-model-check-") as temporary_root:
        root = Path(temporary_root)
        isolated_settings = settings.model_copy(
            update={"data_dir": root / "data", "memory_dir": root / "memory"}
        )

        with TestClient(create_app(isolated_settings)) as client:
            first_conversation = _create_conversation(client)
            first_reply = _send_message(
                client,
                first_conversation,
                "Reply with a short greeting and mention that this is a model connectivity check.",
            )
            if not first_reply:
                raise RuntimeError("The model returned an empty assistant response.")

            second_conversation = _create_conversation(client)
            second_history = client.get(f"/api/v1/conversations/{second_conversation}/messages")
            second_history.raise_for_status()
            if second_history.json()["messages"]:
                raise RuntimeError("A new conversation unexpectedly contains prior messages.")

        with TestClient(create_app(isolated_settings)) as restarted_client:
            restored_history = restarted_client.get(
                f"/api/v1/conversations/{first_conversation}/messages"
            )
            restored_history.raise_for_status()
            restored_messages = restored_history.json()["messages"]
            if len(restored_messages) != 2:
                raise RuntimeError("Conversation history did not survive the app restart.")

            follow_up = _send_message(
                restarted_client,
                first_conversation,
                "Reply briefly that the follow-up after restart succeeded.",
            )
            if not follow_up:
                raise RuntimeError("The model returned an empty follow-up response.")

        print(f"Initial model response: {first_reply}")
        print(f"Follow-up response after restart: {follow_up}")
        print("Conversation persistence and session isolation: passed")


def _create_conversation(client: TestClient) -> str:
    response = client.post("/api/v1/conversations", json={"title": "Model smoke check"})
    response.raise_for_status()
    return response.json()["conversation_id"]


def _send_message(client: TestClient, conversation_id: str, message: str) -> str:
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"message": message},
    )
    response.raise_for_status()
    return response.json()["assistant_message"]


if __name__ == "__main__":
    verify_model_chat()

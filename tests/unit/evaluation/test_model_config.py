import pytest

from hostpilot.evaluation.model_config import model_configuration_from_environment


def test_model_name_only_uses_active_url_and_prompts_for_key(monkeypatch) -> None:
    monkeypatch.setenv("HOSTPILOT_MODEL_NAME", "gpt-6-luna")
    monkeypatch.delenv("HOSTPILOT_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("HOSTPILOT_MODEL_API_KEY", raising=False)
    monkeypatch.delenv("HOSTPILOT_MODEL_STREAMING", raising=False)
    prompted = []

    def read_hidden_key(prompt: str) -> str:
        prompted.append(prompt)
        return "local-secret"

    import hostpilot.evaluation.model_config as config

    monkeypatch.setattr(config.getpass, "getpass", read_hidden_key)

    result = model_configuration_from_environment(
        {"model_name": "app-model", "base_url": "https://proxy.example/v1", "streaming": False},
        interactive=True,
    )

    assert result == ("gpt-6-luna", "https://proxy.example/v1", "local-secret", True)
    assert prompted == ["Model API key (input hidden): "]


def test_model_configuration_uses_environment_key_and_streaming_flag(monkeypatch) -> None:
    monkeypatch.setenv("HOSTPILOT_MODEL_NAME", "gpt-6-luna")
    monkeypatch.setenv("HOSTPILOT_MODEL_BASE_URL", "https://proxy.example/v1")
    monkeypatch.setenv("HOSTPILOT_MODEL_API_KEY", "env-secret")
    monkeypatch.setenv("HOSTPILOT_MODEL_STREAMING", "false")

    result = model_configuration_from_environment({}, interactive=False)

    assert result == ("gpt-6-luna", "https://proxy.example/v1", "env-secret", False)


def test_model_configuration_ignores_empty_variables_and_requires_model(monkeypatch) -> None:
    monkeypatch.setenv("HOSTPILOT_MODEL_NAME", "")
    monkeypatch.setenv("HOSTPILOT_MODEL_BASE_URL", "")
    monkeypatch.setenv("HOSTPILOT_MODEL_API_KEY", "")
    monkeypatch.setenv("HOSTPILOT_MODEL_STREAMING", "")

    with pytest.raises(SystemExit, match="No model is configured"):
        model_configuration_from_environment({}, interactive=False)


def test_model_configuration_rejects_invalid_streaming_value(monkeypatch) -> None:
    monkeypatch.setenv("HOSTPILOT_MODEL_NAME", "gpt-6-luna")
    monkeypatch.setenv("HOSTPILOT_MODEL_API_KEY", "env-secret")
    monkeypatch.setenv("HOSTPILOT_MODEL_STREAMING", "sometimes")

    with pytest.raises(SystemExit, match="HOSTPILOT_MODEL_STREAMING must be a boolean"):
        model_configuration_from_environment({}, interactive=False)

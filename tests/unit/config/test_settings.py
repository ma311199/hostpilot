from pathlib import Path

import pytest
from pydantic import ValidationError

from hostpilot.config import Environment, RemoteMCPServerSettings, Settings, load_settings


def test_settings_load_with_prefix_and_validate_values(monkeypatch) -> None:
    monkeypatch.setenv("HOSTPILOT_ENVIRONMENT", "test")
    monkeypatch.setenv("HOSTPILOT_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("HOSTPILOT_SKILLS_DIR", "/tmp/hostpilot-skills")

    settings = load_settings()

    assert settings.environment is Environment.TEST
    assert settings.log_level == "DEBUG"
    assert settings.skills_dir == Path("/tmp/hostpilot-skills")


def test_settings_have_safe_non_secret_defaults(monkeypatch) -> None:
    monkeypatch.delenv("HOSTPILOT_ENVIRONMENT", raising=False)
    monkeypatch.delenv("HOSTPILOT_LOG_LEVEL", raising=False)
    monkeypatch.delenv("HOSTPILOT_SKILLS_DIR", raising=False)

    settings = Settings()

    assert settings.environment is Environment.DEVELOPMENT
    assert settings.log_level == "INFO"
    assert settings.skills_dir is None


def test_api_settings_allow_only_loopback_and_parse_web_origins(monkeypatch) -> None:
    monkeypatch.setenv("HOSTPILOT_WEB_ORIGINS", '["http://127.0.0.1:5173"]')
    settings = load_settings()

    assert settings.api_host == "127.0.0.1"
    assert settings.web_origins == ("http://127.0.0.1:5173",)

    with pytest.raises(ValidationError):
        Settings(api_host="0.0.0.0")


def test_model_settings_require_a_name_and_keep_api_key_secret() -> None:
    with pytest.raises(ValidationError, match="HOSTPILOT_MODEL_NAME"):
        Settings(model_api_key="secret")

    settings = Settings(model_name="example-model", model_api_key="secret")
    assert settings.model_name == "example-model"
    assert "secret" not in repr(settings)
    assert settings.model_api_key is not None
    assert settings.model_api_key.get_secret_value() == "secret"


def test_model_base_url_requires_a_model_name() -> None:
    with pytest.raises(ValidationError, match="HOSTPILOT_MODEL_NAME"):
        Settings(model_base_url="http://localhost:8001/v1")


def test_rag_key_uses_the_unprefixed_secret_environment_variable(monkeypatch) -> None:
    monkeypatch.setenv("RAG_BASE_URL", "https://rag.example/")
    monkeypatch.setenv("RAG_USER_ID", "user-1")
    monkeypatch.setenv("RAG_KEY", "rag-secret")
    settings = Settings()

    assert settings.rag_key is not None
    assert settings.rag_key.get_secret_value() == "rag-secret"
    assert "rag-secret" not in repr(settings)


def test_rag_configuration_requires_base_url_user_id_and_key_together() -> None:
    with pytest.raises(ValidationError, match="RAG_BASE_URL, RAG_USER_ID, and RAG_KEY"):
        Settings(rag_base_url="https://rag.example/", rag_user_id="user-1")


def test_remote_mcp_server_configuration_uses_secret_headers_and_unique_names(monkeypatch) -> None:
    monkeypatch.setenv(
        "HOSTPILOT_MCP_SERVERS",
        '[{"name":"knowledge","url":"https://mcp.example/mcp",'
        '"headers":{"Authorization":"Bearer private-token"},"timeout_seconds":8}]',
    )

    settings = load_settings()

    assert len(settings.mcp_servers) == 1
    assert settings.mcp_servers[0].name == "knowledge"
    assert settings.mcp_servers[0].headers["Authorization"].get_secret_value() == (
        "Bearer private-token"
    )
    assert "private-token" not in repr(settings)

    with pytest.raises(ValidationError, match="names must be unique"):
        Settings(
            mcp_servers=(
                RemoteMCPServerSettings(name="knowledge", url="https://one.example/mcp"),
                RemoteMCPServerSettings(name="knowledge", url="https://two.example/mcp"),
            )
        )


@pytest.mark.parametrize(
    "url",
    (
        "file:///tmp/mcp",
        "https://user:password@mcp.example/mcp",
        "https://mcp.example/mcp#fragment",
    ),
)
def test_remote_mcp_server_rejects_unsafe_endpoint_urls(url: str) -> None:
    with pytest.raises(ValidationError):
        RemoteMCPServerSettings(name="knowledge", url=url)

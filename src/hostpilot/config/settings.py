"""Validated non-secret application settings loaded from the environment."""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Supported runtime environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class RemoteMCPServerSettings(BaseModel):
    """Connection settings for one trusted, remotely hosted MCP server."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    url: str = Field(min_length=1, max_length=2048)
    headers: dict[str, SecretStr] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=15, gt=0, le=120)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        """Accept an HTTP(S) MCP endpoint without embedded credentials."""
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("MCP server URL must be an absolute HTTP or HTTPS URL.")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("MCP server credentials must be supplied through headers.")
        if parsed.fragment:
            raise ValueError("MCP server URL must not contain a fragment.")
        return value

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, value: dict[str, SecretStr]) -> dict[str, SecretStr]:
        """Reject malformed or duplicate HTTP header names and line breaks."""
        seen: set[str] = set()
        for name, secret in value.items():
            normalized = name.strip().lower()
            if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name):
                raise ValueError("MCP server header names must be valid HTTP header names.")
            if normalized in seen:
                raise ValueError("MCP server header names must be unique case-insensitively.")
            if "\r" in secret.get_secret_value() or "\n" in secret.get_secret_value():
                raise ValueError("MCP server header values must not contain line breaks.")
            seen.add(normalized)
        return value


class Settings(BaseSettings):
    """Application settings with safe defaults and HOSTPILOT_ env prefix."""

    model_config = SettingsConfigDict(
        env_prefix="HOSTPILOT_",
        env_file=None,
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "HostPilot"
    environment: Environment = Environment.DEVELOPMENT
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    skills_dir: Path | None = None
    data_dir: Path = Path.home() / ".hostpilot" / "data"
    memory_dir: Path = Path.home() / ".hostpilot" / "memory"
    api_host: Literal["127.0.0.1", "localhost", "::1"] = "127.0.0.1"
    api_port: int = Field(default=8000, gt=0, le=65535)
    web_origins: tuple[str, ...] = ()
    max_context_messages: int = Field(default=40, gt=0, le=500)
    max_memory_context_chars: int = Field(default=16000, gt=0, le=100000)
    max_request_chars: int = Field(default=32000, gt=0, le=100000)
    max_response_chars: int = Field(default=100000, gt=0, le=200000)
    request_token_budget: int = Field(default=20000, gt=0)
    request_tool_call_budget: int = Field(default=5, ge=0)
    request_retrieval_call_budget: int = Field(default=2, ge=0)
    request_timeout_seconds: float = Field(default=60, gt=0, le=600)
    model_name: str | None = Field(default=None, min_length=1)
    model_api_key: SecretStr | None = Field(default=None, min_length=1)
    model_base_url: str | None = Field(default=None, min_length=1)
    rag_base_url: str | None = Field(default=None, validation_alias="RAG_BASE_URL")
    rag_user_id: str | None = Field(default=None, min_length=1, validation_alias="RAG_USER_ID")
    rag_retrieval_path: str = Field(default="/retrieve", validation_alias="RAG_RETRIEVAL_PATH")
    rag_key: SecretStr | None = Field(default=None, min_length=1, validation_alias="RAG_KEY")
    rag_timeout_seconds: float = Field(
        default=15, gt=0, le=120, validation_alias="RAG_TIMEOUT_SECONDS"
    )
    mcp_servers: tuple[RemoteMCPServerSettings, ...] = Field(default=())

    @model_validator(mode="after")
    def validate_model_configuration(self) -> Settings:
        """Require complete credentials for each configured external service."""
        if self.model_api_key and not self.model_name:
            raise ValueError("HOSTPILOT_MODEL_NAME is required when a model API key is set.")
        if self.model_base_url and not self.model_name:
            raise ValueError("HOSTPILOT_MODEL_NAME is required when a model base URL is set.")
        if self.model_name and not self.model_api_key:
            raise ValueError("HOSTPILOT_MODEL_API_KEY is required when a model is configured.")
        rag_configuration = (self.rag_base_url, self.rag_user_id, self.rag_key)
        if any(rag_configuration) and not all(rag_configuration):
            raise ValueError("RAG_BASE_URL, RAG_USER_ID, and RAG_KEY must be configured together.")
        server_names = [server.name for server in self.mcp_servers]
        if len(server_names) != len(set(server_names)):
            raise ValueError("MCP server names must be unique.")
        return self


def load_settings() -> Settings:
    """Load settings from the process environment."""
    return Settings()


__all__ = ["Environment", "RemoteMCPServerSettings", "Settings", "load_settings"]

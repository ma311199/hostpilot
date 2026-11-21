"""Validated non-secret application settings loaded from the environment."""

from __future__ import annotations

import re
from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path, PurePath
from typing import Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_CREDENTIAL_ARGUMENT_NAME = re.compile(
    r"api[-_.]?key|access[-_.]?token|refresh[-_.]?token|auth(?:orization)?"
    r"|password|passwd|secret|credential|token|private[-_.]?key|client[-_.]?secret",
    re.IGNORECASE,
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?:^|[?&;=\s])(?:api[-_.]?key|access[-_.]?token|refresh[-_.]?token|"
    r"authorization|auth[-_.]?token|password|passwd|secret|credential|token|"
    r"private[-_.]?key|client[-_.]?secret)\s*[:=]\s*\S+",
    re.IGNORECASE,
)


def contains_credential_argument(arguments: Sequence[str]) -> bool:
    """Detect common credential-shaped CLI options and assignments."""
    for argument in arguments:
        option = re.fullmatch(r"--?([A-Za-z0-9_.-]+)(?:=(.*))?", argument)
        if option is not None and _CREDENTIAL_ARGUMENT_NAME.search(option.group(1)):
            return True
        if _CREDENTIAL_ASSIGNMENT.search(argument):
            return True
        for assignment in re.finditer(
            r"(?:^|[?&;\s])([A-Za-z_][A-Za-z0-9_.-]*)\s*=\s*\S+", argument
        ):
            if _CREDENTIAL_ARGUMENT_NAME.search(assignment.group(1)):
                return True
    return False


class Environment(StrEnum):
    """Supported runtime environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class RemoteMCPServerSettings(BaseModel):
    """Connection settings for one HTTP or local STDIO MCP server."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    transport: Literal["streamable_http", "stdio"] = "streamable_http"
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    command: str | None = Field(default=None, min_length=1, max_length=512)
    args: tuple[str, ...] = Field(default=(), max_length=128)
    cwd: Path | None = None
    headers: dict[str, SecretStr] = Field(default_factory=dict)
    env: dict[str, SecretStr] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=15, gt=0, le=120)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        """Accept HTTP(S) endpoints without embedded credentials."""
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("MCP server URL must be an absolute HTTP or HTTPS URL.")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("MCP server credentials must be supplied through headers.")
        if parsed.fragment:
            raise ValueError("MCP server URL must not contain a fragment.")
        sensitive_query_names = {
            "api_key",
            "apikey",
            "authorization",
            "access_token",
            "token",
            "secret",
            "password",
        }
        if any(name.lower() in sensitive_query_names for name, _ in parse_qsl(parsed.query)):
            raise ValueError("MCP server credentials must not be embedded in the URL query.")
        return value

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: str | None) -> str | None:
        """Allow a direct executable name or path; transports never invoke a shell."""
        if value is None:
            return None
        if "\x00" in value or "\n" in value or "\r" in value:
            raise ValueError("STDIO command must be a single executable path.")
        executable = PurePath(value).name.lower()
        allowed_executables = {
            "npx",
            "npx.cmd",
            "node",
            "node.exe",
            "uvx",
            "uvx.exe",
            "python",
            "python.exe",
            "python3",
            "python3.exe",
            "bun",
            "bun.exe",
            "deno",
            "deno.exe",
        }
        if executable not in allowed_executables and not re.fullmatch(
            r"python3(?:\.\d+)?(?:\.exe)?", executable
        ):
            raise ValueError("STDIO command is not in the supported executable allowlist.")
        return value

    @model_validator(mode="after")
    def validate_transport_configuration(self) -> RemoteMCPServerSettings:
        """Require exactly the endpoint or process fields for the selected transport."""
        if self.transport == "streamable_http":
            if self.url is None or self.command is not None:
                raise ValueError("Streamable HTTP servers require url and cannot set command.")
            if self.env:
                raise ValueError("HTTP server credentials must use headers, not process env.")
        else:
            if self.command is None or self.url is not None:
                raise ValueError("STDIO servers require command and cannot set url.")
            if self.headers:
                raise ValueError("STDIO server credentials must use process environment.")
            if any("\x00" in argument for argument in self.args):
                raise ValueError("STDIO arguments must not contain null characters.")
        return self

    @field_validator("args")
    @classmethod
    def reject_credentials_in_arguments(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if contains_credential_argument(value):
            raise ValueError("Credentials must be supplied through the API key field, not args.")
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
        env_ignore_empty=True,
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
    plan_max_tasks: int = Field(default=10, gt=0, le=100)
    plan_tool_agent_max_turns: int = Field(default=5, gt=0, le=32)
    plan_tool_candidate_limit: int = Field(default=5, gt=0, le=20)
    routing_minimum_business_confidence: float = Field(default=0.85, ge=0.5, le=1)
    routing_minimum_business_margin: float = Field(default=0.30, ge=0, le=1)
    routing_minimum_plan_candidate_confidence: float = Field(default=0.50, ge=0.5, le=1)
    routing_max_plan_candidates: int = Field(default=5, gt=0, le=20)
    routing_max_business_candidates: int = Field(default=32, gt=0, le=100)
    chat_runtime_plugin: str = Field(
        default="builtin.runtime-chat", pattern=r"^[a-z][a-z0-9_.-]{0,63}$"
    )
    plan_planner_plugin: str = Field(default="builtin.llm", pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    plan_validator_plugin: str = Field(
        default="builtin.runtime", pattern=r"^[a-z][a-z0-9_.-]{0,63}$"
    )
    plan_task_executor_plugin: str = Field(
        default="builtin.governed", pattern=r"^[a-z][a-z0-9_.-]{0,63}$"
    )
    plan_verifier_plugin: str = Field(default="builtin.llm", pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    plan_replanner_plugin: str = Field(default="builtin.llm", pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    model_name: str | None = Field(default=None, min_length=1)
    model_provider_plugin: str = Field(
        default="builtin.openai-compatible", pattern=r"^[a-z][a-z0-9_.-]{0,63}$"
    )
    model_api_key: SecretStr | None = Field(default=None, min_length=1)
    model_base_url: str | None = Field(default=None, min_length=1)
    model_streaming: bool = True
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

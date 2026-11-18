"""Bounded validation helpers for untrusted MCP input schemas."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from jsonschema import Draft202012Validator

_MAX_SCHEMA_NODES = 512
_MAX_SCHEMA_DEPTH = 16


def json_schema_argument_validator(
    schema: Any,
) -> Callable[[Mapping[str, Any]], Mapping[str, Any]]:
    """Compile a bounded object schema into a ToolRegistration argument validator."""
    if not isinstance(schema, Mapping):
        raise ValueError("MCP tool input schema must be a JSON object.")
    _check_schema_bounds(schema)
    Draft202012Validator.check_schema(dict(schema))
    _check_local_references(schema, schema)
    validator = Draft202012Validator(dict(schema))

    def validate(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(arguments, Mapping) or any(not isinstance(key, str) for key in arguments):
            raise ValueError("Tool arguments must be an object with string keys.")
        normalized = dict(arguments)
        validator.validate(normalized)
        return normalized

    return validate


def _check_schema_bounds(schema: Any) -> None:
    nodes = 0
    pending: list[tuple[Any, int]] = [(schema, 0)]
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if nodes > _MAX_SCHEMA_NODES:
            raise ValueError("MCP tool input schema exceeds the node limit.")
        if depth > _MAX_SCHEMA_DEPTH:
            raise ValueError("MCP tool input schema exceeds the nesting limit.")
        if isinstance(value, Mapping):
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value)


def _check_local_references(schema: Any, root: Mapping[str, Any]) -> None:
    if isinstance(schema, Mapping):
        if "$dynamicRef" in schema or "$recursiveRef" in schema:
            raise ValueError("Dynamic MCP tool input schema references are not supported.")
        reference = schema.get("$ref")
        if reference is not None:
            if not isinstance(reference, str) or not (
                reference == "#" or reference.startswith("#/")
            ):
                raise ValueError("MCP tool input schema references must be local JSON Pointers.")
            _resolve_pointer(root, reference)
        for item in schema.values():
            _check_local_references(item, root)
    elif isinstance(schema, list):
        for item in schema:
            _check_local_references(item, root)


def _resolve_pointer(root: Mapping[str, Any], reference: str) -> Any:
    if reference == "#":
        return root
    current: Any = root
    for encoded in reference[2:].split("/"):
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdecimal() and int(token) < len(current):
            current = current[int(token)]
        else:
            raise ValueError("MCP tool input schema contains an unresolved local reference.")
    return current


__all__ = ["json_schema_argument_validator"]

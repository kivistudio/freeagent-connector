"""Shared helpers for tool handlers.

Logic here is re-expressed in Python from the ideas in samaxbytez/freeagent-mcp's
`utils.ts` (ID validation, param building) — no code is copied. `build_body` is new to
this project.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel

logger = logging.getLogger("freeagent_mcp.tools")

_SAFE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")

# Strings longer than this are truncated in logs — long values are the ones most likely
# to carry a secret or personal data we have no reason to persist.
_LOG_TRUNCATE_OVER = 100
_LOG_TRUNCATE_TO = 20


def safe_id(value: str) -> str:
    """Validate a FreeAgent ID that will be interpolated into a request path.

    This is the first of two defences against path injection. Every ID reaching a URL
    path goes through here. The second defence is the origin
    check in `client.py`, which catches anything that gets past this one.

    Raises:
        ValueError: if the ID contains anything outside `[a-zA-Z0-9_-]`.
    """
    if not _SAFE_ID_PATTERN.match(value):
        raise ValueError(
            f"ID must contain only letters, digits, hyphens and underscores; got {value!r}"
        )
    return value


# The form tool signatures should use, so FastMCP surfaces a field-level validation
# error the model can recover from rather than an opaque exception.
SafeId = Annotated[str, AfterValidator(safe_id)]


def _query_value(value: Any) -> str:
    """Render a value the way FreeAgent's query string expects it.

    Booleans need care: `str(True)` is `"True"`, which FreeAgent rejects.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def build_params(**kwargs: Any) -> dict[str, str]:
    """Build a query-string mapping, dropping unset values and stringifying the rest."""
    return {k: _query_value(v) for k, v in kwargs.items() if v is not None}


def _body_value(value: Any) -> Any:
    """Convert Pydantic models to plain data, leaving everything else untouched."""
    if isinstance(value, BaseModel):
        return value.model_dump(exclude_none=True)
    if isinstance(value, list):
        return [_body_value(item) for item in value]
    if isinstance(value, dict):
        return {k: _body_value(v) for k, v in value.items()}
    return value


def build_body(**kwargs: Any) -> dict[str, Any]:
    """Build a JSON request body, dropping unset values but preserving native types.

    Unlike `build_params`, values are NOT stringified — FreeAgent's JSON bodies expect
    real numbers and booleans. Pydantic models (line items) become plain dicts.
    """
    return {k: _body_value(v) for k, v in kwargs.items() if v is not None}


def _redact(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _LOG_TRUNCATE_OVER:
        return value[:_LOG_TRUNCATE_TO] + "..."
    return value


def log_tool_call(tool: str, params: dict[str, Any] | None = None) -> None:
    """Record that a tool was invoked, with long values truncated.

    Never logs the bearer token — it is not a tool parameter and must not be passed here.
    """
    record: dict[str, Any] = {"tool": tool}
    if params:
        record["params"] = {k: _redact(v) for k, v in params.items()}
    logger.info(json.dumps(record, default=str))

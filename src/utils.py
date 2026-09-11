"""Shared helpers for tool handlers.

Logic here is re-expressed in Python from the ideas in samaxbytez/freeagent-mcp's
`utils.ts` (ID validation, param building) — no code is copied. `build_body` is new to
this project.
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel

_SAFE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


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


# A `str` subtype that runs `safe_id` automatically during validation. `Annotated[str, x]`
# tags a plain `str` with metadata `x`; Pydantic (the library FastMCP uses to parse tool
# arguments) reads that metadata, and `AfterValidator` tells it to pass the value through
# `safe_id` once it has confirmed the value is a string.
#
# Declare a tool parameter as `SafeId` instead of `str` and the check runs at the schema
# boundary, before the handler body. A bad ID then comes back as a field-level validation
# error the model can read and retry, rather than an exception raised mid-handler.
SafeId = Annotated[str, AfterValidator(safe_id)]


def build_params(**kwargs: Any) -> dict[str, Any]:
    """Build a query-string mapping, dropping unset (None) values.

    Values keep their native types; httpx renders them when it builds the URL.
    """
    return {k: v for k, v in kwargs.items() if v is not None}


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

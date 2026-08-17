"""Helpers shared by every tool module."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from fastmcp.exceptions import ToolError

from src.client import FreeAgentApiError, UnsafePathError


@contextlib.contextmanager
def freeagent_errors() -> Iterator[None]:
    """Translate client-layer failures into ToolError.

    ToolError's message always reaches the client, whereas an unguarded exception may be
    masked — so a model gets something it can actually act on. `FreeAgentApiError`'s
    message is already scrubbed of unparseable upstream bodies (see client.py), and no
    branch here has access to the bearer token.
    """
    try:
        yield
    except UnsafePathError as exc:
        # Should be unreachable: safe_id validates IDs before they reach a path. If it
        # does fire, it is a genuine security event and must not look like a normal 404.
        raise ToolError(f"Request blocked by the path safety guard: {exc}") from exc
    except FreeAgentApiError as exc:
        raise ToolError(str(exc)) from exc

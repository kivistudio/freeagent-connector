"""The application's shared logger.

A single named logger for the whole app, so callers configure and filter one name.
Consumed by `src/auth.py` (token-verification diagnostics) and by the tool-call logging
middleware wired in `src/server.py`.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("freeagent_mcp")

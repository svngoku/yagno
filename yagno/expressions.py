"""Reference resolver for ${...} expressions in YAML specs.

Supports:
  ${env.DATABASE_URL}        — environment variable
  ${input.topic}             — from workflow input payload
  ${session_state.last_run}  — from persisted session state
  ${previous.step_id}        — output from a previous step
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

_logger = logging.getLogger(__name__)

_ALLOWED_ENV_VARS: set[str] = {
    "DATABASE_URL",
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "TAVILY_API_KEY",
    "DAYTONA_API_KEY",
    "DAYTONA_TARGET",
    "EDGAR_USER_AGENT",
}

# Extend via env var (comma-separated)
_extra_env = os.environ.get("YAGNO_ALLOWED_ENV_VARS", "")
if _extra_env:
    _ALLOWED_ENV_VARS.update(v.strip() for v in _extra_env.split(",") if v.strip())

_REF_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _resolve_single(expr: str, context: dict[str, Any]) -> Any:
    """Resolve a single ${...} expression."""
    if expr.startswith("env:") or expr.startswith("env."):
        env_key = expr[4:]
        if env_key not in _ALLOWED_ENV_VARS:
            raise ValueError(
                f"Environment variable '{env_key}' not in allowlist. "
                f"Allowed: {sorted(_ALLOWED_ENV_VARS)}. "
                f"Set YAGNO_ALLOWED_ENV_VARS to extend."
            )
        value = os.getenv(env_key)
        if value is None:
            _logger.warning(
                "Environment variable '%s' is in allowlist but not set — resolving to empty string.",
                env_key,
            )
            return ""
        return value

    parts = expr.split(".")
    cur: Any = context
    for part in parts:
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def resolve_refs(obj: Any, context: dict[str, Any]) -> Any:
    """Recursively resolve ${...} references in a nested structure.

    If the entire string is a single reference like "${input.topic}",
    the resolved value is returned as-is (preserving type).
    If the string contains embedded references like "Hello ${input.name}!",
    they are interpolated as strings.
    """
    if isinstance(obj, str):
        # Full-match: return the resolved value directly (preserves type)
        full_match = re.fullmatch(r"\$\{([^}]+)\}", obj.strip())
        if full_match:
            return _resolve_single(full_match.group(1), context)

        # Partial interpolation: substitute all ${...} within the string
        def replacer(m: re.Match) -> str:
            val = _resolve_single(m.group(1), context)
            return str(val) if val is not None else ""

        return _REF_PATTERN.sub(replacer, obj)

    if isinstance(obj, dict):
        return {k: resolve_refs(v, context) for k, v in obj.items()}

    if isinstance(obj, list):
        return [resolve_refs(item, context) for item in obj]

    return obj

"""Dynamic import and prompt file loading utilities.

SECURITY NOTE
─────────────
``import_from_string`` will import and execute any dotted Python path.  If
your YAML spec files come from untrusted sources, an attacker can reference
arbitrary modules (e.g. ``os.system``).  Only load specs you trust, or add
an allowlist of permitted top-level packages below.
"""

from __future__ import annotations

import logging
import os
from importlib import import_module
from pathlib import Path
from typing import Any

logger = logging.getLogger("yagno.registry")

# Restrict importable packages to a known set.  Extend via the
# YAGNO_ALLOWED_PACKAGES env var (comma-separated).
_ALLOWED_PACKAGES: set[str] = {"yagno", "agno"}

_extra = os.environ.get("YAGNO_ALLOWED_PACKAGES", "")
if _extra:
    _ALLOWED_PACKAGES.update(pkg.strip() for pkg in _extra.split(",") if pkg.strip())


def import_from_string(dotted_path: str) -> Any:
    """Import an object from a dotted path like 'yagno.tools.web.web_search'.

    Raises ``ImportError`` with a descriptive message on failure.
    """
    try:
        module_path, attr_name = dotted_path.rsplit(".", 1)
    except ValueError:
        raise ImportError(
            f"Cannot import '{dotted_path}': expected a dotted path like 'pkg.module.func'"
        )

    # Allowlist check — always enforced
    top_level = dotted_path.split(".")[0]
    if top_level not in _ALLOWED_PACKAGES:
        raise ImportError(
            f"Import of '{dotted_path}' blocked: package '{top_level}' not in "
            f"allowlist {_ALLOWED_PACKAGES}. Set YAGNO_ALLOWED_PACKAGES env var to extend."
        )

    try:
        module = import_module(module_path)
    except ModuleNotFoundError as exc:
        raise ImportError(
            f"Cannot import module '{module_path}' (from '{dotted_path}'): {exc}"
        ) from exc

    try:
        return getattr(module, attr_name)
    except AttributeError:
        raise ImportError(
            f"Module '{module_path}' has no attribute '{attr_name}' "
            f"(from '{dotted_path}')"
        )


def load_prompt_file(path: str | None, base_dir: Path | None = None) -> list[str]:
    """Load a prompt from a file, with path traversal protection.

    Args:
        path: Path to the prompt file (absolute or relative).
        base_dir: Base directory to restrict file access to. Defaults to cwd.

    Raises:
        ValueError: If the resolved path escapes the base directory.
    """
    if not path:
        return []
    allowed = (base_dir or Path.cwd()).resolve()
    p = Path(path)
    if not p.is_absolute():
        p = allowed / p
    p = p.resolve()
    if not p.is_relative_to(allowed):
        raise ValueError(
            f"Prompt file '{path}' resolves to '{p}' which is outside "
            f"the allowed directory '{allowed}'. Path traversal is not permitted."
        )
    if not p.exists():
        logger.warning("Prompt file '%s' not found — skipping.", path)
        return []
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        return []
    return [text]

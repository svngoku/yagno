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
from importlib import import_module
from pathlib import Path
from typing import Any

logger = logging.getLogger("yagno.registry")

# Optional: restrict importable packages to a known set.  Set to ``None`` to
# disable the allowlist (current behaviour).  Uncomment and populate to harden.
# _ALLOWED_PACKAGES: set[str] | None = {"yagno", "agno"}
_ALLOWED_PACKAGES: set[str] | None = None


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

    # Optional allowlist check
    if _ALLOWED_PACKAGES is not None:
        top_package = module_path.split(".")[0]
        if top_package not in _ALLOWED_PACKAGES:
            raise ImportError(
                f"Import of '{dotted_path}' blocked: package '{top_package}' "
                f"is not in the allowed set {_ALLOWED_PACKAGES}"
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


def load_prompt_file(path: str | None) -> list[str]:
    """Read a markdown/text file and return its content as a single-element instruction list."""
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        logger.warning("Prompt file '%s' not found — skipping.", path)
        return []
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        return []
    return [text]

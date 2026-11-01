"""API key lookup and project paths. The key is never printed or written anywhere."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# Keys from the international platform (platform.tripo3d.ai) authenticate against the .ai host.
# The docs at developers.tripo3d.com show openapi.tripo3d.com, which rejected this key (2026-10-18).
DEFAULT_API_BASE = "https://openapi.tripo3d.ai/v3"
KEY_NAME = "TRIPO_API_KEY"


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def env_files() -> list[Path]:
    """Project .env first, then an optional shared .env one level up."""
    return [PROJECT_ROOT / ".env", PROJECT_ROOT.parent / ".env"]


def api_key() -> str:
    if os.environ.get(KEY_NAME):
        return os.environ[KEY_NAME]
    for path in env_files():
        value = _read_env_file(path).get(KEY_NAME)
        if value:
            return value
    raise SystemExit(
        f"{KEY_NAME} not found. Set it in the environment or in one of: "
        + ", ".join(str(p) for p in env_files())
    )


def api_base() -> str:
    return os.environ.get("TRIPO_API_BASE", DEFAULT_API_BASE).rstrip("/")

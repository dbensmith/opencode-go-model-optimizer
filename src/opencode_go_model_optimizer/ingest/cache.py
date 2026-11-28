"""Shared on-disk cache helpers."""

import json
import os
from pathlib import Path
from typing import Any

from opencode_go_model_optimizer.ingest.http import atomic_write_text


def get_cache_dir() -> Path:
    """Return the application cache directory, honoring ``XDG_CACHE_HOME``."""
    base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return base / "opencode-go-model-optimizer"


def read_json_object(path: Path) -> dict[str, Any] | None:
    """Read a JSON object, returning ``None`` for missing or invalid files."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def write_cache_text(path: Path, content: str) -> Path | None:
    """Atomically write cache content, returning ``None`` on filesystem errors."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, content)
    except OSError:
        return None
    return path

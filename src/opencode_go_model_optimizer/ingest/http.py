"""Shared HTTP and cache helpers for ingest modules."""

import contextlib
import json
import os
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel


def package_version() -> str:
    """Return the installed package version, or a placeholder when not installed."""
    try:
        return version("opencode-go-model-optimizer")
    except PackageNotFoundError:
        return "0+unknown"


USER_AGENT = f"opencode-go-model-optimizer/{package_version()}"


@contextmanager
def client_scope(client: httpx.Client | None) -> Iterator[httpx.Client]:
    """Yield the given client, or a new default client that is closed on exit."""
    if client is not None:
        yield client
        return
    with httpx.Client(timeout=10.0, headers={"User-Agent": USER_AGENT}) as owned:
        yield owned


def _default_file_mode() -> int:
    """Return 0o666 masked by the process umask without mutating process state when possible."""
    with contextlib.suppress(OSError, ValueError):
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("Umask:"):
                return 0o666 & ~int(line.split()[1], 8)
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask


def atomic_write_text(path: Path, content: str) -> None:
    """Write text to a temp file in the same directory, then atomically replace the target."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        try:
            mode = os.stat(path).st_mode & 0o777
        except FileNotFoundError:
            mode = _default_file_mode()
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


AgeFunc = Callable[[Path, dict[str, Any], float], float]


def get_cache_dir() -> Path:
    """Return the application cache directory, honoring ``XDG_CACHE_HOME``."""
    base = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache"))
    return base / "opencode-go-model-optimizer"


def load_json(
    path: Path,
    ttl_seconds: float | None = None,
    *,
    now: float | None = None,
    age: AgeFunc | None = None,
) -> tuple[dict[str, Any], float] | None:
    """Load a JSON object and its age, optionally requiring freshness."""
    if ttl_seconds is not None and ttl_seconds <= 0:
        return None
    try:
        mtime = path.stat().st_mtime if ttl_seconds is not None and age is None else 0.0
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        if ttl_seconds is None:
            return payload, 0.0
        current_time = time.time() if now is None else now
        age_seconds = age(path, payload, current_time) if age else current_time - mtime
    except OSError, ValueError:
        return None
    return (payload, age_seconds) if age_seconds <= ttl_seconds else None


def write_json(path: Path, payload: Any, *, indent: int | None = None) -> Path | None:
    """Atomically write JSON, returning the path or ``None`` on filesystem errors."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = (
            payload.model_dump_json(indent=indent)
            if isinstance(payload, BaseModel)
            else json.dumps(payload, indent=indent)
        )
        atomic_write_text(path, content)
    except OSError:
        return None
    return path

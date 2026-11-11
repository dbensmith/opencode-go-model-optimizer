"""Shared HTTP client helpers for ingest modules."""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import httpx


def _package_version() -> str:
    try:
        return version("opencode-go-model-optimizer")
    except PackageNotFoundError:
        return "0+unknown"


USER_AGENT = f"opencode-go-model-optimizer/{_package_version()}"


@contextmanager
def client_scope(client: httpx.Client | None) -> Iterator[httpx.Client]:
    """Yield the given client, or a new default client that is closed on exit."""
    if client is not None:
        yield client
        return
    with httpx.Client(timeout=10.0, headers={"User-Agent": USER_AGENT}) as owned:
        yield owned


def atomic_write_text(path: Path, content: str) -> None:
    """Write text to a temp file in the same directory, then atomically replace the target."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        try:
            mode = os.stat(path).st_mode & 0o777
        except FileNotFoundError:
            umask = os.umask(0)
            os.umask(umask)
            mode = 0o666 & ~umask
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise

"""Shared HTTP client helpers for ingest modules."""

import contextlib
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import httpx


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

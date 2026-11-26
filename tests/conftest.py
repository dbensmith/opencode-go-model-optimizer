"""Shared pytest fixtures and test constants."""

import json
from pathlib import Path

import pytest

from opencode_go_model_optimizer.ingest.catalogue import parse_catalogue_http
from opencode_go_model_optimizer.ingest.console import RequestProfiles
from opencode_go_model_optimizer.ingest.docs import parse_privacy
from opencode_go_model_optimizer.ingest.snapshot import (
    create_snapshot,
    save_snapshot,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
MDX_TEXT = (FIXTURES_DIR / "go_docs.mdx").read_text(encoding="utf-8")
CATALOGUE_HTTP_DATA = json.loads((FIXTURES_DIR / "catalogue_http.json").read_text(encoding="utf-8"))

TEST_CONSOLE_PROFILES = RequestProfiles(
    default=(800, 65_000, 250),
    by_model={"test-model": (1_000, 50_000, 200)},
    source_url="test://console",
)


@pytest.fixture(autouse=True)
def _stub_console_profiles(monkeypatch):
    """Never hit the real console from offline tests."""
    monkeypatch.setattr(
        "opencode_go_model_optimizer.ingest.snapshot.fetch_console_profiles",
        lambda *args, **kwargs: TEST_CONSOLE_PROFILES,
    )


def create_temp_snapshot(tmp_path: Path) -> Path:
    """Create a temporary snapshot file from local fixtures."""
    models = parse_catalogue_http(CATALOGUE_HTTP_DATA)
    snap = create_snapshot(models, privacy=parse_privacy(MDX_TEXT))
    snap_path = tmp_path / "snapshot.json"
    save_snapshot(snap, snap_path)
    return snap_path


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path_factory, monkeypatch):
    """Keep the consumer-side snapshot cache out of the real home directory."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path_factory.mktemp("xdg-cache")))

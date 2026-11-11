"""Shared pytest fixtures and test constants."""

import json
from pathlib import Path

import pytest

from opencode_go_model_optimizer.ingest.catalogue import parse_catalogue_cli
from opencode_go_model_optimizer.ingest.docs import parse_go_pricing
from opencode_go_model_optimizer.ingest.snapshot import (
    create_snapshot,
    save_snapshot,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
MDX_TEXT = (FIXTURES_DIR / "go_docs.mdx").read_text(encoding="utf-8")
CATALOGUE_HTTP_DATA = json.loads((FIXTURES_DIR / "catalogue_http.json").read_text(encoding="utf-8"))


def create_temp_snapshot(tmp_path: Path) -> Path:
    """Create a temporary snapshot file from local fixtures."""
    cli_text = (FIXTURES_DIR / "catalogue_cli.txt").read_text(encoding="utf-8")
    models = parse_catalogue_cli(cli_text)
    pricing = parse_go_pricing(MDX_TEXT)
    snap = create_snapshot(models, pricing)
    snap_path = tmp_path / "snapshot.json"
    save_snapshot(snap, snap_path)
    return snap_path


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path_factory, monkeypatch):
    """Keep the consumer-side snapshot cache out of the real home directory."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path_factory.mktemp("xdg-cache")))

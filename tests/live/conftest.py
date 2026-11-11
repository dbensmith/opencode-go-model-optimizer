"""Live contract tests: skipped unless OCGO_LIVE=1 so the default run stays offline."""

import os

import pytest

from opencode_go_model_optimizer.ingest.snapshot import fetch_and_build_snapshot


def pytest_collection_modifyitems(config, items):
    if os.environ.get("OCGO_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live contract tests need OCGO_LIVE=1")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def live_docs():
    from opencode_go_model_optimizer.ingest.docs import fetch_docs_mdx

    return fetch_docs_mdx()


@pytest.fixture(scope="session")
def live_snapshot():
    """One live snapshot (no Artificial Analysis) shared by the whole live session."""
    return fetch_and_build_snapshot(plan="go", use_aa=False)

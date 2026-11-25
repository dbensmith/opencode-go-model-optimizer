"""Ingestion modules for OpenCode Go Model Optimizer."""

from opencode_go_model_optimizer.ingest.catalogue import (
    apply_request_estimates,
    estimate_requests,
    fetch_catalogue,
    model_from_dict,
    parse_catalogue_cli,
    parse_catalogue_http,
)
from opencode_go_model_optimizer.ingest.console import (
    RequestProfiles,
    fetch_console_profiles,
    parse_console_profiles,
)
from opencode_go_model_optimizer.ingest.docs import (
    fetch_docs_mdx,
    norm_name,
    parse_privacy,
    parse_table_rows,
)
from opencode_go_model_optimizer.ingest.models import (
    PLANS,
    ModelLimit,
    NormalizedModel,
    Privacy,
    Snapshot,
    SnapshotMetadata,
    TokenCost,
    normalize_plan,
)
from opencode_go_model_optimizer.ingest.models_dev import (
    fetch_models_dev,
    parse_models_dev,
)
from opencode_go_model_optimizer.ingest.snapshot import (
    apply_plan,
    create_snapshot,
    fetch_and_build_snapshot,
    get_cache_snapshot_path,
    load_cached_snapshot,
    load_snapshot,
    merge_catalogue_metadata,
    save_cached_snapshot,
    save_snapshot,
)

__all__ = [
    "PLANS",
    "ModelLimit",
    "NormalizedModel",
    "Privacy",
    "RequestProfiles",
    "Snapshot",
    "SnapshotMetadata",
    "TokenCost",
    "apply_plan",
    "apply_request_estimates",
    "create_snapshot",
    "estimate_requests",
    "fetch_and_build_snapshot",
    "fetch_catalogue",
    "fetch_console_profiles",
    "fetch_docs_mdx",
    "fetch_models_dev",
    "get_cache_snapshot_path",
    "load_cached_snapshot",
    "load_snapshot",
    "merge_catalogue_metadata",
    "model_from_dict",
    "norm_name",
    "normalize_plan",
    "parse_catalogue_cli",
    "parse_catalogue_http",
    "parse_console_profiles",
    "parse_models_dev",
    "parse_privacy",
    "parse_table_rows",
    "save_cached_snapshot",
    "save_snapshot",
]

"""Ingestion modules for OpenCode Go Model Optimizer."""

from opencode_go_model_optimizer.ingest.catalogue import (
    fetch_catalogue,
    model_from_dict,
    parse_catalogue_cli,
    parse_catalogue_http,
)
from opencode_go_model_optimizer.ingest.docs import (
    extract_plan_section,
    fetch_docs_mdx,
    norm_name,
    parse_go_pricing,
    parse_price_value,
    parse_table_rows,
)
from opencode_go_model_optimizer.ingest.models import (
    ModelLimit,
    NormalizedModel,
    Snapshot,
    SnapshotMetadata,
    TokenCost,
)
from opencode_go_model_optimizer.ingest.snapshot import (
    create_snapshot,
    fetch_and_build_snapshot,
    load_snapshot,
    merge_docs_pricing,
    save_snapshot,
)

__all__ = [
    "ModelLimit",
    "NormalizedModel",
    "Snapshot",
    "SnapshotMetadata",
    "TokenCost",
    "create_snapshot",
    "extract_plan_section",
    "fetch_and_build_snapshot",
    "fetch_catalogue",
    "fetch_docs_mdx",
    "load_snapshot",
    "merge_docs_pricing",
    "model_from_dict",
    "norm_name",
    "parse_catalogue_cli",
    "parse_catalogue_http",
    "parse_go_pricing",
    "parse_price_value",
    "parse_table_rows",
    "save_snapshot",
]

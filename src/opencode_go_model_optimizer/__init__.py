"""OpenCode Go Model Optimizer package."""

from opencode_go_model_optimizer.cli import main
from opencode_go_model_optimizer.ingest.http import package_version

__version__ = package_version()

__all__ = ["__version__", "main"]

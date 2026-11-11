"""Filter and ranking knobs shared by every selection entrypoint."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SelectionCriteria:
    """Immutable bundle of filters and ranking parameters.

    Adding a knob is a one-line change here: engine functions receive the whole object.
    """

    mode: str = "allowance"
    top_n: int = 1
    preferred_tiers: dict[str, str] = field(default_factory=dict)
    skip_models: frozenset[str] = frozenset()
    allow_contributor: bool = True
    min_context: int = 0
    min_output: int = 0
    max_output_cost: float | None = None
    max_burn: float | None = None
    profile: str = "general"
    token_mix: tuple[int, int, int] | None = None
    weights: tuple[float, float] | None = None

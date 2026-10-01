"""Strategy registry. Adding a strategy = subclass `Strategy` in library.py and list it in ALL_STRATEGIES."""
from __future__ import annotations

from delta_intelligence.strategies.base import Strategy
from delta_intelligence.strategies.library import ALL_STRATEGIES

STRATEGY_CLASSES: dict[str, type[Strategy]] = {cls.name: cls for cls in ALL_STRATEGIES}


def get_all_strategies(parameters_by_name: dict | None = None) -> list[Strategy]:
    parameters_by_name = parameters_by_name or {}
    return [cls(parameters_by_name.get(name)) for name, cls in STRATEGY_CLASSES.items()]


def get_strategy(name: str, parameters: dict | None = None) -> Strategy:
    try:
        return STRATEGY_CLASSES[name](parameters)
    except KeyError:
        raise KeyError(f"unknown strategy: {name}") from None

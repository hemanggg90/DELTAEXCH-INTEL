"""Random-signal control: same option selector, same costs, same number of entries, but no skill.

- Directional strategies: keep every entry time, flip each direction with probability 1/2 and mirror the stop/target
  around the entry price (so the geometry stays valid).
- Volatility (straddle) strategies have no direction, so the control draws the same number of entry times at random
  among the bars where the strategy's inputs exist.

An apparent edge only matters if it beats this null.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np

from delta_intelligence.strategies.base import Setup


def randomize_directions(setups: list[Setup], rng: np.random.Generator) -> list[Setup]:
    out = []
    for s in setups:
        if s.direction in ("LONG", "SHORT") and rng.random() < 0.5:
            out.append(replace(s, direction="SHORT" if s.direction == "LONG" else "LONG",
                               stop_price=2 * s.entry_price - s.stop_price,
                               target_price=2 * s.entry_price - s.target_price))
        else:
            out.append(s)
    return out


def random_entry_setups(strategy, frame, n: int, rng: np.random.Generator) -> list[Setup]:
    """n random straddle entries among rows where the strategy's required inputs are finite."""
    ok = np.flatnonzero(~strategy.missing(frame).to_numpy())
    if not len(ok) or n <= 0:
        return []
    pick = np.sort(rng.choice(ok, size=min(n, len(ok)), replace=False))
    return [Setup(frame["timestamp"].iloc[i], "VOL", float(frame["close"].iloc[i]), 0.0, 0.0, strategy.name,
                  {"expected_abs_move": strategy.expected_abs_move(frame, int(i))}) for i in pick]

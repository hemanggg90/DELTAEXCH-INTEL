# Strategy search: top 10 by win rate and drawdown among positive-R variants

Generated 2026-10-01 22:51 UTC by `scripts/strategy_search.py`.

- Candidates: **270** (23 strategies × exit variants × ATM/1-ITM option × normal/2× hold).
- Discovery: 2025-12-01 → 2026-06-30 on BTC + ETH. Holdout: 2026-07-01 → 2026-10-01 on BTC + ETH + XAUT, never used for selection.
- Eligible in discovery (net R > 0 on BTC AND ETH, >= 40 trades each): **4 of 270**.
- **Null benchmark:** the same candidates with RANDOM directions -> **3 of 270** eligible.
  Picking winners from a list where random signals also 'win' is mostly selecting luck.

Net R is per trade, after modelled spread, fees + GST, the breakeven gate and all exits; R = premium paid + entry fees.

## The 10 picks (chosen on discovery only), and how they did on unseen data

| # | Candidate | Disc. trades | Disc. win % | Disc. net R | Disc. max DD (R) | Holdout trades | Holdout win % | Holdout net R | Holdout max DD (R) | Holdout verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Supertrend Trend Following | tight | ATM | hold x1 | 89 | 49% | +0.176 | -2.1 | 33 | 48% | +0.075 | -2.9 | positive |
| 2 | Supertrend Trend Following | tight | ITM1 | hold x1 | 133 | 46% | +0.088 | -3.3 | 47 | 51% | +0.069 | -3.7 | positive |
| 3 | Momentum | base | ATM | hold x1 | 175 | 42% | +0.079 | -4.0 | 58 | 33% | -0.050 | -4.7 | NEGATIVE |
| 4 | Momentum | base | ITM1 | hold x1 | 226 | 40% | +0.048 | -4.3 | 79 | 37% | -0.018 | -4.5 | NEGATIVE |

**2 of 4 picks stayed positive on unseen data** (>= 20 holdout trades).

### Holdout by underlying

| Candidate | BTC net R (n) | ETH net R (n) | XAUT net R (n) |
|---|---|---|---|
| Supertrend Trend Following | tight | ATM | hold x1 | +0.174 (16) | +0.005 (16) | -0.410 (1) |
| Supertrend Trend Following | tight | ITM1 | hold x1 | +0.167 (22) | -0.001 (24) | -0.410 (1) |
| Momentum | base | ATM | hold x1 | -0.102 (23) | -0.027 (30) | +0.055 (5) |
| Momentum | base | ITM1 | hold x1 | -0.031 (35) | -0.033 (36) | +0.104 (8) |

## What random signals look like in the same search

The top random-direction candidates in discovery (same ranking rule):

| Candidate (random directions) | Disc. trades | Disc. win % | Disc. net R | Disc. max DD (R) |
|---|---|---|---|---|
| Momentum | base | ITM1 | hold x2 | 122 | 42% | +0.054 | -4.3 |
| Inside Bar Breakout | base | ATM | hold x2 | 131 | 37% | +0.077 | -2.4 |
| Liquidity Sweep + MSS | rr1.5 | ATM | hold x2 | 240 | 35% | +0.007 | -8.9 |

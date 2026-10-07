# Phase 2 protocol v2.0 (frozen before the full Phase 2 run)

Question: does the UNDERLYING signal have an edge, and separately, does the option-selection policy preserve or destroy
that edge after realistic costs? And: where real recorded option quotes exist, do they change the Phase 1 conclusions?

The hash of this experiment (policies, thresholds, the source of the files that implement it, and the Phase 1 hash) is in
`FROZEN_PHASE2_HASH.txt`, printed in the report and enforced by `tests/test_phase2.py::test_phase2_protocol_matches_the_frozen_hash`.
The Phase 1 strategies are frozen separately (`FROZEN_PROTOCOL_HASH.txt`) and `run_phase2.py` aborts if they changed.

## Data tiers, never mixed
- **REAL_RECORDED**: option-chain snapshots recorded by `chain_recorder` / `scripts/record_chain.py`. Tiers: REAL_QUOTE (usable
  two-sided fresh quote), REAL_MARK_ONLY (a mark is never a bid or an ask), UNUSABLE. Missing fields stay NULL.
- **MODELED**: Black-Scholes at the as-of ATM IV inferred from real option trades, plus a fitted smile and a modelled spread.
  This is Phase 1's method and the only source for almost all results, because the recorder only started on 2026-10-02.

## Pricing modes
`MODEL_ONLY` (default, reproduces Phase 1), `REAL_ONLY` (recorded quotes only, ask in / bid out, no silent fallback),
`REAL_THEN_MODEL_FALLBACK` (every trade labelled REAL_QUOTE / MODELED / MIXED). Lookups are as-of: a quote is used only if its
snapshot was taken at or before the pricing time and is at most 600 s old. A REAL_ONLY exit without a real quote is counted
(`no_real_exit_quote`, with its premium at risk), not silently dropped.

## Option-selection policies (14, in `phase2/policies.py`; research hypotheses, nothing assumed to help)
BASE (the project's own selection), delta bands (broad 0.30-0.60, ITM 0.50-0.70, OTM 0.20-0.40), DTE buckets (2-3, 4-7, 8-14,
15-30 days), filters (spread <= 6%, theta <= 40% of premium per day, premium <= 0.5% of spot, IV <= 1.1x realised vol, IV
percentile <= 50) and a real-liquidity policy (OI >= 100, volume >= 10) that cannot run on modelled data. Ranking among
contracts that pass the filters is deterministic: closest to the band middle (or lowest spread / theta), then spread, then
distance to spot, then strike. Delta bands do not apply to straddles.

## Selection and anti-overfitting
- ONE policy per cell is chosen on DISCOVERY trades only (entry before 2026-07-01): the highest discovery mean net R among
  policies with >= 100 discovery trades; ties go to the earlier policy; if none qualifies, BASE. The holdout is never read to choose.
- The chosen policy is frozen and the unchanged acceptance gates (>= 200 trades, positive holdout net R, +-20% perturbations,
  >= 2 assets, positive in >= 2 of 3 delta buckets, random-signal control) apply to it.
- Extra gate, **policy breadth**: >= 50% of the evaluable policies (>= 30 holdout trades) must be positive on the holdout. It
  tests that the economics do not hinge on one option choice. It is a check, not a selector.
- Compute rule: only Phase 1 cells with >= 200 BASE trades are run (the rest are DATA-INSUFFICIENT already).

## Signal edge vs option implementation
Signal edge = mean underlying-space R (stop/target geometry; for straddles the frictionless option R). It counts only if
positive in discovery AND holdout with a bootstrap 95% one-sided lower bound > 0. If the signal edge is shown and net R after
costs is not positive, the cell is an OPTION-IMPLEMENTATION FAILURE (costs: gross > 0; decay: gross <= 0), not a strategy failure.
A net-positive result without a shown signal edge is treated as noise. Net R = gross mid-to-mid R - spread R - fees R, exactly.

## Statuses
ACCEPTED, EXPERIMENTAL, REJECTED, DATA-INSUFFICIENT as in Phase 1, plus **REAL-DATA-VALIDATED**, only if all gates pass AND:
>= 14 days of recorded snapshots; >= 200 real-priced trades (>= 30 on >= 2 assets); >= 80% of trades priced from real quotes at both
ends; and the gates also pass on the real-only trades. The thresholds are configurable (`RealDataThresholds`); the rationale is
in `analysis.py`: the real-only counts must meet the acceptance minimums themselves, and a modelled remainder of at most a fifth
cannot dominate a pooled mean. Revisit them with the measured model-vs-real error.

## Limitations
Real exits use the latest recorded quote at or before the exit bar's close (intra-bar quotes do not exist). Real theta/vega units
are as recorded (UNVERIFIED); attribution theta is a model estimate. Open interest and volume effects cannot be measured on
modelled data. Slippage is not measurable (quotes only, no fills). The 18% GST is unverified.

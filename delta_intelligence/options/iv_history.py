"""
Historical implied volatility, built from REAL traded option prices on Delta.

Why: the hybrid backtest prices hypothetical option trades with Black-Scholes. That needs the volatility the market
was actually charging at the time, and Delta has no historical IV endpoint. So IV is inferred from traded option
candles (verified available back to at least Dec 2025, but sparse: bars exist only when a trade happened).

Pipeline (each step cached under data_cache/options/<env>/ and rebuildable from public endpoints):
1. `list_expired_options`: expired BTC/ETH calls and puts with strike, expiry and `settlement_price`.
2. `select_contracts`: per expiry, the strikes within +-`band` of the index range over the contract's last
   `window_hours`. Those are the strikes a near-ATM strategy could have traded.
3. `fetch_expiry_candles`: 5m candles per selected contract over [settle − window, settle], cut at settlement. Candles
   continue flat after settlement (verified), so those bars are dropped.
4. `compute_iv_observations`: IV for each bar with volume > 0, using the index close of the same bar and
   T = settlement − bar close.
5. `atm_iv_hourly`: volume-weighted median IV of near-ATM observations per hour and time-to-expiry bucket.
   `fit_smile` estimates the average IV smile (iv − atm_iv vs log-moneyness).

Every observation records `known_at` (its bar close), so consumers can join it as-of without look-ahead.
"""
from __future__ import annotations

import datetime as dt
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from delta_intelligence.brokers.delta_api_client import DeltaClient
from delta_intelligence.data_adapters.delta_adapter import candles_to_frame
from delta_intelligence.options.pricing import implied_vol, year_fraction
from delta_intelligence.utils.logging_utils import log_event

TF = "5m"
TF_SEC = 300
T_BUCKETS_H: tuple[tuple[str, float, float], ...] = (
    ("0_6h", 0.25, 6.0), ("6_30h", 6.0, 30.0), ("30_54h", 30.0, 54.0),
    ("54_168h", 54.0, 168.0), ("168_720h", 168.0, 720.0),  # weekly / monthly (long-dated pipeline)
)
ATM_BAND = 0.01  # |ln(K/S)| <= 1% counts as at-the-money for the ATM series


@dataclass
class IvBuildConfig:
    since: dt.datetime
    until: dt.datetime
    underlyings: tuple[str, ...] = ("BTC", "ETH")
    band: float = 0.03
    window_hours: float = 32.0
    workers: int = 6


# ---- 1. expired products ---------------------------------------------------------------------------------------
def list_expired_options(client: DeltaClient, underlyings: tuple[str, ...], since: dt.datetime) -> pd.DataFrame:
    """Expired calls and puts settled at or after `since`. The listing is newest-first; we stop once a whole page is
    older than `since`."""
    rows: list[dict] = []
    after: str | None = None
    for _ in range(2000):
        payload = client.request("GET", "/v2/products", params={
            "contract_types": "call_options,put_options", "states": "expired",
            "underlying_asset_symbols": ",".join(underlyings), "page_size": 500, "after": after}, weight=3)
        page = payload.get("result") or []
        newest_on_page = None
        for p in page:
            st = pd.Timestamp(p["settlement_time"])
            newest_on_page = st if newest_on_page is None else max(newest_on_page, st)
            if st < pd.Timestamp(since):
                continue
            rows.append({
                "symbol": p["symbol"], "product_id": p["id"], "underlying": p["underlying_asset"]["symbol"],
                "kind": "C" if p["contract_type"] == "call_options" else "P",
                "strike": float(p["strike_price"]), "expiry": st,
                "settlement_price": float(p.get("settlement_price") or 0.0),
                "contract_value": float(p["contract_value"]),
            })
        after = (payload.get("meta") or {}).get("after")
        if not after or not page or (newest_on_page is not None and newest_on_page < pd.Timestamp(since)):
            break
    df = pd.DataFrame(rows)
    return df.drop_duplicates("symbol").sort_values(["expiry", "underlying", "kind", "strike"]).reset_index(drop=True) \
        if len(df) else df


# ---- 2. selection ----------------------------------------------------------------------------------------------
def select_contracts(meta: pd.DataFrame, index_by_underlying: dict[str, pd.DataFrame], band: float,
                     window_hours: float) -> pd.DataFrame:
    """Contracts whose strike lies within +-band of the index's [low, high] over the contract's final window."""
    keep = []
    for (u, expiry), grp in meta.groupby(["underlying", "expiry"]):
        idx = index_by_underlying.get(u)
        if idx is None or not len(idx):
            continue
        lo_t = expiry - pd.Timedelta(hours=window_hours)
        win = idx[(idx["timestamp"] >= lo_t) & (idx["timestamp"] < expiry)]
        if not len(win):
            continue
        lo, hi = win["low"].min() * (1 - band), win["high"].max() * (1 + band)
        keep.append(grp[(grp["strike"] >= lo) & (grp["strike"] <= hi)])
    return pd.concat(keep, ignore_index=True) if keep else meta.iloc[0:0]


# ---- 3. candles --------------------------------------------------------------------------------------------------
def cut_at_settlement(candles: pd.DataFrame, expiry: pd.Timestamp) -> pd.DataFrame:
    """Keep only bars that CLOSED at or before settlement. Delta keeps printing flat zero-volume bars afterwards."""
    return candles[candles["timestamp"] + pd.Timedelta(seconds=TF_SEC) <= expiry].reset_index(drop=True)


def fetch_contract_candles(client: DeltaClient, symbol: str, expiry: pd.Timestamp, window_hours: float) -> pd.DataFrame:
    start = int((expiry - pd.Timedelta(hours=window_hours)).timestamp())
    end = int(expiry.timestamp())
    df = cut_at_settlement(candles_to_frame(client.get_candles(symbol, TF, start, end)), expiry)
    return df.assign(symbol=symbol)


class IvHistoryStore:
    """On-disk cache: meta/, candles/<U>/<YYYY-MM-DD>.parquet (one file per settled expiry), obs/<U>.parquet."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def meta_path(self) -> Path:
        return self.root / "expired_options.parquet"

    def expiry_path(self, underlying: str, expiry: pd.Timestamp) -> Path:
        return self.root / "candles" / underlying / f"{expiry:%Y-%m-%d}.parquet"

    def obs_path(self, underlying: str) -> Path:
        return self.root / "iv_obs" / f"{underlying}.parquet"

    @staticmethod
    def write(df: pd.DataFrame, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        df.to_parquet(tmp, index=False)
        tmp.replace(path)


def fetch_expiry_candles(make_client, store: IvHistoryStore, selected: pd.DataFrame, window_hours: float,
                         workers: int, progress=None) -> dict:
    """Fetch and cache candles per (underlying, expiry). Already-cached expiries are skipped (settled data never
    changes). `make_client()` returns a DeltaClient per worker thread; all share the process-wide rate limiter."""
    groups = [(u, e, g) for (u, e), g in selected.groupby(["underlying", "expiry"])
              if not store.expiry_path(u, e).exists()]
    stats = {"expiries_total": selected.groupby(["underlying", "expiry"]).ngroups, "expiries_fetched": 0,
             "contracts_fetched": 0, "errors": 0}
    local = threading.local()

    def client() -> DeltaClient:
        if not hasattr(local, "c"):
            local.c = make_client()
        return local.c

    def work(u, e, g):
        frames, errors = [], 0
        for sym in g["symbol"]:
            try:
                frames.append(fetch_contract_candles(client(), sym, e, window_hours))
            except Exception as exc:
                errors += 1
                log_event("iv_history", f"candles for {sym} failed: {exc}", level="WARNING")
        if errors == 0:  # cache only complete expiries, so a later run retries partial ones
            df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume", "symbol"])
            store.write(df, store.expiry_path(u, e))
        return len(g), errors

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(work, u, e, g) for u, e, g in groups]
        for i, fut in enumerate(as_completed(futures), 1):
            n, errors = fut.result()
            stats["contracts_fetched"] += n
            stats["errors"] += errors
            stats["expiries_fetched"] += 1
            if progress:
                progress(i, len(futures))
    return stats


# ---- 4. IV observations ----------------------------------------------------------------------------------------------
def compute_iv_observations(candles: pd.DataFrame, meta: pd.DataFrame, index: pd.DataFrame,
                            tf_seconds: int = TF_SEC) -> pd.DataFrame:
    """IV per traded bar. Joins on symbol (strike/kind/expiry) and on the index bar with the SAME open time (both
    series at the same resolution `tf_seconds`)."""
    if not len(candles):
        return pd.DataFrame()
    df = candles[candles["volume"].fillna(0) > 0].merge(
        meta[["symbol", "underlying", "kind", "strike", "expiry"]], on="symbol", how="inner")
    idx = index[["timestamp", "close"]].rename(columns={"close": "spot"})
    df["timestamp"] = df["timestamp"].astype("datetime64[ns, UTC]")
    idx = idx.assign(timestamp=idx["timestamp"].astype("datetime64[ns, UTC]"))
    df = df.merge(idx, on="timestamp", how="inner")
    df["known_at"] = df["timestamp"] + pd.Timedelta(seconds=tf_seconds)
    secs = (df["expiry"].astype("datetime64[ns, UTC]") - df["known_at"]).dt.total_seconds()
    df["t_hours"] = secs / 3600.0
    df["log_moneyness"] = np.log(df["strike"] / df["spot"])
    df["price"] = df["close"]
    df["iv"] = implied_vol(df["price"].to_numpy(), df["spot"].to_numpy(), df["strike"].to_numpy(),
                           year_fraction(secs.to_numpy()), df["kind"].to_numpy())
    cols = ["timestamp", "known_at", "underlying", "symbol", "kind", "strike", "expiry", "t_hours", "spot",
            "log_moneyness", "price", "volume", "iv"]
    return df[cols].sort_values("known_at").reset_index(drop=True)


# ---- 5. aggregate --------------------------------------------------------------------------------------------------
def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values)
    v, w = values[order], weights[order]
    cw = np.cumsum(w)
    return float(v[np.searchsorted(cw, 0.5 * cw[-1])])


def atm_iv_hourly(obs: pd.DataFrame, atm_band: float = ATM_BAND) -> pd.DataFrame:
    """Hourly ATM IV per time-to-expiry bucket. `hour` is the bucket START; values are known at hour + 1h (use
    `available_at`)."""
    ok = obs[obs["iv"].notna() & (obs["log_moneyness"].abs() <= atm_band)].copy()
    if not len(ok):
        return pd.DataFrame(columns=["hour", "available_at"])
    ok["hour"] = ok["known_at"].dt.floor("1h")
    # A bar closing exactly on the hour belongs to the previous hour's information set.
    ok.loc[ok["known_at"] == ok["hour"], "hour"] -= pd.Timedelta(hours=1)
    out = None
    for name, lo, hi in T_BUCKETS_H:
        b = ok[(ok["t_hours"] >= lo) & (ok["t_hours"] < hi)]
        if not len(b):
            continue
        agg = b.groupby("hour").apply(lambda g: pd.Series({
            f"atm_iv_{name}": _weighted_median(g["iv"].to_numpy(), g["volume"].to_numpy() + 1e-9),
            f"n_{name}": len(g)}), include_groups=False).reset_index()
        out = agg if out is None else out.merge(agg, on="hour", how="outer")
    out = out.sort_values("hour").reset_index(drop=True)
    out["available_at"] = out["hour"] + pd.Timedelta(hours=1)
    return out


def fit_smile(obs: pd.DataFrame, hourly: pd.DataFrame, max_abs_k: float = 0.05) -> dict[str, dict]:
    """Average smile per bucket: (iv − that hour's ATM iv) ≈ a·k + b·k², with k = ln(K/S). OLS, no intercept."""
    out: dict[str, dict] = {}
    if not len(hourly):
        return out
    o = obs[obs["iv"].notna() & (obs["log_moneyness"].abs() <= max_abs_k)].copy()
    o["hour"] = o["known_at"].dt.floor("1h")
    o.loc[o["known_at"] == o["hour"], "hour"] -= pd.Timedelta(hours=1)
    for name, lo, hi in T_BUCKETS_H:
        col = f"atm_iv_{name}"
        if col not in hourly:
            continue
        b = o[(o["t_hours"] >= lo) & (o["t_hours"] < hi)].merge(hourly[["hour", col]], on="hour").dropna(subset=[col])
        if len(b) < 50:
            continue
        k = b["log_moneyness"].to_numpy()
        y = (b["iv"] - b[col]).to_numpy()
        X = np.column_stack([k, k * k])
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ coef
        out[name] = {"slope": float(coef[0]), "curvature": float(coef[1]), "n": int(len(b)),
                     "resid_std": float(resid.std())}
    return out


def settlement_check(meta: pd.DataFrame, index_by_underlying: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Compare Delta's settlement (implied from ITM contracts) with our model: the 30-min TWAP of 5m index closes
    before 12:00 UTC."""
    rows = []
    itm = meta[meta["settlement_price"] > 0]
    for (u, expiry), g in itm.groupby(["underlying", "expiry"]):
        implied = np.where(g["kind"] == "C", g["strike"] + g["settlement_price"], g["strike"] - g["settlement_price"])
        idx = index_by_underlying.get(u)
        if idx is None:
            continue
        win = idx[(idx["timestamp"] >= expiry - pd.Timedelta(minutes=30)) & (idx["timestamp"] < expiry)]
        if len(win) < 6:
            continue
        model = win["close"].mean()
        actual = float(np.median(implied))
        rows.append({"underlying": u, "expiry": expiry, "delta_settlement": actual, "model_twap": model,
                     "error_pct": (model - actual) / actual * 100})
    return pd.DataFrame(rows)


def settlement_index_twap(index: pd.DataFrame, expiry: pd.Timestamp) -> float | None:
    """Model of Delta's settlement: mean of the 5m index closes in the 30 minutes before 12:00 UTC."""
    win = index[(index["timestamp"] >= expiry - pd.Timedelta(minutes=30)) & (index["timestamp"] < expiry)]
    return float(win["close"].mean()) if len(win) >= 3 else None


# ---- long-dated (weekly / monthly) pipeline -------------------------------------------------------------------------
LONG_TF = "1h"
LONG_TF_SEC = 3600


def thin_strikes(strikes: np.ndarray, ref: float, step_frac: float = 0.01) -> np.ndarray:
    """Keep listed strikes nearest a grid of `step_frac` × ref spacing (so a near-ATM strike, |k| <= ~0.5 step, always
    exists without fetching every listed strike)."""
    strikes = np.sort(np.unique(strikes))
    if not len(strikes):
        return strikes
    step = ref * step_frac
    grid = np.arange(strikes[0], strikes[-1] + step, step)
    picked = {float(strikes[np.argmin(np.abs(strikes - g))]) for g in grid}
    return np.array(sorted(picked))


def select_long_dated(meta: pd.DataFrame, index_by_underlying: dict[str, pd.DataFrame], window_days: float = 35.0,
                      gap_hours: float = 32.0, band: float = 0.05, step_frac: float = 0.01) -> pd.DataFrame:
    """Friday expiries (weekly/monthly contracts that trade for weeks), strikes within the index range over
    [expiry − window, expiry − gap] ± band, thinned to ~1% spacing."""
    keep = []
    for (u, expiry), grp in meta.groupby(["underlying", "expiry"]):
        if expiry.day_name() != "Friday":
            continue
        idx = index_by_underlying.get(u)
        if idx is None:
            continue
        win = idx[(idx["timestamp"] >= expiry - pd.Timedelta(days=window_days))
                  & (idx["timestamp"] < expiry - pd.Timedelta(hours=gap_hours))]
        if not len(win):
            continue
        lo, hi = win["low"].min() * (1 - band), win["high"].max() * (1 + band)
        inband = grp[(grp["strike"] >= lo) & (grp["strike"] <= hi)]
        chosen = thin_strikes(inband["strike"].to_numpy(), float(win["close"].median()), step_frac)
        keep.append(inband[inband["strike"].isin(chosen)])
    return pd.concat(keep, ignore_index=True) if keep else meta.iloc[0:0]


def long_dated_path(store: IvHistoryStore, underlying: str, expiry: pd.Timestamp) -> Path:
    return store.root / "candles_1h" / underlying / f"{expiry:%Y-%m-%d}.parquet"


def fetch_long_dated(make_client, store: IvHistoryStore, selected: pd.DataFrame, window_days: float = 35.0,
                     gap_hours: float = 32.0, workers: int = 6, progress=None) -> dict:
    """1h candles over [expiry − window, expiry − gap] per contract, cached per expiry (only when complete)."""
    groups = [(u, e, g) for (u, e), g in selected.groupby(["underlying", "expiry"])
              if not long_dated_path(store, u, e).exists()]
    stats = {"expiries": len(groups), "contracts": 0, "errors": 0}
    local = threading.local()

    def client():
        if not hasattr(local, "c"):
            local.c = make_client()
        return local.c

    def work(u, e, g):
        frames, errors = [], 0
        start = int((e - pd.Timedelta(days=window_days)).timestamp())
        end = int((e - pd.Timedelta(hours=gap_hours)).timestamp())
        for sym in g["symbol"]:
            try:
                rows = client().get_candles(sym, LONG_TF, start, end)
                frames.append(candles_to_frame(rows).assign(symbol=sym))
            except Exception as exc:
                errors += 1
                log_event("iv_history", f"1h candles for {sym} failed: {exc}", level="WARNING")
        if errors == 0:
            df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume", "symbol"])
            store.write(df, long_dated_path(store, u, e))
        return len(g), errors

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(work, u, e, g) for u, e, g in groups]
        for i, fut in enumerate(as_completed(futures), 1):
            n, err = fut.result()
            stats["contracts"] += n
            stats["errors"] += err
            if progress:
                progress(i, len(futures))
    return stats


def index_hourly(index_5m: pd.DataFrame) -> pd.DataFrame:
    """1h index bars from 5m bars (bar-open labelled). Only complete hours (12 bars) are kept."""
    s = index_5m.set_index("timestamp")
    agg = s.resample("1h", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min",
                                                             "close": "last"})
    n = s["close"].resample("1h", label="left", closed="left").count()
    return agg[n == 12].dropna().reset_index()

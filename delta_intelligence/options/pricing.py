"""
Black-Scholes pricing, Greeks and implied volatility, in Delta's convention.

Verified live on 2026-10-02 (docs/DELTA_API_NOTES.md section 12): with r = 0 and q = 0, S = the spot index, T in
365-day years to the 12:00 UTC expiry, and σ = Delta's `mark_iv`, this reproduces Delta's mark prices (median error
0.014% over 546 BTC options). Prices are in USD per 1 unit of the underlying (per 1 BTC).

Everything is vectorised over numpy arrays, and every function also accepts scalars. `kind` is "C" or "P" (or an
array of them).
"""
from __future__ import annotations

import numpy as np
from scipy.special import ndtr

YEAR_SECONDS = 365.0 * 86400.0
_SQRT_2PI = np.sqrt(2.0 * np.pi)
IV_LOW, IV_HIGH = 0.01, 5.0  # 1% .. 500% annualised


def _arr(x):
    return np.asarray(x, dtype="float64")


def _is_call(kind) -> np.ndarray:
    """True for calls. Accepts "C"/"P" (str, numpy str or OBJECT arrays, e.g. a pandas column). Anything else
    raises: a silent default would misprice puts as calls."""
    k = np.asarray(kind)
    if k.dtype.kind in "USO":
        u = np.char.upper(k.astype(str))
        if not np.isin(u, ["C", "P"]).all():
            raise ValueError("option kind must be 'C' or 'P'")
        return u == "C"
    raise TypeError(f"option kind must be 'C'/'P' strings, got dtype {k.dtype}")


def _d1d2(S, K, T, sigma):
    vol_t = sigma * np.sqrt(T)
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(S / K) + 0.5 * sigma * sigma * T) / vol_t
    return d1, d1 - vol_t


def bs_price(S, K, T, sigma, kind):
    """Option value. At T <= 0 (or sigma <= 0) this is the intrinsic value."""
    S, K, T, sigma = _arr(S), _arr(K), _arr(T), _arr(sigma)
    call = _is_call(kind)
    intrinsic = np.where(call, np.maximum(S - K, 0.0), np.maximum(K - S, 0.0))
    live = (T > 0) & (sigma > 0)
    Ts = np.where(live, T, 1.0)
    ss = np.where(live, sigma, 1.0)
    d1, d2 = _d1d2(S, K, Ts, ss)
    c = S * ndtr(d1) - K * ndtr(d2)
    p = K * ndtr(-d2) - S * ndtr(-d1)
    out = np.where(live, np.where(call, c, p), intrinsic)
    return out if out.ndim else float(out)


def greeks(S, K, T, sigma, kind) -> dict[str, np.ndarray]:
    """delta, gamma, vega (per 1.00 vol), theta (per YEAR and per DAY), and rho (zero here, since r = 0)."""
    S, K, T, sigma = _arr(S), _arr(K), _arr(T), _arr(sigma)
    call = _is_call(kind)
    live = (T > 0) & (sigma > 0)
    Ts, ss = np.where(live, T, 1.0), np.where(live, sigma, 1.0)
    d1, _ = _d1d2(S, K, Ts, ss)
    pdf = np.exp(-0.5 * d1 * d1) / _SQRT_2PI
    delta = np.where(call, ndtr(d1), ndtr(d1) - 1.0)
    itm = np.where(call, S > K, S < K)
    delta = np.where(live, delta, np.where(itm, np.where(call, 1.0, -1.0), 0.0))
    gamma = np.where(live, pdf / (S * ss * np.sqrt(Ts)), 0.0)
    vega = np.where(live, S * pdf * np.sqrt(Ts), 0.0)
    theta_year = np.where(live, -S * pdf * ss / (2.0 * np.sqrt(Ts)), 0.0)
    out = {"delta": delta, "gamma": gamma, "vega": vega, "theta_year": theta_year, "theta_day": theta_year / 365.0}
    return {k: (v if v.ndim else float(v)) for k, v in out.items()}


def no_arbitrage_bounds(S, K, kind):
    """(lower, upper) bounds for an option price with r = 0: intrinsic <= price <= S (call) or K (put)."""
    S, K = _arr(S), _arr(K)
    call = _is_call(kind)
    lower = np.where(call, np.maximum(S - K, 0.0), np.maximum(K - S, 0.0))
    upper = np.where(call, S, K)
    return lower, upper


def implied_vol(price, S, K, T, kind, tol: float = 1e-7, max_iter: int = 100):
    """Implied volatility by vectorised bisection on [IV_LOW, IV_HIGH].

    Returns NaN where no volatility in range reproduces the price:
    - price at or below intrinsic (no time value), or above the no-arbitrage upper bound;
    - T <= 0;
    - the price is so close to intrinsic that the inversion is meaningless (time value < 1e-9 of S).

    Bisection is slower than Newton but cannot diverge on deep-OTM or near-expiry inputs, which is exactly where
    sparse traded option prices live.
    """
    price, S, K, T = _arr(price), _arr(S), _arr(K), _arr(T)
    shape = np.broadcast(price, S, K, T, np.asarray(kind)).shape
    price, S, K, T = (np.broadcast_to(x, shape).astype("float64") for x in (price, S, K, T))
    kind = np.broadcast_to(np.asarray(kind), shape)
    lower, upper = no_arbitrage_bounds(S, K, kind)
    valid = (T > 0) & (price > lower + 1e-9 * S) & (price < upper) & np.isfinite(price)
    lo = np.full(shape, IV_LOW)
    hi = np.full(shape, IV_HIGH)
    p_lo = bs_price(S, K, T, lo, kind)
    p_hi = bs_price(S, K, T, hi, kind)
    valid &= (price >= p_lo) & (price <= p_hi)
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        p_mid = bs_price(S, K, T, mid, kind)
        too_high = p_mid > price
        hi = np.where(too_high, mid, hi)
        lo = np.where(too_high, lo, mid)
        if np.all((hi - lo)[valid] < tol) if valid.any() else True:
            break
    iv = np.where(valid, 0.5 * (lo + hi), np.nan)
    return iv if iv.ndim else float(iv)


def year_fraction(seconds) -> np.ndarray:
    return _arr(seconds) / YEAR_SECONDS

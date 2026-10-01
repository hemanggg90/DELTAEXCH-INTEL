from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from delta_intelligence.options import iv_history as ivh
from delta_intelligence.options.pricing import bs_price, year_fraction

EXP = pd.Timestamp("2026-09-30T12:00Z")


def index_frame(start="2026-09-29T00:00Z", n=600, price=85000.0) -> pd.DataFrame:
    ts = pd.date_range(start, periods=n, freq="5min")
    close = price * np.exp(np.cumsum(np.random.default_rng(1).normal(0, 0.0005, n)))
    return pd.DataFrame({"timestamp": ts, "open": close, "high": close * 1.0005, "low": close * 0.9995,
                         "close": close, "volume": np.nan})


def meta_frame() -> pd.DataFrame:
    rows = []
    for k in (80000, 84000, 85000, 86000, 90000):
        for kind in ("C", "P"):
            rows.append({"symbol": f"{kind}-BTC-{k}-300926", "product_id": len(rows), "underlying": "BTC",
                         "kind": kind, "strike": float(k), "expiry": EXP, "settlement_price": 0.0,
                         "contract_value": 0.001})
    return pd.DataFrame(rows)


def test_cut_at_settlement_drops_post_expiry_flat_bars() -> None:
    c = pd.DataFrame({"timestamp": pd.date_range("2026-09-30T11:50Z", periods=6, freq="5min")})
    out = ivh.cut_at_settlement(c, EXP)
    assert list(out["timestamp"].dt.strftime("%H:%M")) == ["11:50", "11:55"]  # 11:55 bar closes at 12:00


def test_select_contracts_band() -> None:
    idx = index_frame()
    lo, hi = idx["low"].min(), idx["high"].max()
    sel = ivh.select_contracts(meta_frame(), {"BTC": idx}, band=0.03, window_hours=32)
    assert set(sel["strike"]) == {k for k in (80000, 84000, 85000, 86000, 90000) if lo * 0.97 <= k <= hi * 1.03}
    assert len(ivh.select_contracts(meta_frame(), {}, 0.03, 32)) == 0


def synthetic_option_candles(idx: pd.DataFrame, meta: pd.DataFrame, iv: float = 0.45) -> pd.DataFrame:
    rows = []
    for _, m in meta.iterrows():
        for _, b in idx.iterrows():
            close_t = b["timestamp"] + pd.Timedelta("5min")
            if close_t > EXP or b["timestamp"] < EXP - pd.Timedelta(hours=30):
                continue
            t = year_fraction((EXP - close_t).total_seconds())
            px = bs_price(b["close"], m["strike"], t, iv, m["kind"])
            rows.append({"timestamp": b["timestamp"], "open": px, "high": px, "low": px, "close": px,
                         "volume": 0.0 if b.name % 7 == 0 else 5.0, "symbol": m["symbol"]})
    return pd.DataFrame(rows)


def test_iv_observations_recover_true_vol_for_calls_and_puts() -> None:
    idx, meta = index_frame(), meta_frame()
    meta = meta[meta["strike"].isin([84000, 85000, 86000])]
    obs = ivh.compute_iv_observations(synthetic_option_candles(idx, meta), meta, idx)
    assert len(obs) and (obs["volume"] > 0).all()  # zero-volume (untraded) bars excluded
    good = obs[obs["t_hours"] > 0.5]
    assert good["iv"].notna().mean() > 0.97
    np.testing.assert_allclose(good["iv"].dropna(), 0.45, rtol=1e-4)
    assert set(good["kind"]) == {"C", "P"}
    assert (obs["known_at"] == obs["timestamp"] + pd.Timedelta("5min")).all()
    expected = (EXP - obs["known_at"]).dt.total_seconds() / 3600
    np.testing.assert_allclose(obs["t_hours"], expected)
    assert obs["t_hours"].min() >= 0 and obs["known_at"].max() <= EXP


def test_atm_hourly_buckets_and_availability() -> None:
    obs = pd.DataFrame({
        "known_at": pd.to_datetime(["2026-09-30T05:00Z", "2026-09-30T05:05Z", "2026-09-30T05:10Z"]),
        "t_hours": [7.0, 6.9, 6.8], "log_moneyness": [0.0, 0.005, 0.02], "iv": [0.4, 0.5, 0.9],
        "volume": [1.0, 3.0, 9.0]})
    h = ivh.atm_iv_hourly(obs)
    # the bar closing exactly at 05:00 belongs to the 04:00 hour; the 0.02-moneyness bar is not ATM
    assert list(h["hour"].dt.strftime("%H:%M")) == ["04:00", "05:00"]
    assert list(h["atm_iv_6_30h"]) == [0.4, 0.5]
    assert (h["available_at"] == h["hour"] + pd.Timedelta("1h")).all()


def test_weighted_median() -> None:
    assert ivh._weighted_median(np.array([0.3, 0.4, 0.9]), np.array([1.0, 1.0, 5.0])) == 0.9
    assert ivh._weighted_median(np.array([0.3, 0.4, 0.9]), np.array([5.0, 1.0, 1.0])) == 0.3


def test_fit_smile_recovers_shape() -> None:
    rng = np.random.default_rng(2)
    n = 400
    k = rng.uniform(-0.04, 0.04, n)
    known = pd.Timestamp("2026-09-30T05:30Z") + pd.to_timedelta(rng.integers(0, 3600 * 5, n), unit="s")
    atm = 0.4
    obs = pd.DataFrame({"known_at": known, "t_hours": 10.0, "log_moneyness": k,
                        "iv": atm - 0.3 * k + 50 * k ** 2, "volume": 1.0})
    hourly = pd.DataFrame({"hour": pd.date_range("2026-09-30T04:00Z", periods=8, freq="1h"), "atm_iv_6_30h": atm})
    sm = ivh.fit_smile(obs, hourly)["6_30h"]
    assert sm["slope"] == pytest.approx(-0.3, abs=1e-6) and sm["curvature"] == pytest.approx(50, abs=1e-4)


def test_settlement_twap_and_check() -> None:
    idx = index_frame(start="2026-09-30T10:00Z", n=40)
    twap = ivh.settlement_index_twap(idx, EXP)
    win = idx[(idx["timestamp"] >= EXP - pd.Timedelta("30min")) & (idx["timestamp"] < EXP)]
    assert len(win) == 6 and twap == pytest.approx(win["close"].mean())
    meta = pd.DataFrame([{"underlying": "BTC", "expiry": EXP, "kind": "C", "strike": 84000.0,
                          "settlement_price": twap - 84000.0}])
    chk = ivh.settlement_check(meta, {"BTC": idx})
    assert chk["error_pct"].abs().iloc[0] < 1e-9


class FakeListClient:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def request(self, method, path, params=None, **kw):
        self.calls.append(params)
        i = len(self.calls) - 1
        return {"result": self.pages[i], "meta": {"after": f"c{i + 1}" if i + 1 < len(self.pages) else None}}


def _product(sym, settle, kind="call_options"):
    return {"symbol": sym, "id": 1, "underlying_asset": {"symbol": "BTC"}, "contract_type": kind,
            "strike_price": "85000", "settlement_time": settle, "settlement_price": "12.5", "contract_value": "0.001"}


def test_list_expired_stops_after_old_pages() -> None:
    pages = [[_product("C-1", "2026-09-30T12:00:00Z"), _product("P-1", "2026-09-29T12:00:00Z", "put_options")],
             [_product("C-old", "2025-11-01T12:00:00Z")],
             [_product("C-older", "2025-10-01T12:00:00Z")]]
    client = FakeListClient(pages)
    df = ivh.list_expired_options(client, ("BTC",), dt.datetime(2025, 12, 1, tzinfo=dt.timezone.utc))
    assert list(df["symbol"]) == ["P-1", "C-1"] and len(client.calls) == 2
    assert client.calls[0]["contract_types"] == "call_options,put_options"
    assert df["kind"].tolist() == ["P", "C"] and df["settlement_price"].iloc[0] == 12.5


class FakeCandleClient:
    def __init__(self, fail_symbols=()):
        self.fail, self.n = set(fail_symbols), 0

    def get_candles(self, symbol, resolution, start, end):
        self.n += 1
        if symbol in self.fail:
            raise ConnectionError("boom")
        return [{"time": end - 600, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 2},
                {"time": end + 300, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0}]  # post-settlement bar


def test_fetch_caches_complete_expiries_only(tmp_path) -> None:
    store = ivh.IvHistoryStore(tmp_path)
    sel = meta_frame().iloc[:4]
    bad = FakeCandleClient(fail_symbols={sel["symbol"].iloc[0]})
    stats = ivh.fetch_expiry_candles(lambda: bad, store, sel, 32, workers=1)
    assert stats["errors"] == 1 and not store.expiry_path("BTC", EXP).exists()  # partial: retried next run
    good = FakeCandleClient()
    ivh.fetch_expiry_candles(lambda: good, store, sel, 32, workers=2)
    df = pd.read_parquet(store.expiry_path("BTC", EXP))
    assert len(df) == 4 and set(df["symbol"]) == set(sel["symbol"])  # one pre-settlement bar each
    again = FakeCandleClient()
    ivh.fetch_expiry_candles(lambda: again, store, sel, 32, workers=1)
    assert again.n == 0  # settled data never refetched

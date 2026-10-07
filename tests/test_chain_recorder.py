"""Phase 2: the option-chain recorder (normalisation, persistence, deduplication, timestamps) and the data-quality /
coverage layer. Offline: tickers are hand-written dicts shaped like Delta's verified `/v2/tickers` payload."""
from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest

from delta_intelligence.database import db
from delta_intelligence.database.models import ChainSnapshot
from delta_intelligence.execution import chain_recorder as cr
from delta_intelligence.options import chain_quality as cq
from delta_intelligence.options.chain import OptionChain

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 2, 6, 0, tzinfo=UTC)
NOW_US = int(NOW.timestamp() * 1e6)


def ticker(symbol="C-BTC-85000-031026", bid="720", ask="740", strike=85000, ts_us=NOW_US, **over):
    t = {
        "symbol": symbol, "strike_price": str(strike), "mark_price": "730", "oi": "12.5", "oi_contracts": "12500",
        "volume": 3021.4, "timestamp": ts_us, "product_id": 123, "tick_size": "0.5", "contract_value": "0.001",
        "turnover_usd": 12345.6, "oi_value_usd": 99999.0, "product_trading_status": "operational",
        "quotes": {"best_bid": bid, "best_ask": ask, "bid_size": "300", "ask_size": "250", "mark_iv": "0.45",
                   "bid_iv": "0.44", "ask_iv": "0.46", "iv_extra": "0.1"},
        "greeks": {"delta": "0.52", "gamma": "0.0001", "theta": "-35.5", "vega": "40.1", "rho": "0.5", "spot": "85010"},
        "leverage": 100, "mark_vol": "0.45", "sort_priority": 1,
    }
    t.update(over)
    return t


def chain_of(*tickers) -> OptionChain:
    return OptionChain.from_tickers(list(tickers))


@pytest.fixture
def temp_db(tmp_path):
    db.reset_engine()
    db.init_db(f"sqlite:///{(tmp_path / 'c.db').as_posix()}")
    yield
    db.reset_engine()


# ---- normalisation ---------------------------------------------------------------------------------------------------
def test_normalise_maps_every_exposed_field_and_keeps_the_rest() -> None:
    n = cr.normalize_raw(ticker())
    assert n["quote_ts_us"] == NOW_US and n["gamma"] == 0.0001 and n["theta"] == -35.5 and n["vega"] == 40.1
    assert n["rho"] == 0.5 and n["bid_iv"] == 0.44 and n["ask_iv"] == 0.46 and n["tick_size"] == 0.5
    assert n["contract_value"] == 0.001 and n["product_id"] == 123 and n["trading_status"] == "operational"
    assert n["turnover_usd"] == 12345.6 and n["oi_value_usd"] == 99999.0
    extra = json.loads(n["extra"])
    assert extra["leverage"] == 100 and extra["mark_vol"] == "0.45" and extra["quotes.iv_extra"] == "0.1"  # nothing lost


def test_missing_or_malformed_fields_become_none_never_zero() -> None:
    n = cr.normalize_raw(ticker(quotes={"best_bid": "", "best_ask": None, "mark_iv": "abc"}, greeks={}, timestamp=None,
                                tick_size="n/a", turnover_usd=float("nan")))
    for k in ("quote_ts_us", "gamma", "theta", "vega", "rho", "bid_iv", "ask_iv", "tick_size", "turnover_usd"):
        assert n[k] is None, k
    assert cr.normalize_raw(None) == {} and cr.normalize_raw({}) == {}
    assert cr.num("1e3") == 1000.0 and cr.num(True) is None and cr.num(float("inf")) is None and cr.num("") is None


def test_unknown_future_api_fields_do_not_break_recording(temp_db) -> None:
    t = ticker(brand_new_field="x", nested_new={"a": 1}, quotes={"best_bid": "720", "best_ask": "740", "new_quote_thing": 7})
    assert cr.record(chain_of(t), {"BTC": 85000}, NOW) == 1
    with db.get_session() as s:
        row = s.query(ChainSnapshot).one()
        assert json.loads(row.extra)["brand_new_field"] == "x" and "nested_new" not in json.loads(row.extra)


# ---- persistence, timestamps, deduplication ---------------------------------------------------------------------------
def test_record_persists_all_fields_with_utc_times_and_dte(temp_db) -> None:
    n = cr.record(chain_of(ticker(), ticker("P-BTC-85000-031026", bid="700", ask="715")), {"BTC": 85000}, NOW)
    assert n == 2
    with db.get_session() as s:
        r = s.query(ChainSnapshot).filter_by(symbol="C-BTC-85000-031026").one()
        assert r.taken_at == NOW.replace(tzinfo=None) and r.expiry == dt.datetime(2026, 10, 3, 12, 0)
        assert r.dte_days == pytest.approx(30 / 24) and r.source == "REAL_RECORDED"
        assert (r.bid, r.ask, r.bid_size, r.ask_size, r.mark, r.mark_iv, r.delta) == (720.0, 740.0, 300.0, 250.0, 730.0, 0.45, 0.52)
        assert r.gamma == 0.0001 and r.theta == -35.5 and r.vega == 40.1 and r.quote_ts_us == NOW_US
        assert r.open_interest == 12500.0 and r.volume == 3021.4 and r.spot == 85010.0


def test_recording_the_same_snapshot_twice_adds_nothing(temp_db) -> None:
    ch = chain_of(ticker(), ticker("P-BTC-85000-031026"))
    assert cr.record(ch, {"BTC": 85000}, NOW) == 2
    assert cr.record(ch, {"BTC": 85000}, NOW) == 0
    assert cr.record(ch, {"BTC": 85000}, NOW + dt.timedelta(minutes=5)) == 2  # a later snapshot is new
    with db.get_session() as s:
        assert s.query(ChainSnapshot).count() == 4


def test_naive_timestamps_are_refused(temp_db) -> None:
    with pytest.raises(ValueError):
        cr.record(chain_of(ticker()), {"BTC": 85000}, dt.datetime(2026, 10, 2, 6, 0))


def test_band_and_expired_contracts_are_excluded(temp_db) -> None:
    far = ticker("C-BTC-99000-031026", strike=99000)
    expired = ticker("C-BTC-85000-011026")  # expiry 2026-10-01 12:00, before NOW
    n = cr.record(chain_of(ticker(), far, expired), {"BTC": 85000}, NOW)
    assert n == 1


def test_modelled_values_never_enter_the_table(temp_db) -> None:
    cr.record(chain_of(ticker()), {"BTC": 85000}, NOW)
    with db.get_session() as s:
        assert {r.source for r in s.query(ChainSnapshot)} == {"REAL_RECORDED"}


def test_additive_migration_adds_the_new_columns_to_an_old_table(tmp_path) -> None:
    import sqlalchemy as sa

    db.reset_engine()
    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    eng = sa.create_engine(url)
    with eng.begin() as c:
        c.execute(sa.text("CREATE TABLE chain_snapshots (id INTEGER PRIMARY KEY, taken_at DATETIME NOT NULL, underlying VARCHAR(8) "
                          "NOT NULL, symbol VARCHAR(48) NOT NULL, kind VARCHAR(1) NOT NULL, strike FLOAT NOT NULL, expiry DATETIME "
                          "NOT NULL, spot FLOAT, bid FLOAT, ask FLOAT, bid_size FLOAT, ask_size FLOAT, mark FLOAT, mark_iv FLOAT, "
                          "delta FLOAT, open_interest FLOAT, volume FLOAT)"))
        c.execute(sa.text("INSERT INTO chain_snapshots (taken_at, underlying, symbol, kind, strike, expiry) VALUES "
                          "('2026-10-01 00:00:00', 'BTC', 'C-BTC-1-1', 'C', 1, '2026-10-02 12:00:00')"))
    added = db.init_db(url)
    assert {"chain_snapshots.gamma", "chain_snapshots.theta", "chain_snapshots.quote_ts_us", "chain_snapshots.extra"} <= set(added)
    with db.get_session() as s:
        assert s.query(ChainSnapshot).one().gamma is None  # old rows keep NULLs, never invented values
    db.reset_engine()


# ---- data quality -----------------------------------------------------------------------------------------------------
def frame(rows: list[dict]) -> pd.DataFrame:
    base = {"underlying": "BTC", "symbol": "C-BTC-85000-031026", "kind": "C", "strike": 85000.0, "spot": 85000.0,
            "bid": 720.0, "ask": 740.0, "mark": 730.0, "bid_size": 300.0, "ask_size": 250.0, "open_interest": 5000.0,
            "volume": 100.0, "delta": 0.5, "gamma": 1e-4, "theta": -30.0, "vega": 40.0, "mark_iv": 0.45,
            "taken_at": pd.Timestamp(NOW), "expiry": pd.Timestamp("2026-10-03T12:00Z"), "dte_days": 30 / 24,
            "quote_ts_us": NOW_US}
    return pd.DataFrame([{**base, **r} for r in rows])


def flags_of(row: dict, **kw) -> dict:
    d, _ = cq.assess(frame([row]), **kw)
    return {f: bool(d[f].iloc[0]) for f in cq.FLAGS} | {"tier": d["tier"].iloc[0]}


def test_clean_two_sided_quote_is_a_real_quote() -> None:
    f = flags_of({})
    assert f["tier"] == cq.REAL_QUOTE and not any(f[x] for x in cq.FLAGS if x != "ILLIQUID")


def test_crossed_market_is_flagged_and_unusable_for_prices() -> None:
    f = flags_of({"bid": 750.0, "ask": 740.0})
    assert f["CROSSED"] and f["tier"] == cq.REAL_MARK_ONLY  # the mark still exists, but it is not a bid or an ask


def test_zero_negative_and_missing_prices() -> None:
    assert flags_of({"bid": 0.0})["NONPOSITIVE_PRICE"] and flags_of({"bid": 0.0})["ONE_SIDED"]
    assert flags_of({"bid": 0.0})["tier"] == cq.REAL_MARK_ONLY
    f = flags_of({"bid": -1.0, "ask": -2.0, "mark": -3.0})
    assert f["NONPOSITIVE_PRICE"] and f["tier"] == cq.UNUSABLE
    assert flags_of({"bid": np.nan, "ask": np.nan, "mark": np.nan})["tier"] == cq.UNUSABLE


def test_a_mark_without_a_two_sided_quote_is_never_a_real_quote() -> None:
    assert flags_of({"bid": np.nan, "ask": 740.0})["tier"] == cq.REAL_MARK_ONLY
    assert flags_of({"bid": np.nan, "ask": 740.0})["ONE_SIDED"]


def test_stale_and_future_quotes() -> None:
    old = NOW_US - 3600 * 1_000_000
    assert flags_of({"quote_ts_us": old})["STALE_QUOTE"] and flags_of({"quote_ts_us": old})["tier"] == cq.REAL_MARK_ONLY
    assert not flags_of({"quote_ts_us": NOW_US - 120 * 1_000_000})["STALE_QUOTE"]
    assert flags_of({"quote_ts_us": NOW_US + 3600 * 1_000_000})["STALE_QUOTE"]  # a clock fault, not a live quote
    assert flags_of({"quote_ts_us": NOW_US - 700 * 1_000_000}, cfg=cq.QualityConfig(max_quote_age_sec=900))["STALE_QUOTE"] is False


def test_extreme_spread_illiquid_and_missing_greeks() -> None:
    assert flags_of({"bid": 100.0, "ask": 900.0})["EXTREME_SPREAD"]
    assert not flags_of({})["EXTREME_SPREAD"]
    assert flags_of({"open_interest": 5.0})["ILLIQUID"] and flags_of({"bid_size": 1.0})["ILLIQUID"]
    assert flags_of({"gamma": np.nan})["MISSING_GREEKS"] and flags_of({"vega": np.nan})["MISSING_GREEKS"]


def test_impossible_dte_and_expiry_mismatch() -> None:
    assert flags_of({"dte_days": -0.5})["IMPOSSIBLE_DTE"]
    assert flags_of({"dte_days": 400.0, "expiry": pd.Timestamp("2027-11-01T12:00Z"), "symbol": "C-BTC-85000-011127"})["IMPOSSIBLE_DTE"]
    assert flags_of({"dte_days": 3.0})["IMPOSSIBLE_DTE"]  # stored DTE disagrees with expiry - taken_at
    assert flags_of({"expiry": pd.Timestamp("2026-10-04T12:00Z"), "dte_days": 54 / 24})["EXPIRY_MISMATCH"]  # symbol says 031026
    assert not flags_of({})["EXPIRY_MISMATCH"]


def test_duplicate_snapshots_are_flagged_once() -> None:
    d, rep = cq.assess(frame([{}, {}, {"symbol": "P-BTC-85000-031026", "kind": "P"}]))
    assert rep.flag_counts["DUPLICATE"] == 1 and d["DUPLICATE"].tolist() == [False, True, False]
    assert d["tier"].tolist() == [cq.REAL_QUOTE, cq.UNUSABLE, cq.REAL_QUOTE]  # a duplicate is never counted twice


def test_snapshot_gaps_are_found_but_a_steady_cadence_is_not_a_gap() -> None:
    times = [NOW + dt.timedelta(minutes=5 * i) for i in range(6)] + [NOW + dt.timedelta(minutes=5 * 5 + 120)]
    d, rep = cq.assess(frame([{"taken_at": pd.Timestamp(t), "quote_ts_us": int(t.timestamp() * 1e6),
                               "dte_days": (pd.Timestamp("2026-10-03T12:00Z") - pd.Timestamp(t)).total_seconds() / 86400} for t in times]))
    assert len(rep.gaps) == 1 and rep.gaps[0][3] == 7200.0 and rep.median_interval_sec["BTC"] == 300.0
    steady = frame([{"taken_at": pd.Timestamp(NOW + dt.timedelta(seconds=60 * i)),
                     "quote_ts_us": int((NOW + dt.timedelta(seconds=60 * i)).timestamp() * 1e6),
                     "dte_days": (pd.Timestamp("2026-10-03T12:00Z") - pd.Timestamp(NOW + dt.timedelta(seconds=60 * i))).total_seconds() / 86400}
                    for i in range(10)])
    assert cq.assess(steady)[1].gaps == []


def test_assess_never_changes_recorded_values() -> None:
    f = frame([{"bid": 750.0, "ask": 740.0}, {"bid": 0.0}])
    d, _ = cq.assess(f)
    assert d["bid"].tolist() == [750.0, 0.0] and d["ask"].tolist() == [740.0, 740.0]


def test_empty_input_is_handled() -> None:
    d, rep = cq.assess(frame([]).iloc[0:0].reindex(columns=frame([{}]).columns))
    assert rep.n_rows == 0 and cq.coverage(d)["by_day"].empty


# ---- coverage ---------------------------------------------------------------------------------------------------------
def test_coverage_counts_real_quotes_by_asset_date_expiry_type_region_dte() -> None:
    rows = []
    for i in range(4):
        t = NOW + dt.timedelta(minutes=5 * i)
        dte = (pd.Timestamp("2026-10-03T12:00Z") - pd.Timestamp(t)).total_seconds() / 86400
        base = {"taken_at": pd.Timestamp(t), "quote_ts_us": int(t.timestamp() * 1e6), "dte_days": dte}
        rows += [{**base}, {**base, "symbol": "P-BTC-85000-031026", "kind": "P"},
                 {**base, "symbol": "C-BTC-90000-031026", "strike": 90000.0, "bid": np.nan}]
    d, rep = cq.assess(frame(rows))
    c = cq.coverage(d)
    day = c["by_day"].iloc[0]
    assert day["rows"] == 12 and day["snapshots"] == 4 and day["contracts"] == 3 and day["real_quote_rows"] == 8
    assert day["real_quote_pct"] == pytest.approx(66.7, abs=0.1)
    assert set(c["by_kind"]["kind"]) == {"C", "P"} and c["by_expiry"]["expiry_date"].iloc[0] == "2026-10-03"
    assert any("ATM" in r for r in c["by_region"]["region"]) and any("OTM" in r for r in c["by_region"]["region"])
    assert c["by_timeframe"]["bars_with_a_snapshot_pct"].iloc[0] == 100.0 and c["by_timeframe"]["timeframe"].iloc[0] == "5m bars"


def test_load_snapshots_round_trips_through_the_database(temp_db) -> None:
    cr.record(chain_of(ticker(), ticker("P-BTC-85000-031026", bid="", ask="")), {"BTC": 85000}, NOW)
    d = cq.load_snapshots()
    assert len(d) == 2 and str(d["taken_at"].dt.tz) == "UTC" and d["bid"].isna().sum() == 1
    assessed, rep = cq.assess(d)
    assert rep.tier_counts[cq.REAL_QUOTE] == 1 and rep.tier_counts[cq.REAL_MARK_ONLY] == 1
    assert len(cq.load_snapshots(underlying="ETH")) == 0
    assert len(cq.load_snapshots(since=pd.Timestamp(NOW + dt.timedelta(hours=1)))) == 0

"""
DataManager: the single entry point for candles.

**Source order:**
1. User CSV/Parquet in `data_cache/csv/`. When a file exists for the symbol/timeframe it is used as-is and never
   mixed with exchange data.
2. Delta public candles, through a Parquet cache in `data_cache/parquet_cache/<data env>/`.
3. Otherwise `DataUnavailableError`, with the reason. **There is no synthetic fallback.**

**Cache.** A cached series is re-requested only when a newer bar has CLOSED, and then only the missing tail is
fetched and merged. That costs about one small request per symbol per bar. The cache can always be rebuilt from the
public endpoint (e.g. after a Streamlit Cloud reboot wipes the disk). A refresh that fails serves the cached bars,
and the quality gate marks them stale. The result is NO TRADE, never a silent guess.

Every result goes through the quality gate, and the returned metadata records its source and quality.
"""
from __future__ import annotations

import datetime as dt
import threading
import time
from pathlib import Path

import pandas as pd

from delta_intelligence.data.quality import QUALITY_FAIL, QualityReport, clean_ohlcv, validate_ohlcv
from delta_intelligence.data_adapters.base import DataAdapter
from delta_intelligence.data_adapters.csv_adapter import CSVAdapter
from delta_intelligence.data_adapters.delta_adapter import DeltaAdapter
from delta_intelligence.utils.logging_utils import log_event
from delta_intelligence.utils.timeutil import ensure_utc, last_closed_bar_start, now_utc, timeframe_seconds

# Don't re-ask Delta for the same series more often than this: a just-closed bar may not be published yet, and every
# caller (scanner, tabs) reaching here would otherwise re-request it.
MIN_REFETCH_INTERVAL_SEC = 20.0
# A cache this far behind is rebuilt in full rather than tail-patched.
MAX_TAIL_GAP_DAYS = 30
# History kept in each cache file.
MAX_CACHE_DAYS = 400


class DataUnavailableError(RuntimeError):
    """No real market data (CSV or Delta) could be obtained. The message says why."""


_attempt_lock = threading.Lock()
_last_attempt: dict[tuple[str, str, str], float] = {}


def _safe_name(symbol: str) -> str:
    return symbol.replace(":", "_").replace("/", "_")


class DataManager:
    def __init__(self, delta_adapter: DeltaAdapter, cache_dir: Path, csv_adapter: CSVAdapter | None = None,
                 now=now_utc, monotonic=time.monotonic):
        self.delta = delta_adapter
        self.csv = csv_adapter
        self.cache_dir = Path(cache_dir)
        self.now = now
        self.monotonic = monotonic
        self.last_errors: list[str] = []

    @classmethod
    def from_settings(cls, settings=None) -> "DataManager":
        from delta_intelligence.brokers.delta_api_client import public_client
        from delta_intelligence.config.settings import get_settings

        s = settings or get_settings()
        return cls(DeltaAdapter(public_client(s)), s.parquet_cache_dir, CSVAdapter(s.csv_dir))

    def cache_path(self, symbol: str, timeframe: str) -> Path:
        return self.cache_dir / f"{_safe_name(symbol)}_{timeframe}.parquet"

    # ---- public API ----------------------------------------------------------------------------------------------
    def get_ohlcv(self, symbol: str, timeframe: str, start: dt.datetime, end: dt.datetime | None = None,
                  force_refresh: bool = False) -> tuple[pd.DataFrame, dict]:
        """(candles, metadata) for closed bars in [start, end]. Metadata includes source and the quality report."""
        start = ensure_utc(start)
        end = ensure_utc(end or self.now())
        if self.csv is not None and self.csv.path_for(symbol, timeframe) is not None:
            df = self.csv.get_ohlcv(symbol, timeframe, start, end)
            if len(df):
                return self._finalize(df, symbol, timeframe, "csv", check_volume=True)
        df, source, refresh_error = self._from_delta(symbol, timeframe, start, end, force_refresh,
                                                    fetch=lambda s, e: self.delta.get_ohlcv(symbol, timeframe, s, e))
        return self._finalize(df, symbol, timeframe, source, refresh_error=refresh_error, check_volume=True)

    def get_series(self, kind: str, symbol: str, timeframe: str, start: dt.datetime, end: dt.datetime | None = None,
                   force_refresh: bool = False) -> tuple[pd.DataFrame, dict]:
        """MARK, FUNDING or OI series for `symbol` (cached like candles). Volume is NaN for these series."""
        key = f"{kind.upper()}:{symbol}"
        start = ensure_utc(start)
        end = ensure_utc(end or self.now())
        df, source, refresh_error = self._from_delta(
            key, timeframe, start, end, force_refresh,
            fetch=lambda s, e: self.delta.get_series(kind, symbol, timeframe, s, e))
        return self._finalize(df, key, timeframe, source, refresh_error=refresh_error, check_volume=False,
                              recent_window_bars=0, allow_nonpositive=kind.upper() == "FUNDING")

    # ---- delta + cache -------------------------------------------------------------------------------------------
    def _may_request(self, key: tuple[str, str, str]) -> bool:
        with _attempt_lock:
            now = self.monotonic()
            if now - _last_attempt.get(key, float("-inf")) < MIN_REFETCH_INTERVAL_SEC:
                return False
            _last_attempt[key] = now
            return True

    def _fetch(self, fetch, start: dt.datetime, end: dt.datetime) -> pd.DataFrame | None:
        try:
            return fetch(start, end)
        except Exception as exc:  # network, rate limit, breaker: report, don't invent
            self.last_errors.append(f"{type(exc).__name__}: {exc}")
            return None

    def _from_delta(self, symbol, timeframe, start, end, force_refresh, fetch) -> tuple[pd.DataFrame, str, str | None]:
        self.last_errors = []
        path = self.cache_path(symbol, timeframe)
        step = pd.Timedelta(seconds=timeframe_seconds(timeframe))
        key = (str(self.cache_dir), symbol, timeframe)
        cached = self._read_cache(path) if not force_refresh else None

        if cached is not None and len(cached) and cached["timestamp"].iloc[0] <= pd.Timestamp(start) + step:
            last_cached = cached["timestamp"].iloc[-1]
            expected = pd.Timestamp(last_closed_bar_start(min(end, self.now()), timeframe))
            if last_cached >= expected:
                return self._slice(cached, start, end), "cache", None
            if (pd.Timestamp(end) - last_cached).days <= MAX_TAIL_GAP_DAYS:
                if not self._may_request(key):
                    return self._slice(cached, start, end), "cache", None
                tail_start = (last_cached - 2 * step).to_pydatetime()
                tail = self._fetch(fetch, tail_start, end)
                if tail is None:
                    reason = "; ".join(self.last_errors)
                    log_event("data_manager", f"Tail refresh failed for {symbol} {timeframe}; serving cached bars",
                              level="WARNING", reason=reason)
                    return self._slice(cached, start, end), "cache", reason
                merged = self._merge(cached, tail[tail["timestamp"] >= pd.Timestamp(tail_start)])
                self._write_cache(path, merged)
                return self._slice(merged, start, end), "cache+tail", None

        with _attempt_lock:
            _last_attempt[key] = self.monotonic()
        full = self._fetch(fetch, start, end)
        if full is None or len(full) == 0:
            reason = "; ".join(self.last_errors) or "Delta returned no candles for this range"
            log_event("data_manager", f"No real data for {symbol} {timeframe}: {reason}", level="ERROR")
            raise DataUnavailableError(f"No real market data for {symbol} {timeframe}: {reason}. "
                                       "Check connectivity on the System Health page or add a CSV to data_cache/csv/.")
        merged = self._merge(cached, full) if cached is not None and len(cached) else full
        self._write_cache(path, merged)
        return self._slice(merged, start, end), "delta", None

    @staticmethod
    def _slice(df: pd.DataFrame, start: dt.datetime, end: dt.datetime) -> pd.DataFrame:
        return df[(df["timestamp"] >= pd.Timestamp(start)) & (df["timestamp"] <= pd.Timestamp(end))].reset_index(drop=True)

    @staticmethod
    def _merge(a: pd.DataFrame | None, b: pd.DataFrame) -> pd.DataFrame:
        frames = [f for f in (a, b) if f is not None and len(f)]
        if not frames:
            return b
        out = pd.concat(frames, ignore_index=True)
        out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True)
        return out.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)

    @staticmethod
    def _read_cache(path: Path) -> pd.DataFrame | None:
        if not path.exists():
            return None
        try:
            df = pd.read_parquet(path)
        except Exception:
            return None  # corrupt cache: rebuild from the exchange
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        return df.sort_values("timestamp").reset_index(drop=True)

    def _write_cache(self, path: Path, df: pd.DataFrame) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            cutoff = pd.Timestamp(self.now()) - pd.Timedelta(days=MAX_CACHE_DAYS)
            tmp = path.with_suffix(".tmp")
            df[df["timestamp"] >= cutoff].to_parquet(tmp, index=False)
            tmp.replace(path)  # atomic: a crash never leaves a half-written cache
        except Exception as exc:
            log_event("data_manager", f"Could not write candle cache {path.name}: {exc}", level="WARNING")

    # ---- quality + metadata --------------------------------------------------------------------------------------
    def _finalize(self, df: pd.DataFrame, symbol: str, timeframe: str, source: str, refresh_error: str | None = None,
                  check_volume: bool = True, recent_window_bars: int = 288,
                  allow_nonpositive: bool = False) -> tuple[pd.DataFrame, dict]:
        now = self.now()
        df = clean_ohlcv(df, timeframe, now=now)
        report: QualityReport = validate_ohlcv(df, timeframe, now=now, check_volume=check_volume,
                                               recent_window_bars=recent_window_bars,
                                               allow_nonpositive=allow_nonpositive)
        if refresh_error:
            report.issues.append(f"latest refresh failed: {refresh_error}")
        metadata = {
            "instrument": symbol,
            "timeframe": timeframe,
            "source": source,
            "start_ts": df["timestamp"].min() if len(df) else None,
            "end_ts": df["timestamp"].max() if len(df) else None,
            "n_rows": len(df),
            "quality_status": report.status,
            "quality_report": report.as_dict(),
        }
        if report.status != "OK":
            log_event("data_manager", f"{symbol} {timeframe} from {source}: quality {report.status}",
                      level="ERROR" if report.status == QUALITY_FAIL else "WARNING", issues=report.issues)
        try:
            from delta_intelligence.database.db import persist_market_data_metadata  # available from P4

            persist_market_data_metadata(metadata)
        except Exception:
            pass
        return df, metadata


__all__ = ["DataManager", "DataUnavailableError", "DataAdapter"]

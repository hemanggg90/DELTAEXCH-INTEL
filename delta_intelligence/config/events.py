"""
Scheduled-events calendar for the Pre-Event Long Straddle/Strangle strategy.

File: `config/events.csv` (user-maintained), with columns:

    timestamp_utc,name,importance
    2026-11-04T19:00:00Z,FOMC rate decision,high

- Times must be in UTC (ISO 8601 with Z or an offset).
- The assistant does NOT invent or verify future event dates: the user supplies them. With no file, or an empty one,
  the event strategy never fires, and the research report says so.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from delta_intelligence.config.settings import PROJECT_ROOT

EVENTS_PATH = PROJECT_ROOT / "config" / "events.csv"
COLUMNS = ["timestamp_utc", "name", "importance"]


def load_events(path: Path | None = None) -> pd.DataFrame:
    p = Path(path or EVENTS_PATH)
    if not p.exists():
        return pd.DataFrame(columns=COLUMNS).assign(timestamp_utc=pd.Series(dtype="datetime64[ns, UTC]"))
    df = pd.read_csv(p, comment="#")
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{p.name}: missing columns {missing}")
    ts = pd.to_datetime(df["timestamp_utc"], utc=False, format="ISO8601")
    if ts.dt.tz is None:
        raise ValueError(f"{p.name}: timestamps need an explicit timezone (e.g. ...Z)")
    return df.assign(timestamp_utc=ts.dt.tz_convert("UTC")).sort_values("timestamp_utc").reset_index(drop=True)

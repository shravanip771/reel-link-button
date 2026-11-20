"""Small time helpers. All timestamps are naive UTC strings: YYYY-MM-DDTHH:MM:SS."""
from __future__ import annotations

from datetime import datetime, timezone

FMT = "%Y-%m-%dT%H:%M:%S"


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def iso(dt: datetime) -> str:
    return dt.strftime(FMT)


def parse(s: str) -> datetime:
    return datetime.strptime(s, FMT)

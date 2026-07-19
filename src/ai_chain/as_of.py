from __future__ import annotations

from datetime import date, datetime, time, timezone


def as_of_timestamp(value: date | datetime) -> datetime:
    """Normalize a research cutoff; a DATE means end-of-day UTC."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return datetime.combine(value, time.max, tzinfo=timezone.utc)


def as_of_date(value: date | datetime) -> date:
    return as_of_timestamp(value).date()

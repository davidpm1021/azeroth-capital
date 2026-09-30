"""Parse source times before ordering; HTTP dates are not lexically sortable."""
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from functools import lru_cache


@lru_cache(maxsize=8192)
def parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = parsedate_to_datetime(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def observed_at(row: dict) -> str:
    return row.get("observed_at") or row.get("source_modified_at") or row["started_at"]


def future_row(history: list[dict], start_at: str, horizon_hours: int,
               tolerance_hours: float = 1.5) -> dict | None:
    """First observation at/after the target, within the allowed delay.

    Never report an unfinished holding period as a matured result. The same
    historical observation remains selected when newer snapshots arrive.
    """
    target = parse_timestamp(start_at) + timedelta(hours=horizon_hours)
    deadline = target + timedelta(hours=tolerance_hours)
    candidates = (row for row in history
                  if target <= parse_timestamp(observed_at(row)) <= deadline)
    return min(candidates, key=lambda row: parse_timestamp(observed_at(row)), default=None)

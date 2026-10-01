import pytest

from azeroth_capital.timestamps import future_row, parse_timestamp
from azeroth_capital.backtest import _future_row as backtest_future
from azeroth_capital.discovery import _future_row as research_future
from azeroth_capital.paper import _future_row as paper_future


@pytest.mark.parametrize("select", [
    lambda rows: backtest_future(rows, 0, 3),
    lambda rows: research_future(rows, 0, 3),
    lambda rows: paper_future(rows, rows[0]["observed_at"], 3),
])
def test_evaluation_waits_for_target_and_remains_stable(select):
    rows = [{"observed_at": f"2026-09-30T{hour:02d}:00:00Z"} for hour in [0, 2]]
    assert select(rows) is None
    rows.append({"observed_at": "Wed, 30 Sep 2026 03:02:00 GMT"})
    chosen = select(rows)
    assert chosen is rows[-1]
    rows.append({"observed_at": "2026-09-30T04:00:00Z"})
    assert select(rows) is chosen


def test_outage_does_not_become_a_shorter_or_unbounded_holding_period():
    start = "2026-09-30T00:00:00Z"
    assert future_row([{"observed_at": "2026-09-30T02:59:59Z"},
                       {"observed_at": "2026-09-30T04:30:01Z"}], start, 3) is None
    boundary = {"observed_at": "2026-09-30T04:30:00Z"}
    assert future_row([boundary], start, 3) == boundary


def test_timestamp_formats_represent_the_same_instant():
    assert parse_timestamp("Wed, 30 Sep 2026 03:00:00 GMT") == parse_timestamp("2026-09-29T23:00:00-04:00")

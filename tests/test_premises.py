from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from azeroth_capital.premises import (
    ARMS, evaluate_premises, historical_cohorts, premise_rows, strategy_names,
    summarize_premises,
)


def _signals():
    return [SimpleNamespace(item_id=i, reference_price=100,
                            baseline_depth_5_change_pct=-i*5,
                            baseline_price_change_pct=i-5) for i in range(10)]


def _fixture():
    start = datetime(2026, 9, 27, tzinfo=UTC)
    frozen = premise_rows(_signals(), start.isoformat())
    for r in frozen:
        r['created_at'] = (start+timedelta(minutes=10)).isoformat()
    histories = {(i,0): [dict(observed_at=(start+timedelta(hours=h)).isoformat(), reference_price=p)
                         for h,p in [(0,100), (1/12,1), (1,110), (6,120), (7,121)]] for i in range(10)}
    return frozen, histories


def test_premises_rank_distinct_economic_inputs_with_deterministic_ties():
    rows = premise_rows(list(reversed(_signals())), '2026-09-27T00:00:00Z')
    names = strategy_names()
    selected = {arm: [r['item_id'] for r in rows if r['strategy']==name] for arm,name in names.items()}
    assert selected['discount'] == [0,1]
    assert selected['depth'] == [9,8]
    assert selected['compression'] == [9,8]
    assert selected['market'] == list(range(10))


def test_delayed_entry_uses_publication_after_freeze_and_new_exit_clock():
    frozen, histories = _fixture()
    evaluation = evaluate_premises(frozen, histories)
    assert evaluation.cohorts_complete == 1
    summaries = summarize_premises(evaluation)
    assert len(summaries) == 8
    for row in summaries:
        assert row['net_avg'] == pytest.approx(14 if row['mode']=='source_quote' else 4.5)
        assert row['excess_discount_pp'] == pytest.approx(0)
    delayed = [r for r in evaluation.results if r.mode=='next_snapshot']
    assert {r.entry_price for r in delayed} == {110}
    assert {r.exit_price for r in delayed} == {121}


def test_missing_outcome_omits_whole_cohort_in_both_modes():
    frozen, histories = _fixture()
    histories[(9,0)] = histories[(9,0)][:-1]  # No delayed 6h exit.
    evaluation = evaluate_premises(frozen, histories)
    assert evaluation.cohorts_with_all_arms == 1
    assert evaluation.cohorts_complete == 0
    assert not evaluation.results


def test_legacy_and_partial_benchmarks_cannot_masquerade_as_matched_controls():
    frozen, histories = _fixture()
    evaluation = evaluate_premises(frozen[:-1], histories)  # Incomplete market arm.
    assert evaluation.cohorts_with_all_arms == 0
    for row in frozen:
        if row['strategy'].startswith('compression-gap-v2'):
            row['strategy'] = row['strategy'].replace('-v2-', '-')
    assert evaluate_premises(frozen, histories).cohorts_with_all_arms == 0


def test_historical_ranks_do_not_depend_on_future_prices_or_missing_exits():
    start = datetime(2026,9,27,tzinfo=UTC)
    histories = {}
    for i in range(10):
        histories[(i,0)] = [dict(item_id=i,observed_at=(start+timedelta(hours=h)).isoformat(),
            completed_at=(start+timedelta(hours=h,minutes=15)).isoformat(),
            best_price=100000-h*i*100,reference_price=100000-h*i*100,
            total_quantity=2000,depth_5pct=1000,reference_depth_5pct=1000,
            approx_market_value=200000000,listing_count=100,price_level_count=10) for h in range(6)]
    original = historical_cohorts(histories,set(range(10)))
    stamp=(start+timedelta(hours=4)).isoformat()
    expected=[r for r in original if r['observed_at']==stamp]
    histories[(9,0)][-1]['reference_price']=999999999
    histories[(0,0)].pop()
    after=[r for r in historical_cohorts(histories,set(range(10))) if r['observed_at']==stamp]
    assert after == expected


def test_summary_uses_paired_equal_cohort_weights_not_candidate_counts():
    from dataclasses import replace
    from azeroth_capital.premises import PremiseEvaluation
    frozen, histories = _fixture()
    first = evaluate_premises(frozen, histories).results
    rows = []
    for r in first:
        rows.append(replace(r, net_pct=100 if r.arm=='compression' else 0))
        for repeat in range(2):
            value = -10 if r.arm=='compression' else 20 if r.arm=='discount' else 10
            rows.append(replace(r, cohort_at='2026-09-28T00:00:00+00:00',
                                item_id=r.item_id+repeat*10, net_pct=value))
    summary = next(r for r in summarize_premises(PremiseEvaluation(results=rows))
                   if r['arm']=='compression' and r['mode']=='source_quote')
    assert summary['net_avg'] == 45  # (100-10)/2, not candidate-weighted.
    assert summary['excess_discount_pp'] == 35  # (100 + (-10-20))/2.


def test_prospective_cli_exports_auditable_entry_and_exit_rows(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from azeroth_capital import cli
    frozen, histories = _fixture()
    storage = SimpleNamespace(paper_signals=lambda: frozen, all_market_histories=lambda: histories)
    monkeypatch.setattr(cli, 'services', lambda: (None,storage))
    output = tmp_path/'premises.csv'
    result = CliRunner().invoke(cli.app, ['premise-results','--output',str(output)])
    assert result.exit_code == 0, result.output
    assert 'PROSPECTIVE' in result.output
    assert 'complete in both entry modes=1' in result.output
    import csv
    with output.open() as f:
        rows=list(csv.DictReader(f))
    assert len(rows)==32
    assert {r['mode'] for r in rows} == {'source_quote','next_snapshot'}
    assert all(r['entry_at'] and r['exit_at'] and r['available_at'] for r in rows)

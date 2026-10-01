from dataclasses import replace

import pytest

from azeroth_capital.execution import buy_with_budget, stress_execution, summarize_execution
from azeroth_capital.premises import ARMS, PremiseEvaluation, PremiseResult


ENTRY = '2026-10-01T01:00:00+00:00'
EXIT = '2026-10-01T07:00:00+00:00'


def fixture():
    rows = [PremiseResult('2026-10-01T00:00:00+00:00', ENTRY, arm,
            'next_snapshot', i, 6, ENTRY, 100, EXIT, 9999, 9899, 9399)
            for i, arm in enumerate(ARMS)]
    return PremiseEvaluation(cohorts_complete=1, results=rows)


def test_buy_walks_sorted_depth_with_whole_units_and_keeps_unspent_cash():
    book = [(300, 100), (100, 3), (200, 2)]
    assert buy_with_budget(book, 999, 100) == (5, 700)
    assert buy_with_budget(book, 1000, 4) == (4, 500)
    assert buy_with_budget(book, 100_000, 200) == (105, 30_700)
    assert buy_with_budget(book, 99, 100) == (0, 0)
    assert buy_with_budget(book, 1000, 0) == (0, 0)
    with pytest.raises(ValueError):
        buy_with_budget([(0, 1)], 1000, 1)


def test_break_even_uses_cost_of_depth_and_exit_floor_with_integer_fee_rounding():
    evaluation = stress_execution(fixture(),
        lambda item, stamp: [(100, 3), (200, 97)] if stamp == ENTRY else [(300, 1), (9999, 99)],
        budget_g=1, max_inventory_share=.05)
    row = evaluation.results[0]
    assert (row.units, row.spent_copper, row.unused_copper) == (5, 700, 9300)
    assert row.entry_vwap_copper == 140
    assert row.exit_floor_copper == 300  # Reference quote 9999 is not used for proceeds.
    assert row.all_sold_net_copper == 1425
    assert (row.break_even_exit_copper, row.break_even_units) == (148, 3)
    assert (row.break_even_sell_pct, row.half_sold_units, row.unsold_units_at_half) == (60, 2, 3)
    assert row.half_sold_net_copper == 570
    assert row.break_even_units*row.exit_floor_copper*95//100 >= row.spent_copper
    assert (row.break_even_units-1)*row.exit_floor_copper*95//100 < row.spent_copper
    summary = summarize_execution(evaluation)[0]
    assert summary['all_sold_return_on_budget_pct'] == pytest.approx(7.25)
    assert summary['all_sold_return_on_spend_pct'] == pytest.approx(103.57142857)
    assert summary['half_sale_cash_recovery_pct'] == pytest.approx(81.42857143)


def test_even_full_sale_can_fail_to_recover_cost_and_zero_purchase_is_retained():
    flat = stress_execution(fixture(), lambda *_: [(101, 100)], budget_g=1, max_inventory_share=.03)
    row = flat.results[0]
    assert (row.units, row.all_sold_net_copper, row.break_even_exit_copper) == (3, 287, 107)
    assert row.break_even_units == 4
    assert row.break_even_sell_pct > 100
    zero = stress_execution(fixture(), lambda *_: [(10001, 100)], budget_g=1)
    assert zero.complete_book_cohorts == 1
    assert len(zero.results) == 4
    summary = summarize_execution(zero)[0]
    assert summary['all_sold_return_on_budget_pct'] == 0
    assert summary['all_sold_return_on_spend_pct'] is None
    assert summary['median_break_even_sell_pct'] is None
    assert stress_execution(fixture(), lambda *_: [(1, 99)]).results[0].units == 0


def test_missing_book_removes_whole_matched_cohort_and_never_substitutes_quote():
    original = fixture()
    original.results += [replace(original.results[0], mode='source_quote')]
    report = stress_execution(original, lambda item, stamp: [] if item == 2 and stamp == EXIT else [(100, 100)])
    assert report.premise_cohorts == 1
    assert report.missing_book_orders == 1
    assert report.complete_book_cohorts == 0
    assert report.results == []


def test_summary_weights_cohorts_equally_and_includes_idle_cash():
    first = stress_execution(fixture(), lambda *_: [(100, 100)]).results[0]
    # One +100% allocated-return cohort versus three -10% orders in another.
    positive = replace(first, budget_copper=100, spent_copper=100, all_sold_net_copper=200)
    negatives = [replace(positive, cohort_at='2026-10-02', item_id=i,
                         all_sold_net_copper=90) for i in range(3)]
    evaluation = stress_execution(fixture(), lambda *_: [(100, 100)])
    evaluation.results = [positive, *negatives]
    summary = summarize_execution(evaluation)[0]
    assert summary['all_sold_return_on_budget_pct'] == pytest.approx(45)
    assert summary['largest_positive_item_id'] == 0
    assert summary['largest_positive_item_contribution_pp'] == pytest.approx(50-5/3)
    assert summary['return_without_largest_positive_item_pct'] == pytest.approx(-10/3)


@pytest.mark.parametrize('budget,share', [(0,.01), (1.5,.01), (1,0), (1,1.01), (1,float('nan'))])
def test_invalid_scenarios_rejected(budget, share):
    with pytest.raises(ValueError):
        stress_execution(fixture(), lambda *_: [], budget, share)


def test_cli_exports_auditable_conditional_orders_and_reports_empty_coverage(tmp_path, monkeypatch):
    import csv
    from types import SimpleNamespace
    from typer.testing import CliRunner
    from azeroth_capital import cli
    storage = SimpleNamespace(paper_signals=lambda: [], all_market_histories=lambda: {},
                              commodity_book=lambda *_: [(101, 100)])
    monkeypatch.setattr(cli, 'services', lambda: (None, storage))
    monkeypatch.setattr(cli, 'evaluate_premises', lambda *_: fixture())
    output = tmp_path/'orders.csv'
    result = CliRunner().invoke(cli.app, ['execution-stress', '--budget-g', '1', '--output', str(output)])
    assert result.exit_code == 0, result.output
    assert 'CONDITIONAL' in result.output
    assert 'complete books=1' in result.output
    with output.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 4
    assert rows[0]['entry_at'] == ENTRY
    assert rows[0]['exit_at'] == EXIT
    assert rows[0]['break_even_units'] == '2'
    assert rows[0]['unsold_units_at_half'] == '1'
    storage.commodity_book = lambda *_: []
    result = CliRunner().invoke(cli.app, ['execution-stress', '--output', str(output)])
    assert result.exit_code == 0, result.output
    assert 'complete books=0' in result.output
    with output.open() as f:
        assert list(csv.DictReader(f)) == []

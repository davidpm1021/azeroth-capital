from datetime import UTC, datetime, timedelta
from pathlib import Path

from azeroth_capital.paper import evaluate_paper, scan_compression_gap, summarize_paper
from azeroth_capital.storage import Storage


def _seed_market(storage: Storage, item_id: int, prices: list[int], depths: list[int]) -> None:
    start = datetime(2026, 9, 27, 0, 0, tzinfo=UTC)
    for idx, (price, depth) in enumerate(zip(prices, depths)):
        observed = start + timedelta(hours=idx)
        payload_hash = f"{item_id}-{idx}"
        raw = storage.raw_dir / f"{payload_hash}.json.gz"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(b"")
        run_id = storage.begin_run(
            "us",
            "commodities",
            payload_hash,
            raw,
            source_modified_at=observed.isoformat(),
        )
        storage.insert_observations(
            run_id,
            [{
                "item_id": item_id,
                "best_price": price,
                "quantity_at_best": 100,
                "total_quantity": 1000,
                "depth_1pct": depth,
                "depth_5pct": depth,
                "depth_10pct": depth,
                "weighted_price": float(price),
                "reference_price": price,
                "reference_quantity": 20,
                "reference_depth_5pct": depth,
                "approx_market_value": price * 1000,
                "listing_count": 100,
                "price_level_count": 20,
            }],
            "commodity",
        )
        storage.finish_run(run_id)


def test_paper_scan_and_results_are_prospective(tmp_path: Path):
    storage = Storage(tmp_path / "test.db", tmp_path / "raw")
    storage.init()
    storage.replace_expansion_catalog("Midnight", {1: {"reagent"}, 2: {"reagent"}})

    _seed_market(
        storage,
        1,
        [10_000, 10_000, 10_000, 10_000, 10_000, 11_000, 12_000, 13_000, 14_000, 15_000, 16_000, 17_000, 18_000, 19_000, 20_000, 21_000, 22_000],
        [1000, 900, 800, 700, 500, 450, 400, 350, 300, 280, 260, 240, 220, 200, 180, 160, 150],
    )
    _seed_market(
        storage,
        2,
        [10_000] * 17,
        [1000] * 17,
    )

    inserted, universe, observed_at = scan_compression_gap(
        storage,
        history_window=5,
        top_fraction=0.5,
        min_quantity=0,
        min_market_value_g=0,
        min_listings=0,
        min_price_levels=0,
    )

    assert inserted == 2
    assert universe == 2
    assert observed_at is not None
    assert {row["strategy"] for row in storage.paper_signals()} == {
        "compression-gap-v2-h5-q0.50", "compression-gap-bottom-v2-h5-q0.50"
    }
    again, _, _ = scan_compression_gap(storage, history_window=5, top_fraction=0.5,
        min_quantity=0, min_market_value_g=0, min_listings=0, min_price_levels=0)
    assert again == 0

    # Paper signal is at the latest snapshot, so it should not have matured yet.
    assert evaluate_paper(storage) == []

    # Adding economic benchmarks never rewrites an already frozen v2 selection.
    original = storage.paper_signals()
    added, _, _ = scan_compression_gap(storage, history_window=5, top_fraction=0.5,
        min_quantity=0, min_market_value_g=0, min_listings=0, min_price_levels=0,
        include_benchmarks=True)
    assert added == 4  # One discount, one depth, and both eligible-market items.
    assert [r for r in storage.paper_signals() if r['strategy'].startswith('compression-gap')] == original


def test_paper_summary_applies_auction_house_cut(tmp_path: Path):
    storage = Storage(tmp_path / "test.db", tmp_path / "raw")
    storage.init()
    storage.insert_paper_signals([
        {
            "strategy": "compression-gap-h5-q0.20",
            "observed_at": "2026-09-27T00:00:00+00:00",
            "item_id": 1,
            "feature_value": 50.0,
            "percentile": 1.0,
            "rank": 1,
            "universe_size": 10,
            "entry_price": 10_000,
        },
        {
            "strategy": "compression-gap-bottom-h5-q0.20",
            "observed_at": "2026-09-27T00:00:00+00:00",
            "item_id": 2,
            "feature_value": -50.0,
            "percentile": 0.1,
            "rank": 1,
            "universe_size": 10,
            "entry_price": 10_000,
        },
    ])

    _seed_market(
        storage,
        1,
        [10_000, 10_000, 10_000, 12_000, 12_000, 12_000, 12_000, 12_000, 12_000, 12_000, 12_000, 12_000, 12_000],
        [1000] * 13,
    )
    _seed_market(
        storage,
        2,
        [10_000, 10_000, 10_000, 10_500, 10_500, 10_500, 10_500, 10_500, 10_500, 10_500, 10_500, 10_500, 10_500],
        [1000] * 13,
    )

    results = evaluate_paper(storage, horizons=(3,))
    assert len(results) == 2
    top = next(row for row in results if row.group == "top")
    bottom = next(row for row in results if row.group == "bottom")
    assert round(top.gross_return_pct, 1) == 20.0
    assert round(top.net_return_pct, 1) == 14.0
    assert round(bottom.gross_return_pct, 1) == 5.0

    summary = summarize_paper(results)[0]
    assert round(summary["net_avg"], 1) == 14.0
    assert round(summary["gross_spread"], 1) == 15.0


def test_summary_matches_times_and_experiments_and_weights_cohorts_equally():
    from azeroth_capital.paper import PaperResult
    def result(hour, item, gross, bottom=False, version=""):
        return PaperResult(
            strategy=f"compression-gap-{'bottom-' if bottom else ''}{version}h5-q0.20",
            group="bottom" if bottom else "top",
            observed_at=f"2026-09-27T{hour:02d}:00:00+00:00", item_id=item,
            rank=1, universe_size=10, feature_value=0, entry_price=100,
            horizon_hours=3, future_at=f"2026-09-27T{hour+3:02d}:00:00+00:00",
            future_price=100, gross_return_pct=gross, net_return_pct=gross*0.95-5)
    rows = [result(0,1,1000),  # Legacy top-only time must not influence spread.
            result(1,1,10), result(1,2,10), result(1,3,0,True),
            result(2,1,30), result(2,3,0,True),
            result(3,3,-1000,True),  # Unmatched control also excluded.
            result(1,1,500,version="v2-")]
    old, new = summarize_paper(rows)
    assert old["samples"] == 4
    assert old["cohorts"] == 3
    assert old["matched_cohorts"] == 2
    assert old["gross_spread"] == 20  # Equal cohort weights, not 50/3.
    assert old["matched_top_gross_avg"] == 20
    assert new["matched_cohorts"] == 0
    assert new["gross_spread"] is None
    assert new["samples"] == 1

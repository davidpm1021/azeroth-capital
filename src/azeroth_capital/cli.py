import csv
import re
import sys
import webbrowser
from dataclasses import asdict
from pathlib import Path

import typer

from .backtest import backtest_history, result_row, summarize_results
from .blizzard import BlizzardClient
from .catalog import sync_expansion_catalog
from .collector import Collector
from .config import Settings
from .demo import create_demo
from .discovery import discover_features
from .paper import evaluate_paper, scan_compression_gap, summarize_paper
from .report import build_report
from .storage import Storage
from .temporal import signal_from_history

app = typer.Typer(no_args_is_help=True, help="Azeroth Capital auction-market data collector.")


def services() -> tuple[Settings, Storage]:
    settings = Settings()
    storage = Storage(settings.ac_database_path, settings.ac_raw_dir)
    storage.init()
    return settings, storage


def format_money(copper: int | None) -> str:
    if copper is None:
        return "-"
    gold, remainder = divmod(int(copper), 10_000)
    silver, copper_value = divmod(remainder, 100)
    return f"{gold:,}g {silver:02d}s {copper_value:02d}c"


@app.command()
def init() -> None:
    """Initialize local database and raw-data directories."""
    settings, _ = services()
    typer.echo(f"Database ready: {settings.ac_database_path}")
    typer.echo(f"Raw snapshots: {settings.ac_raw_dir}")


@app.command()
def doctor(
    live: bool = typer.Option(False, "--live", help="Also test Blizzard authentication/API access."),
) -> None:
    """Check local configuration and optionally verify Blizzard connectivity."""
    settings, _ = services()
    has_id = bool(settings.blizzard_client_id)
    has_secret = bool(settings.blizzard_client_secret)

    typer.echo(f"Python: {sys.version.split()[0]}")
    typer.echo(f"Region: {settings.wow_region}")
    typer.echo(f"Database: {settings.ac_database_path}")
    typer.echo(f"Raw directory: {settings.ac_raw_dir}")
    typer.echo(f"Client ID: {'set' if has_id else 'MISSING'}")
    typer.echo(f"Client secret: {'set' if has_secret else 'MISSING'}")

    if not live:
        return

    if not (has_id and has_secret):
        typer.echo("Live check skipped: add Blizzard credentials to .env first.")
        raise typer.Exit(code=1)

    try:
        with BlizzardClient(settings) as client:
            payload = client.connected_realm_index()
        count = len(payload.get("connected_realms", []))
        typer.echo(f"Blizzard API: OK ({count:,} connected-realm references)")
    except Exception as exc:
        typer.echo(f"Blizzard API: FAILED ({exc})")
        raise typer.Exit(code=1) from exc


@app.command()
def collect(
    target: str = typer.Argument(..., help="commodities or realm"),
    realm: int | None = typer.Option(None, "--realm", help="Connected-realm ID for target=realm"),
) -> None:
    """Collect one Blizzard Auction House snapshot."""
    settings, storage = services()
    with BlizzardClient(settings) as client:
        collector = Collector(client, storage, settings.wow_region)
        if target == "commodities":
            result = collector.commodities()
        elif target == "realm":
            if realm is None:
                raise typer.BadParameter("--realm is required when target=realm")
            result = collector.realm(realm)
        else:
            raise typer.BadParameter("target must be 'commodities' or 'realm'")

    if result.not_modified:
        typer.echo(
            f"No new Blizzard snapshot for {result.source}. "
            f"Last-Modified={result.source_modified_at or 'unknown'}"
        )
        return

    digest = result.payload_hash[:12] if result.payload_hash else "-"
    typer.echo(
        f"Stored {result.source}: run={result.run_id}, auctions={result.auctions:,}, "
        f"observations={result.observations:,}, hash={digest}"
    )
    if result.source_modified_at:
        typer.echo(f"Blizzard Last-Modified: {result.source_modified_at}")


@app.command()
def status() -> None:
    """Show local collection status."""
    _, storage = services()
    info = storage.status()
    typer.echo(f"Successful snapshots: {info['runs']:,}")
    typer.echo(f"Unique raw snapshots: {info['raw_snapshots']:,}")
    typer.echo(f"Market observations: {info['observations']:,}")
    if info["latest"]:
        published = info["latest"].get("source_modified_at") or "unknown"
        typer.echo(
            f"Latest collection: {info['latest']['started_at']}  {info['latest']['source']}  "
            f"{info['latest']['payload_hash'][:12]}"
        )
        typer.echo(f"Latest Blizzard snapshot: {published}")


@app.command()
def realms() -> None:
    """Print connected-realm IDs available in the configured region."""
    settings, _ = services()
    with BlizzardClient(settings) as client:
        payload = client.connected_realm_index()

    for entry in payload.get("connected_realms", []):
        href = entry.get("href", "")
        match = re.search(r"/connected-realm/(\d+)", href)
        if match:
            typer.echo(match.group(1))
        else:
            typer.echo(href)


@app.command("catalog-sync")
def catalog_sync(
    expansion: str = typer.Option("Midnight", "--expansion"),
) -> None:
    """Build an expansion item universe from Blizzard profession recipes."""
    settings, storage = services()
    typer.echo(f"Building {expansion} profession catalog from Blizzard...")
    with BlizzardClient(settings) as client:
        result = sync_expansion_catalog(client, storage, expansion)
    typer.echo(
        f"Catalog ready: {result.items:,} items from {result.recipes:,} recipes, "
        f"{result.skill_tiers:,} skill tiers, {result.professions:,} professions."
    )


@app.command("catalog-status")
def catalog_status(
    expansion: str = typer.Option("Midnight", "--expansion"),
) -> None:
    """Show local expansion-catalog status."""
    _, storage = services()
    info = storage.expansion_catalog_status(expansion)
    typer.echo(f"Expansion: {info['expansion']}")
    typer.echo(f"Catalog items: {info['items']:,}")
    typer.echo(f"Updated: {info['updated_at'] or 'not synced'}")


@app.command()
def analyze(
    top: int = typer.Option(25, "--top", min=1, max=200),
    min_quantity: int = typer.Option(100, "--min-quantity", min=0),
    min_market_value_g: int = typer.Option(10_000, "--min-market-value-g", min=0),
    min_listings: int = typer.Option(50, "--min-listings", min=0),
    min_price_levels: int = typer.Option(5, "--min-price-levels", min=0),
    mode: str = typer.Option("liquid", "--mode", help="liquid, thin, or all"),
    expansion: str = typer.Option("Midnight", "--expansion", help="Expansion catalog name, or 'all'"),
    history: int = typer.Option(5, "--history", min=2, max=24),
    names: bool = typer.Option(True, "--names/--no-names"),
    output: Path | None = typer.Option(Path("data/latest_signals.csv"), "--output"),
) -> None:
    """Rank the latest commodity changes by an explainable market-pressure heuristic."""
    settings, storage = services()
    histories = storage.market_histories("commodity", snapshots=history)
    usable = [rows for rows in histories.values() if len(rows) >= 2]
    if not usable:
        typer.echo("Need at least two distinct Blizzard commodity snapshots before temporal analysis is available.")
        raise typer.Exit(code=1)

    signals = [
        signal_from_history(rows)
        for rows in usable
        if int(rows[-1]["total_quantity"]) >= min_quantity
    ]
    signals = [
        signal for signal in signals
        if signal.approx_market_value >= min_market_value_g * 10_000
    ]

    if expansion.casefold() != "all":
        expansion_ids = storage.expansion_item_ids(expansion)
        if not expansion_ids:
            typer.echo(
                f"No {expansion} catalog is loaded. Run: ac catalog-sync --expansion {expansion}"
            )
            raise typer.Exit(code=1)
        signals = [signal for signal in signals if signal.item_id in expansion_ids]

    if mode == "liquid":
        signals = [
            signal for signal in signals
            if signal.listing_count >= min_listings
            and signal.price_level_count >= min_price_levels
        ]
    elif mode == "thin":
        signals = [
            signal for signal in signals
            if signal.listing_count < min_listings
            or signal.price_level_count < min_price_levels
        ]
    elif mode != "all":
        raise typer.BadParameter("--mode must be 'liquid', 'thin', or 'all'")
    signals.sort(key=lambda s: s.pressure_score, reverse=True)
    selected = signals[:top]

    item_names: dict[int, str] = {}
    if names and settings.blizzard_client_id and settings.blizzard_client_secret:
        with BlizzardClient(settings) as client:
            for signal in selected:
                cached = storage.get_item(signal.item_id)
                if cached and cached.get("name"):
                    item_names[signal.item_id] = cached["name"]
                    continue
                try:
                    payload = client.item(signal.item_id)
                    storage.upsert_item(signal.item_id, payload)
                    item_names[signal.item_id] = payload.get("name") or f"Item {signal.item_id}"
                except Exception:
                    item_names[signal.item_id] = f"Item {signal.item_id}"

    typer.echo(
        "Rank  Item                        Ref Price       Δ1hRef   ΔBaseRef  ΔBaseQty  ΔBaseNear  Trend  Breadth   Pressure"
    )
    typer.echo("-" * 124)
    for rank, signal in enumerate(selected, start=1):
        name = item_names.get(signal.item_id, f"Item {signal.item_id}")
        if len(name) > 28:
            name = name[:27] + "…"
        trend = f"{signal.tightening_intervals}/{signal.interval_count}"
        typer.echo(
            f"{rank:>4}  {name:<28}  {format_money(signal.reference_price):>16}  "
            f"{signal.price_change_pct:>+7.1f}%  {signal.baseline_price_change_pct:>+8.1f}%  "
            f"{signal.baseline_quantity_change_pct:>+8.1f}%  "
            f"{signal.baseline_depth_5_change_pct:>+9.1f}%  {trend:>5}  "
            f"{signal.listing_count:>4}/{signal.price_level_count:<3}  {signal.pressure_score:>8.1f}"
        )

    typer.echo("")
    typer.echo("Ref Price ignores tiny floor listings by pricing the first meaningful slice of inventory.")
    typer.echo("Base compares the latest snapshot with the median of earlier snapshots in the selected history window.")
    typer.echo("Trend is tightening intervals / observed intervals. Breadth is listings / distinct price levels.")
    typer.echo(f"Expansion filter: {expansion}. Use --expansion all to include legacy markets.")
    typer.echo("Default liquid mode requires at least 50 listings and 5 price levels; use --mode thin or --mode all to inspect the rest.")
    typer.echo("Pressure rewards repeated tightening.")
    typer.echo("Pressure is an attention-ranking heuristic, not a buy/sell recommendation.")
    typer.echo("Depletion can reflect purchases, cancellations, expirations, or reposting.")

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for rank, signal in enumerate(selected, start=1):
            row = asdict(signal)
            row["rank"] = rank
            row["name"] = item_names.get(signal.item_id)
            rows.append(row)

        if rows:
            fields = ["rank", "name"] + [k for k in rows[0].keys() if k not in {"rank", "name"}]
            with output.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            typer.echo(f"Saved latest signal report: {output}")


@app.command()
def backtest(
    expansion: str = typer.Option("Midnight", "--expansion", help="Expansion catalog name, or 'all'"),
    history: int = typer.Option(5, "--history", min=2, max=24),
    strategy: str = typer.Option("prebreak", "--strategy", help="prebreak or pressure"),
    min_pressure: float = typer.Option(30.0, "--min-pressure", min=0),
    cooldown_hours: float = typer.Option(6.0, "--cooldown-hours", min=0),
    min_quantity: int = typer.Option(100, "--min-quantity", min=0),
    min_market_value_g: int = typer.Option(10_000, "--min-market-value-g", min=0),
    min_listings: int = typer.Option(50, "--min-listings", min=0),
    min_price_levels: int = typer.Option(5, "--min-price-levels", min=0),
    output: Path = typer.Option(Path("data/backtest.csv"), "--output", "-o"),
) -> None:
    """Measure realized 3h/6h/12h/24h returns after historical pressure signals."""
    settings, storage = services()
    histories = storage.all_market_histories("commodity")

    if strategy not in {"pressure", "prebreak"}:
        raise typer.BadParameter("--strategy must be 'pressure' or 'prebreak'")

    expansion_ids: set[int] | None = None
    if expansion.casefold() != "all":
        expansion_ids = storage.expansion_item_ids(expansion)
        if not expansion_ids:
            typer.echo(
                f"No {expansion} catalog is loaded. Run: ac catalog-sync --expansion {expansion}"
            )
            raise typer.Exit(code=1)

    all_signals = []
    all_results = []
    all_baseline_results = []
    for (item_id, _), rows in histories.items():
        if expansion_ids is not None and item_id not in expansion_ids:
            continue
        signals, results, baseline_results = backtest_history(
            rows,
            history_window=history,
            strategy=strategy,
            min_pressure=min_pressure,
            cooldown_hours=cooldown_hours,
            min_quantity=min_quantity,
            min_market_value_g=min_market_value_g,
            min_listings=min_listings,
            min_price_levels=min_price_levels,
        )
        all_signals.extend(signals)
        all_results.extend(results)
        all_baseline_results.extend(baseline_results)

    if strategy == "prebreak":
        criteria = (
            "price near baseline, quantity <= -10%, near-depth <= -30%, "
            "persistence >= 35%"
        )
    else:
        criteria = f"pressure >= {min_pressure:.1f}"

    typer.echo(
        f"Historical qualifying events: {len(all_signals):,} "
        f"({expansion}, strategy={strategy}, {criteria}, cooldown={cooldown_hours:g}h)"
    )

    summaries = summarize_results(all_results)
    baseline_summaries = summarize_results(all_baseline_results)
    if not summaries:
        typer.echo("Not enough future history yet to calculate forward returns.")
        raise typer.Exit(code=0)

    baseline_by_horizon = {
        summary.horizon_hours: summary for summary in baseline_summaries
    }

    typer.echo("")
    typer.echo(
        "Horizon  Signal N  Signal Avg  Baseline Avg  Excess Avg  "
        "Signal Med  Baseline Med  >0% Hit"
    )
    typer.echo("-" * 96)
    for summary in summaries:
        baseline = baseline_by_horizon.get(summary.horizon_hours)
        if baseline is None:
            continue
        typer.echo(
            f"{summary.horizon_hours:>5}h  "
            f"{summary.samples:>8}  "
            f"{summary.average_return_pct:>+10.2f}%  "
            f"{baseline.average_return_pct:>+12.2f}%  "
            f"{summary.average_return_pct - baseline.average_return_pct:>+10.2f}%  "
            f"{summary.median_return_pct:>+10.2f}%  "
            f"{baseline.median_return_pct:>+12.2f}%  "
            f"{summary.positive_rate_pct:>7.1f}%"
        )

    typer.echo("")
    typer.echo("Threshold hit rates for qualifying events:")
    typer.echo("Horizon   >=5% Hit   >=10% Hit")
    typer.echo("-" * 34)
    for summary in summaries:
        typer.echo(
            f"{summary.horizon_hours:>5}h  "
            f"{summary.return_5pct_rate_pct:>8.1f}%  "
            f"{summary.return_10pct_rate_pct:>9.1f}%"
        )

    item_names: dict[int, str] = {}
    interesting = sorted(
        all_results,
        key=lambda result: (result.horizon_hours, -result.pressure),
    )
    ids = {result.item_id for result in interesting}
    for item_id in ids:
        cached = storage.get_item(item_id)
        if cached and cached.get("name"):
            item_names[item_id] = cached["name"]

    missing_ids = [item_id for item_id in ids if item_id not in item_names]
    if missing_ids and settings.blizzard_client_id and settings.blizzard_client_secret:
        with BlizzardClient(settings) as client:
            for item_id in missing_ids[:100]:
                try:
                    payload = client.item(item_id)
                    storage.upsert_item(item_id, payload)
                    item_names[item_id] = payload.get("name") or f"Item {item_id}"
                except Exception:
                    item_names[item_id] = f"Item {item_id}"

    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for result in all_results:
        row = result_row(result)
        row["name"] = item_names.get(result.item_id, f"Item {result.item_id}")
        rows.append(row)

    if rows:
        fields = [
            "name",
            "item_id",
            "signal_at",
            "pressure",
            "reference_price",
            "horizon_hours",
            "future_at",
            "future_price",
            "forward_return_pct",
        ]
        with output.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        typer.echo("")
        typer.echo(f"Saved {len(rows):,} realized forward returns to {output}")

    completed_24h = [
        result for result in all_results
        if result.horizon_hours == 24
    ]
    if completed_24h:
        typer.echo("")
        typer.echo("Strongest completed 24h signal examples:")
        for result in sorted(completed_24h, key=lambda r: r.pressure, reverse=True)[:10]:
            name = item_names.get(result.item_id, f"Item {result.item_id}")
            typer.echo(
                f"  {name[:30]:<30} pressure {result.pressure:>6.1f}  "
                f"24h return {result.forward_return_pct:>+7.2f}%"
            )

@app.command()
def research(
    expansion: str = typer.Option("Midnight", "--expansion", help="Expansion catalog name, or 'all'"),
    history: int = typer.Option(5, "--history", min=2, max=24),
    min_quantity: int = typer.Option(100, "--min-quantity", min=0),
    min_market_value_g: int = typer.Option(10_000, "--min-market-value-g", min=0),
    min_listings: int = typer.Option(50, "--min-listings", min=0),
    min_price_levels: int = typer.Option(5, "--min-price-levels", min=0),
) -> None:
    """Discover which individual market features predict future returns."""
    _, storage = services()
    histories = storage.all_market_histories("commodity")

    expansion_ids: set[int] | None = None
    if expansion.casefold() != "all":
        expansion_ids = storage.expansion_item_ids(expansion)
        if not expansion_ids:
            typer.echo(
                f"No {expansion} catalog is loaded. Run: ac catalog-sync --expansion {expansion}"
            )
            raise typer.Exit(code=1)

    results = discover_features(
        histories,
        item_ids=expansion_ids,
        history_window=history,
        min_quantity=min_quantity,
        min_market_value_g=min_market_value_g,
        min_listings=min_listings,
        min_price_levels=min_price_levels,
    )

    if not results:
        typer.echo("Not enough history to evaluate feature predictiveness yet.")
        raise typer.Exit(code=0)

    typer.echo(
        "Feature                 Hor  Slices  Markets   Top20 Avg  Bottom20 Avg  "
        "Spread   MedSpread  +Spread%  RankCorr"
    )
    typer.echo("-" * 112)

    for row in results:
        typer.echo(
            f"{row.feature:<22} "
            f"{row.horizon_hours:>3}h "
            f"{row.slices:>7} "
            f"{row.markets:>8} "
            f"{row.top_return_pct:>+10.2f}% "
            f"{row.bottom_return_pct:>+12.2f}% "
            f"{row.spread_pct:>+7.2f}% "
            f"{row.median_slice_spread_pct:>+9.2f}% "
            f"{row.positive_spread_rate_pct:>8.1f}% "
            f"{row.average_rank_correlation:>+8.3f}"
        )

    typer.echo("")
    typer.echo("Interpretation:")
    typer.echo("  Spread = average future return of the top 20% by feature minus the bottom 20%.")
    typer.echo("  +Spread% = share of timestamp slices where that spread was positive.")
    typer.echo("  RankCorr = average within-snapshot Spearman correlation with future return.")
    typer.echo("  Positive Spread means higher feature values tended to outperform; negative means lower values did.")

@app.command("paper-scan")
def paper_scan(
    expansion: str = typer.Option("Midnight", "--expansion"),
    history: int = typer.Option(5, "--history", min=2, max=24),
    top_fraction: float = typer.Option(0.20, "--top-fraction", min=0.01, max=0.50),
) -> None:
    """Freeze today's top compression-gap candidates for prospective evaluation."""
    _, storage = services()
    inserted, universe, observed_at = scan_compression_gap(
        storage,
        expansion=expansion,
        history_window=history,
        top_fraction=top_fraction,
    )
    if observed_at is None:
        typer.echo("No current Blizzard snapshot/catalog available for paper scan.")
        raise typer.Exit(code=1)

    typer.echo(
        f"Paper scan {observed_at}: universe={universe:,}, "
        f"new frozen rows={inserted:,}, top/bottom={top_fraction:.0%}"
    )


@app.command("paper-status")
def paper_status() -> None:
    """Show prospective paper-signal collection status."""
    _, storage = services()
    info = storage.paper_status()
    typer.echo(f"Paper rows: {info['n']:,}")
    typer.echo(f"Snapshot cohorts: {info['snapshots']:,}")
    typer.echo(f"First: {info['first_at'] or '-'}")
    typer.echo(f"Latest: {info['last_at'] or '-'}")


@app.command("paper-candidates")
def paper_candidates(
    latest_only: bool = typer.Option(True, "--latest/--all"),
) -> None:
    """Show frozen prospective compression-gap cohorts."""
    _, storage = services()
    rows = storage.paper_signals()
    if not rows:
        typer.echo("No paper cohorts recorded yet.")
        raise typer.Exit(code=0)

    if latest_only:
        latest = max(row["observed_at"] for row in rows)
        rows = [row for row in rows if row["observed_at"] == latest]

    item_ids = {int(row["item_id"]) for row in rows}
    names = {}
    for item_id in item_ids:
        cached = storage.get_item(item_id)
        if cached and cached.get("name"):
            names[item_id] = cached["name"]

    typer.echo("Group   Rank  Item                           Gap      Entry")
    typer.echo("-" * 68)
    for row in sorted(
        rows,
        key=lambda r: (
            r["observed_at"],
            0 if "-bottom-" not in r["strategy"] else 1,
            int(r["rank"]),
        ),
    ):
        group = "bottom" if "-bottom-" in row["strategy"] else "top"
        item_id = int(row["item_id"])
        name = names.get(item_id, f"Item {item_id}")
        if len(name) > 30:
            name = name[:29] + "…"
        typer.echo(
            f"{group:<7} {int(row['rank']):>4}  {name:<30} "
            f"{float(row['feature_value']):>+8.2f}  {format_money(int(row['entry_price'])):>16}"
        )

    if latest_only:
        typer.echo("")
        typer.echo(f"Cohort: {rows[0]['observed_at']}")


@app.command("paper-results")
def paper_results() -> None:
    """Evaluate prospective compression-gap candidates after AH cut."""
    _, storage = services()
    results = evaluate_paper(storage)
    summaries = summarize_paper(results)

    if not summaries:
        typer.echo("No paper candidates have matured yet.")
        raise typer.Exit(code=0)

    typer.echo(
        "Horizon  Top N  Top Gross  Bottom Gross  Gross Spread  Top Net  Net Med  Net >0%  Net >=5%"
    )
    typer.echo("-" * 96)
    for row in summaries:
        bottom = "-" if row["bottom_gross_avg"] is None else f"{row['bottom_gross_avg']:+.2f}%"
        spread = "-" if row["gross_spread"] is None else f"{row['gross_spread']:+.2f}%"
        typer.echo(
            f"{row['horizon_hours']:>5}h "
            f"{row['samples']:>6} "
            f"{row['gross_avg']:>+9.2f}% "
            f"{bottom:>12} "
            f"{spread:>12} "
            f"{row['net_avg']:>+8.2f}% "
            f"{row['net_median']:>+7.2f}% "
            f"{row['net_positive_rate']:>7.1f}% "
            f"{row['net_5pct_rate']:>8.1f}%"
        )

    typer.echo("")
    typer.echo("Gross Spread compares frozen top-vs-bottom compression-gap cohorts from the same prospective snapshots.")
    typer.echo("Top Net assumes a 5% successful-sale Auction House cut and does not include slippage or failed sales.")

@app.command()
def report(
    output: Path = typer.Option(Path("data/report.html"), "--output", "-o"),
    top: int = typer.Option(50, "--top", min=1, max=500),
    expansion: str = typer.Option("Midnight", "--expansion"),
    open_report: bool = typer.Option(False, "--open"),
) -> None:
    """Generate a local HTML market report from collected data."""
    _, storage = services()
    if expansion.casefold() != "all" and not storage.expansion_item_ids(expansion):
        typer.echo(
            f"No {expansion} catalog is loaded. Run: ac catalog-sync --expansion {expansion}"
        )
        raise typer.Exit(code=1)
    path = build_report(storage, output, top=top, expansion=expansion)
    typer.echo(f"Report written: {path.resolve()}")
    if open_report:
        webbrowser.open(path.resolve().as_uri())


@app.command()
def demo(
    root: Path = typer.Option(Path("data/demo"), "--root"),
    open_report: bool = typer.Option(False, "--open"),
) -> None:
    """Run the complete analysis pipeline against synthetic snapshots, no credentials required."""
    storage, path = create_demo(root, reset=True)
    info = storage.status()
    typer.echo(
        f"Demo complete: {info['runs']} snapshots, "
        f"{info['observations']} observations, {info['raw_snapshots']} raw payloads"
    )
    typer.echo(f"Demo report: {path.resolve()}")
    if open_report:
        webbrowser.open(path.resolve().as_uri())


@app.command()
def export(
    output: Path = typer.Option(Path("data/market_observations.csv"), "--output", "-o"),
    item: int | None = typer.Option(None, "--item"),
) -> None:
    """Export normalized market observations to CSV."""
    _, storage = services()
    count = storage.export_observations(output, item)
    typer.echo(f"Exported {count:,} rows to {output}")


if __name__ == "__main__":
    app()

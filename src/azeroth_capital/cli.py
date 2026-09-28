import csv
import re
import sys
import webbrowser
from dataclasses import asdict
from pathlib import Path

import typer

from .blizzard import BlizzardClient
from .collector import Collector
from .config import Settings
from .demo import create_demo
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


@app.command()
def analyze(
    top: int = typer.Option(25, "--top", min=1, max=200),
    min_quantity: int = typer.Option(100, "--min-quantity", min=0),
    min_market_value_g: int = typer.Option(10_000, "--min-market-value-g", min=0),
    min_listings: int = typer.Option(50, "--min-listings", min=0),
    min_price_levels: int = typer.Option(5, "--min-price-levels", min=0),
    mode: str = typer.Option("liquid", "--mode", help="liquid, thin, or all"),
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
def report(
    output: Path = typer.Option(Path("data/report.html"), "--output", "-o"),
    top: int = typer.Option(50, "--top", min=1, max=500),
    open_report: bool = typer.Option(False, "--open"),
) -> None:
    """Generate a local HTML market report from collected data."""
    _, storage = services()
    path = build_report(storage, output, top=top)
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

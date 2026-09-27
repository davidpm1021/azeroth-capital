import csv
import re
import sys
from dataclasses import asdict
from pathlib import Path

import typer

from .blizzard import BlizzardClient
from .collector import Collector
from .config import Settings
from .storage import Storage
from .temporal import signal_from_pair

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

    typer.echo(
        f"Stored {result.source}: run={result.run_id}, auctions={result.auctions:,}, "
        f"observations={result.observations:,}, hash={result.payload_hash[:12]}"
    )


@app.command()
def status() -> None:
    """Show local collection status."""
    _, storage = services()
    info = storage.status()
    typer.echo(f"Successful polls: {info['runs']:,}")
    typer.echo(f"Unique raw snapshots: {info['raw_snapshots']:,}")
    typer.echo(f"Market observations: {info['observations']:,}")
    if info["latest"]:
        typer.echo(
            f"Latest: {info['latest']['started_at']}  {info['latest']['source']}  "
            f"{info['latest']['payload_hash'][:12]}"
        )


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
    names: bool = typer.Option(True, "--names/--no-names"),
    output: Path | None = typer.Option(Path("data/latest_signals.csv"), "--output"),
) -> None:
    """Rank the latest commodity changes by an explainable market-pressure heuristic."""
    settings, storage = services()
    pairs = storage.latest_market_pairs("commodity")
    if not pairs:
        typer.echo("Need at least two successful commodity polls before temporal analysis is available.")
        raise typer.Exit(code=1)

    signals = [
        signal_from_pair(current, previous)
        for current, previous in pairs
        if int(current["total_quantity"]) >= min_quantity
    ]
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
        "Rank  Item                          Price             ΔPrice    ΔQty      ΔDepth5   ETA5h   Pressure"
    )
    typer.echo("-" * 103)
    for rank, signal in enumerate(selected, start=1):
        name = item_names.get(signal.item_id, f"Item {signal.item_id}")
        if len(name) > 28:
            name = name[:27] + "…"
        eta = "-" if signal.depth_5_eta_hours is None else f"{signal.depth_5_eta_hours:5.1f}"
        typer.echo(
            f"{rank:>4}  {name:<28}  {format_money(signal.best_price):>16}  "
            f"{signal.price_change_pct:>+7.1f}%  {signal.quantity_change_pct:>+7.1f}%  "
            f"{signal.depth_5_change_pct:>+8.1f}%  {eta:>5}  {signal.pressure_score:>8.1f}"
        )

    typer.echo("")
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

from pathlib import Path

import typer

from .blizzard import BlizzardClient
from .collector import Collector
from .config import Settings
from .storage import Storage

app = typer.Typer(no_args_is_help=True, help="Azeroth Capital auction-market data collector.")


def services() -> tuple[Settings, Storage]:
    settings = Settings()
    storage = Storage(settings.ac_database_path, settings.ac_raw_dir)
    storage.init()
    return settings, storage


@app.command()
def init() -> None:
    """Initialize local database and raw-data directories."""
    settings, storage = services()
    typer.echo(f"Database ready: {settings.ac_database_path}")
    typer.echo(f"Raw snapshots: {settings.ac_raw_dir}")


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

    if result.skipped:
        typer.echo(f"Unchanged snapshot already stored: {result.source} ({result.auctions:,} auctions)")
    else:
        typer.echo(
            f"Stored {result.source}: run={result.run_id}, auctions={result.auctions:,}, "
            f"observations={result.observations:,}"
        )


@app.command()
def status() -> None:
    """Show local collection status."""
    _, storage = services()
    info = storage.status()
    typer.echo(f"Successful runs: {info['runs']:,}")
    typer.echo(f"Market observations: {info['observations']:,}")
    if info["latest"]:
        typer.echo(f"Latest: {info['latest']['started_at']}  {info['latest']['source']}")


@app.command()
def realms() -> None:
    """Print connected-realm API references available in the configured region."""
    settings, _ = services()
    with BlizzardClient(settings) as client:
        payload = client.connected_realm_index()
    for entry in payload.get("connected_realms", []):
        typer.echo(entry.get("href", ""))


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

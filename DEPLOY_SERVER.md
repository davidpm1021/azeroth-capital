# Always-on Ubuntu deployment

This deployment is intended for an always-on Ubuntu/Docker host.

## Files

- `Dockerfile`: builds the Azeroth Capital CLI
- `docker-compose.server.yml`: runs the collector continuously
- `scripts/collector-loop.sh`: checks Blizzard once per hour
- `./data`: host-persistent SQLite database, raw snapshots, reports, and exports
- `.env`: Blizzard credentials; never commit this file

## Start

```bash
docker compose -f docker-compose.server.yml up -d --build
```

The service checks immediately at startup and then once every 3600 seconds.

## Logs

```bash
docker compose -f docker-compose.server.yml logs -f --tail=100
```

## Status

```bash
docker compose -f docker-compose.server.yml exec azeroth-capital ac status
```

## Analyze

```bash
docker compose -f docker-compose.server.yml exec azeroth-capital ac analyze
```

## Sync Midnight catalog

```bash
docker compose -f docker-compose.server.yml exec azeroth-capital ac catalog-sync --expansion Midnight
```

## Inspect prospective results

```bash
docker compose -f docker-compose.server.yml exec azeroth-capital ac paper-status
docker compose -f docker-compose.server.yml exec azeroth-capital ac paper-results --output data/paper-results.csv
```

See [RESEARCH.md](RESEARCH.md) before interpreting legacy and v2 results. Keep
SQLite backups and the previous Docker image before deploying collector changes;
do not replace the persistent data directory during a rebuild.

## Stop

```bash
docker compose -f docker-compose.server.yml down
```

The `data` directory remains on the host after the container stops or is rebuilt.

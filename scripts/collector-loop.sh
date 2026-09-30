#!/bin/sh
set -u

INTERVAL="${AC_POLL_INTERVAL_SECONDS:-3600}"

ac init

while true; do
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Checking Blizzard commodity snapshot..."
    if ! ac collect commodities; then
        echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] Collection failed; will retry next interval." >&2
    fi

    # Prospective experiment: freeze the top and bottom compression-gap quintiles at each
    # distinct Blizzard snapshot. INSERT OR IGNORE makes repeated polls safe.
    ac paper-scan --expansion Midnight --history 5 --top-fraction 0.20 || true

    sleep "$INTERVAL"
done

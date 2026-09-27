from __future__ import annotations

from dataclasses import asdict
from html import escape
from pathlib import Path

from .storage import Storage
from .temporal import signal_from_history


def _money(copper: int | None) -> str:
    if copper is None:
        return "-"
    gold, remainder = divmod(int(copper), 10_000)
    silver, copper_value = divmod(remainder, 100)
    return f"{gold:,}g {silver:02d}s {copper_value:02d}c"


def build_report(storage: Storage, output: Path, top: int = 50) -> Path:
    status = storage.status()
    histories = storage.market_histories("commodity", snapshots=5)
    signals = [
        signal_from_history(history)
        for history in histories.values()
        if len(history) >= 2
    ]
    signals = [
        signal for signal in signals
        if signal.total_quantity >= 100
        and signal.approx_market_value >= 1000 * 10_000
    ]
    signals.sort(key=lambda s: s.pressure_score, reverse=True)
    signals = signals[:top]

    rows = []
    for rank, signal in enumerate(signals, start=1):
        item = storage.get_item(signal.item_id) or {}
        data = asdict(signal)
        rows.append(
            {
                "rank": rank,
                "name": item.get("name") or f"Item {signal.item_id}",
                **data,
            }
        )

    latest = status.get("latest") or {}
    latest_time = latest.get("source_modified_at") or latest.get("started_at") or "No data yet"

    body_rows = "\n".join(
        f"""<tr>
<td>{row['rank']}</td>
<td>{escape(str(row['name']))}<br><small>{row['item_id']}</small></td>
<td>{escape(_money(row['reference_price']))}</td>
<td>{row['price_change_pct']:+.1f}%</td>
<td>{row['baseline_price_change_pct']:+.1f}%</td>
<td>{row['baseline_quantity_change_pct']:+.1f}%</td>
<td>{row['baseline_depth_5_change_pct']:+.1f}%</td>
<td>{row['tightening_intervals']}/{row['interval_count']}</td>
<td>{row['pressure_score']:.1f}</td>
</tr>"""
        for row in rows
    )

    if not body_rows:
        body_rows = '<tr><td colspan="9">At least two distinct commodity snapshots are needed.</td></tr>'

    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Azeroth Capital</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 1200px; margin: 32px auto; padding: 0 20px; }}
h1 {{ margin-bottom: 4px; }}
.subtle {{ opacity: .7; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(180px,1fr)); gap: 12px; margin: 24px 0; }}
.card {{ border: 1px solid #ccc; border-radius: 10px; padding: 14px; }}
.value {{ font-size: 1.5rem; font-weight: 700; }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ text-align: right; padding: 9px; border-bottom: 1px solid #ddd; }}
th:nth-child(2), td:nth-child(2) {{ text-align: left; }}
small {{ opacity: .65; }}
.note {{ margin-top: 24px; padding: 14px; border: 1px solid #ccc; border-radius: 10px; }}
</style>
</head>
<body>
<h1>Azeroth Capital</h1>
<div class="subtle">Market research dashboard</div>
<div class="cards">
  <div class="card"><div class="subtle">Distinct raw snapshots</div><div class="value">{status['raw_snapshots']:,}</div></div>
  <div class="card"><div class="subtle">Successful collections</div><div class="value">{status['runs']:,}</div></div>
  <div class="card"><div class="subtle">Market observations</div><div class="value">{status['observations']:,}</div></div>
  <div class="card"><div class="subtle">Latest Blizzard snapshot</div><div>{escape(str(latest_time))}</div></div>
</div>
<h2>Market pressure watch</h2>
<table>
<thead><tr>
<th>#</th><th>Item</th><th>Reference price</th><th>1h ref Δ</th><th>Baseline ref Δ</th><th>Baseline qty Δ</th><th>Baseline near Δ</th><th>Trend</th><th>Pressure</th>
</tr></thead>
<tbody>
{body_rows}
</tbody>
</table>
<div class="note">
<strong>Interpretation:</strong> Reference price ignores tiny floor listings by pricing the first meaningful slice of visible inventory.
Baseline changes compare the newest snapshot with the median of prior snapshots in the recent window.
Trend counts repeated intervals where reference price held or rose while near-market depth fell.
Pressure is an explainable attention-ranking heuristic, not a buy or sell instruction.
Observed depletion is not confirmed sales. Auctions can disappear because of purchases, cancellations, expirations, or reposting.
</div>
</body>
</html>
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    return output

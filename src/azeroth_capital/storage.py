import csv
import gzip
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS collection_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    region TEXT NOT NULL,
    source TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    raw_path TEXT,
    success INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    UNIQUE(source, payload_hash)
);

CREATE TABLE IF NOT EXISTS commodity_level (
    run_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    unit_price INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    PRIMARY KEY (run_id, item_id, unit_price),
    FOREIGN KEY (run_id) REFERENCES collection_run(id)
);

CREATE TABLE IF NOT EXISTS realm_auction (
    run_id INTEGER NOT NULL,
    connected_realm_id INTEGER NOT NULL,
    auction_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    quantity INTEGER,
    buyout INTEGER,
    unit_price INTEGER,
    bid INTEGER,
    time_left TEXT,
    PRIMARY KEY (run_id, connected_realm_id, auction_id),
    FOREIGN KEY (run_id) REFERENCES collection_run(id)
);

CREATE TABLE IF NOT EXISTS market_observation (
    run_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    market_type TEXT NOT NULL,
    connected_realm_id INTEGER NOT NULL DEFAULT 0,
    best_price INTEGER,
    total_quantity INTEGER,
    quantity_at_best INTEGER,
    depth_1pct INTEGER,
    depth_5pct INTEGER,
    depth_10pct INTEGER,
    weighted_price REAL,
    PRIMARY KEY (run_id, item_id, market_type, connected_realm_id),
    FOREIGN KEY (run_id) REFERENCES collection_run(id)
);

CREATE INDEX IF NOT EXISTS idx_commodity_item ON commodity_level(item_id);
CREATE INDEX IF NOT EXISTS idx_observation_item ON market_observation(item_id, market_type);
CREATE INDEX IF NOT EXISTS idx_run_source_time ON collection_run(source, started_at);
"""


def canonical_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def payload_hash(payload: dict) -> str:
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


class Storage:
    def __init__(self, db_path: Path, raw_dir: Path):
        self.db_path = Path(db_path)
        self.raw_dir = Path(raw_dir)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def has_payload(self, source: str, digest: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM collection_run WHERE source=? AND payload_hash=? AND success=1",
                (source, digest),
            ).fetchone()
            return row is not None

    def save_raw(self, source: str, digest: str, payload: dict) -> Path:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        safe = source.replace(":", "_").replace("/", "_")
        path = self.raw_dir / f"{stamp}_{safe}_{digest[:12]}.json.gz"
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        return path

    def begin_run(self, region: str, source: str, digest: str, raw_path: Path) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """INSERT INTO collection_run(started_at, region, source, payload_hash, raw_path)
                   VALUES (?, ?, ?, ?, ?)""",
                (datetime.now(UTC).isoformat(), region, source, digest, str(raw_path)),
            )
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, error: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE collection_run SET completed_at=?, success=?, error=? WHERE id=?",
                (datetime.now(UTC).isoformat(), 0 if error else 1, error, run_id),
            )

    def insert_commodity_levels(self, run_id: int, levels: Iterable[dict]) -> None:
        with self.connect() as conn:
            conn.executemany(
                "INSERT INTO commodity_level(run_id,item_id,unit_price,quantity) VALUES (?,?,?,?)",
                [(run_id, r["item_id"], r["unit_price"], r["quantity"]) for r in levels],
            )

    def insert_observations(self, run_id: int, metrics: Iterable[dict], market_type: str, connected_realm_id: int = 0) -> None:
        with self.connect() as conn:
            conn.executemany(
                """INSERT INTO market_observation(
                    run_id,item_id,market_type,connected_realm_id,best_price,total_quantity,
                    quantity_at_best,depth_1pct,depth_5pct,depth_10pct,weighted_price
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        run_id, m["item_id"], market_type, connected_realm_id, m["best_price"],
                        m["total_quantity"], m["quantity_at_best"], m["depth_1pct"],
                        m["depth_5pct"], m["depth_10pct"], m["weighted_price"],
                    )
                    for m in metrics
                ],
            )

    def insert_realm_auctions(self, run_id: int, connected_realm_id: int, auctions: Iterable[dict]) -> None:
        rows = []
        for a in auctions:
            rows.append(
                (
                    run_id, connected_realm_id, int(a["id"]), int(a["item"]["id"]),
                    int(a.get("quantity", 1)), a.get("buyout"), a.get("unit_price"),
                    a.get("bid"), a.get("time_left"),
                )
            )
        with self.connect() as conn:
            conn.executemany(
                """INSERT INTO realm_auction(
                    run_id,connected_realm_id,auction_id,item_id,quantity,buyout,unit_price,bid,time_left
                ) VALUES (?,?,?,?,?,?,?,?,?)""",
                rows,
            )

    def status(self) -> dict:
        with self.connect() as conn:
            runs = conn.execute("SELECT COUNT(*) AS n FROM collection_run WHERE success=1").fetchone()["n"]
            latest = conn.execute(
                "SELECT started_at,source FROM collection_run WHERE success=1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
            observations = conn.execute("SELECT COUNT(*) AS n FROM market_observation").fetchone()["n"]
            return {"runs": runs, "observations": observations, "latest": dict(latest) if latest else None}

    def export_observations(self, output: Path, item_id: int | None = None) -> int:
        sql = """SELECT cr.started_at, mo.* FROM market_observation mo
                 JOIN collection_run cr ON cr.id=mo.run_id"""
        params: tuple = ()
        if item_id is not None:
            sql += " WHERE mo.item_id=?"
            params = (item_id,)
        sql += " ORDER BY cr.started_at, mo.item_id"

        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", newline="", encoding="utf-8") as fh:
            if rows:
                writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(dict(r) for r in rows)
        return len(rows)

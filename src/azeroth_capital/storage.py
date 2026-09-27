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

CREATE TABLE IF NOT EXISTS raw_snapshot (
    source TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    raw_path TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    PRIMARY KEY (source, payload_hash)
);

CREATE TABLE IF NOT EXISTS collection_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    region TEXT NOT NULL,
    source TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    raw_path TEXT,
    source_modified_at TEXT,
    success INTEGER NOT NULL DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS source_state (
    source TEXT PRIMARY KEY,
    last_modified TEXT,
    last_payload_hash TEXT,
    updated_at TEXT NOT NULL
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

CREATE TABLE IF NOT EXISTS item_cache (
    item_id INTEGER PRIMARY KEY,
    name TEXT,
    quality TEXT,
    item_class TEXT,
    item_subclass TEXT,
    updated_at TEXT NOT NULL
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
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(collection_run)").fetchall()
        }
        if "source_modified_at" not in columns:
            conn.execute("ALTER TABLE collection_run ADD COLUMN source_modified_at TEXT")

    def raw_path_for_hash(self, source: str, digest: str) -> Path | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT raw_path FROM raw_snapshot WHERE source=? AND payload_hash=?",
                (source, digest),
            ).fetchone()
        if not row:
            return None
        path = Path(row["raw_path"])
        return path if path.exists() else None

    def save_raw(self, source: str, digest: str, payload: dict) -> Path:
        existing = self.raw_path_for_hash(source, digest)
        if existing:
            return existing

        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        safe = source.replace(":", "_").replace("/", "_")
        path = self.raw_dir / f"{stamp}_{safe}_{digest[:12]}.json.gz"
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))

        with self.connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO raw_snapshot(source,payload_hash,raw_path,first_seen_at)
                   VALUES (?,?,?,?)""",
                (source, digest, str(path), datetime.now(UTC).isoformat()),
            )
        return path

    def last_modified_for_source(self, source: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT last_modified FROM source_state WHERE source=?",
                (source,),
            ).fetchone()
        return row["last_modified"] if row and row["last_modified"] else None

    def begin_run(
        self,
        region: str,
        source: str,
        digest: str,
        raw_path: Path,
        source_modified_at: str | None = None,
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """INSERT INTO collection_run(
                       started_at,region,source,payload_hash,raw_path,source_modified_at
                   ) VALUES (?,?,?,?,?,?)""",
                (
                    datetime.now(UTC).isoformat(),
                    region,
                    source,
                    digest,
                    str(raw_path),
                    source_modified_at,
                ),
            )
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, error: str | None = None) -> None:
        now = datetime.now(UTC).isoformat()
        with self.connect() as conn:
            conn.execute(
                "UPDATE collection_run SET completed_at=?, success=?, error=? WHERE id=?",
                (now, 0 if error else 1, error, run_id),
            )
            if error is None:
                run = conn.execute(
                    """SELECT source,payload_hash,source_modified_at
                       FROM collection_run WHERE id=?""",
                    (run_id,),
                ).fetchone()
                conn.execute(
                    """INSERT INTO source_state(source,last_modified,last_payload_hash,updated_at)
                       VALUES (?,?,?,?)
                       ON CONFLICT(source) DO UPDATE SET
                           last_modified=COALESCE(excluded.last_modified,source_state.last_modified),
                           last_payload_hash=excluded.last_payload_hash,
                           updated_at=excluded.updated_at""",
                    (
                        run["source"],
                        run["source_modified_at"],
                        run["payload_hash"],
                        now,
                    ),
                )

    def insert_commodity_levels(self, run_id: int, levels: Iterable[dict]) -> None:
        with self.connect() as conn:
            conn.executemany(
                "INSERT INTO commodity_level(run_id,item_id,unit_price,quantity) VALUES (?,?,?,?)",
                [(run_id, r["item_id"], r["unit_price"], r["quantity"]) for r in levels],
            )

    def insert_observations(
        self,
        run_id: int,
        metrics: Iterable[dict],
        market_type: str,
        connected_realm_id: int = 0,
    ) -> None:
        with self.connect() as conn:
            conn.executemany(
                """INSERT INTO market_observation(
                    run_id,item_id,market_type,connected_realm_id,best_price,total_quantity,
                    quantity_at_best,depth_1pct,depth_5pct,depth_10pct,weighted_price
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        run_id,
                        m["item_id"],
                        market_type,
                        connected_realm_id,
                        m["best_price"],
                        m["total_quantity"],
                        m["quantity_at_best"],
                        m["depth_1pct"],
                        m["depth_5pct"],
                        m["depth_10pct"],
                        m["weighted_price"],
                    )
                    for m in metrics
                ],
            )

    def insert_realm_auctions(
        self,
        run_id: int,
        connected_realm_id: int,
        auctions: Iterable[dict],
    ) -> None:
        rows = []
        for a in auctions:
            rows.append(
                (
                    run_id,
                    connected_realm_id,
                    int(a["id"]),
                    int(a["item"]["id"]),
                    int(a.get("quantity", 1)),
                    a.get("buyout"),
                    a.get("unit_price"),
                    a.get("bid"),
                    a.get("time_left"),
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
            runs = conn.execute(
                "SELECT COUNT(*) AS n FROM collection_run WHERE success=1"
            ).fetchone()["n"]
            latest = conn.execute(
                """SELECT started_at,source_modified_at,source,payload_hash
                   FROM collection_run
                   WHERE success=1
                   ORDER BY id DESC LIMIT 1"""
            ).fetchone()
            observations = conn.execute(
                "SELECT COUNT(*) AS n FROM market_observation"
            ).fetchone()["n"]
            raw_snapshots = conn.execute(
                "SELECT COUNT(*) AS n FROM raw_snapshot"
            ).fetchone()["n"]
            return {
                "runs": runs,
                "observations": observations,
                "raw_snapshots": raw_snapshots,
                "latest": dict(latest) if latest else None,
            }

    def latest_market_pairs(self, market_type: str = "commodity") -> list[tuple[dict, dict]]:
        sql = """
        WITH unique_runs AS (
            SELECT
                cr.*,
                ROW_NUMBER() OVER (
                    PARTITION BY cr.source, cr.payload_hash
                    ORDER BY cr.id
                ) AS payload_rank
            FROM collection_run cr
            WHERE cr.success=1
        ),
        ranked AS (
            SELECT
                mo.*,
                ur.started_at,
                ur.source_modified_at,
                COALESCE(ur.source_modified_at, ur.started_at) AS observed_at,
                ROW_NUMBER() OVER (
                    PARTITION BY mo.item_id, mo.connected_realm_id
                    ORDER BY COALESCE(ur.source_modified_at, ur.started_at) DESC, mo.run_id DESC
                ) AS rn
            FROM market_observation mo
            JOIN unique_runs ur ON ur.id = mo.run_id
            WHERE ur.payload_rank=1 AND mo.market_type=?
        )
        SELECT * FROM ranked WHERE rn <= 2
        ORDER BY item_id, connected_realm_id, rn
        """
        with self.connect() as conn:
            rows = [dict(r) for r in conn.execute(sql, (market_type,)).fetchall()]

        grouped: dict[tuple[int, int], dict[int, dict]] = {}
        for row in rows:
            key = (int(row["item_id"]), int(row["connected_realm_id"]))
            grouped.setdefault(key, {})[int(row["rn"])] = row

        pairs = []
        for ranked in grouped.values():
            if 1 in ranked and 2 in ranked:
                pairs.append((ranked[1], ranked[2]))
        return pairs

    def get_item(self, item_id: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM item_cache WHERE item_id=?",
                (item_id,),
            ).fetchone()
        return dict(row) if row else None

    def upsert_item(self, item_id: int, payload: dict) -> None:
        quality = payload.get("quality") or {}
        item_class = payload.get("item_class") or {}
        item_subclass = payload.get("item_subclass") or {}
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO item_cache(
                    item_id,name,quality,item_class,item_subclass,updated_at
                ) VALUES (?,?,?,?,?,?)
                ON CONFLICT(item_id) DO UPDATE SET
                    name=excluded.name,
                    quality=excluded.quality,
                    item_class=excluded.item_class,
                    item_subclass=excluded.item_subclass,
                    updated_at=excluded.updated_at""",
                (
                    item_id,
                    payload.get("name"),
                    quality.get("name"),
                    item_class.get("name"),
                    item_subclass.get("name"),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def export_observations(self, output: Path, item_id: int | None = None) -> int:
        sql = """SELECT
                     COALESCE(cr.source_modified_at,cr.started_at) AS observed_at,
                     cr.started_at AS collected_at,
                     mo.*
                 FROM market_observation mo
                 JOIN collection_run cr ON cr.id=mo.run_id"""
        params: tuple = ()
        if item_id is not None:
            sql += " WHERE mo.item_id=?"
            params = (item_id,)
        sql += " ORDER BY observed_at, mo.item_id"

        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", newline="", encoding="utf-8") as fh:
            if rows:
                writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(dict(r) for r in rows)
        return len(rows)

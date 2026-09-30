import csv
import gzip
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from .metrics import robust_market_fields
from .timestamps import parse_timestamp


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
    listing_count INTEGER,
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
    reference_price INTEGER,
    reference_quantity INTEGER,
    reference_depth_5pct INTEGER,
    approx_market_value INTEGER,
    listing_count INTEGER,
    price_level_count INTEGER,
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

CREATE TABLE IF NOT EXISTS expansion_item (
    expansion TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    roles TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (expansion, item_id)
);

CREATE TABLE IF NOT EXISTS paper_signal (
    strategy TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    feature_value REAL NOT NULL,
    percentile REAL NOT NULL,
    rank INTEGER NOT NULL,
    universe_size INTEGER NOT NULL,
    entry_price INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (strategy, observed_at, item_id)
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
        conn.create_function("timestamp_epoch", 1, lambda value: parse_timestamp(value).timestamp(), deterministic=True)
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)
            self._backfill_reference_metrics(conn)
            self._backfill_market_breadth(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(collection_run)").fetchall()
        }
        if "source_modified_at" not in columns:
            conn.execute("ALTER TABLE collection_run ADD COLUMN source_modified_at TEXT")

        commodity_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(commodity_level)").fetchall()
        }
        if "listing_count" not in commodity_columns:
            conn.execute("ALTER TABLE commodity_level ADD COLUMN listing_count INTEGER")

        observation_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(market_observation)").fetchall()
        }
        additions = {
            "reference_price": "INTEGER",
            "reference_quantity": "INTEGER",
            "reference_depth_5pct": "INTEGER",
            "approx_market_value": "INTEGER",
            "listing_count": "INTEGER",
            "price_level_count": "INTEGER",
        }
        for name, sql_type in additions.items():
            if name not in observation_columns:
                conn.execute(
                    f"ALTER TABLE market_observation ADD COLUMN {name} {sql_type}"
                )

    @staticmethod
    def _backfill_reference_metrics(conn: sqlite3.Connection) -> None:
        missing = conn.execute(
            """SELECT DISTINCT mo.run_id, mo.item_id
               FROM market_observation mo
               WHERE mo.market_type='commodity'
                 AND mo.reference_price IS NULL"""
        ).fetchall()
        if not missing:
            return

        run_ids = sorted({int(row["run_id"]) for row in missing})
        placeholders = ",".join("?" for _ in run_ids)
        level_rows = conn.execute(
            f"""SELECT run_id,item_id,unit_price,quantity
                FROM commodity_level
                WHERE run_id IN ({placeholders})
                ORDER BY run_id,item_id,unit_price""",
            tuple(run_ids),
        ).fetchall()

        grouped: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for row in level_rows:
            key = (int(row["run_id"]), int(row["item_id"]))
            grouped.setdefault(key, []).append(
                (int(row["unit_price"]), int(row["quantity"]))
            )

        updates = []
        for row in missing:
            key = (int(row["run_id"]), int(row["item_id"]))
            levels = grouped.get(key)
            if not levels:
                continue
            robust = robust_market_fields(levels)
            updates.append(
                (
                    robust["reference_price"],
                    robust["reference_quantity"],
                    robust["reference_depth_5pct"],
                    robust["approx_market_value"],
                    key[0],
                    key[1],
                )
            )

        conn.executemany(
            """UPDATE market_observation
               SET reference_price=?,
                   reference_quantity=?,
                   reference_depth_5pct=?,
                   approx_market_value=?
               WHERE run_id=? AND item_id=? AND market_type='commodity'""",
            updates,
        )

    @staticmethod
    def _backfill_market_breadth(conn: sqlite3.Connection) -> None:
        """Backfill listing breadth from retained raw Blizzard snapshots.

        Older normalized rows predate listing_count, but the raw payloads still
        contain every auction listing, so we can reconstruct breadth without
        losing the user's collected history.
        """
        missing_runs = [
            dict(row)
            for row in conn.execute(
                """SELECT DISTINCT cr.id AS run_id, cr.raw_path
                   FROM collection_run cr
                   JOIN market_observation mo ON mo.run_id=cr.id
                   WHERE cr.success=1
                     AND mo.market_type='commodity'
                     AND (mo.listing_count IS NULL OR mo.price_level_count IS NULL)"""
            ).fetchall()
        ]
        for run in missing_runs:
            raw_path = Path(run["raw_path"])
            if not raw_path.exists():
                continue

            try:
                with gzip.open(raw_path, "rt", encoding="utf-8") as fh:
                    payload = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue

            counts: dict[int, int] = {}
            level_counts: dict[tuple[int, int], int] = {}
            for auction in payload.get("auctions", []):
                try:
                    item_id = int(auction["item"]["id"])
                    unit_price = int(auction["unit_price"])
                except (KeyError, TypeError, ValueError):
                    continue
                counts[item_id] = counts.get(item_id, 0) + 1
                level_counts[(item_id, unit_price)] = (
                    level_counts.get((item_id, unit_price), 0) + 1
                )

            per_item_levels: dict[int, int] = {}
            for item_id, _ in level_counts:
                per_item_levels[item_id] = per_item_levels.get(item_id, 0) + 1

            conn.executemany(
                """UPDATE market_observation
                   SET listing_count=?, price_level_count=?
                   WHERE run_id=? AND item_id=? AND market_type='commodity'""",
                [
                    (count, per_item_levels.get(item_id, 0), int(run["run_id"]), item_id)
                    for item_id, count in counts.items()
                ],
            )

            conn.executemany(
                """UPDATE commodity_level
                   SET listing_count=?
                   WHERE run_id=? AND item_id=? AND unit_price=?""",
                [
                    (count, int(run["run_id"]), item_id, unit_price)
                    for (item_id, unit_price), count in level_counts.items()
                ],
            )

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
                """INSERT INTO commodity_level(
                    run_id,item_id,unit_price,quantity,listing_count
                ) VALUES (?,?,?,?,?)""",
                [
                    (
                        run_id,
                        r["item_id"],
                        r["unit_price"],
                        r["quantity"],
                        r.get("listing_count"),
                    )
                    for r in levels
                ],
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
                    quantity_at_best,depth_1pct,depth_5pct,depth_10pct,weighted_price,
                    reference_price,reference_quantity,reference_depth_5pct,approx_market_value,
                    listing_count,price_level_count
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                        m.get("reference_price"),
                        m.get("reference_quantity"),
                        m.get("reference_depth_5pct"),
                        m.get("approx_market_value"),
                        m.get("listing_count"),
                        m.get("price_level_count"),
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

    def market_histories(
        self,
        market_type: str = "commodity",
        snapshots: int = 5,
    ) -> dict[tuple[int, int], list[dict]]:
        """Return up to N distinct Blizzard snapshots per market, oldest to newest."""
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
                    ORDER BY timestamp_epoch(COALESCE(ur.source_modified_at, ur.started_at)) DESC, mo.run_id DESC
                ) AS rn
            FROM market_observation mo
            JOIN unique_runs ur ON ur.id = mo.run_id
            WHERE ur.payload_rank=1 AND mo.market_type=?
        )
        SELECT * FROM ranked WHERE rn <= ?
        ORDER BY item_id, connected_realm_id, rn DESC
        """
        with self.connect() as conn:
            rows = [
                dict(r)
                for r in conn.execute(sql, (market_type, snapshots)).fetchall()
            ]

        grouped: dict[tuple[int, int], list[dict]] = {}
        for row in rows:
            key = (int(row["item_id"]), int(row["connected_realm_id"]))
            grouped.setdefault(key, []).append(row)
        return grouped

    def all_market_histories(
        self,
        market_type: str = "commodity",
    ) -> dict[tuple[int, int], list[dict]]:
        """Return all distinct Blizzard snapshots per market, oldest to newest."""
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
        )
        SELECT
            mo.*,
            ur.started_at,
            ur.source_modified_at,
            COALESCE(ur.source_modified_at, ur.started_at) AS observed_at
        FROM market_observation mo
        JOIN unique_runs ur ON ur.id = mo.run_id
        WHERE ur.payload_rank=1 AND mo.market_type=?
        ORDER BY mo.item_id, mo.connected_realm_id,
                 timestamp_epoch(COALESCE(ur.source_modified_at, ur.started_at)), mo.run_id
        """
        with self.connect() as conn:
            rows = [dict(r) for r in conn.execute(sql, (market_type,)).fetchall()]

        grouped: dict[tuple[int, int], list[dict]] = {}
        for row in rows:
            key = (int(row["item_id"]), int(row["connected_realm_id"]))
            grouped.setdefault(key, []).append(row)
        return grouped

    def latest_market_pairs(self, market_type: str = "commodity") -> list[tuple[dict, dict]]:
        histories = self.market_histories(market_type, snapshots=2)
        pairs = []
        for history in histories.values():
            if len(history) >= 2:
                pairs.append((history[-1], history[-2]))
        return pairs

    def replace_expansion_catalog(
        self,
        expansion: str,
        item_roles: dict[int, set[str]],
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM expansion_item WHERE lower(expansion)=lower(?)",
                (expansion,),
            )
            conn.executemany(
                """INSERT INTO expansion_item(expansion,item_id,roles,updated_at)
                   VALUES (?,?,?,?)""",
                [
                    (
                        expansion,
                        int(item_id),
                        ",".join(sorted(roles)),
                        now,
                    )
                    for item_id, roles in sorted(item_roles.items())
                ],
            )

    def expansion_item_ids(self, expansion: str) -> set[int]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT item_id FROM expansion_item
                   WHERE lower(expansion)=lower(?)""",
                (expansion,),
            ).fetchall()
        return {int(row["item_id"]) for row in rows}

    def expansion_catalog_status(self, expansion: str) -> dict:
        with self.connect() as conn:
            row = conn.execute(
                """SELECT COUNT(*) AS n, MAX(updated_at) AS updated_at
                   FROM expansion_item
                   WHERE lower(expansion)=lower(?)""",
                (expansion,),
            ).fetchone()
        return {
            "expansion": expansion,
            "items": int(row["n"]) if row else 0,
            "updated_at": row["updated_at"] if row else None,
        }

    def insert_paper_signals(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        now = datetime.now(UTC).isoformat()
        with self.connect() as conn:
            before = conn.total_changes
            conn.executemany(
                """INSERT OR IGNORE INTO paper_signal(
                    strategy,observed_at,item_id,feature_value,percentile,
                    rank,universe_size,entry_price,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        row["strategy"],
                        row["observed_at"],
                        int(row["item_id"]),
                        float(row["feature_value"]),
                        float(row["percentile"]),
                        int(row["rank"]),
                        int(row["universe_size"]),
                        int(row["entry_price"]),
                        now,
                    )
                    for row in rows
                ],
            )
            return conn.total_changes - before

    def paper_signals(self, strategy: str | None = None) -> list[dict]:
        sql = "SELECT * FROM paper_signal"
        params: tuple = ()
        if strategy is not None:
            sql += " WHERE strategy=?"
            params = (strategy,)
        sql += " ORDER BY timestamp_epoch(observed_at),item_id"
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def paper_status(self, strategy: str | None = None) -> dict:
        signals = self.paper_signals(strategy)
        return {
            "n": len(signals),
            "snapshots": len({parse_timestamp(row["observed_at"]) for row in signals}),
            "first_at": signals[0]["observed_at"] if signals else None,
            "last_at": signals[-1]["observed_at"] if signals else None,
        }

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
        sql += " ORDER BY timestamp_epoch(observed_at), mo.item_id"

        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", newline="", encoding="utf-8") as fh:
            if rows:
                writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(dict(r) for r in rows)
        return len(rows)

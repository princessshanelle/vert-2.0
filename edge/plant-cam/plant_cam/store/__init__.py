"""Local persistence in one small SQLite file (WAL mode).

  outbox   store-and-forward queue of envelopes not yet acknowledged by the
           broker. A row is deleted only after its PUBACK, so a crash or
           power cut between "published" and "acknowledged" means a
           duplicate (at-least-once), never a loss. Capped by rows and age.
  kv       small state: the message sequence counter, condition tracker.
  history  per-ROI metric history (growth tracking), pruned after 8 days.
  env      zone temperature/humidity samples for the risk indices.

Writes are small and infrequent (one outbox row per analysis, one env
sample per 5 min), which keeps SD-card wear negligible. Images are never
stored here.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from ..core.types import HistoryStore
from ..env.risk import EnvSample

log = logging.getLogger("plant-cam.store")

_SCHEMA = """
create table if not exists outbox (
    seq integer primary key,
    topic text not null,
    body text not null,
    qos integer not null default 1,
    created real not null
);
create table if not exists kv (key text primary key, value text not null);
create table if not exists history (key text not null, ts real not null, value real not null);
create index if not exists history_key_ts on history (key, ts);
create table if not exists env (ts real primary key, temp_c real, rh_pct real, leaf_wetness real);
"""

SEQ_BLOCK = 100


class Store:
    def __init__(self, path: str | Path, outbox_max_rows: int = 2000, outbox_max_age_h: float = 168.0) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.outbox_max_rows = outbox_max_rows
        self.outbox_max_age_s = outbox_max_age_h * 3600
        self._lock = threading.RLock()
        self._db = self._open()
        self._seq_next, self._seq_limit = 0, 0

    def _open(self) -> sqlite3.Connection:
        try:
            db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=10)
            db.execute("pragma journal_mode=wal")
            db.execute("pragma synchronous=normal")
            db.executescript(_SCHEMA)
            db.execute("pragma quick_check").fetchone()
            return db
        except sqlite3.DatabaseError as exc:
            # A corrupt file (SD card trouble) must not keep the device down:
            # set it aside and start fresh; the broker side is unaffected.
            bad = self.path.with_suffix(f".corrupt-{int(time.time())}")
            log.error("store is corrupt, moving it aside", extra={"fields": {"path": str(bad), "error": repr(exc)}})
            for suffix in ("", "-wal", "-shm"):
                p = Path(str(self.path) + suffix)
                if p.exists():
                    p.rename(Path(str(bad) + suffix))
            db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=10)
            db.execute("pragma journal_mode=wal")
            db.executescript(_SCHEMA)
            return db

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- kv -----------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._db.execute("select value from kv where key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._db.execute("insert or replace into kv (key, value) values (?, ?)", (key, json.dumps(value)))

    # ---- sequence numbers -------------------------------------------------------

    def next_seq(self) -> int:
        """Monotonic across restarts. Reserves blocks of 100 so the counter
        is written once per 100 messages, not per message; a restart skips
        the rest of the block (gaps are fine, going backwards is not)."""
        with self._lock:
            if self._seq_next >= self._seq_limit:
                start = int(self.get("seq_high_water", 0))
                self._seq_next, self._seq_limit = start, start + SEQ_BLOCK
                self.put("seq_high_water", self._seq_limit)
            seq = self._seq_next
            self._seq_next += 1
            return seq

    # ---- outbox -----------------------------------------------------------------

    def enqueue(self, seq: int, topic: str, body: str, qos: int = 1) -> None:
        with self._lock:
            self._db.execute(
                "insert or replace into outbox (seq, topic, body, qos, created) values (?, ?, ?, ?, ?)",
                (seq, topic, body, qos, time.time()),
            )
            self._trim()

    def _trim(self) -> None:
        cutoff = time.time() - self.outbox_max_age_s
        dropped = self._db.execute("delete from outbox where created < ?", (cutoff,)).rowcount
        n = self._db.execute("select count(*) from outbox").fetchone()[0]
        if n > self.outbox_max_rows:
            dropped += self._db.execute(
                "delete from outbox where seq in (select seq from outbox order by seq limit ?)",
                (n - self.outbox_max_rows,),
            ).rowcount
        if dropped:
            log.warning("outbox full or stale, dropped oldest", extra={"fields": {"dropped": dropped}})

    def pending(self, limit: int = 50) -> list[tuple[int, str, str, int]]:
        with self._lock:
            return list(self._db.execute("select seq, topic, body, qos from outbox order by seq limit ?", (limit,)))

    def ack(self, seqs: list[int]) -> None:
        if not seqs:
            return
        with self._lock:
            self._db.executemany("delete from outbox where seq = ?", [(s,) for s in seqs])

    def outbox_size(self) -> int:
        with self._lock:
            return int(self._db.execute("select count(*) from outbox").fetchone()[0])

    # ---- env history --------------------------------------------------------------

    def add_env(self, sample: EnvSample) -> None:
        with self._lock:
            self._db.execute(
                "insert or replace into env values (?, ?, ?, ?)",
                (sample.ts, sample.temp_c, sample.rh_pct, sample.leaf_wetness),
            )
            self._db.execute("delete from env where ts < ?", (time.time() - 72 * 3600,))

    def env_since(self, ts: float) -> list[EnvSample]:
        with self._lock:
            rows = self._db.execute("select ts, temp_c, rh_pct, leaf_wetness from env where ts >= ? order by ts", (ts,))
            return [EnvSample(*r) for r in rows]


class SqliteHistory(HistoryStore):
    def __init__(self, store: Store, retention_s: float = 8 * 86400) -> None:
        super().__init__()
        self.store = store
        self.retention_s = retention_s

    def add(self, key: str, ts: float, value: float) -> None:
        with self.store._lock:
            self.store._db.execute("insert into history values (?, ?, ?)", (key, ts, value))
            self.store._db.execute("delete from history where ts < ?", (ts - self.retention_s,))

    def near(self, key: str, ts: float, tolerance_s: float) -> float | None:
        with self.store._lock:
            row = self.store._db.execute(
                "select value from history where key = ? and ts between ? and ? order by abs(ts - ?) limit 1",
                (key, ts - tolerance_s, ts + tolerance_s, ts),
            ).fetchone()
        return float(row[0]) if row else None

"""SQLite job store.

FIX: the spec said "the server waits" for a Telegram tap but kept no record
linking a Telegram button to a job or a file. Every source and every clip is a
row here; status changes are compare-and-set so a double tap on "Approve"
cannot publish twice.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

STATUSES = (
    "queued", "downloading", "recording", "analyzing", "rendering",
    "awaiting_approval", "publishing", "published", "rejected", "failed", "done",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    parent_id TEXT,
    kind TEXT NOT NULL,            -- 'source' or 'clip'
    source_type TEXT,              -- upload | url | live
    source TEXT,                   -- original URL or upload filename
    raw_path TEXT,
    start_s REAL,
    end_s REAL,
    score REAL,
    output_path TEXT,
    title TEXT,
    status TEXT NOT NULL,
    error TEXT,
    meta TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_parent ON jobs(parent_id);
"""

_lock = threading.Lock()


class JobStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
        if r is None:
            return None
        d = dict(r)
        d["meta"] = json.loads(d.get("meta") or "{}")
        return d

    def create(self, kind: str, status: str = "queued", **fields: Any) -> str:
        job_id = uuid.uuid4().hex[:12]  # short: fits Telegram's 64-byte callback_data
        now = time.time()
        meta = json.dumps(fields.pop("meta", {}))
        cols = ["id", "kind", "status", "meta", "created_at", "updated_at", *fields]
        vals = [job_id, kind, status, meta, now, now, *fields.values()]
        with self._conn() as c:
            c.execute(f"INSERT INTO jobs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)
        return job_id

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            return self._row(c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def children(self, job_id: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM jobs WHERE parent_id=? ORDER BY start_s", (job_id,)).fetchall()
        return [self._row(r) for r in rows]

    def list_sources(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM jobs WHERE kind='source' ORDER BY created_at DESC LIMIT ?",
                             (limit,)).fetchall()
        return [self._row(r) for r in rows]

    def mark_interrupted(self) -> int:
        """After a restart in thread mode, in-flight work is gone: mark it failed so it can be retried."""
        with self._conn() as c:
            cur = c.execute("UPDATE jobs SET status='failed', error='interrupted by restart', updated_at=? "
                            "WHERE status IN ('downloading','recording','analyzing','rendering','publishing','queued')",
                            (time.time(),))
            return cur.rowcount

    def update(self, job_id: str, **fields: Any) -> None:
        if "meta" in fields:
            current = (self.get(job_id) or {}).get("meta", {})
            current.update(fields["meta"])
            fields["meta"] = json.dumps(current)
        fields["updated_at"] = time.time()
        sets = ",".join(f"{k}=?" for k in fields)
        with self._conn() as c:
            c.execute(f"UPDATE jobs SET {sets} WHERE id=?", [*fields.values(), job_id])

    def transition(self, job_id: str, from_status: str | tuple[str, ...], to_status: str, **fields: Any) -> bool:
        """Atomically move a job between statuses. Returns False if it was not in from_status."""
        froms = (from_status,) if isinstance(from_status, str) else tuple(from_status)
        fields["status"] = to_status
        fields["updated_at"] = time.time()
        sets = ",".join(f"{k}=?" for k in fields)
        q = f"UPDATE jobs SET {sets} WHERE id=? AND status IN ({','.join('?' * len(froms))})"
        with _lock, self._conn() as c:
            cur = c.execute(q, [*fields.values(), job_id, *froms])
            return cur.rowcount == 1

    def fail(self, job_id: str, error: str) -> None:
        self.update(job_id, status="failed", error=error[-2000:])


_store: JobStore | None = None


def get_store() -> JobStore:
    global _store
    from app.config import get_settings

    path = get_settings().database
    if _store is None or _store.path != path:
        _store = JobStore(path)
    return _store

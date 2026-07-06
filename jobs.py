"""Ingest job store + background worker.

Downloads are slow (a yt-dlp pull is minutes), so ingestion is asynchronous: a
tool or the view *enqueues* a job and returns immediately with a job id; a single
daemon worker thread drains the queue, runs the download/metadata pipeline, and
updates job status. The view polls the job table.

State lives in a small SQLite table so it survives a container roll (the db is on
the persistent /sandbox volume). The worker is started/stopped via the plugin's
register_surface() hooks.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

_LOCK = threading.RLock()
_DB_PATH = "/sandbox/media-manager/jobs.db"
_WORKER: Optional["Worker"] = None

# Terminal + live states.
QUEUED = "queued"
RUNNING = "running"
DONE = "done"
ERROR = "error"


def _now() -> float:
    return time.time()


def configure(db_path: str) -> None:
    global _DB_PATH
    _DB_PATH = db_path
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    _init_db()


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _init_db() -> None:
    with _LOCK, _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id          TEXT PRIMARY KEY,
                kind        TEXT NOT NULL,        -- url | upload
                source      TEXT NOT NULL,        -- url or staged file path
                title       TEXT,
                status      TEXT NOT NULL,
                progress    REAL DEFAULT 0,
                message     TEXT DEFAULT '',
                output_path TEXT DEFAULT '',
                options     TEXT DEFAULT '{}',
                parent_id   TEXT DEFAULT '',
                created_at  REAL NOT NULL,
                updated_at  REAL NOT NULL
            )
            """
        )
        c.commit()


def enqueue(
    kind: str,
    source: str,
    title: str = "",
    options: Optional[dict] = None,
    parent_id: str = "",
) -> str:
    job_id = uuid.uuid4().hex[:12]
    ts = _now()
    with _LOCK, _conn() as c:
        c.execute(
            "INSERT INTO jobs (id, kind, source, title, status, options, parent_id, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (job_id, kind, source, title, QUEUED, json.dumps(options or {}), parent_id, ts, ts),
        )
        c.commit()
    return job_id


def update(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = _now()
    cols = ", ".join(f"{k}=?" for k in fields)
    with _LOCK, _conn() as c:
        c.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))
        c.commit()


def get(job_id: str) -> Optional[dict]:
    with _LOCK, _conn() as c:
        row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None


def recent(limit: int = 50, status: str = "") -> list[dict]:
    q = "SELECT * FROM jobs"
    args: tuple = ()
    if status:
        q += " WHERE status=?"
        args = (status,)
    q += " ORDER BY created_at DESC LIMIT ?"
    args = (*args, limit)
    with _LOCK, _conn() as c:
        return [dict(r) for r in c.execute(q, args).fetchall()]


def _claim_next() -> Optional[dict]:
    """Atomically pull the oldest queued job and mark it running."""
    with _LOCK, _conn() as c:
        row = c.execute(
            "SELECT * FROM jobs WHERE status=? ORDER BY created_at ASC LIMIT 1", (QUEUED,)
        ).fetchone()
        if not row:
            return None
        c.execute("UPDATE jobs SET status=?, updated_at=? WHERE id=?", (RUNNING, _now(), row["id"]))
        c.commit()
        return dict(row)


@dataclass
class Worker:
    process: Callable[[dict], None]        # process(job) -> runs the pipeline, may call update()
    poll_interval: float = 2.0
    _thread: Optional[threading.Thread] = field(default=None, init=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False)

    def start(self) -> None:
        # On boot, reset any job left RUNNING by a crash back to QUEUED.
        with _LOCK, _conn() as c:
            c.execute("UPDATE jobs SET status=?, message=? WHERE status=?",
                      (QUEUED, "requeued after restart", RUNNING))
            c.commit()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="media-ingest-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            job = _claim_next()
            if not job:
                self._stop.wait(self.poll_interval)
                continue
            try:
                self.process(job)
                # process() sets DONE/ERROR itself; if it left it RUNNING, mark DONE.
                cur = get(job["id"])
                if cur and cur["status"] == RUNNING:
                    update(job["id"], status=DONE, progress=100)
            except Exception as exc:  # noqa: BLE001
                update(job["id"], status=ERROR, message=f"{type(exc).__name__}: {exc}")


def start_worker(process: Callable[[dict], None]) -> None:
    global _WORKER
    if _WORKER is not None:
        return
    _WORKER = Worker(process=process)
    _WORKER.start()


def stop_worker() -> None:
    global _WORKER
    if _WORKER is not None:
        _WORKER.stop()
        _WORKER = None

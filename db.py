"""
db.py — SQLite persistence layer for waibee_mcp job tracking.

Thread-safety model:
  - check_same_thread=False: connection shared across threads
  - _write_lock (threading.Lock): serializes all writes
  - WAL mode: readers never block writers, writers never block readers
  - Called from async code via asyncio.to_thread()
"""

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = Path(__file__).parent / "waibee.db"

_write_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Connection factory
# ---------------------------------------------------------------------------

@contextmanager
def _conn():
    """Yield a WAL-mode sqlite3 connection; always closes on exit."""
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create tables + enable WAL. Safe to call multiple times (IF NOT EXISTS)."""
    with _write_lock, _conn() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")   # safe with WAL, faster
        con.execute("PRAGMA foreign_keys=ON")

        con.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                job_id      TEXT PRIMARY KEY,
                parent_id   TEXT,
                status      TEXT,
                task        TEXT,
                model       TEXT,
                agent_name  TEXT,
                workdir     TEXT,
                last_step   INTEGER DEFAULT 0,
                cost        REAL    DEFAULT 0,
                result      TEXT,
                error       TEXT,
                created_at  REAL,
                updated_at  REAL,
                heartbeat   REAL
            );

            CREATE TABLE IF NOT EXISTS checkpoints (
                job_id      TEXT,
                step        INTEGER,
                messages    TEXT,
                done        TEXT,
                remaining   TEXT,
                findings    TEXT,
                created_at  REAL,
                PRIMARY KEY (job_id, step)
            );

            CREATE TABLE IF NOT EXISTS trace (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id         TEXT,
                agent_id       TEXT,
                step           INTEGER,
                tool           TEXT,
                args_preview   TEXT,
                result_preview TEXT,
                ts             REAL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id  TEXT NOT NULL,
                seq     INTEGER NOT NULL,
                role    TEXT NOT NULL,
                data    TEXT NOT NULL,
                ts      REAL NOT NULL,
                UNIQUE(job_id, seq)
            );

            -- Indexes for common query paths
            CREATE INDEX IF NOT EXISTS idx_jobs_status
                ON jobs(status);
            CREATE INDEX IF NOT EXISTS idx_jobs_parent
                ON jobs(parent_id);
            CREATE INDEX IF NOT EXISTS idx_checkpoints_job
                ON checkpoints(job_id);
            CREATE INDEX IF NOT EXISTS idx_trace_job
                ON trace(job_id);
            CREATE INDEX IF NOT EXISTS idx_messages_job
                ON messages(job_id);
        """)
        con.commit()


# ---------------------------------------------------------------------------
# Orphan recovery
# ---------------------------------------------------------------------------

def recover_orphans(stale_after: float = 240.0) -> list[str]:
    """
    Mark 'running' jobs whose heartbeat is older than stale_after seconds
    (or NULL) as 'interrupted'.  Returns list of affected job_ids.
    """
    cutoff = time.time() - stale_after
    with _write_lock, _conn() as con:
        rows = con.execute(
            """
            SELECT job_id FROM jobs
            WHERE status = 'running'
              AND (heartbeat IS NULL OR heartbeat < ?)
            """,
            (cutoff,),
        ).fetchall()

        job_ids = [r["job_id"] for r in rows]

        if job_ids:
            now = time.time()
            con.executemany(
                """
                UPDATE jobs
                SET status = 'interrupted', updated_at = ?
                WHERE job_id = ?
                """,
                [(now, jid) for jid in job_ids],
            )
            con.commit()

    return job_ids


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def create_job(
    job_id: str,
    task: str,
    model: str,
    agent_name: str,
    workdir: str | None = None,
    parent_id: str | None = None,
) -> None:
    """Insert a new job row with status='running'."""
    now = time.time()
    with _write_lock, _conn() as con:
        con.execute(
            """
            INSERT INTO jobs
                (job_id, parent_id, status, task, model, agent_name,
                 workdir, last_step, cost, created_at, updated_at, heartbeat)
            VALUES (?, ?, 'running', ?, ?, ?, ?, 0, 0.0, ?, ?, ?)
            """,
            (job_id, parent_id, task, model, agent_name, workdir, now, now, now),
        )
        con.commit()


def get_job(job_id: str) -> dict | None:
    """Return job row as dict, or None if not found."""
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
    return dict(row) if row else None


def list_jobs(status: str | None = None, limit: int = 20) -> list[dict]:
    """
    Return jobs ordered by created_at DESC.
    Optionally filter by status; limit caps result count.
    """
    with _conn() as con:
        if status is not None:
            rows = con.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def update_job_step(job_id: str, step: int, cost_delta: float = 0.0) -> None:
    """Advance last_step, accumulate cost, refresh heartbeat + updated_at."""
    now = time.time()
    with _write_lock, _conn() as con:
        con.execute(
            """
            UPDATE jobs
            SET last_step  = ?,
                cost       = cost + ?,
                heartbeat  = ?,
                updated_at = ?
            WHERE job_id = ?
            """,
            (step, cost_delta, now, now, job_id),
        )
        con.commit()


def finish_job(
    job_id: str,
    status: str,
    result: str | None = None,
    error: str | None = None,
) -> None:
    """
    Transition job to terminal status.
    status: done | failed | timeout | cancelled | interrupted
    """
    now = time.time()
    with _write_lock, _conn() as con:
        con.execute(
            """
            UPDATE jobs
            SET status     = ?,
                result     = ?,
                error      = ?,
                updated_at = ?
            WHERE job_id = ?
            """,
            (status, result, error, now, job_id),
        )
        con.commit()


def touch_heartbeat(job_id: str) -> None:
    """Update heartbeat timestamp; used by long-running agents to signal liveness."""
    now = time.time()
    with _write_lock, _conn() as con:
        con.execute(
            "UPDATE jobs SET heartbeat = ?, updated_at = ? WHERE job_id = ?",
            (now, now, job_id),
        )
        con.commit()


# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------

def save_checkpoint(
    job_id: str,
    step: int,
    done: list,
    remaining: list,
    findings: dict,
) -> None:
    """
    Upsert checkpoint metadata for (job_id, step).
    Messages are stored per-row in the messages table — not here.
    """
    now = time.time()
    with _write_lock, _conn() as con:
        con.execute(
            """
            INSERT INTO checkpoints
                (job_id, step, messages, done, remaining, findings, created_at)
            VALUES (?, ?, '', ?, ?, ?, ?)
            ON CONFLICT(job_id, step) DO UPDATE SET
                done       = excluded.done,
                remaining  = excluded.remaining,
                findings   = excluded.findings,
                created_at = excluded.created_at
            """,
            (
                job_id,
                step,
                json.dumps(done),
                json.dumps(remaining),
                json.dumps(findings),
                now,
            ),
        )
        con.commit()


def get_latest_checkpoint(job_id: str) -> dict | None:
    """
    Return highest-step checkpoint for job_id.
    Deserializes done/remaining/findings back to Python types.
    Returns None if no checkpoint exists.
    """
    with _conn() as con:
        row = con.execute(
            """
            SELECT * FROM checkpoints
            WHERE job_id = ?
            ORDER BY step DESC
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()

    if not row:
        return None

    cp = dict(row)
    cp["done"]      = json.loads(cp["done"])      if cp["done"]      else []
    cp["remaining"] = json.loads(cp["remaining"]) if cp["remaining"] else []
    cp["findings"]  = json.loads(cp["findings"])  if cp["findings"]  else {}
    return cp


# ---------------------------------------------------------------------------
# Messages — per-row storage for crash-safe resume
# ---------------------------------------------------------------------------

def append_message(job_id: str, seq: int, msg: dict) -> None:
    """Insert one message row. INSERT OR IGNORE — safe to call twice for same seq."""
    now = time.time()
    with _write_lock, _conn() as con:
        con.execute(
            "INSERT OR IGNORE INTO messages (job_id, seq, role, data, ts) VALUES (?, ?, ?, ?, ?)",
            (job_id, seq, msg.get("role", ""), json.dumps(msg), now),
        )
        con.commit()


def get_resume_messages(job_id: str, head: int = 2, tail: int = 20) -> list[dict]:
    """
    Return first `head` + last `tail` messages for the job, deduplicated.
    head=2 preserves original task; tail=20 gives recent conversation context.
    If total messages <= head+tail, returns all in order.
    """
    with _conn() as con:
        total = con.execute(
            "SELECT COUNT(*) FROM messages WHERE job_id=?", (job_id,)
        ).fetchone()[0]
        if total == 0:
            return []
        if total <= head + tail:
            rows = con.execute(
                "SELECT data FROM messages WHERE job_id=? ORDER BY seq ASC", (job_id,)
            ).fetchall()
            return [json.loads(r["data"]) for r in rows]
        head_rows = con.execute(
            "SELECT data FROM messages WHERE job_id=? ORDER BY seq ASC LIMIT ?",
            (job_id, head),
        ).fetchall()
        tail_rows = con.execute(
            "SELECT data FROM messages WHERE job_id=? ORDER BY seq DESC LIMIT ?",
            (job_id, tail),
        ).fetchall()
    head_msgs = [json.loads(r["data"]) for r in head_rows]
    tail_msgs = [json.loads(r["data"]) for r in reversed(tail_rows)]
    return head_msgs + tail_msgs


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------

def append_trace(
    job_id: str,
    agent_id: str,
    step: int,
    tool: str,
    args_preview: str,
    result_preview: str,
) -> None:
    """Append one trace entry; id is auto-assigned by SQLite."""
    now = time.time()
    with _write_lock, _conn() as con:
        con.execute(
            """
            INSERT INTO trace
                (job_id, agent_id, step, tool, args_preview, result_preview, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, agent_id, step, tool, args_preview, result_preview, now),
        )
        con.commit()


def get_trace(job_id: str, limit: int = 10) -> list[dict]:
    """Return most-recent `limit` trace rows for job_id, newest first."""
    with _conn() as con:
        rows = con.execute(
            """
            SELECT * FROM trace
            WHERE job_id = ?
            ORDER BY ts DESC
            LIMIT ?
            """,
            (job_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Module-level init — runs once on import
# ---------------------------------------------------------------------------

init_db()

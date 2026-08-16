"""
SQLite state store.

The orchestrator is the durable, *stateful* component that connects
scan -> issue -> Devin session -> PR -> verdict. Devin sessions are
ephemeral workers; this table is the workflow's memory and the source of the
issue<->session<->PR mapping the dashboard renders.

SQLite on purpose: one file, no migrations, runs in a single container.
Swap to Postgres via DATABASE_URL for a real deployment — this module is the
only thing that changes.
"""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Any

DB_PATH = os.environ.get("DB_PATH", "/app/data/state.db")


@contextmanager
def _conn(db_path: str = DB_PATH):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db(db_path: str = DB_PATH) -> None:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    with _conn(db_path) as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                finding_id    INTEGER,
                issue_number  INTEGER,
                title         TEXT,
                severity      TEXT,
                kind          TEXT,
                status        TEXT DEFAULT 'queued',   -- queued|running|verified|escalated|failed
                devin_session TEXT,
                session_url   TEXT,
                pr_url        TEXT,
                ci_state      TEXT,
                correction_rounds INTEGER DEFAULT 0,
                acu           REAL DEFAULT 0,
                created_at    REAL,
                started_at    REAL,
                finished_at   REAL,
                detail        TEXT
            );
            CREATE TABLE IF NOT EXISTS scans (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at REAL,
                finished_at REAL,
                findings   INTEGER DEFAULT 0
            );
            """
        )


def create_scan(db_path: str = DB_PATH) -> int:
    with _conn(db_path) as con:
        cur = con.execute("INSERT INTO scans (started_at) VALUES (?)", (time.time(),))
        return cur.lastrowid


def finish_scan(scan_id: int, findings: int, db_path: str = DB_PATH) -> None:
    with _conn(db_path) as con:
        con.execute(
            "UPDATE scans SET finished_at=?, findings=? WHERE id=?",
            (time.time(), findings, scan_id),
        )


def create_task(finding: dict, issue_number: int, db_path: str = DB_PATH) -> int:
    with _conn(db_path) as con:
        cur = con.execute(
            """INSERT INTO tasks (finding_id, issue_number, title, severity, kind,
                                  status, created_at)
               VALUES (?,?,?,?,?, 'queued', ?)""",
            (
                finding["id"],
                issue_number,
                finding["title"],
                finding["severity"],
                finding["kind"],
                time.time(),
            ),
        )
        return cur.lastrowid


def start_task(task_id: int, session_id: str, session_url: str, db_path: str = DB_PATH) -> None:
    with _conn(db_path) as con:
        con.execute(
            """UPDATE tasks SET status='running', devin_session=?, session_url=?,
                                started_at=? WHERE id=?""",
            (session_id, session_url, time.time(), task_id),
        )


def update_task(task_id: int, db_path: str = DB_PATH, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = [*fields.values(), task_id]
    with _conn(db_path) as con:
        con.execute(f"UPDATE tasks SET {cols} WHERE id=?", vals)


def get_task(task_id: int, db_path: str = DB_PATH) -> dict | None:
    with _conn(db_path) as con:
        row = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None


def get_task_by_issue(issue_number: int, db_path: str = DB_PATH) -> dict | None:
    with _conn(db_path) as con:
        row = con.execute(
            "SELECT * FROM tasks WHERE issue_number=? ORDER BY id DESC LIMIT 1",
            (issue_number,),
        ).fetchone()
        return dict(row) if row else None


def list_running_tasks(db_path: str = DB_PATH) -> list[dict]:
    with _conn(db_path) as con:
        rows = con.execute("SELECT * FROM tasks WHERE status='running'").fetchall()
        return [dict(r) for r in rows]


def list_tasks(db_path: str = DB_PATH) -> list[dict]:
    with _conn(db_path) as con:
        rows = con.execute("SELECT * FROM tasks ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def get_metrics(db_path: str = DB_PATH) -> dict:
    """The numbers an engineering leader actually reads."""
    tasks = list_tasks(db_path)
    total = len(tasks)
    running = sum(1 for t in tasks if t["status"] == "running")
    verified = sum(1 for t in tasks if t["status"] == "verified")
    escalated = sum(1 for t in tasks if t["status"] == "escalated")
    failed = sum(1 for t in tasks if t["status"] == "failed")
    resolved = verified + escalated  # both are "correct" system outcomes
    corrections = sum(t["correction_rounds"] or 0 for t in tasks)
    acu = round(sum(t["acu"] or 0 for t in tasks), 1)

    # Mean time to resolution (issue -> verified/escalated), minutes.
    durations = [
        (t["finished_at"] - t["started_at"]) / 60.0
        for t in tasks
        if t["finished_at"] and t["started_at"]
    ]
    mttr = round(sum(durations) / len(durations), 1) if durations else 0.0

    # Cost per successfully-shipped PR (ACU). The VP-facing economics number.
    prs = sum(1 for t in tasks if t["pr_url"])
    acu_per_pr = round(acu / prs, 2) if prs else 0.0

    return {
        "total_tasks": total,
        "running": running,
        "verified": verified,
        "escalated": escalated,
        "failed": failed,
        "prs_opened": prs,
        "self_corrections": corrections,  # automated CI-failure retry nudges (no human)
        "human_handoffs": escalated + failed,  # tasks that actually need a human
        "success_rate_pct": round(100 * resolved / total, 1) if total else 0.0,
        "mttr_minutes": mttr,
        "total_acu": acu,
        "acu_per_pr": acu_per_pr,
    }

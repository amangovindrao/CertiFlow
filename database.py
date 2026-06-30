"""
database.py
-----------
Local SQLite persistence for Intern IDs and generated offer-letter records.

Purpose
=======
    * Allocate unique, gap-free, never-duplicated Intern IDs (e.g. SO260001)
      that keep counting across application restarts.
    * Store one record per generated letter so it can be verified offline.

The format is ``SO`` + 2-digit year + 4-digit auto-increment, e.g. for 2026:
``SO260001, SO260002, …, SO260124``.

Design notes (future-ready, #14)
--------------------------------
The schema keeps the Intern ID as the primary key and stores a status field,
so later features - QR-code verification, an online portal, completion
certificates reusing the same ID, employee-conversion tracking - can be layered
on without changing existing code. All access goes through :class:`InternDatabase`
so the storage engine could be swapped (e.g. to Postgres) in one place.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


class InternDatabase:
    """Thread-safe SQLite wrapper for Intern IDs and letter records."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False so the bulk worker thread can reuse it;
        # all writes are serialised through a lock.
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._create_schema()

    # ------------------------------------------------------------------ #
    # Schema
    # ------------------------------------------------------------------ #
    def _create_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS interns (
                    intern_id      TEXT PRIMARY KEY,
                    candidate_name TEXT,
                    position       TEXT,
                    domain         TEXT,
                    issue_date     TEXT,
                    start_date     TEXT,
                    end_date       TEXT,
                    duration       TEXT,
                    pdf_path       TEXT,
                    status         TEXT,
                    created_at     TEXT
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS counters (
                    prefix   TEXT PRIMARY KEY,
                    last_num INTEGER NOT NULL
                )
                """
            )

    # ------------------------------------------------------------------ #
    # ID allocation
    # ------------------------------------------------------------------ #
    @staticmethod
    def _prefix_for(year: Optional[int]) -> str:
        year = year or datetime.now().year
        return f"SO{year % 100:02d}"

    def peek_next_intern_id(self, year: Optional[int] = None) -> str:
        """Return what the next Intern ID *would* be without consuming it."""
        prefix = self._prefix_for(year)
        with self._lock:
            row = self._conn.execute(
                "SELECT last_num FROM counters WHERE prefix = ?",
                (prefix,)).fetchone()
        last = row["last_num"] if row else 0
        return f"{prefix}{last + 1:04d}"

    def next_intern_id(self, year: Optional[int] = None) -> str:
        """Allocate and persist the next unique Intern ID for ``year``."""
        prefix = self._prefix_for(year)
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT last_num FROM counters WHERE prefix = ?",
                (prefix,)).fetchone()
            num = (row["last_num"] if row else 0) + 1
            # Guard against any pre-existing record using this ID.
            while self._conn.execute(
                    "SELECT 1 FROM interns WHERE intern_id = ?",
                    (f"{prefix}{num:04d}",)).fetchone():
                num += 1
            self._conn.execute(
                "INSERT INTO counters(prefix, last_num) VALUES(?, ?) "
                "ON CONFLICT(prefix) DO UPDATE SET last_num = excluded.last_num",
                (prefix, num))
        return f"{prefix}{num:04d}"

    # ------------------------------------------------------------------ #
    # Records
    # ------------------------------------------------------------------ #
    def add_record(self, record: Dict[str, str]) -> None:
        """Insert (or replace) a generated-letter record."""
        fields = ("intern_id", "candidate_name", "position", "domain",
                  "issue_date", "start_date", "end_date", "duration",
                  "pdf_path", "status", "created_at")
        values = [record.get(f, "") for f in fields]
        if not record.get("created_at"):
            values[fields.index("created_at")] = datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S")
        with self._lock, self._conn:
            self._conn.execute(
                f"INSERT OR REPLACE INTO interns({', '.join(fields)}) "
                f"VALUES({', '.join('?' * len(fields))})", values)

    def get(self, intern_id: str) -> Optional[Dict[str, str]]:
        """Fetch a single record by Intern ID (case-insensitive)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM interns WHERE UPPER(intern_id) = UPPER(?)",
                (intern_id.strip(),)).fetchone()
        return dict(row) if row else None

    def get_by_name(self, name: str) -> Optional[Dict[str, str]]:
        """Return the most recent record for a candidate name (case-insensitive).

        Used to reuse the same Intern ID when the same person receives another
        document (e.g. an offer letter then a completion certificate).
        """
        if not name or not name.strip():
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM interns WHERE LOWER(candidate_name) = LOWER(?) "
                "ORDER BY created_at DESC LIMIT 1", (name.strip(),)).fetchone()
        return dict(row) if row else None

    def search(self, term: str = "") -> List[Dict[str, str]]:
        """Return records matching ``term`` in id/name/position (newest first)."""
        like = f"%{term.strip()}%"
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM interns WHERE intern_id LIKE ? OR "
                "candidate_name LIKE ? OR position LIKE ? "
                "ORDER BY created_at DESC", (like, like, like)).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) AS n FROM interns").fetchone()["n"]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

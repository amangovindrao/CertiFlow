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

import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from settings import store_path
from utils import normalize_name


def _id_sort_key(intern_id: str):
    """Sort Intern IDs by their numeric tail so SO260009 precedes SO260027."""
    digits = re.findall(r"\d+", str(intern_id or ""))
    return (int(digits[-1]) if digits else 0, str(intern_id or ""))


def _tune(conn: sqlite3.Connection) -> None:
    """Apply the pragmas this app needs for concurrent access.

    The bulk generator writes from a worker thread while the UI reads on the
    main thread, and two separate connections share the file. WAL lets readers
    and a writer coexist; the busy timeout replaces an instant
    "database is locked" error with a short wait.
    """
    for pragma in ("PRAGMA journal_mode=WAL",
                   "PRAGMA busy_timeout=5000",
                   "PRAGMA synchronous=NORMAL"):
        try:
            conn.execute(pragma)
        except sqlite3.Error:
            pass


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
        _tune(self._conn)
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
        self._migrate_paths()

    def _migrate_paths(self) -> None:
        """Convert absolute pdf_path values inside the project to relative.

        One-off, idempotent: rows already relative are left untouched.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT intern_id, pdf_path FROM interns "
                "WHERE pdf_path IS NOT NULL AND pdf_path != ''").fetchall()
        updates = []
        for row in rows:
            stored = store_path(row["pdf_path"])
            if stored != row["pdf_path"]:
                updates.append((stored, row["intern_id"]))
        if not updates:
            return
        with self._lock, self._conn:
            self._conn.executemany(
                "UPDATE interns SET pdf_path = ? WHERE intern_id = ?", updates)

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
        """Insert (or replace) a generated-letter record.

        This is an ``INSERT OR REPLACE`` on the Intern ID, so issuing another
        document updates the intern's row. The original ``created_at`` is kept
        rather than reset, otherwise every update would look like a brand new
        record and the audit trail (and "newest first" ordering) would be lost.
        """
        fields = ("intern_id", "candidate_name", "position", "domain",
                  "issue_date", "start_date", "end_date", "duration",
                  "pdf_path", "status", "created_at")
        values = [record.get(f, "") for f in fields]
        # Store project files relative to the project root.
        values[fields.index("pdf_path")] = store_path(record.get("pdf_path", ""))
        if not record.get("created_at"):
            existing = self.get(record.get("intern_id", ""))
            values[fields.index("created_at")] = (
                (existing or {}).get("created_at")
                or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
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
        """Return the record for a candidate name, or ``None``.

        Matching ignores case and any difference in surrounding or repeated
        whitespace, so ``"  Aakif   Jawaid"`` still finds ``"Aakif Jawaid"``.
        This is what stops a second document creating a second Intern ID for
        the same person.

        When several records share a name (legacy duplicates) the one with the
        lowest Intern ID is returned - the ID that was assigned first, and the
        same record that duplicate cleanup keeps.
        """
        key = normalize_name(name)
        if not key:
            return None
        matches = [r for r in self.search("")
                   if normalize_name(r.get("candidate_name", "")) == key]
        if not matches:
            return None
        return min(matches, key=lambda r: _id_sort_key(r.get("intern_id", "")))

    def find_duplicate_groups(self) -> List[List[Dict[str, str]]]:
        """Groups of intern records that describe the same person.

        Records are grouped by normalised name. Within a group the *primary*
        (the record to keep) comes first: the lowest Intern ID, i.e. the one
        assigned first. ``created_at`` is deliberately not used for ranking -
        on records written before it was preserved across updates it reflects
        the last update, not creation.
        """
        groups: Dict[str, List[Dict[str, str]]] = {}
        for record in self.search(""):
            key = normalize_name(record.get("candidate_name", ""))
            if key:
                groups.setdefault(key, []).append(record)

        return [sorted(items, key=lambda r: _id_sort_key(r.get("intern_id", "")))
                for items in groups.values() if len(items) > 1]

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

    # ------------------------------------------------------------------ #
    # Correcting an Intern ID
    # ------------------------------------------------------------------ #
    def reserve_counter(self, intern_id: str) -> None:
        """Ensure the allocator can never hand out ``intern_id`` again.

        Called after an ID is set by hand, so a corrected ID like SO260123 does
        not later get assigned to a different person.
        """
        text = (intern_id or "").strip().upper()
        if len(text) < 5 or not text[-4:].isdigit():
            return
        prefix, number = text[:-4], int(text[-4:])
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT last_num FROM counters WHERE prefix = ?",
                (prefix,)).fetchone()
            if number > (row["last_num"] if row else 0):
                self._conn.execute(
                    "INSERT INTO counters(prefix, last_num) VALUES(?, ?) "
                    "ON CONFLICT(prefix) DO UPDATE SET "
                    "last_num = excluded.last_num", (prefix, number))

    def change_intern_id(self, old_id: str, new_id: str) -> bool:
        """Rename an intern's ID in place, keeping the rest of the record.

        Raises ``ValueError`` if the record is missing or the new ID already
        belongs to somebody else. Returns ``False`` when nothing changed.
        """
        old = (old_id or "").strip()
        new = (new_id or "").strip()
        if not old or not new:
            raise ValueError("Both the current and the new Intern ID are "
                             "required.")
        if old.upper() == new.upper():
            return False
        if not self.get(old):
            raise ValueError(f"No intern record found for {old}.")
        clash = self.get(new)
        if clash:
            raise ValueError(
                f"{new} is already used by "
                f"{clash.get('candidate_name') or 'another intern'}.")
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE interns SET intern_id = ? WHERE UPPER(intern_id) = "
                "UPPER(?)", (new, old))
        self.reserve_counter(new)
        return True

    # ------------------------------------------------------------------ #
    # Deletion
    # ------------------------------------------------------------------ #
    def delete(self, intern_id: str) -> bool:
        """Delete one intern record. Returns ``True`` if a row was removed.

        The ID counter is deliberately left alone so a deleted Intern ID is
        never handed to a different person later.
        """
        if not intern_id or not intern_id.strip():
            return False
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM interns WHERE UPPER(intern_id) = UPPER(?)",
                (intern_id.strip(),))
        return cursor.rowcount > 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()

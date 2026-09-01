"""
backup.py
---------
Safety net for the local SQLite database.

Every destructive action in the app - deleting interns, merging duplicates,
changing an Intern ID, reissuing a certificate - rewrites records that cannot
otherwise be recovered. A snapshot is taken immediately before each of those,
plus one per application launch, so there is always a recent copy to fall back
on.

Snapshots use SQLite's own online backup API rather than a file copy, so they
are consistent even while the app holds open connections (and correct under
WAL, where a plain copy can miss the write-ahead log).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# Keep this many snapshots per reason, newest first.
MAX_PER_REASON = 8
BACKUP_DIRNAME = "backups"


def backup_dir(db_path: Path) -> Path:
    return Path(db_path).parent / BACKUP_DIRNAME


def _safe_reason(reason: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-"
                      for ch in str(reason or "manual").strip().lower())
    return cleaned.strip("-") or "manual"


def create_backup(db_path: Path, reason: str = "manual") -> Optional[Path]:
    """Snapshot ``db_path`` into ``company/backups/``.

    Returns the snapshot path, or ``None`` when the database does not exist yet
    or the snapshot could not be taken. Never raises: a failed backup must not
    block the user's actual action.
    """
    source = Path(db_path)
    if not source.exists():
        return None

    tag = _safe_reason(reason)
    folder = backup_dir(source)
    folder.mkdir(parents=True, exist_ok=True)
    # Millisecond precision: two destructive actions in the same second must
    # not overwrite each other's snapshot. Still sorts chronologically by name.
    now = datetime.now()
    stamp = f"{now:%Y%m%d-%H%M%S}-{now.microsecond // 1000:03d}"
    target = folder / f"{source.stem}-{stamp}-{tag}{source.suffix}"

    src = dst = None
    try:
        # Read-only source connection; the online backup API produces a
        # consistent snapshot without pausing the app.
        src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=5)
        dst = sqlite3.connect(str(target), timeout=5)
        with dst:
            src.backup(dst)
    except Exception:
        try:
            if target.exists():
                target.unlink()
        except OSError:
            pass
        return None
    finally:
        for conn in (dst, src):
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass

    prune(source, tag)
    return target


def list_backups(db_path: Path, reason: Optional[str] = None) -> List[Path]:
    """Snapshots for ``db_path``, newest first."""
    folder = backup_dir(db_path)
    if not folder.exists():
        return []
    stem = Path(db_path).stem
    pattern = f"{stem}-*-{_safe_reason(reason)}{Path(db_path).suffix}" \
        if reason else f"{stem}-*{Path(db_path).suffix}"
    files = [p for p in folder.glob(pattern) if p.is_file()]
    files.sort(key=lambda p: p.name, reverse=True)
    return files


def prune(db_path: Path, reason: str, keep: int = MAX_PER_REASON) -> int:
    """Delete all but the newest ``keep`` snapshots for one reason."""
    removed = 0
    for old in list_backups(db_path, reason)[keep:]:
        try:
            old.unlink()
            removed += 1
        except OSError:
            pass
    return removed

"""
data_transfer.py
----------------
User-facing backup, restore and migration of **all** application data.

Not to be confused with :mod:`backup`, which takes small automatic snapshots of
the database alone before destructive actions. This module moves the *entire*
application state in and out of a single portable ``.zip``:

    company/company.json        company profile - branding, HR block, footer
    company/app_settings.json   preferences (theme)
    company/interns.db          interns + certificate records + ID counters
    templates/*.json            designs saved in the Template Designer
    assets/**                   logo, signature, stamp, watermark, fonts
    output/**                   every generated PDF            (optional)

Deliberately excluded:

    logs/                       machine-local noise, never worth restoring
    company/backups/            snapshots of the database that is already here
    Backup & Restore/           the backup folder itself - including it would
                                make each backup contain the previous one and
                                grow without bound

What this is for
================
Moving from ``python app.py`` to the packaged .exe, upgrading, reinstalling, or
moving to a different computer, without losing certificates already issued.

Safety
======
* The database is copied with SQLite's **online backup API**, so the archive is
  consistent even while the app is running - under WAL journalling a plain file
  copy can miss committed transactions still in the ``-wal`` sidecar.
* Every member is recorded in ``manifest.json`` with its SHA-256, and
  :func:`inspect` re-checks them, so a truncated or edited archive is reported
  before anything is restored.
* Restoring in ``merge`` mode routes records through :mod:`intern_io`, which
  preserves Intern IDs and certificate numbers, advances both allocators past
  them, and never creates a duplicate.
* Restoring in ``replace`` mode snapshots the current database first.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import backup
import intern_io
import settings

# The folder users drop backups into. Named exactly as it appears in the UI so
# there is never a question of which folder is meant.
BACKUP_DIRNAME = "Backup & Restore"
MANIFEST_NAME = "manifest.json"
PAYLOAD_PREFIX = "data"
SCHEMA_VERSION = 1
FILE_SUFFIX = ".zip"

# Trees copied wholesale. ``output`` is last because it is by far the largest
# and is the one the caller can switch off.
_TREES = ("assets", "templates")
_OUTPUT_TREE = "output"
_LOOSE_FILES = ("company/company.json", "company/app_settings.json")
_DB_MEMBER = "company/interns.db"

_SKIP_DIRS = {"logs", "backups", BACKUP_DIRNAME.lower(), "__pycache__"}
_SKIP_SUFFIXES = (".db-wal", ".db-shm", ".tmp", ".pyc")


# --------------------------------------------------------------------------- #
# Value objects
# --------------------------------------------------------------------------- #
@dataclass
class BackupInfo:
    """A backup archive on disk, described by its manifest."""
    path: Path
    created_at: str = ""
    app_version: str = ""
    created_on: str = ""
    data_mode: str = ""
    interns: int = 0
    certificates: int = 0
    templates: int = 0
    documents: int = 0
    includes_output: bool = False
    file_count: int = 0
    size_bytes: int = 0
    integrity_ok: bool = True
    problems: List[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def size_mb(self) -> float:
        return self.size_bytes / 1024 / 1024

    @property
    def when(self) -> str:
        """``2026-08-30 17:04`` for display, falling back to the file's mtime."""
        raw = (self.created_at or "").strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(raw, fmt).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                continue
        try:
            return datetime.fromtimestamp(
                self.path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        except OSError:
            return raw or "\u2014"

    def summary(self) -> str:
        parts = [f"{self.interns} intern(s)",
                 f"{self.certificates} certificate(s)"]
        if self.templates:
            parts.append(f"{self.templates} design(s)")
        parts.append(f"{self.documents} PDF(s)" if self.includes_output
                     else "no PDFs")
        return " \u00b7 ".join(parts)


@dataclass
class RestoreResult:
    """What a restore actually changed."""
    mode: str = "merge"
    interns_added: int = 0
    interns_updated: int = 0
    certs_added: int = 0
    certs_updated: int = 0
    files_copied: int = 0
    files_skipped: int = 0
    company_restored: bool = False
    settings_restored: bool = False
    templates_copied: int = 0
    snapshot: Optional[Path] = None
    restart_required: bool = False
    notes: List[str] = field(default_factory=list)

    @property
    def records(self) -> int:
        return (self.interns_added + self.interns_updated
                + self.certs_added + self.certs_updated)


# --------------------------------------------------------------------------- #
# Locations
# --------------------------------------------------------------------------- #
def backup_root() -> Path:
    """The ``Backup & Restore`` folder, created on demand.

    Lives inside the data folder, so it follows the same rule as everything
    else: beside the .exe for a portable copy, in ``%LOCALAPPDATA%\\CertiFlow``
    for an installed one.
    """
    folder = settings.BASE_DIR / BACKUP_DIRNAME
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def suggested_name(when: Optional[datetime] = None, label: str = "") -> str:
    when = when or datetime.now()
    stamp = when.strftime("%Y%m%d-%H%M%S")
    safe = "".join(ch for ch in str(label or "") if ch.isalnum() or ch in "-_")
    tail = f"-{safe}" if safe else ""
    return f"{settings.APP_NAME}-Backup-{stamp}{tail}{FILE_SUFFIX}"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _skip(path: Path, root: Path) -> bool:
    """True when ``path`` must never enter an archive."""
    relative = path.relative_to(root)
    if any(part.lower() in _SKIP_DIRS for part in relative.parts):
        return True
    return path.name.lower().endswith(_SKIP_SUFFIXES)


def _copy_database(source: Path, target: Path) -> Tuple[int, int]:
    """Consistent copy of the SQLite database. Returns ``(interns, certs)``."""
    target.parent.mkdir(parents=True, exist_ok=True)
    src = dst = None
    interns = certs = 0
    try:
        src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=10)
        dst = sqlite3.connect(str(target), timeout=10)
        with dst:
            src.backup(dst)
        interns = _table_count(dst, "interns")
        certs = _table_count(dst, "certificates")
    finally:
        for conn in (dst, src):
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    return interns, certs


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.Error:
        return 0


def _read_records(db_path: Path) -> Tuple[List[Dict], List[Dict]]:
    """Pull interns and certificates out of a database file as plain dicts."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    interns: List[Dict] = []
    certs: List[Dict] = []
    try:
        try:
            rows = conn.execute("SELECT * FROM interns").fetchall()
            interns = [{f: (dict(r).get(f) or "")
                        for f, _ in intern_io.INTERN_COLUMNS} for r in rows]
        except sqlite3.Error:
            pass
        try:
            rows = conn.execute("SELECT * FROM certificates").fetchall()
            certs = [{f: (dict(r).get(f) or "")
                      for f, _ in intern_io.CERT_COLUMNS} for r in rows]
        except sqlite3.Error:
            pass
    finally:
        conn.close()
    return interns, certs


# --------------------------------------------------------------------------- #
# Creating a backup
# --------------------------------------------------------------------------- #
def create_backup(target: Optional[Path] = None, include_output: bool = True,
                  label: str = "", progress=None) -> BackupInfo:
    """Archive the whole application state into a single ``.zip``.

    ``target`` defaults to a timestamped file inside ``Backup & Restore``.
    ``progress`` is an optional ``callable(done, total, label)``.
    """
    root = settings.BASE_DIR
    destination = Path(target) if target else backup_root() / suggested_name(
        label=label)
    destination.parent.mkdir(parents=True, exist_ok=True)

    def announce(done: int, total: int, what: str) -> None:
        if progress:
            try:
                progress(done, total, what)
            except Exception:
                pass

    # -- collect the file list ------------------------------------------- #
    members: List[Tuple[Path, str]] = []       # (source on disk, member name)

    for relative in _LOOSE_FILES:
        candidate = root / relative
        if candidate.is_file():
            members.append((candidate, f"{PAYLOAD_PREFIX}/{relative}"))

    trees = list(_TREES) + ([_OUTPUT_TREE] if include_output else [])
    template_count = document_count = 0
    for tree in trees:
        folder = root / tree
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*")):
            if not path.is_file() or _skip(path, root):
                continue
            relative = path.relative_to(root)
            members.append((path, f"{PAYLOAD_PREFIX}/{relative.as_posix()}"))
            if tree == "templates" and path.suffix.lower() == ".json":
                template_count += 1
            elif tree == _OUTPUT_TREE and path.suffix.lower() == ".pdf":
                document_count += 1

    # -- database, snapshotted into a temp file first --------------------- #
    staging = Path(tempfile.mkdtemp(prefix="certiflow_backup_"))
    interns = certificates = 0
    try:
        live_db = settings.COMPANY_DIR / "interns.db"
        if live_db.is_file():
            staged_db = staging / "interns.db"
            interns, certificates = _copy_database(live_db, staged_db)
            members.append((staged_db, f"{PAYLOAD_PREFIX}/{_DB_MEMBER}"))

        total = len(members) + 1
        manifest = {
            "app": settings.APP_NAME,
            "schema": SCHEMA_VERSION,
            "app_version": settings.APP_VERSION,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "created_on": _hostname(),
            "data_mode": settings.DATA_MODE,
            "includes_output": bool(include_output),
            "counts": {
                "interns": interns,
                "certificates": certificates,
                "templates": template_count,
                "documents": document_count,
            },
            "files": {},
        }

        temp_zip = destination.with_suffix(destination.suffix + ".part")
        if temp_zip.exists():
            temp_zip.unlink()
        with zipfile.ZipFile(temp_zip, "w", zipfile.ZIP_DEFLATED,
                             compresslevel=6) as archive:
            for index, (source, member) in enumerate(members, start=1):
                announce(index, total, Path(member).name)
                archive.write(source, arcname=member)
                manifest["files"][member] = {
                    "sha256": _sha256(source),
                    "bytes": source.stat().st_size,
                }
            announce(total, total, MANIFEST_NAME)
            archive.writestr(MANIFEST_NAME,
                             json.dumps(manifest, indent=2, ensure_ascii=False))
            archive.writestr("READ ME.txt", _ARCHIVE_README)

        # Only now becomes a real backup, so an interrupted run leaves no
        # half-written file that looks restorable.
        if destination.exists():
            destination.unlink()
        temp_zip.replace(destination)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return BackupInfo(
        path=destination,
        created_at=manifest["created_at"],
        app_version=manifest["app_version"],
        created_on=manifest["created_on"],
        data_mode=manifest["data_mode"],
        interns=interns,
        certificates=certificates,
        templates=template_count,
        documents=document_count,
        includes_output=bool(include_output),
        file_count=len(members),
        size_bytes=destination.stat().st_size,
    )


def _hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return os.environ.get("COMPUTERNAME", "") or "unknown"


_ARCHIVE_README = """CertiFlow backup
================

A complete copy of one CertiFlow installation's data: company profile, intern
database, certificate records, saved designs, branding assets and (unless it was
switched off) every generated PDF.

To restore it
-------------
Open CertiFlow, go to "Backup & Restore", then either pick this file from the
list or use "Import Backup" to browse to it.

Restoring offers two modes:

  Merge    adds what is missing and leaves your current records alone.
           Intern IDs and certificate numbers are preserved exactly.
  Replace  discards the current data and restores this backup as-is.
           CertiFlow snapshots your current database first.

Moving to another computer? Copy this single file across; nothing else is
needed. It also works for moving from the source version to the installed .exe.

manifest.json lists every file with a SHA-256 checksum. CertiFlow verifies those
before restoring, so a corrupted or partial archive is caught rather than
silently applied.
"""


# --------------------------------------------------------------------------- #
# Reading a backup
# --------------------------------------------------------------------------- #
def inspect(path: Path, verify: bool = True) -> BackupInfo:
    """Describe an archive, optionally re-checking every checksum.

    Raises ``ValueError`` when the file is not a CertiFlow backup.
    """
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"{path.name} does not exist.")
    if not zipfile.is_zipfile(path):
        raise ValueError(f"{path.name} is not a .zip archive.")

    info = BackupInfo(path=path, size_bytes=path.stat().st_size)
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if MANIFEST_NAME not in names:
            raise ValueError(
                f"{path.name} has no {MANIFEST_NAME}, so it is not a "
                f"CertiFlow backup.")
        try:
            manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"{path.name} has an unreadable manifest.") from exc

        if str(manifest.get("app", "")).strip() != settings.APP_NAME:
            info.problems.append(
                f"made by {manifest.get('app') or 'another app'}")
        counts = manifest.get("counts") or {}
        info.created_at = str(manifest.get("created_at", ""))
        info.app_version = str(manifest.get("app_version", ""))
        info.created_on = str(manifest.get("created_on", ""))
        info.data_mode = str(manifest.get("data_mode", ""))
        info.includes_output = bool(manifest.get("includes_output"))
        info.interns = int(counts.get("interns") or 0)
        info.certificates = int(counts.get("certificates") or 0)
        info.templates = int(counts.get("templates") or 0)
        info.documents = int(counts.get("documents") or 0)

        recorded = manifest.get("files") or {}
        info.file_count = len(recorded)

        missing = [m for m in recorded if m not in names]
        if missing:
            info.problems.append(f"{len(missing)} file(s) listed but absent")

        if verify:
            bad = []
            for member, meta in recorded.items():
                if member not in names:
                    continue
                expected = str(meta.get("sha256") or "")
                if not expected:
                    continue
                digest = hashlib.sha256()
                with archive.open(member) as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != expected:
                    bad.append(member)
            if bad:
                info.problems.append(f"{len(bad)} file(s) failed the checksum")

    info.integrity_ok = not info.problems
    return info


def list_backups(folder: Optional[Path] = None) -> List[BackupInfo]:
    """Every readable backup in ``folder``, newest first.

    Checksums are not verified here - that would mean hashing gigabytes just to
    draw a list. :func:`inspect` is called with ``verify=True`` before a restore.
    """
    target = Path(folder) if folder else backup_root()
    found: List[BackupInfo] = []
    if not target.is_dir():
        return found
    for path in target.glob(f"*{FILE_SUFFIX}"):
        try:
            found.append(inspect(path, verify=False))
        except ValueError:
            continue                       # not one of ours; ignore quietly
    found.sort(key=lambda i: (i.created_at or "", i.path.name), reverse=True)
    return found


# --------------------------------------------------------------------------- #
# Restoring
# --------------------------------------------------------------------------- #
def current_state() -> Dict[str, int]:
    """Counts describing the data that is here right now."""
    root = settings.BASE_DIR
    db_path = settings.COMPANY_DIR / "interns.db"
    interns = certificates = 0
    if db_path.is_file():
        conn = None
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True,
                                   timeout=5)
            interns = _table_count(conn, "interns")
            certificates = _table_count(conn, "certificates")
        except sqlite3.Error:
            pass
        finally:
            if conn is not None:
                conn.close()

    def count(tree: str, suffix: str) -> int:
        folder = root / tree
        if not folder.is_dir():
            return 0
        return sum(1 for p in folder.rglob(f"*{suffix}")
                   if p.is_file() and not _skip(p, root))

    output_dir = root / _OUTPUT_TREE
    size = 0
    if output_dir.is_dir():
        size = sum(p.stat().st_size for p in output_dir.rglob("*")
                   if p.is_file())
    return {
        "interns": interns,
        "certificates": certificates,
        "templates": count("templates", ".json"),
        "documents": count(_OUTPUT_TREE, ".pdf"),
        "output_bytes": size,
    }


def is_empty() -> bool:
    """True when there is essentially nothing here to lose.

    Used to decide whether a full ``replace`` needs a warning: on a fresh
    install it is the obviously right choice and should not be scary.
    """
    state = current_state()
    if state["interns"] or state["certificates"] or state["documents"]:
        return False
    try:
        # Explicit path: CompanyProfile's default is bound at import time and
        # would ignore a redirected data folder.
        profile = settings.CompanyProfile(settings.COMPANY_FILE)
        return not profile.is_configured()
    except Exception:
        return True


def restore(path: Path, mode: str = "merge", db=None, cert_store=None,
            include_output: bool = True, progress=None) -> RestoreResult:
    """Restore ``path`` into the current data folder.

    ``mode="merge"``   Adds missing records and files; existing ones are kept.
                      Safe on a populated installation, and applied live
                      through ``db``/``cert_store`` so no restart is needed.
    ``mode="replace"`` Restores the archive as the new state. The database file
                      is swapped wholesale, which cannot be done while the app
                      holds it open - so the caller must close the connections
                      and restart. ``restart_required`` is set to say so.

    ``db`` and ``cert_store`` are required for ``merge``.
    """
    mode = (mode or "merge").strip().lower()
    if mode not in ("merge", "replace"):
        raise ValueError(f"Unknown restore mode: {mode}")

    info = inspect(path, verify=True)
    if not info.integrity_ok:
        raise ValueError("This backup failed its integrity check: "
                         + "; ".join(info.problems))

    result = RestoreResult(mode=mode)
    root = settings.BASE_DIR
    staging = Path(tempfile.mkdtemp(prefix="certiflow_restore_"))

    def announce(done: int, total: int, what: str) -> None:
        if progress:
            try:
                progress(done, total, what)
            except Exception:
                pass

    try:
        with zipfile.ZipFile(path) as archive:
            wanted = [m for m in archive.namelist()
                      if m.startswith(f"{PAYLOAD_PREFIX}/")]
            if not include_output:
                wanted = [m for m in wanted
                          if not m.startswith(
                              f"{PAYLOAD_PREFIX}/{_OUTPUT_TREE}/")]
            for index, member in enumerate(wanted, start=1):
                announce(index, len(wanted) + 2, Path(member).name)
                archive.extract(member, staging)

        payload = staging / PAYLOAD_PREFIX
        if not payload.is_dir():
            raise ValueError("This backup contains no data payload.")

        # Always snapshot the live database before touching anything.
        live_db = settings.COMPANY_DIR / "interns.db"
        if live_db.is_file():
            result.snapshot = backup.create_backup(live_db, f"restore-{mode}")

        restored_db = payload / _DB_MEMBER

        if mode == "replace":
            announce(len(wanted) + 1, len(wanted) + 2, "replacing data")
            _replace_files(payload, root, result, include_output)
            if restored_db.is_file():
                # Connections are closed by the caller; swap the file in.
                for suffix in ("-wal", "-shm"):
                    stale = Path(str(live_db) + suffix)
                    if stale.exists():
                        try:
                            stale.unlink()
                        except OSError:
                            pass
                live_db.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(restored_db, live_db)
                result.restart_required = True
                result.notes.append(
                    "The database was replaced, so CertiFlow needs to "
                    "restart to reopen it.")
        else:
            announce(len(wanted) + 1, len(wanted) + 2, "merging records")
            if db is None or cert_store is None:
                raise ValueError("A merge restore needs the open database.")
            if restored_db.is_file():
                interns, certs = _read_records(restored_db)
                archive_records = intern_io.Archive(
                    interns=interns, certificates=certs, source=path.name)
                merged = intern_io.apply_archive(db, cert_store,
                                                 archive_records,
                                                 overwrite=False)
                result.interns_added = merged.interns_added
                result.interns_updated = merged.interns_updated
                result.certs_added = merged.certs_added
                result.certs_updated = merged.certs_updated
                if merged.skipped:
                    result.notes.append(
                        f"{len(merged.skipped)} record(s) already present were "
                        f"left as they are.")
            _merge_files(payload, root, result, include_output)

        announce(len(wanted) + 2, len(wanted) + 2, "done")
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    return result


def _iter_payload(payload: Path, include_output: bool):
    for source in sorted(payload.rglob("*")):
        if not source.is_file():
            continue
        relative = source.relative_to(payload)
        if not include_output and relative.parts[:1] == (_OUTPUT_TREE,):
            continue
        yield source, relative


def _merge_files(payload: Path, root: Path, result: RestoreResult,
                 include_output: bool) -> None:
    """Copy in only what is missing, so nothing local is overwritten."""
    for source, relative in _iter_payload(payload, include_output):
        posix = relative.as_posix()
        target = root / relative

        if posix == _DB_MEMBER:
            continue                               # handled via intern_io
        if posix == "company/company.json":
            # Only adopt the archived profile when there is nothing set up yet,
            # otherwise a merge would silently rebrand the user's documents.
            try:
                configured = settings.CompanyProfile(
                    settings.COMPANY_FILE).is_configured()
            except Exception:
                configured = False
            if configured:
                result.files_skipped += 1
                result.notes.append(
                    "Kept your existing company profile; the backup's profile "
                    "was not applied.")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            result.company_restored = True
            continue
        if posix == "company/app_settings.json":
            if target.exists():
                result.files_skipped += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            result.settings_restored = True
            continue

        if target.exists():
            result.files_skipped += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        result.files_copied += 1
        if relative.parts[:1] == ("templates",):
            result.templates_copied += 1


def _replace_files(payload: Path, root: Path, result: RestoreResult,
                   include_output: bool) -> None:
    """Overwrite local copies with the archived ones."""
    for source, relative in _iter_payload(payload, include_output):
        posix = relative.as_posix()
        if posix == _DB_MEMBER:
            continue                               # copied by the caller
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        result.files_copied += 1
        if posix == "company/company.json":
            result.company_restored = True
        elif posix == "company/app_settings.json":
            result.settings_restored = True
        elif relative.parts[:1] == ("templates",):
            result.templates_copied += 1


def delete_backup(path: Path) -> bool:
    """Remove a backup file. Returns ``True`` when it went."""
    try:
        Path(path).unlink()
        return True
    except OSError:
        return False

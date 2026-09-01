"""
deploy_data.py
--------------
Copy the live working data into a packaged build.

A fresh build starts empty on purpose: ``company/interns.db`` holds intern
personal data and is deliberately kept out of the binary (see ``CertiFlow.spec``).
This script fills a built folder in afterwards, for the normal case where the
build is for *your own* use rather than for sharing.

    python tools/deploy_data.py                      # -> dist/CertiFlow
    python tools/deploy_data.py "D:/CertiFlow"       # -> anywhere else

Re-run it after every rebuild: ``PyInstaller --clean`` deletes the whole
``dist/CertiFlow`` folder, data included.

What gets copied
================
    company/interns.db          via SQLite's online backup API, so the copy is
                                consistent even under WAL (a plain file copy
                                can miss the write-ahead log)
    company/company.json        the company profile
    company/app_settings.json   theme and preferences
    templates/*.json            designs saved in the Template Designer
    assets/**                   logo, signature, stamp, watermark, fonts
    output/**                   generated PDFs, so the stored paths resolve and
                                the Open buttons work for past documents

Deliberately skipped: ``company/backups/`` (historical snapshots, not needed to
run) and ``logs/``.

WARNING: after running this, the target folder contains intern personal data.
Do not hand that folder to anyone who should not have it - delete
``company/interns.db`` and ``output/`` from the copy first, or just rebuild.
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

# This script lives in tools/, so the project root is one level up.
BASE = Path(__file__).resolve().parent.parent
DEFAULT_TARGET = BASE / "dist" / "CertiFlow"


def copy_database(source: Path, target: Path) -> str:
    """Copy a SQLite database consistently, without its WAL sidecars."""
    if not source.exists():
        return f"skipped {source.name} (not found)"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    # Remove stale sidecars so the copy is not paired with someone else's WAL.
    for suffix in ("-wal", "-shm"):
        stale = Path(str(target) + suffix)
        if stale.exists():
            stale.unlink()

    src = dst = None
    try:
        src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=10)
        dst = sqlite3.connect(str(target), timeout=10)
        with dst:
            src.backup(dst)
        rows = dst.execute("SELECT COUNT(*) FROM interns").fetchone()[0]
        certs = dst.execute("SELECT COUNT(*) FROM certificates").fetchone()[0]
    finally:
        for conn in (dst, src):
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    return f"{target.name}: {rows} intern(s), {certs} certificate(s)"


def copy_tree(source: Path, target: Path) -> str:
    """Mirror a folder, adding and replacing but never deleting extras."""
    if not source.is_dir():
        return f"skipped {source.name}/ (not found)"
    copied = 0
    for item in source.rglob("*"):
        if not item.is_file():
            continue
        relative = item.relative_to(source)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, destination)
        copied += 1
    return f"{source.name}/: {copied} file(s)"


def copy_file(source: Path, target: Path) -> str:
    if not source.is_file():
        return f"skipped {source.name} (not found)"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return f"{source.name}: copied"


def main(argv: list) -> int:
    target = Path(argv[1]).resolve() if len(argv) > 1 else DEFAULT_TARGET
    if not target.exists():
        print(f"No build at {target}")
        print("Build one first:  python -m PyInstaller --noconfirm "
              "CertiFlow.spec")
        return 1
    if target.resolve() == BASE.resolve():
        print("Refusing to copy the project over itself.")
        return 1

    exe = target / "CertiFlow.exe"
    print(f"Target : {target}")
    print(f"Build  : {'CertiFlow.exe found' if exe.is_file() else 'no exe here'}")
    print()

    steps = [
        copy_database(BASE / "company" / "interns.db",
                      target / "company" / "interns.db"),
        copy_file(BASE / "company" / "company.json",
                  target / "company" / "company.json"),
        copy_file(BASE / "company" / "app_settings.json",
                  target / "company" / "app_settings.json"),
        copy_tree(BASE / "assets", target / "assets"),
        copy_tree(BASE / "templates", target / "templates"),
        copy_tree(BASE / "output", target / "output"),
    ]
    for line in steps:
        print(f"  {line}")

    total = sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
    print(f"\nFolder now {total / 1024 / 1024:.1f} MB")
    print("\nThis folder now contains intern personal data. Do not share it "
          "as-is.")
    if exe.is_file():
        print(f"Verify with:  \"{exe}\" --selftest")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

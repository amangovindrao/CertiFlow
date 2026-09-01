"""
package.py
----------
Produce the shareable artefacts: a Windows installer and a portable ZIP.

    python tools/package.py                # clean build, verify, ZIP + installer
    python tools/package.py --skip-build   # reuse dist/CertiFlow as it stands
    python tools/package.py --no-installer # portable ZIP only

Output lands in ``dist/release/``:

    CertiFlow-<version>-Setup.exe    installer, no admin rights needed
    CertiFlow-<version>-portable.zip unzip and run, data stays in the folder

Why this exists rather than a bare PyInstaller call
===================================================
A build meant for other people must not carry this company's records. The
intern database holds personal data, and ``output/`` holds real certificates,
so :func:`verify_clean` fails the build if either got in. That check is the
whole point: it is easy to run ``deploy_data.py`` for your own use and then
forget which folder you are about to hand over.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# This script lives in tools/, so the project root is one level up.
BASE = Path(__file__).resolve().parent.parent
DIST = BASE / "dist" / "CertiFlow"
RELEASE = BASE / "dist" / "release"
ISS = BASE / "installer" / "CertiFlow.iss"

# Anything matching these must never appear in a build meant for sharing.
FORBIDDEN_NAMES = ("interns.db", "interns.db-wal", "interns.db-shm")
FORBIDDEN_DIRS = ("output", "backups")

ISCC_CANDIDATES = (
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "InnoSetup6"
    / "ISCC.exe",
    Path(os.environ.get("ProgramFiles(x86)", "")) / "Inno Setup 6" / "ISCC.exe",
    Path(os.environ.get("ProgramFiles", "")) / "Inno Setup 6" / "ISCC.exe",
)


def version() -> str:
    sys.path.insert(0, str(BASE))
    import settings

    return settings.APP_VERSION


def find_iscc() -> Path | None:
    for candidate in ISCC_CANDIDATES:
        if candidate.is_file():
            return candidate
    found = shutil.which("ISCC.exe") or shutil.which("iscc")
    return Path(found) if found else None


def run(command: list, label: str) -> None:
    print(f"\n=== {label} ===")
    print("  " + " ".join(str(c) for c in command))
    result = subprocess.run(command, cwd=str(BASE))
    if result.returncode != 0:
        raise SystemExit(f"{label} failed (exit {result.returncode})")


def build() -> None:
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "CertiFlow.spec"], "PyInstaller")


def verify_clean() -> None:
    """Refuse to package a build that contains anybody's records."""
    print("\n=== Checking the build carries no personal data ===")
    if not DIST.is_dir():
        raise SystemExit(f"No build at {DIST}")

    problems = []
    for path in DIST.rglob("*"):
        relative = path.relative_to(DIST)
        parts = {p.lower() for p in relative.parts}
        if path.is_file() and path.name.lower() in FORBIDDEN_NAMES:
            problems.append(str(relative))
        elif path.is_file() and parts & {d.lower() for d in FORBIDDEN_DIRS}:
            problems.append(str(relative))

    exe = DIST / "CertiFlow.exe"
    if not exe.is_file():
        raise SystemExit(f"Missing {exe}")

    if problems:
        print(f"  FOUND {len(problems)} file(s) that must not be shared:")
        for item in problems[:12]:
            print(f"    {item}")
        if len(problems) > 12:
            print(f"    +{len(problems) - 12} more")
        raise SystemExit(
            "\nRefusing to package. This looks like a build that "
            "deploy_data.py filled with real records.\nRun a clean build "
            "first:  python -m PyInstaller --noconfirm --clean CertiFlow.spec")

    marker = DIST / "installed.marker"
    if marker.exists():
        # Would make the portable ZIP write to AppData instead of its folder.
        marker.unlink()
        print("  removed a stray installed.marker")

    files = [p for p in DIST.rglob("*") if p.is_file()]
    size = sum(p.stat().st_size for p in files) / 1024 / 1024
    print(f"  clean: {len(files)} files, {size:.1f} MB, no personal data")


def make_zip(tag: str) -> Path:
    RELEASE.mkdir(parents=True, exist_ok=True)
    target = RELEASE / f"CertiFlow-{tag}-portable.zip"
    if target.exists():
        target.unlink()
    print(f"\n=== Portable ZIP ===")
    files = sorted(p for p in DIST.rglob("*") if p.is_file())
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=9) as archive:
        for path in files:
            archive.write(path, arcname=str(
                Path("CertiFlow") / path.relative_to(DIST)))
        archive.writestr("CertiFlow/READ ME FIRST.txt", PORTABLE_README)
    print(f"  {target.name}  {target.stat().st_size / 1024 / 1024:.1f} MB "
          f"({len(files)} files)")
    return target


PORTABLE_README = """CertiFlow - portable copy
=========================

Run CertiFlow.exe. Nothing to install.

This copy keeps everything it creates inside this folder:

    company\\    company profile + the intern database
    output\\     generated PDFs
    templates\\  designs saved in the Template Designer
    assets\\     your logo, signature, stamp and watermark
    logs\\       activity and error logs

So the whole folder is your backup: copy it to a USB stick or another PC and
your records travel with it. Keep it somewhere you can write to - Documents or
the Desktop is fine, Program Files is not.

First run
---------
Open Company Settings and fill in your company name, logo and signature. Every
document is generated from that profile.

Verifying this copy works
-------------------------
    CertiFlow.exe --selftest

That generates one of each document type and writes a report to
logs\\selftest.log.

Prefer a proper installation with a Start Menu entry? Use the Setup .exe
instead. An installed copy keeps its data in %LOCALAPPDATA%\\CertiFlow, so it
survives upgrades.
"""


def make_installer(tag: str) -> Path | None:
    iscc = find_iscc()
    if iscc is None:
        print("\n=== Installer ===")
        print("  SKIPPED: Inno Setup (ISCC.exe) not found.")
        print("  Install it from https://jrsoftware.org/isdl.php, then re-run.")
        return None
    RELEASE.mkdir(parents=True, exist_ok=True)
    run([str(iscc), f"/DAppVersion={tag}",
         f"/DSourceDir={DIST}", f"/DOutputDir={RELEASE}", str(ISS)],
        "Inno Setup")
    produced = RELEASE / f"CertiFlow-{tag}-Setup.exe"
    if not produced.is_file():
        candidates = sorted(RELEASE.glob("*Setup.exe"))
        produced = candidates[-1] if candidates else None
    if produced:
        print(f"  {produced.name}  "
              f"{produced.stat().st_size / 1024 / 1024:.1f} MB")
    return produced


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description="Package CertiFlow for release")
    parser.add_argument("--skip-build", action="store_true",
                        help="reuse dist/CertiFlow instead of rebuilding")
    parser.add_argument("--no-installer", action="store_true",
                        help="portable ZIP only")
    parser.add_argument("--no-zip", action="store_true",
                        help="installer only")
    args = parser.parse_args(argv[1:])

    tag = version()
    print(f"CertiFlow {tag}")

    if not args.skip_build:
        build()
    verify_clean()

    artefacts = []
    if not args.no_zip:
        artefacts.append(make_zip(tag))
    if not args.no_installer:
        made = make_installer(tag)
        if made:
            artefacts.append(made)

    print("\n=== Release ===")
    print(f"  {RELEASE}")
    for item in artefacts:
        print(f"    {item.name}")
    print("\nBoth are safe to share: neither contains any intern records.")
    print("For your own machine, install or unzip, then optionally run "
          "deploy_data.py against it.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

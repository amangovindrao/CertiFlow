"""
settings.py
-----------
Persistent storage layer for the Offer Letter Generator.

Responsibilities:
    * Load / save the company profile      -> company/company.json
    * Load / save application preferences   -> company/app_settings.json
      (theme, last output folder, recent candidates, auto-saved draft)

The module exposes two small, well documented manager classes so that the
rest of the application never has to touch the file system or JSON directly.
This keeps the persistence concern isolated and makes it trivial to swap the
backing store (e.g. an SQLite DB or the cloud) in the future.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
# Two different roots, because a packaged build has two different kinds of file.
#
#   BASE_DIR      writable user data - company profile, database, assets the
#                 user uploads, generated PDFs, saved designs, logs.
#   RESOURCE_DIR  read-only payload shipped with the build - bundled fonts and
#                 the example company profile.
#
# Running from source the two are the same folder. Frozen by PyInstaller they
# differ: RESOURCE_DIR points at the bundle (``sys._MEIPASS``), which is
# read-only, while BASE_DIR has to land somewhere the user can actually write.
FROZEN: bool = bool(getattr(sys, "frozen", False))

APP_NAME = "CertiFlow"
APP_VERSION = "1.0.0"
APP_PUBLISHER = "ScaleOn"

# The installer drops this file next to the .exe to say "this copy was
# installed". Its *absence* means a plain folder build, which keeps its data
# beside the .exe so the whole folder stays portable.
#
# Declared by the installer rather than by the portable build because that way
# the default - an ordinary PyInstaller output folder - behaves the same as it
# always has.
INSTALLED_MARKER = "installed.marker"


def _local_appdata() -> Path:
    raw = os.environ.get("LOCALAPPDATA", "").strip()
    if raw:
        return Path(raw)
    return Path.home() / "AppData" / "Local"


def _resolve_data_root(exe_dir: Path) -> tuple:
    """Decide where user data lives, returning ``(path, mode)``.

    ``custom``     ``CERTIFLOW_DATA`` overrides everything - shared network
                   storage, or a sandbox during testing.
    ``installed``  ``installed.marker`` is present, so this came from the
                   installer. Data goes to ``%LOCALAPPDATA%\\CertiFlow``:
                   always writable, private per user, and left alone when the
                   program is upgraded or removed. Writing beside the .exe
                   would fail outright under Program Files, and would be wrong
                   even where it succeeds, because every user of the machine
                   would share one database.
    ``portable``   No marker: a plain folder build. Data sits beside the .exe,
                   so copying the folder to a USB stick takes everything.
    """
    override = os.environ.get("CERTIFLOW_DATA", "").strip()
    if override:
        return Path(override).expanduser(), "custom"
    if (exe_dir / INSTALLED_MARKER).is_file():
        return _local_appdata() / APP_NAME, "installed"
    return exe_dir, "portable"


# Kept as an alias: earlier builds and docs referred to this name.
APP_FOLDER_NAME = APP_NAME


if FROZEN:
    _EXE_DIR: Path = Path(sys.executable).resolve().parent
    RESOURCE_DIR: Path = Path(getattr(sys, "_MEIPASS", _EXE_DIR)).resolve()
    BASE_DIR, DATA_MODE = _resolve_data_root(_EXE_DIR)
    BASE_DIR = BASE_DIR.resolve()
else:
    _EXE_DIR = Path(__file__).resolve().parent
    RESOURCE_DIR = _EXE_DIR
    _override = os.environ.get("CERTIFLOW_DATA", "").strip()
    BASE_DIR = Path(_override).expanduser().resolve() if _override else _EXE_DIR
    DATA_MODE = "custom" if _override else "source"

ASSETS_DIR: Path = BASE_DIR / "assets"
FONTS_DIR: Path = ASSETS_DIR / "fonts"
COMPANY_DIR: Path = BASE_DIR / "company"
TEMPLATES_DIR: Path = BASE_DIR / "templates"
OUTPUT_DIR: Path = BASE_DIR / "output"
LOGS_DIR: Path = BASE_DIR / "logs"

COMPANY_FILE: Path = COMPANY_DIR / "company.json"
APP_SETTINGS_FILE: Path = COMPANY_DIR / "app_settings.json"


def _seed_from_bundle() -> None:
    """Copy bundled starter files next to the exe on first run.

    Only ever *adds* missing files: anything the user has already put in place
    is left exactly as it is, so upgrading the exe never overwrites a logo or
    a company profile.
    """
    if RESOURCE_DIR == BASE_DIR:
        return                      # running from source, nothing to seed
    payload = RESOURCE_DIR / "_bundled"
    if not payload.is_dir():
        return
    for source in payload.rglob("*"):
        if not source.is_file():
            continue
        target = BASE_DIR / source.relative_to(payload)
        if target.exists():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        except OSError:
            continue                # a read-only install dir must not crash


def ensure_directories() -> None:
    """Create every folder the application relies on (idempotent)."""
    for directory in (ASSETS_DIR, FONTS_DIR, COMPANY_DIR, TEMPLATES_DIR,
                      OUTPUT_DIR, LOGS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    # Dedicated asset sub-folders (requirement: logo/ signature/ stamp/ watermark/).
    for kind in ASSET_KINDS:
        (ASSETS_DIR / kind).mkdir(parents=True, exist_ok=True)
    _seed_from_bundle()


# Asset categories that each get their own folder under assets/.
ASSET_KINDS = ("logo", "signature", "stamp", "watermark")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")


def asset_dir(kind: str) -> Path:
    """Return the dedicated folder for an asset kind, e.g. assets/logo/."""
    return ASSETS_DIR / kind


# --------------------------------------------------------------------------- #
# Stored file paths
# --------------------------------------------------------------------------- #
def store_path(path: Any) -> str:
    """Normalise a path for storage in the database.

    Anything inside the project is stored **relative to the project root**, so
    the folder can be moved or copied without breaking every stored reference -
    and so a stored path can never silently point outside the project. Paths
    elsewhere on disk are kept absolute.
    """
    text = str(path or "").strip()
    if not text:
        return ""
    candidate = Path(text)
    try:
        if candidate.is_absolute():
            return str(candidate.resolve().relative_to(BASE_DIR))
    except (ValueError, OSError):
        return str(candidate)
    return str(candidate)


def resolve_path(path: Any) -> Path:
    """Turn a stored path back into a usable absolute path.

    An empty value stays empty: ``Path("")`` renders as ``"."``, so resolving it
    would otherwise yield the project root - a directory that *exists*, which
    would make callers think a missing PDF is present.
    """
    text = str(path or "").strip()
    if not text:
        return Path("")
    candidate = Path(text)
    return candidate if candidate.is_absolute() else BASE_DIR / candidate


def autoload_assets(profile: "CompanyProfile") -> None:
    """Auto-load logo/signature/watermark from their folders on startup.

    If the profile already points to an existing file it is kept; otherwise the
    first image found in the matching ``assets/<kind>/`` folder is adopted so
    the user never has to re-upload after restarting the app.
    """
    changed = False
    for kind in ("logo", "signature", "watermark", "stamp"):
        current = str(profile.get(kind, "")).strip()
        if current:
            resolved = Path(current.replace("\\", "/"))
            if not resolved.is_absolute():
                resolved = BASE_DIR / resolved
            if resolved.exists():
                continue  # existing asset is valid, leave it
        folder = asset_dir(kind)
        if folder.exists():
            images = sorted(p for p in folder.iterdir()
                            if p.suffix.lower() in IMAGE_EXTS)
            if images:
                try:
                    rel = str(images[0].relative_to(BASE_DIR))
                except ValueError:
                    rel = str(images[0])
                profile.update({kind: rel})
                changed = True
    if changed:
        profile.save()


# --------------------------------------------------------------------------- #
# Defaults
# --------------------------------------------------------------------------- #
DEFAULT_COMPANY: Dict[str, Any] = {
    "company_name": "",
    "tagline": "",
    "email": "",
    "phone": "",
    "website": "",
    "address": "",
    "hr_name": "",
    "hr_designation": "",
    "footer_text": "",
    "logo": "",
    "watermark": "",
    "signature": "",
    "stamp": "",
    "footer_color": "#111111",
    "scaleon_font": "Sans",
}

DEFAULT_APP_SETTINGS: Dict[str, Any] = {
    "theme": "Light",                 # "Light" | "Dark"
}


# --------------------------------------------------------------------------- #
# Generic JSON helpers
# --------------------------------------------------------------------------- #
def _read_json(path: Path, fallback: Dict[str, Any]) -> Dict[str, Any]:
    """Read a JSON file, returning ``fallback`` on any error."""
    try:
        if path.exists():
            with path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            # Merge with fallback so newly added keys always exist.
            merged = {**fallback, **data}
            return merged
    except (json.JSONDecodeError, OSError):
        pass
    return dict(fallback)


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    """Atomically write ``data`` as pretty JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=4, ensure_ascii=False)
    tmp.replace(path)


# --------------------------------------------------------------------------- #
# Company profile manager
# --------------------------------------------------------------------------- #
class CompanyProfile:
    """Loads and persists the single company profile.

    Future ready: the class can easily be extended to support *multiple*
    company profiles by accepting a ``profile_id`` and storing each profile
    under ``company/<profile_id>.json``.
    """

    def __init__(self, path: Path = COMPANY_FILE) -> None:
        self.path = path
        self.data: Dict[str, Any] = _read_json(path, DEFAULT_COMPANY)

    # -- accessors -------------------------------------------------------- #
    def get(self, key: str, default: Any = "") -> Any:
        return self.data.get(key, default)

    def update(self, values: Dict[str, Any]) -> None:
        """Update the in-memory profile (does not save automatically)."""
        self.data.update(values)

    def save(self) -> None:
        _write_json(self.path, self.data)

    def is_configured(self) -> bool:
        """True when the minimum required company info has been entered."""
        return bool(self.data.get("company_name", "").strip())


# --------------------------------------------------------------------------- #
# Application settings manager
# --------------------------------------------------------------------------- #
class AppSettings:
    """Stores user preferences and lightweight application state."""

    def __init__(self, path: Path = APP_SETTINGS_FILE) -> None:
        self.path = path
        self.data: Dict[str, Any] = _read_json(path, DEFAULT_APP_SETTINGS)

    # -- generic ---------------------------------------------------------- #
    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value
        self.save()

    def save(self) -> None:
        _write_json(self.path, self.data)

    # -- theme ------------------------------------------------------------ #
    @property
    def theme(self) -> str:
        return self.data.get("theme", "Light")

    @theme.setter
    def theme(self, value: str) -> None:
        self.set("theme", value)

    # Everything generated goes through ``bulk_generator.output_dir_for``, so
    # there is no per-user output folder preference to keep any more.

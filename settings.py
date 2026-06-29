"""                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          """"""
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
from pathlib import Path
from typing import Any, Dict, List

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
BASE_DIR: Path = Path(__file__).resolve().parent

ASSETS_DIR: Path = BASE_DIR / "assets"
FONTS_DIR: Path = ASSETS_DIR / "fonts"
COMPANY_DIR: Path = BASE_DIR / "company"
TEMPLATES_DIR: Path = BASE_DIR / "templates"
OUTPUT_DIR: Path = BASE_DIR / "output"

COMPANY_FILE: Path = COMPANY_DIR / "company.json"
APP_SETTINGS_FILE: Path = COMPANY_DIR / "app_settings.json"


def ensure_directories() -> None:
    """Create every folder the application relies on (idempotent)."""
    for directory in (ASSETS_DIR, FONTS_DIR, COMPANY_DIR, TEMPLATES_DIR, OUTPUT_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    # Dedicated asset sub-folders (requirement: logo/ signature/ stamp/ watermark/).
    for kind in ASSET_KINDS:
        (ASSETS_DIR / kind).mkdir(parents=True, exist_ok=True)


# Asset categories that each get their own folder under assets/.
ASSET_KINDS = ("logo", "signature", "stamp", "watermark")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp")


def asset_dir(kind: str) -> Path:
    """Return the dedicated folder for an asset kind, e.g. assets/logo/."""
    return ASSETS_DIR / kind


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
    "last_output_folder": str(OUTPUT_DIR),
    "recent_candidates": [],          # list[dict] - last filled forms
    "draft": {},                      # auto-saved in-progress form
}

MAX_RECENT_CANDIDATES: int = 10


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

    # -- output folder ---------------------------------------------------- #
    @property
    def last_output_folder(self) -> str:
        return self.data.get("last_output_folder", str(OUTPUT_DIR))

    @last_output_folder.setter
    def last_output_folder(self, value: str) -> None:
        self.set("last_output_folder", value)

    # -- recent candidates ------------------------------------------------ #
    def add_recent_candidate(self, candidate: Dict[str, Any]) -> None:
        """Insert ``candidate`` at the front of the recent list (de-duped)."""
        name = candidate.get("candidate_name", "").strip()
        if not name:
            return
        recent: List[Dict[str, Any]] = [
            c for c in self.data.get("recent_candidates", [])
            if c.get("candidate_name", "").strip().lower() != name.lower()
        ]
        recent.insert(0, candidate)
        self.data["recent_candidates"] = recent[:MAX_RECENT_CANDIDATES]
        self.save()

    @property
    def recent_candidates(self) -> List[Dict[str, Any]]:
        return self.data.get("recent_candidates", [])

    # -- draft auto-save -------------------------------------------------- #
    def save_draft(self, draft: Dict[str, Any]) -> None:
        self.data["draft"] = draft
        self.save()

    @property
    def draft(self) -> Dict[str, Any]:
        return self.data.get("draft", {})

    def clear_draft(self) -> None:
        self.data["draft"] = {}
        self.save()

"""
utils.py
--------
Pure, side-effect-light helper functions shared across the application:

    * Date parsing / formatting / duration maths
    * Filename sanitising & de-duplication
    * Cross-platform "open file", "reveal in folder", "print"
    * Form validation
    * Safe image copying for asset uploads

Keeping these helpers free of UI / PDF concerns means they are easy to unit
test and reuse from future features (bulk generation, CLI, etc.).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

# --------------------------------------------------------------------------- #
# Constants used by the UI layer (kept here so they live with the logic)
# --------------------------------------------------------------------------- #
POSITION_OPTIONS: List[str] = [
    "AI Agent Developer Intern",
    "Social Media Marketing Intern",
    "AI Tools Research Intern",
    "Project Management Intern",
    "Business Development Intern",
    "Sales Intern",
]

# Label -> approximate number of days, used to auto-calculate the end date.
DURATION_OPTIONS: Dict[str, int] = {
    "15 Days": 15,
    "1 Month": 30,
    "2 Months": 60,
    "3 Months": 90,
    "6 Months": 180,
    "12 Months": 365,
}

DATE_FORMAT: str = "%d-%m-%Y"          # display + storage format (DD-MM-YYYY)


# --------------------------------------------------------------------------- #
# Date helpers
# --------------------------------------------------------------------------- #
def parse_date(value: str) -> datetime | None:
    """Parse a ``DD-MM-YYYY`` string, returning ``None`` when invalid."""
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), DATE_FORMAT)
    except ValueError:
        return None


def format_date(date: datetime) -> str:
    """Format a datetime as ``DD-MM-YYYY``."""
    return date.strftime(DATE_FORMAT)


def format_long_date(value: str) -> str:
    """Convert ``DD-MM-YYYY`` to a human friendly ``05 January 2025``."""
    parsed = parse_date(value)
    if parsed is None:
        return value
    return parsed.strftime("%d %B %Y")


def _add_months(d: datetime, months: int) -> datetime:
    """Add ``months`` calendar months to ``d`` (clamping the day if needed)."""
    import calendar
    m = d.month - 1 + months
    year = d.year + m // 12
    month = m % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return d.replace(year=year, month=month, day=day)


def calculate_end_date(start: str, duration_label: str) -> str:
    """Return the end date string given a start date and a duration label.

    Uses real calendar arithmetic so months of differing lengths are handled
    correctly, then subtracts one day so an inclusive period that *starts* on
    the 1st ends on the last day of the period. Examples:

        1 July + "1 Month"   -> 31 July
        1 July + "3 Months"  -> 30 September
        1 July + "15 Days"   -> 15 July
    """
    start_date = parse_date(start)
    if start_date is None or not duration_label:
        return ""
    label = duration_label.strip().lower()
    # Leading number, e.g. "15 Days" -> 15, "3 Months" -> 3.
    digits = "".join(ch for ch in label if ch.isdigit())
    if not digits:
        return ""
    num = int(digits)
    if "day" in label:
        end_date = start_date + timedelta(days=num - 1)
    elif "month" in label:
        end_date = _add_months(start_date, num) - timedelta(days=1)
    else:
        return ""
    return format_date(end_date)


def duration_between(start: str, end: str) -> str:
    """Derive a human friendly duration label from two date strings.

    Used by bulk import where only start/end dates are supplied. Matches a
    known label exactly when possible, otherwise returns a sensible
    ``"N Months"`` / ``"N Days"`` string.
    """
    s = parse_date(start)
    e = parse_date(end)
    if s is None or e is None or e < s:
        return ""
    days = (e - s).days + 1
    # Prefer an exact match against the standard options.
    for label, d in DURATION_OPTIONS.items():
        if days == d:
            return label
    months = round(days / 30)
    if months >= 1:
        return f"{months} Month" + ("s" if months > 1 else "")
    return f"{days} Day" + ("s" if days != 1 else "")


# --------------------------------------------------------------------------- #
# Filename helpers
# --------------------------------------------------------------------------- #
def sanitize_filename(name: str) -> str:
    """Make ``name`` safe to use as a file name on every OS."""
    name = name.strip()
    name = re.sub(r"[^\w\s-]", "", name)        # drop punctuation
    name = re.sub(r"\s+", "_", name)            # spaces -> underscores
    return name or "Offer_Letter"


def unique_path(folder: Path, base_name: str, extension: str = ".pdf") -> Path:
    """Return a non-colliding path inside ``folder``.

    ``Aditya_Choudhary_Offer_Letter.pdf`` becomes
    ``Aditya_Choudhary_Offer_Letter(1).pdf`` if the first already exists.
    """
    folder.mkdir(parents=True, exist_ok=True)
    candidate = folder / f"{base_name}{extension}"
    counter = 1
    while candidate.exists():
        candidate = folder / f"{base_name}({counter}){extension}"
        counter += 1
    return candidate


def build_filename(candidate_name: str, template_label: str) -> str:
    """Build the base file name, e.g. ``Aditya_Choudhary_Offer_Letter``."""
    suffix = template_label.replace(" ", "_")
    return f"{sanitize_filename(candidate_name)}_{suffix}"


# --------------------------------------------------------------------------- #
# OS integration (open / reveal / print)
# --------------------------------------------------------------------------- #
def open_file(path: str | Path) -> None:
    """Open a file with the OS default application."""
    path = str(path)
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)                       # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", path], check=False)
        else:
            subprocess.run(["xdg-open", path], check=False)
    except OSError:
        pass


def reveal_in_folder(path: str | Path) -> None:
    """Open the file manager with ``path`` selected / its folder opened."""
    path = Path(path)
    try:
        if sys.platform.startswith("win"):
            subprocess.run(["explorer", "/select,", str(path)], check=False)
        elif sys.platform == "darwin":
            subprocess.run(["open", "-R", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path.parent)], check=False)
    except OSError:
        pass


def print_file(path: str | Path) -> bool:
    """Send a file to the default printer. Returns ``True`` on a best effort."""
    path = str(path)
    try:
        if sys.platform.startswith("win"):
            os.startfile(path, "print")              # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["lpr", path], check=False)
        else:
            subprocess.run(["lpr", path], check=False)
        return True
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# Asset upload helper
# --------------------------------------------------------------------------- #
def copy_asset(source: str | Path, dest_dir: Path, target_name: str,
               root: Path | None = None) -> str:
    """Copy an uploaded image into ``dest_dir`` keeping its extension.

    Returns the path string relative to ``root`` (the project root) when given,
    e.g. ``assets/logo/logo.png`` - otherwise an absolute path. Empty on failure.
    """
    source = Path(source)
    if not source.exists():
        return ""
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"{target_name}{source.suffix.lower()}"
    try:
        shutil.copy(source, target)
    except (OSError, shutil.SameFileError):
        if not target.exists():
            return ""
    if root is not None:
        try:
            return str(target.relative_to(root))
        except ValueError:
            return str(target)
    return str(target)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
REQUIRED_FIELDS: Dict[str, str] = {
    "candidate_name": "Candidate Name",
    "position": "Position",
    "issue_date": "Issue Date",
    "start_date": "Internship Start Date",
    "end_date": "Internship End Date",
}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def validate_form(data: Dict[str, str]) -> Tuple[bool, List[str]]:
    """Validate a candidate form. Returns ``(is_valid, error_messages)``."""
    errors: List[str] = []

    for key, label in REQUIRED_FIELDS.items():
        if not str(data.get(key, "")).strip():
            errors.append(f"• {label} is required.")

    # Optional email, but if present it must look valid.
    email = str(data.get("email", "")).strip()
    if email and not EMAIL_RE.match(email):
        errors.append("• Email address is not valid.")

    # Logical date checks (only when both dates parse cleanly).
    start = parse_date(str(data.get("start_date", "")))
    end = parse_date(str(data.get("end_date", "")))
    if start and end and end < start:
        errors.append("• End Date cannot be before Start Date.")

    return (len(errors) == 0, errors)

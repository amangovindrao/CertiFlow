"""
app.py
------
Application entry point for the Offer Letter Generator.

Run with:  python app.py

Responsibilities are intentionally tiny here - just wire everything together:
    1. Ensure the on-disk folder structure exists.
    2. Launch the CustomTkinter UI (which owns the business/persistence layers).

Keeping the bootstrap thin makes the app easy to embed, test or wrap with a
future CLI / packaging step (PyInstaller, etc.).
"""

from __future__ import annotations

import sys

from settings import ensure_directories


def main() -> int:
    """Bootstrap and run the desktop application."""
    ensure_directories()

    try:
        from ui import OfferLetterApp
    except ImportError as exc:
        # Friendly message when a dependency is missing.
        print("Missing dependency:", exc)
        print("Install requirements with:  pip install -r requirements.txt")
        return 1

    app = OfferLetterApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

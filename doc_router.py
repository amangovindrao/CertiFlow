"""
doc_router.py
-------------
Small shared router that maps a *document type* to the correct renderer and
file-name, so both the single-letter UI and the bulk generator stay in sync.

The curated ``DOC_TYPES`` list is the single source of truth for the document
dropdowns across the app.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict

import certificate_generator as cg

# The only document types offered in the dropdowns (curated per request).
DOC_TYPES = [
    "Internship Offer",
    "Internship Certificate",
    "Completion Certificate",
]

# Document types rendered by the premium landscape certificate engine.
LANDSCAPE_CERTS = {"Completion Certificate"}


def filename_suffix(template: str) -> str:
    """Return the file-name suffix for a document type."""
    return {
        "Completion Certificate": "Certificate_of_Completion",
        "Internship Certificate": "Internship_Certificate",
    }.get(template, "Offer_Letter")


def is_landscape_cert(template: str) -> bool:
    return template in LANDSCAPE_CERTS


def render(path: Path, company: Dict, data: Dict, template: str,
           base_dir: Path, pdf_generator) -> Path:
    """Render ``data`` to ``path`` using the right engine for ``template``.

    * Completion Certificate -> premium A4 landscape certificate engine.
    * Everything else        -> the existing portrait ReportLab templates.

    For landscape certificates the caller is expected to have populated
    ``cert_no`` and ``duration`` on ``data`` beforehand.
    """
    if template in LANDSCAPE_CERTS:
        cg.generate_certificate(path, company, data, base_dir)
    else:
        pdf_generator.generate(path, company, data, template)
    return path

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
from typing import Dict, List

import certificate_generator as cg
import internship_certificate as ic

# The only document types offered in the dropdowns (curated per request).
#
#   * "Internship Certificate" -> premium A4 landscape certificate for an
#     internship that is ongoing / officially confirmed (SO-INT numbers).
#   * "Completion Certificate" -> the separate Certificate of Completion for
#     finished internships (SO-CERT numbers).
#
# The old portrait "Internship Completion Certificate" was removed: it
# duplicated the completion certificate and used completion wording.
DOC_TYPES = [
    "Internship Offer",
    "Internship Certificate",
    "Completion Certificate",
]

# Document types rendered by a premium landscape certificate engine.
LANDSCAPE_CERTS = {"Internship Certificate", "Completion Certificate"}

# Every document that counts as a certificate, i.e. gets its own verifiable
# certificate number and a row in the ``certificates`` table.  This is wider
# than LANDSCAPE_CERTS because the portrait Internship Certificate is also a
# certificate - it just renders through the portrait engine.
CERT_TYPES = {"Internship Certificate", "Completion Certificate"}


def filename_suffix(template: str) -> str:
    """Return the file-name suffix for a document type."""
    return {
        "Completion Certificate": "Certificate_of_Completion",
        "Internship Certificate": "Internship_Certificate",
    }.get(template, "Offer_Letter")


def is_landscape_cert(template: str) -> bool:
    return template in LANDSCAPE_CERTS


def is_certificate(template: str) -> bool:
    """True when the document should carry a verifiable certificate number."""
    return template in CERT_TYPES


# Certificate number families: SO-INT-26xxxx vs SO-CERT-26xxxx.
CERT_KINDS = {
    "Internship Certificate": "INT",
    "Completion Certificate": "CERT",
}


def cert_kind(template: str) -> str:
    """Certificate number family for a document type."""
    return CERT_KINDS.get(template, "CERT")


def is_ongoing_cert(template: str) -> bool:
    """True for the ongoing-internship certificate (never completion wording)."""
    return template == "Internship Certificate"


# --------------------------------------------------------------------------- #
# Custom templates from the visual designer
# --------------------------------------------------------------------------- #
# These are additive: with no saved designs, ``all_doc_types()`` returns exactly
# ``DOC_TYPES`` and every other module behaves as it always has.
def custom_types() -> List[str]:
    """Names of the templates saved in the Template Designer."""
    try:
        import template_schema as ts
        return [name for name in ts.list_names() if name not in DOC_TYPES]
    except Exception:
        return []


def all_doc_types() -> List[str]:
    """Built-in document types followed by any custom designs."""
    return list(DOC_TYPES) + custom_types()


def is_custom(template: str) -> bool:
    return template not in DOC_TYPES and template in custom_types()


def render(path: Path, company: Dict, data: Dict, template: str,
           base_dir: Path, pdf_generator) -> Path:
    """Render ``data`` to ``path`` using the right engine for ``template``.

    * Internship Certificate -> ongoing-internship landscape certificate.
    * Completion Certificate -> Certificate of Completion (landscape).
    * Everything else        -> the existing portrait ReportLab templates.

    For landscape certificates the caller is expected to have populated
    ``cert_no`` and ``duration`` on ``data`` beforehand.
    """
    if template == "Internship Certificate":
        ic.generate_internship_certificate(path, company, data, base_dir)
    elif template in LANDSCAPE_CERTS:
        cg.generate_certificate(path, company, data, base_dir)
    elif template not in DOC_TYPES:
        # A design from the Template Designer, rendered by its own engine.
        import template_renderer as tr
        import template_schema as ts

        custom = ts.get_by_name(template)
        if custom is None:
            raise ValueError(f"Unknown document type: {template}")
        tr.render_template(custom, path, company, data, base_dir)
    else:
        pdf_generator.generate(path, company, data, template)
    return path


def filename_base(template: str, candidate_name: str) -> str:
    """The file name stem for a document, whichever engine renders it."""
    import utils

    if template == "Internship Certificate":
        return ic.build_internship_certificate_filename(candidate_name)
    if template == "Completion Certificate":
        return cg.build_certificate_filename(candidate_name)
    if template not in DOC_TYPES:
        import template_renderer as tr
        return tr.build_filename(candidate_name, template)
    return utils.build_filename(candidate_name, filename_suffix(template))

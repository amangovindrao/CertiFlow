"""
template_schema.py
------------------
The declarative layout model behind the visual Template Designer.

A custom template is plain JSON: a page size plus an ordered list of elements,
each with a position, a size and a style. That is what makes a drag-and-drop
editor possible - the layout is *data* the editor can move around, rather than
positions computed inside a renderer.

This module is deliberately standalone. The built-in documents
(:mod:`internship_certificate`, :mod:`certificate_generator`,
:mod:`pdf_generator`) keep their own hand-tuned renderers and are not affected
by anything here. With no saved templates, nothing in the rest of the app
changes at all.

Coordinate space
================
``x`` and ``y`` are in **PDF points from the TOP-left** of the page, because
that is how a screen canvas thinks. :mod:`template_renderer` performs the one
flip needed for ReportLab's bottom-left origin. 1 point = 1/72 inch, so an A4
landscape page is 841.89 x 595.28.

Element types
=============
``text``   fixed wording (may contain ``{placeholders}``)
``field``  a single placeholder, e.g. ``candidate_name``
``image``  ``logo`` / ``watermark`` / ``signature`` / ``stamp`` from the company
           profile, or an explicit path
``line``   a horizontal or free line
``rect``   rectangle / border, optionally rounded and/or filled
``pill``   rounded outline with centred text (the status badge)
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from settings import TEMPLATES_DIR

# --------------------------------------------------------------------------- #
# Page sizes (points)
# --------------------------------------------------------------------------- #
PAGE_SIZES: Dict[str, tuple] = {
    "A4 landscape": (841.89, 595.28),
    "A4 portrait": (595.28, 841.89),
}
DEFAULT_PAGE = "A4 landscape"

ELEMENT_TYPES = ("text", "field", "image", "line", "rect", "pill")

# Named fonts the designer offers, resolved to real registered fonts at render
# time by :mod:`template_renderer`.
FONT_CHOICES = ("body", "body-bold", "display", "display-bold", "serif")

ALIGNMENTS = ("left", "center", "right")

IMAGE_SOURCES = ("logo", "watermark", "signature", "stamp")

# --------------------------------------------------------------------------- #
# Placeholders
# --------------------------------------------------------------------------- #
# label -> placeholder key. The label is what the designer shows in its
# "insert field" list; the key is what appears inside {curly braces}.
PLACEHOLDERS: Dict[str, str] = {
    "Intern name": "candidate_name",
    "Intern ID": "intern_id",
    "Certificate no": "cert_no",
    "Role": "position",
    "Role (no 'Intern')": "role_base",
    "Department": "department",
    "Program": "program",
    "Start date": "start_date",
    "End date": "end_date",
    "Issue date": "issue_date",
    "Duration": "duration",
    "Duration in words": "duration_words",
    "Status": "status",
    "Grade": "grade",
    "Remarks": "remarks",
    "Company name": "company_name",
    "Company tagline": "tagline",
    "HR name": "hr_name",
}

_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")


def placeholder_keys() -> List[str]:
    return sorted(set(PLACEHOLDERS.values()))


def sample_values() -> Dict[str, str]:
    """Realistic stand-in values so the designer preview looks like a document."""
    return {
        "candidate_name": "Aakif Jawaid",
        "intern_id": "SO260009",
        "cert_no": "SO-INT-260031",
        "position": "AI Agent Developer Intern",
        "role_base": "AI Agent Developer",
        "department": "Engineering",
        "program": "ScaleOn Internship Program 2026",
        "start_date": "01 July 2026",
        "end_date": "30 September 2026",
        "issue_date": "30 August 2026",
        "duration": "3-Month Internship",
        "duration_words": "three-month",
        "status": "INTERNSHIP ONGOING",
        "grade": "A",
        "remarks": "Outstanding contribution",
        "company_name": "ScaleOn",
        "tagline": "Scale Beyond Limits",
        "hr_name": "HR Team",
    }


def fill(text: str, values: Dict[str, str]) -> str:
    """Substitute ``{placeholders}``, leaving unknown ones blank."""
    def swap(match: "re.Match") -> str:
        return str(values.get(match.group(1), "") or "")

    return _PLACEHOLDER_RE.sub(swap, str(text or ""))


# --------------------------------------------------------------------------- #
# Elements
# --------------------------------------------------------------------------- #
def new_id() -> str:
    return uuid.uuid4().hex[:8]


def make_element(kind: str, **overrides) -> Dict[str, Any]:
    """A new element of ``kind`` with sensible defaults, ready to drag."""
    base: Dict[str, Any] = {
        "id": new_id(),
        "type": kind if kind in ELEMENT_TYPES else "text",
        "x": 120.0,
        "y": 120.0,
        "visible": True,
        "name": "",
    }
    if kind in ("text", "field"):
        base.update({
            "text": "New text" if kind == "text" else "{candidate_name}",
            "font": "body",
            "size": 14.0,
            "color": "#111111",
            "align": "center",
            "tracking": 0.0,
            "width": 0.0,          # 0 = single line, >0 = wrap to this width
            "leading": 0.0,        # 0 = size * 1.35
        })
    elif kind == "image":
        base.update({"source": "logo", "path": "", "w": 160.0, "h": 50.0,
                     "opacity": 1.0})
    elif kind == "line":
        base.update({"w": 140.0, "thickness": 1.0, "color": "#D4AF37"})
    elif kind == "rect":
        base.update({"w": 300.0, "h": 120.0, "radius": 0.0, "thickness": 1.0,
                     "color": "#D4AF37", "fill": ""})
    elif kind == "pill":
        base.update({"text": "{status}", "font": "display-bold", "size": 9.5,
                     "color": "#C9A227", "tracking": 1.4, "pad_x": 16.0,
                     "h": 22.0, "thickness": 0.9})
    base.update(overrides)
    return base


def element_label(element: Dict[str, Any]) -> str:
    """Short human label for the element list."""
    if element.get("name"):
        return str(element["name"])
    kind = element.get("type", "text")
    if kind in ("text", "field", "pill"):
        text = str(element.get("text", "")).strip().replace("\n", " ")
        return (text[:28] + "…") if len(text) > 28 else (text or kind)
    if kind == "image":
        return f"image: {element.get('source') or Path(element.get('path','')).name}"
    return kind


# --------------------------------------------------------------------------- #
# Template documents
# --------------------------------------------------------------------------- #
def blank_template(name: str = "Untitled",
                   page: str = DEFAULT_PAGE) -> Dict[str, Any]:
    return {
        "name": name,
        "page": page if page in PAGE_SIZES else DEFAULT_PAGE,
        "background": "#FFFFFF",
        "elements": [],
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def page_size(template: Dict[str, Any]) -> tuple:
    return PAGE_SIZES.get(template.get("page", DEFAULT_PAGE),
                          PAGE_SIZES[DEFAULT_PAGE])


def used_placeholders(template: Dict[str, Any]) -> set:
    """Every placeholder key referenced anywhere in ``template``."""
    found = set()
    for element in template.get("elements", []):
        text = str(element.get("text", "") or "")
        found.update(_PLACEHOLDER_RE.findall(text))
    return found


def starter_internship_certificate(name: str = "My Certificate"
                                   ) -> Dict[str, Any]:
    """A close copy of the built-in Internship Certificate as editable elements.

    Starting from a blank page is miserable, so the designer offers this: the
    same visual arrangement people already recognise, every part of it now
    draggable. It is a *copy* - editing it never affects the built-in document.
    """
    width, height = PAGE_SIZES[DEFAULT_PAGE]
    cx = width / 2
    margin = 44.0
    template = blank_template(name, DEFAULT_PAGE)
    template["elements"] = [
        make_element("rect", name="Outer border", x=margin, y=margin,
                     w=width - 2 * margin, h=height - 2 * margin,
                     thickness=1.6, color="#D4AF37"),
        make_element("rect", name="Inner border", x=margin + 7, y=margin + 7,
                     w=width - 2 * (margin + 7), h=height - 2 * (margin + 7),
                     thickness=0.6, color="#D4AF37"),
        make_element("image", name="Watermark", source="watermark",
                     x=cx - width * 0.17, y=height / 2 - width * 0.17 * 0.5,
                     w=width * 0.34, h=width * 0.34 * 0.5, opacity=0.05),
        make_element("image", name="Logo", source="logo", x=cx - 92, y=62,
                     w=184, h=48),
        make_element("text", name="Title", text="CERTIFICATE OF INTERNSHIP",
                     x=cx, y=150, font="display-bold", size=25,
                     color="#111111", align="center", tracking=2.6),
        make_element("field", name="Program", text="{program}", x=cx, y=190,
                     font="display", size=10.5, color="#C9A227",
                     align="center"),
        make_element("text", name="Lead-in", text="This is to certify that",
                     x=cx, y=214, font="body", size=10.5, color="#666666",
                     align="center"),
        make_element("field", name="Intern name", text="{candidate_name}",
                     x=cx, y=250, font="serif", size=34, color="#D4AF37",
                     align="center"),
        make_element("field", name="Role", text="{position}", x=cx, y=288,
                     font="display-bold", size=12, color="#111111",
                     align="center", tracking=0.8),
        make_element(
            "text", name="Statement",
            text="is currently undertaking a {duration_words} {role_base} "
                 "Internship at {company_name} under the {program}.",
            x=cx, y=320, font="body", size=11, color="#111111",
            align="center", width=width - 2 * (margin + 30), leading=16.5),
        make_element(
            "text", name="Dates",
            text="The internship commenced on {start_date} and is scheduled "
                 "to continue until {end_date}.",
            x=cx, y=352, font="body", size=11, color="#111111",
            align="center", width=width - 2 * (margin + 30), leading=16.5),
        make_element("pill", name="Status badge", text="{status}", x=cx, y=396,
                     font="display-bold", size=9.5, color="#C9A227"),
        make_element("image", name="Signature", source="signature",
                     x=margin + 94, y=440, w=48, h=40),
        make_element("line", name="Signature rule", x=margin + 118, y=492,
                     w=84, thickness=0.8, color="#666666"),
        make_element("field", name="HR name", text="{hr_name}",
                     x=margin + 118, y=498, font="body", size=10,
                     color="#111111", align="center"),
        make_element("field", name="Company", text="{company_name}",
                     x=margin + 118, y=514, font="display-bold", size=11.5,
                     color="#111111", align="center"),
        make_element("image", name="Seal", source="stamp",
                     x=width - margin - 166, y=430, w=96, h=96, opacity=0.9),
        make_element(
            "text", name="Footer",
            text="Certificate No: {cert_no}      |      Intern ID: "
                 "{intern_id}      |      Issued: {issue_date}",
            x=cx, y=height - margin - 22, font="body", size=8.5,
            color="#666666", align="center"),
    ]
    return template


def starter_completion_certificate(name: str = "My Completion Certificate"
                                   ) -> Dict[str, Any]:
    """An editable copy of the built-in Certificate of Completion."""
    width, height = PAGE_SIZES[DEFAULT_PAGE]
    cx = width / 2
    margin = 46.0
    body_w = width * 0.78
    template = blank_template(name, DEFAULT_PAGE)
    template["elements"] = [
        make_element("rect", name="Outer border", x=margin, y=margin,
                     w=width - 2 * margin, h=height - 2 * margin,
                     thickness=3.0, color="#D4AF37"),
        make_element("rect", name="Inner border", x=margin + 8, y=margin + 8,
                     w=width - 2 * (margin + 8), h=height - 2 * (margin + 8),
                     thickness=1.0, color="#D4AF37"),
        make_element("image", name="Watermark", source="watermark",
                     x=cx - width * 0.23, y=height / 2 - width * 0.115,
                     w=width * 0.46, h=width * 0.23, opacity=0.07),
        make_element("image", name="Logo", source="logo", x=cx - 110, y=64,
                     w=220, h=58),
        make_element("text", name="Title", text="Certificate of Completion",
                     x=cx, y=140, font="display-bold", size=34,
                     color="#111111", align="center"),
        make_element("line", name="Gold rule", x=cx, y=190, w=240,
                     thickness=2.5, color="#D4AF37"),
        make_element("field", name="Program", text="{program}", x=cx, y=200,
                     font="body", size=12, color="#D4AF37", align="center"),
        make_element("text", name="Lead-in",
                     text="This certificate is proudly presented to",
                     x=cx, y=236, font="body", size=13, color="#111111",
                     align="center"),
        make_element("field", name="Recipient", text="{candidate_name}",
                     x=cx, y=262, font="display-bold", size=30,
                     color="#D4AF37", align="center"),
        make_element(
            "text", name="Statement",
            text="for successfully completing the {position} internship at "
                 "{company_name} from {start_date} to {end_date} "
                 "({duration}) under the {program}.",
            x=cx, y=310, font="body", size=12.5, color="#111111",
            align="center", width=body_w, leading=19),
        make_element(
            "text", name="Appreciation",
            text="We sincerely appreciate the dedication, professionalism and "
                 "valuable contribution shown throughout the internship, and "
                 "wish continued success in all future endeavours.",
            x=cx, y=362, font="body", size=11, color="#111111",
            align="center", width=body_w, leading=17),
        make_element("image", name="Signature", source="signature",
                     x=margin + 68, y=438, w=48, h=42),
        # Sized to the block it underlines, and kept clear of the border.
        make_element("line", name="Signature rule", x=margin + 92, y=490,
                     w=88, thickness=0.8, color="#666666"),
        make_element("field", name="HR name", text="{hr_name}",
                     x=margin + 92, y=496, font="body", size=10,
                     color="#111111", align="center"),
        make_element("field", name="Company", text="{company_name}",
                     x=margin + 92, y=512, font="body-bold", size=12,
                     color="#111111", align="center"),
        make_element("image", name="Seal", source="stamp",
                     x=width - margin - 155, y=420, w=110, h=110,
                     opacity=0.9),
        make_element(
            "text", name="Footer",
            text="Certificate No: {cert_no}      |      Issued: "
                 "{issue_date}      |      Intern ID: {intern_id}",
            x=cx, y=height - margin - 22, font="body", size=9,
            color="#666666", align="center"),
    ]
    return template


def starter_offer_letter(name: str = "My Offer Letter") -> Dict[str, Any]:
    """An editable copy of the built-in portrait Internship Offer Letter."""
    width, height = PAGE_SIZES["A4 portrait"]
    cx = width / 2
    margin = 66.0
    body_w = width - 2 * margin
    template = blank_template(name, "A4 portrait")
    template["elements"] = [
        make_element("image", name="Watermark", source="watermark",
                     x=cx - width * 0.28, y=height / 2 - width * 0.28,
                     w=width * 0.56, h=width * 0.56, opacity=0.04),
        make_element("image", name="Logo", source="logo", x=margin, y=margin,
                     w=200, h=56),
        make_element("text", name="Date label", text="DATE OF ISSUE",
                     x=width - margin, y=margin + 4, font="body", size=8.5,
                     color="#666666", align="right"),
        make_element("field", name="Issue date", text="{issue_date}",
                     x=width - margin, y=margin + 16, font="body-bold",
                     size=15, color="#111111", align="right"),
        make_element("text", name="ID label", text="INTERN ID",
                     x=width - margin, y=margin + 42, font="body", size=8.5,
                     color="#666666", align="right"),
        make_element("field", name="Intern ID", text="{intern_id}",
                     x=width - margin, y=margin + 54, font="body-bold",
                     size=12, color="#111111", align="right"),
        make_element("line", name="Header rule", x=cx, y=margin + 88,
                     w=body_w, thickness=1.0, color="#E3E3E3"),
        make_element("text", name="Title", text="Internship Offer Letter",
                     x=cx, y=margin + 122, font="display-bold", size=20,
                     color="#111111", align="center"),
        make_element("line", name="Gold rule", x=cx, y=margin + 152, w=120,
                     thickness=2.0, color="#D4AF37"),
        make_element("field", name="Program", text="{program}", x=cx,
                     y=margin + 160, font="body", size=10.5,
                     color="#D4AF37", align="center"),
        make_element("text", name="Greeting", text="Dear {candidate_name},",
                     x=margin, y=margin + 196, font="body", size=11.5,
                     color="#111111", align="left"),
        make_element(
            "text", name="Paragraph 1",
            text="We are pleased to offer you the opportunity to join "
                 "{company_name} as an Intern.",
            x=margin, y=margin + 226, font="body", size=11, color="#111111",
            align="left", width=body_w, leading=17.6),
        make_element(
            "text", name="Paragraph 2",
            text="We were impressed with your qualifications and your passion "
                 "for {role_base}, and we believe that you will make a "
                 "valuable contribution to our team.",
            x=margin, y=margin + 262, font="body", size=11, color="#111111",
            align="left", width=body_w, leading=17.6),
        make_element("text", name="Position line",
                     text="Position: {position}", x=margin, y=margin + 320,
                     font="body-bold", size=11, color="#111111", align="left"),
        make_element("text", name="Duration line",
                     text="Duration: {start_date} to {end_date}",
                     x=margin, y=margin + 344, font="body-bold", size=11,
                     color="#111111", align="left"),
        make_element(
            "text", name="Closing",
            text="We look forward to welcoming you to the {company_name} team "
                 "and working together to achieve success. Thank you for "
                 "choosing {company_name} as the place to further your career "
                 "and professional growth.",
            x=margin, y=margin + 380, font="body", size=11, color="#111111",
            align="left", width=body_w, leading=17.6),
        make_element("image", name="Signature", source="signature",
                     x=margin + 8, y=height - margin - 152, w=56, h=44),
        make_element("line", name="Signature rule", x=margin + 46,
                     y=height - margin - 104, w=92, thickness=0.8,
                     color="#666666"),
        make_element("field", name="HR name", text="{hr_name}", x=margin + 46,
                     y=height - margin - 98, font="body", size=10.5,
                     color="#111111", align="center"),
        make_element("field", name="Company", text="{company_name}",
                     x=margin + 46, y=height - margin - 82, font="body-bold",
                     size=12, color="#111111", align="center"),
        make_element("image", name="Seal", source="stamp",
                     x=width - margin - 110, y=height - margin - 160,
                     w=100, h=100, opacity=0.9),
        make_element("line", name="Footer rule", x=cx, y=height - margin - 30,
                     w=body_w, thickness=1.0, color="#E3E3E3"),
        make_element("field", name="Footer", text="{company_name}", x=cx,
                     y=height - margin - 20, font="body", size=9,
                     color="#666666", align="center"),
    ]
    return template


# Label shown in the designer -> builder. Each produces an editable copy of a
# built-in document, so a new template can start from any of them.
STARTERS: Dict[str, Any] = {
    "Internship Certificate": starter_internship_certificate,
    "Completion Certificate": starter_completion_certificate,
    "Internship Offer": starter_offer_letter,
}


def starter_template(name: str = "My Certificate",
                     base: str = "Internship Certificate") -> Dict[str, Any]:
    """An editable copy of a built-in document, chosen by ``base``."""
    builder = STARTERS.get(base, starter_internship_certificate)
    return builder(name)


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", str(name or "")).strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned or "template"


def template_path(name: str) -> Path:
    return TEMPLATES_DIR / f"{_safe_filename(name)}.json"


def save(template: Dict[str, Any]) -> Path:
    """Write a template to ``templates/<name>.json`` (atomically)."""
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    path = template_path(template.get("name", "template"))
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(template, fh, indent=2, ensure_ascii=False)
    tmp.replace(path)
    return path


def load(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with Path(path).open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or "elements" not in data:
        return None
    data.setdefault("name", Path(path).stem.replace("_", " "))
    data.setdefault("page", DEFAULT_PAGE)
    data.setdefault("background", "#FFFFFF")
    # Tolerate hand-edited files: drop anything that is not a usable element.
    data["elements"] = [e for e in data.get("elements", [])
                        if isinstance(e, dict) and e.get("type") in ELEMENT_TYPES]
    for element in data["elements"]:
        element.setdefault("id", new_id())
        element.setdefault("visible", True)
    return data


def list_templates() -> List[Dict[str, Any]]:
    """Every saved template, sorted by name."""
    if not TEMPLATES_DIR.exists():
        return []
    found = []
    for path in sorted(TEMPLATES_DIR.glob("*.json")):
        template = load(path)
        if template:
            template["_path"] = str(path)
            found.append(template)
    found.sort(key=lambda t: str(t.get("name", "")).casefold())
    return found


def list_names() -> List[str]:
    return [str(t.get("name", "")) for t in list_templates()
            if str(t.get("name", "")).strip()]


def get_by_name(name: str) -> Optional[Dict[str, Any]]:
    key = str(name or "").strip().casefold()
    for template in list_templates():
        if str(template.get("name", "")).strip().casefold() == key:
            return template
    return None


def delete(name: str) -> bool:
    path = template_path(name)
    try:
        path.unlink()
        return True
    except OSError:
        return False

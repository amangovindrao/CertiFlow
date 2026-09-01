"""
template_renderer.py
--------------------
Draws a declarative template (see :mod:`template_schema`) to a PDF.

Kept separate from the hand-tuned built-in documents on purpose: this renderer
only ever walks an element list, so the visual designer can move things around
freely without any risk to the existing certificates and letters.

The one coordinate conversion lives here - templates store ``y`` from the top
of the page (natural for a canvas), ReportLab draws from the bottom.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Paragraph

import pdf_generator as pg
import template_schema as ts
import utils

_ALIGN = {"left": TA_LEFT, "center": TA_CENTER, "right": TA_RIGHT}


def _color(value: str, fallback: str = "#111111"):
    try:
        return HexColor(str(value or fallback))
    except Exception:
        return HexColor(fallback)


def _font_name(named: str) -> str:
    """Map a designer font choice to a registered ReportLab font."""
    return {
        "body": pg.FONT_REGULAR,
        "body-bold": pg.FONT_BOLD,
        "display": pg.FONT_DISPLAY_REG,
        "display-bold": pg.FONT_DISPLAY,
        "serif": pg.FONT_SERIF_NAME,
    }.get(str(named or "body"), pg.FONT_REGULAR)


def build_values(data: Dict[str, Any], company: Dict[str, Any]
                 ) -> Dict[str, str]:
    """Resolve every placeholder from the document data + company profile."""
    start = data.get("start_date", "") or ""
    end = data.get("end_date", "") or ""
    issue = data.get("issue_date", "") or ""
    position = data.get("position", "") or ""

    # Imported lazily: internship_certificate imports pdf_generator too, and
    # this keeps the dependency one-directional at module load.
    import internship_certificate as ic

    ongoing = utils.is_internship_ongoing(end, issue)
    return {
        "candidate_name": data.get("candidate_name", "") or "",
        "intern_id": data.get("intern_id", "") or "",
        "cert_no": data.get("cert_no", "") or "",
        "position": position,
        "role_base": ic.role_base(position),
        "department": data.get("department", "") or "",
        "program": (data.get("program", "")
                    or ic.program_name(data, company)),
        "start_date": utils.format_long_date(start) if start else "",
        "end_date": utils.format_long_date(end) if end else "",
        "issue_date": utils.format_long_date(issue) if issue else "",
        "duration": (data.get("duration", "")
                     or utils.duration_phrase(start, end)),
        "duration_words": utils.duration_words(start, end),
        "status": data.get("status") or ("INTERNSHIP ONGOING" if ongoing
                                        else "INTERNSHIP CONFIRMED"),
        "grade": data.get("grade", "") or "",
        "remarks": data.get("remarks", "") or "",
        "company_name": company.get("company_name", "") or "",
        "tagline": company.get("tagline", "") or "",
        "hr_name": company.get("hr_name", "") or "",
    }


def _image_reader(element: Dict[str, Any], company: Dict[str, Any],
                  base_dir: Path) -> Optional[ImageReader]:
    source = str(element.get("source") or "").strip()
    opacity = float(element.get("opacity", 1.0) or 1.0)
    if source:
        raw = company.get(source, "")
    else:
        raw = element.get("path", "")
    resolved = pg._resolve(base_dir, raw)
    if not resolved:
        return None
    # A faint image is treated as a watermark so the paper colour is keyed out
    # the same way the built-in certificates do it.
    if opacity < 0.5:
        return pg._watermark_image(resolved, opacity=opacity)
    if source == "signature":
        import internship_certificate as ic
        prepared = ic.prepared_signature(resolved)
        if prepared is not None:
            return prepared
    try:
        return ImageReader(str(resolved))
    except Exception:
        return None


def _draw_tracked(c, text: str, x: float, y: float, font: str, size: float,
                  tracking: float, align: str) -> None:
    """Draw one line, honouring letter spacing and alignment.

    Character spacing is part of the PDF text state and survives ET, so the run
    is wrapped in a graphics-state save/restore.
    """
    width = c.stringWidth(text, font, size) + tracking * max(len(text) - 1, 0)
    if align == "center":
        start = x - width / 2
    elif align == "right":
        start = x - width
    else:
        start = x
    c.saveState()
    try:
        obj = c.beginText(start, y)
        obj.setFont(font, size)
        if tracking:
            obj.setCharSpace(tracking)
        obj.textOut(text)
        c.drawText(obj)
    finally:
        c.restoreState()


def _draw_text(c, element: Dict[str, Any], page_h: float,
               values: Dict[str, str]) -> None:
    text = ts.fill(element.get("text", ""), values)
    if not text.strip():
        return
    font = _font_name(element.get("font"))
    size = float(element.get("size", 12) or 12)
    align = str(element.get("align", "center"))
    tracking = float(element.get("tracking", 0) or 0)
    x = float(element.get("x", 0) or 0)
    top = float(element.get("y", 0) or 0)
    c.setFillColor(_color(element.get("color")))

    wrap_width = float(element.get("width", 0) or 0)
    if wrap_width > 0 or "\n" in text:
        leading = float(element.get("leading", 0) or 0) or size * 1.35
        style = ParagraphStyle("tpl", fontName=font, fontSize=size,
                               leading=leading,
                               textColor=_color(element.get("color")),
                               alignment=_ALIGN.get(align, TA_CENTER))
        width = wrap_width if wrap_width > 0 else 400.0
        para = Paragraph(text.replace("\n", "<br/>"), style)
        _w, h = para.wrap(width, page_h)
        if align == "center":
            left = x - width / 2
        elif align == "right":
            left = x - width
        else:
            left = x
        # ``y`` is the top of the block, measured from the top of the page.
        para.drawOn(c, left, page_h - top - h)
        return

    # Single line: y is the text baseline, offset so the box top matches.
    _draw_tracked(c, text, x, page_h - top - size, font, size, tracking, align)


def _draw_pill(c, element: Dict[str, Any], page_h: float,
               values: Dict[str, str]) -> None:
    text = ts.fill(element.get("text", ""), values)
    if not text.strip():
        return
    font = _font_name(element.get("font"))
    size = float(element.get("size", 9.5) or 9.5)
    tracking = float(element.get("tracking", 1.4) or 0)
    pad_x = float(element.get("pad_x", 16) or 0)
    height = float(element.get("h", 22) or 22)
    colour = _color(element.get("color"), "#C9A227")

    text_w = c.stringWidth(text, font, size) + tracking * max(len(text) - 1, 0)
    width = text_w + pad_x * 2
    cx = float(element.get("x", 0) or 0)
    top = float(element.get("y", 0) or 0)
    left = cx - width / 2
    bottom = page_h - top - height

    c.setStrokeColor(colour)
    c.setLineWidth(float(element.get("thickness", 0.9) or 0.9))
    c.roundRect(left, bottom, width, height, height / 2, stroke=1, fill=0)
    c.setFillColor(colour)
    _draw_tracked(c, text, cx, bottom + height / 2 - size / 2 + 1.2, font,
                  size, tracking, "center")


def render_template(template: Dict[str, Any], output_path: Path,
                    company: Dict[str, Any], data: Dict[str, Any],
                    base_dir: Path) -> Path:
    """Render ``template`` with ``data`` to ``output_path``."""
    # Make sure every font the designer offers is registered.
    pg.register_fonts(base_dir / "assets" / "fonts")
    pg._register_arial()
    pg.register_display_fonts(base_dir / "assets" / "fonts")

    width, height = ts.page_size(template)
    values = build_values(data, company)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    c = rl_canvas.Canvas(str(output_path), pagesize=(width, height))
    c.setTitle(f"{values.get('candidate_name') or 'Document'} - "
               f"{template.get('name', 'Template')}")
    c.setAuthor(company.get("company_name", ""))

    background = template.get("background") or ""
    if background and background.upper() not in ("#FFFFFF", "FFFFFF"):
        c.setFillColor(_color(background, "#FFFFFF"))
        c.rect(0, 0, width, height, stroke=0, fill=1)

    for element in template.get("elements", []):
        if not element.get("visible", True):
            continue
        kind = element.get("type")
        try:
            if kind in ("text", "field"):
                _draw_text(c, element, height, values)
            elif kind == "pill":
                _draw_pill(c, element, height, values)
            elif kind == "image":
                reader = _image_reader(element, company, base_dir)
                if reader is not None:
                    c.drawImage(
                        reader, float(element.get("x", 0) or 0),
                        height - float(element.get("y", 0) or 0)
                        - float(element.get("h", 0) or 0),
                        float(element.get("w", 0) or 0),
                        float(element.get("h", 0) or 0),
                        preserveAspectRatio=True, mask="auto")
            elif kind == "line":
                c.setStrokeColor(_color(element.get("color"), "#D4AF37"))
                c.setLineWidth(float(element.get("thickness", 1) or 1))
                x = float(element.get("x", 0) or 0)
                y = height - float(element.get("y", 0) or 0)
                half = float(element.get("w", 0) or 0) / 2
                c.line(x - half, y, x + half, y)
            elif kind == "rect":
                c.setStrokeColor(_color(element.get("color"), "#D4AF37"))
                c.setLineWidth(float(element.get("thickness", 1) or 1))
                fill = str(element.get("fill") or "")
                if fill:
                    c.setFillColor(_color(fill, "#FFFFFF"))
                w = float(element.get("w", 0) or 0)
                h = float(element.get("h", 0) or 0)
                x = float(element.get("x", 0) or 0)
                y = height - float(element.get("y", 0) or 0) - h
                radius = float(element.get("radius", 0) or 0)
                if radius > 0:
                    c.roundRect(x, y, w, h, radius, stroke=1,
                                fill=1 if fill else 0)
                else:
                    c.rect(x, y, w, h, stroke=1, fill=1 if fill else 0)
        except Exception:
            # One bad element must never lose the whole document.
            continue

    # Always emit the page explicitly: a template with no elements would
    # otherwise produce a PDF with zero pages, which readers reject.
    c.showPage()
    c.save()
    return output_path


def build_filename(candidate_name: str, template_name: str) -> str:
    """``Aakif_Jawaid_My_Certificate``."""
    safe = utils.sanitize_filename(template_name) or "Document"
    return f"{utils.sanitize_filename(candidate_name)}_{safe}"

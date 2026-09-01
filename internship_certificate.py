"""
internship_certificate.py
-------------------------
Premium **Internship Certificate** (A4 landscape) for interns whose internship
is *currently ongoing* or officially confirmed.

This is deliberately NOT a completion certificate: the wording never states or
implies that the internship has finished. The existing Certificate of
Completion (:mod:`certificate_generator`) remains a separate document type.

All ScaleOn branding is inherited from the existing certificate design, which
is the single source of truth:

    * logo, watermark, circular seal/stamp and HR signature block come from
      :class:`certificate_generator.CertificateRenderer`,
    * the gold/black palette comes from :mod:`pdf_generator`,
    * company name, tagline and HR name come from the saved company profile.

Only intern-specific values are supplied by the caller. No company details are
ever requested or invented here.

Typography (at most three families, all resolved locally - never downloaded):

    * Headings   : Montserrat / Poppins -> Segoe UI Semibold -> brand bold
    * Intern name: Playfair Display / Cormorant -> Georgia / Cambria -> Times
    * Body       : the bundled brand sans (IBM Plex Sans)
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Paragraph

import certificate_generator as cg
import pdf_generator as pg
import utils

try:
    from PIL import Image
except ImportError:                                  # pragma: no cover
    Image = None  # type: ignore

PAGE_W, PAGE_H = landscape(A4)
MARGIN = 44

# Wording that must never appear on an ongoing internship certificate. Used by
# :func:`assert_no_completion_wording` as a guard rail.
BANNED_WORDING: Tuple[str, ...] = (
    "successfully completed", "has completed", "successfully completing",
    "completed the internship", "completion of the internship",
    "has successfully", "certificate of completion",
)

DEFAULT_PROGRAM_YEAR = 2026


def build_internship_certificate_filename(candidate_name: str) -> str:
    return f"{utils.sanitize_filename(candidate_name)}_Internship_Certificate"


def role_base(role: str) -> str:
    """Strip a trailing "Intern"/"Internship" so wording never doubles up.

    ``"AI Agent Developer Intern"`` -> ``"AI Agent Developer"`` so the sentence
    reads "a three-month AI Agent Developer Internship", not "... Intern
    Internship".
    """
    text = (role or "").strip()
    for suffix in (" internship", " intern"):
        if text.lower().endswith(suffix):
            return text[: -len(suffix)].strip()
    return text


def role_title(role: str) -> str:
    """Role as shown under the intern name, always ending in "Intern"."""
    base = role_base(role)
    return f"{base} Intern" if base else "Intern"


def program_name(data: Dict, company: Dict) -> str:
    """Program name, defaulting to "<Company> Internship Program <year>"."""
    explicit = str(data.get("program", "") or "").strip()
    if explicit:
        return explicit
    company_name = company.get("company_name", "ScaleOn") or "ScaleOn"
    parsed = utils.parse_date(data.get("issue_date", ""))
    year = parsed.year if parsed else DEFAULT_PROGRAM_YEAR
    return f"{company_name} Internship Program {year}"


_SIGNATURE_CACHE: Dict[tuple, object] = {}


def prepared_signature(path: Path, threshold: int = 235):
    """Signature image with its paper margins trimmed and made transparent.

    HR signatures are usually scans on solid white. Drawn as-is they paint a
    white rectangle over the certificate and sit off-centre inside their own
    margins. Cropping to the ink and keying out the paper means the signature
    is centred on the block, renders larger for the same height (so it stays
    sharp), and blends with the background.

    Returns an ``ImageReader``, or ``None`` to fall back to the raw file.
    """
    if Image is None:
        return None
    try:
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size, threshold)
    except OSError:
        key = None
    if key is not None and key in _SIGNATURE_CACHE:
        return _SIGNATURE_CACHE[key]

    try:
        from io import BytesIO

        from reportlab.lib.utils import ImageReader as _Reader

        img = Image.open(path).convert("RGBA")
        gray = img.convert("L")

        # Trim to the bounding box of anything darker than the paper.
        ink = gray.point(lambda v: 255 if v < threshold else 0)
        bbox = ink.getbbox()
        if bbox:
            pad = 2
            bbox = (max(bbox[0] - pad, 0), max(bbox[1] - pad, 0),
                    min(bbox[2] + pad, img.width),
                    min(bbox[3] + pad, img.height))
            img = img.crop(bbox)
            gray = gray.crop(bbox)

        # Key out the paper, keeping anti-aliased stroke edges.
        ramp = 60

        def alpha_for(value: int) -> int:
            if value >= threshold:
                return 0
            if value <= threshold - ramp:
                return 255
            return int(255 * (threshold - value) / ramp)

        img.putalpha(gray.point(alpha_for))

        buffer = BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)
        reader = _Reader(buffer)
        if key is not None:
            _SIGNATURE_CACHE[key] = reader
        return reader
    except Exception:
        return None


def assert_no_completion_wording(text: str) -> None:
    """Raise when completion wording slips into an ongoing certificate."""
    lowered = " ".join(text.lower().split())
    for phrase in BANNED_WORDING:
        if phrase in lowered:
            raise ValueError(
                f"Internship certificate must not imply completion: "
                f"found {phrase!r}")


class InternshipCertificateRenderer(cg.CertificateRenderer):
    """Renders the ongoing-internship certificate on A4 landscape.

    Inherits the ScaleOn logo, seal and signature drawing from the existing
    certificate renderer; only the border, watermark weight, layout, wording
    and typography are refined here.
    """

    def __init__(self, base_dir: Path):
        super().__init__(base_dir)
        # Display + serif faces for the premium hierarchy.
        pg.register_display_fonts(base_dir / "assets" / "fonts")

    # ------------------------------------------------------------------ #
    # Refined branding (same assets, lighter touch)
    # ------------------------------------------------------------------ #
    def _border(self, c) -> None:
        """Elegant thin double gold border with perfectly equal margins."""
        c.setStrokeColor(pg.GOLD)
        c.setLineWidth(1.6)
        c.rect(MARGIN, MARGIN, PAGE_W - 2 * MARGIN, PAGE_H - 2 * MARGIN)
        inset = MARGIN + 7
        c.setLineWidth(0.6)
        c.rect(inset, inset, PAGE_W - 2 * inset, PAGE_H - 2 * inset)

    def _watermark(self, c) -> None:
        """Subtle centered security watermark - fainter and smaller than the
        completion certificate so it never competes with the text."""
        wm = (pg._resolve(self.base_dir, self.company.get("watermark", ""))
              or pg._resolve(self.base_dir, self.company.get("logo", "")))
        reader = pg._watermark_image(wm, opacity=0.045) if wm else None
        if reader is None:
            return
        try:
            iw, ih = reader.getSize()
            target_w = PAGE_W * 0.34            # smaller, undistorted
            scale = target_w / iw
            target_h = ih * scale
            c.drawImage(reader, (PAGE_W - target_w) / 2,
                        (PAGE_H - target_h) / 2, target_w, target_h,
                        mask="auto", preserveAspectRatio=True)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Text helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _tracked(c, text: str, cx: float, y: float, font: str, size: float,
                 tracking: float) -> None:
        """Draw letter-spaced text centered on ``cx`` (for headings).

        Uses the PDF character-spacing operator rather than placing each glyph
        separately, so the text stays a single selectable/searchable run in the
        finished document.
        """
        total = (c.stringWidth(text, font, size)
                 + tracking * max(len(text) - 1, 0))
        # The PDF character-spacing operator is part of the *text state*, which
        # survives ET and would letter-space everything drawn afterwards.
        # q/Q around the run restores it.
        c.saveState()
        try:
            text_obj = c.beginText(cx - total / 2, y)
            text_obj.setFont(font, size)
            text_obj.setCharSpace(tracking)
            text_obj.textOut(text)
            c.drawText(text_obj)
        finally:
            c.restoreState()

    @staticmethod
    def _tracked_width(c, text: str, font: str, size: float,
                       tracking: float) -> float:
        return (c.stringWidth(text, font, size)
                + tracking * max(len(text) - 1, 0))

    def _fit_font_size(self, c, text: str, font: str, start: float,
                       max_width: float, minimum: float = 16.0,
                       tracking: float = 0.0) -> float:
        """Largest size <= ``start`` at which ``text`` fits ``max_width``."""
        size = start
        while size > minimum and self._tracked_width(
                c, text, font, size, tracking) > max_width:
            size -= 0.5
        return size

    @staticmethod
    def _measure(html: str, width: float, font: str, size: float,
                 leading: float) -> Tuple[Paragraph, float]:
        style = ParagraphStyle("cert", fontName=font, fontSize=size,
                               leading=leading, textColor=pg.BLACK,
                               alignment=TA_CENTER)
        para = Paragraph(html, style)
        _w, h = para.wrap(width, PAGE_H)
        return para, h

    # ------------------------------------------------------------------ #
    # Body copy
    # ------------------------------------------------------------------ #
    def _paragraphs(self, data: Dict, company: Dict) -> List[str]:
        """The certificate statement, as centered HTML paragraphs."""
        company_name = company.get("company_name", "ScaleOn") or "ScaleOn"
        program = program_name(data, company)
        role = role_base(data.get("position", ""))
        start = utils.format_long_date(data.get("start_date", ""))
        end = utils.format_long_date(data.get("end_date", ""))
        words = utils.duration_words(data.get("start_date", ""),
                                     data.get("end_date", ""))
        ongoing = utils.is_internship_ongoing(data.get("end_date", ""),
                                              data.get("issue_date", ""))

        # Present tense in both branches - never completion wording.
        verb = "is currently undertaking" if ongoing else "is undertaking"
        duration_bit = f"a {words} " if words else "a "
        statement = (f"{verb} {duration_bit}<b>{role}</b> Internship at "
                     f"<b>{company_name}</b> under the {program}.")

        dates = (f"The internship commenced on <b>{start}</b> and is "
                 f"scheduled to continue until <b>{end}</b>.")

        engagement = ("During this internship, the intern is engaged in "
                      "practical learning, professional development, and "
                      "project-based work relevant to the role.")

        appreciation = ("We appreciate their dedication, enthusiasm, and "
                        "commitment and wish them continued success in their "
                        "professional journey.")

        paragraphs = [statement, dates, engagement, appreciation]
        for para in paragraphs:
            assert_no_completion_wording(para)
        return paragraphs

    def _signature(self, c, x_center: float, base_y: float) -> None:
        """Signature block whose rule is sized to what it actually underlines.

        The inherited version draws a fixed 160pt rule, which is over three
        times wider than the signature and the names beneath it - it reads as a
        stray line rather than part of the block. Here the rule is measured
        from the real content width so everything lines up.
        """
        hr_name = (self.company.get("hr_name", "") or "HR Team").strip()
        company_name = (self.company.get("company_name", "")
                        or "ScaleOn").strip()
        name_font, name_size = pg.FONT_REGULAR, 10
        co_font, co_size = pg.FONT_DISPLAY, 11.5

        # Signature image, centred on the same axis as everything else.
        sig_w = 0.0
        sig = pg._resolve(self.base_dir, self.company.get("signature", ""))
        if sig:
            try:
                reader = prepared_signature(sig) or ImageReader(str(sig))
                iw, ih = reader.getSize()
                sig_h = 40
                sig_w = min(iw * (sig_h / ih), 170)
                c.drawImage(reader, x_center - sig_w / 2, base_y + 14,
                            sig_w, sig_h, preserveAspectRatio=True,
                            mask="auto")
            except Exception:
                sig_w = 0.0

        # Rule width follows the widest element it sits under.
        content_w = max(sig_w,
                        c.stringWidth(hr_name, name_font, name_size),
                        c.stringWidth(company_name, co_font, co_size))
        rule_w = max(min(content_w + 30, 200), 84)

        c.setStrokeColor(pg.DARK_GRAY)
        c.setLineWidth(0.8)
        c.line(x_center - rule_w / 2, base_y + 6,
               x_center + rule_w / 2, base_y + 6)

        c.setFillColor(pg.BLACK)
        c.setFont(name_font, name_size)
        c.drawCentredString(x_center, base_y - 8, hr_name)
        c.setFont(co_font, co_size)
        c.drawCentredString(x_center, base_y - 24, company_name)

    def _status_text(self, data: Dict) -> str:
        ongoing = utils.is_internship_ongoing(data.get("end_date", ""),
                                              data.get("issue_date", ""))
        return "INTERNSHIP ONGOING" if ongoing else "INTERNSHIP CONFIRMED"

    def _status_badge(self, c, cx: float, cy: float, text: str) -> float:
        """Gold outlined pill. Returns the y of its bottom edge."""
        font, size = pg.FONT_DISPLAY, 9.5
        tracking = 1.4
        text_w = self._tracked_width(c, text, font, size, tracking)
        pad_x, height = 16, 22
        width = text_w + pad_x * 2
        x, y = cx - width / 2, cy - height
        c.setStrokeColor(pg.GOLD)
        c.setLineWidth(0.9)
        c.roundRect(x, y, width, height, height / 2, stroke=1, fill=0)
        c.setFillColor(pg.GOLD_SOFT)
        self._tracked(c, text, cx, y + height / 2 - size / 2 + 1.2, font, size,
                      tracking)
        return y

    # ------------------------------------------------------------------ #
    # Main render
    # ------------------------------------------------------------------ #
    def render(self, output_path: Path, company: Dict, data: Dict) -> Path:
        self.company = company
        output_path.parent.mkdir(parents=True, exist_ok=True)

        name = (data.get("candidate_name", "") or "Candidate").strip()
        c = rl_canvas.Canvas(str(output_path), pagesize=landscape(A4))
        c.setTitle(f"{name} - Internship Certificate")
        c.setAuthor(company.get("company_name", "ScaleOn"))
        c.setSubject("Internship Certificate")

        self._watermark(c)
        self._border(c)

        content_w = PAGE_W - 2 * (MARGIN + 30)
        cx = PAGE_W / 2

        # -- top: logo -------------------------------------------------- #
        y = self._logo(c, PAGE_H - MARGIN - 18)

        # -- title ------------------------------------------------------ #
        title = "CERTIFICATE OF INTERNSHIP"
        t_size = self._fit_font_size(c, title, pg.FONT_DISPLAY, 25.0,
                                     content_w, minimum=15.0, tracking=2.6)
        y -= 30
        c.setFillColor(pg.BLACK)
        self._tracked(c, title, cx, y, pg.FONT_DISPLAY, t_size, 2.6)

        # -- program name ----------------------------------------------- #
        # No divider between the title and the program line: the title carries
        # the hierarchy on its own.
        program = program_name(data, company)
        y -= 24
        c.setFillColor(pg.GOLD_SOFT)
        c.setFont(pg.FONT_DISPLAY_REG, 10.5)
        c.drawCentredString(cx, y, program)

        # -- "This is to certify that" ---------------------------------- #
        y -= 24
        c.setFillColor(pg.DARK_GRAY)
        c.setFont(pg.FONT_REGULAR, 10.5)
        c.drawCentredString(cx, y, "This is to certify that")

        # -- intern name (strongest element) ---------------------------- #
        n_size = self._fit_font_size(c, name, pg.FONT_SERIF_NAME, 34.0,
                                     content_w, minimum=17.0)
        y -= (n_size + 8)
        c.setFillColor(pg.GOLD)
        c.setFont(pg.FONT_SERIF_NAME, n_size)
        c.drawCentredString(cx, y, name)

        # -- role (+ optional department) ------------------------------- #
        role_line = role_title(data.get("position", ""))
        department = str(data.get("department", "") or "").strip()
        if department:
            role_line = f"{role_line}  \u00b7  {department}"
        r_size = self._fit_font_size(c, role_line, pg.FONT_DISPLAY, 12.0,
                                     content_w, minimum=8.5, tracking=0.8)
        y -= 20
        c.setFillColor(pg.BLACK)
        self._tracked(c, role_line, cx, y, pg.FONT_DISPLAY, r_size, 0.8)

        # -- body paragraphs, auto-fitted above the signature band ------ #
        paragraphs = self._paragraphs(data, company)
        band_top = MARGIN + 118          # hard floor: signature / seal / footer
        gap = 9
        body_top = y - 20

        chosen = None
        for size, leading in ((11.0, 16.5), (10.5, 15.8), (10.0, 15.0),
                              (9.5, 14.2), (9.0, 13.4)):
            flowed = [self._measure(html, content_w, pg.FONT_REGULAR, size,
                                    leading) for html in paragraphs]
            total = sum(h for _p, h in flowed) + gap * (len(flowed) - 1)
            # +34 leaves room for the status badge beneath the text.
            if body_top - total - 34 >= band_top:
                chosen = flowed
                break
        if chosen is None:
            chosen = flowed                      # smallest tried; still drawn

        cursor = body_top
        for para, h in chosen:
            para.drawOn(c, cx - content_w / 2, cursor - h)
            cursor -= h + gap

        # -- status badge ----------------------------------------------- #
        badge_cy = max(cursor - 4, band_top + 24)
        self._status_badge(c, cx, badge_cy, self._status_text(data))

        # -- signature (left) + seal (right), balanced ------------------ #
        base_y = MARGIN + 62
        self._signature(c, MARGIN + 118, base_y)
        self._seal(c, PAGE_W - MARGIN - 118, base_y + 26, radius=44)

        # -- footer: Certificate No | Intern ID | Issued ----------------- #
        bits = []
        if data.get("cert_no"):
            bits.append(f"Certificate No: {data['cert_no']}")
        if data.get("intern_id"):
            bits.append(f"Intern ID: {data['intern_id']}")
        issue = data.get("issue_date", "")
        if issue:
            bits.append(f"Issued: {utils.format_long_date(issue)}")
        if bits:
            c.setFillColor(pg.DARK_GRAY)
            c.setFont(pg.FONT_REGULAR, 8.5)
            c.drawCentredString(cx, MARGIN + 16,
                                "      |      ".join(bits))

        c.save()
        return output_path


def generate_internship_certificate(output_path: Path, company: Dict,
                                    data: Dict, base_dir: Path) -> Path:
    """Convenience wrapper used by the UI, router and bulk generator."""
    return InternshipCertificateRenderer(base_dir).render(
        output_path, company, data)

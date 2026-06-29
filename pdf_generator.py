"""
pdf_generator.py
----------------
ScaleOn-branded PDF rendering layer built on ReportLab.

Brand system
============
Every document shares one premium identity so the output looks like it came
from a professional HRMS:

    * Palette  : Gold (#D4AF37), Black (#111111), White, Light/Dark Grey
    * Header   : ScaleOn logo (left) + issue date (right), company name and
                 tagline below, thin grey divider.
    * Title    : centered, bold, with a short GOLD accent rule beneath.
    * Body     : Inter/Poppins (falls back to Helvetica), 1.6 line height.
    * Watermark: the ScaleOn logo, recoloured and faded to ~7% opacity.
    * Signature: the uploaded HR signature image + name / designation / company.
    * Seal     : an auto-generated circular gold & black vector stamp drawn
                 with ReportLab primitives (no image asset required).
    * Footer   : thin divider + contact line in dark grey.

A small template framework (``BaseTemplate`` + subclasses) means every document
type automatically inherits all of the above; subclasses only declare a title
and the body paragraphs.  Everything is dynamic - values come from Company
Settings and the candidate form, never hardcoded.
"""

from __future__ import annotations

import math
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Type

from reportlab.lib.colors import Color, HexColor
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Paragraph

from utils import format_long_date

try:
    from PIL import Image
except ImportError:  # Pillow is a dependency but degrade gracefully.
    Image = None  # type: ignore


# --------------------------------------------------------------------------- #
# ScaleOn brand palette (logo colours only - no blue/red/green)
# --------------------------------------------------------------------------- #
GOLD = HexColor("#D4AF37")
GOLD_SOFT = HexColor("#C9A227")
INK_BLUE = HexColor("#1F4E9C")         # stamp ink colour (per request)
BLACK = HexColor("#111111")
WHITE = HexColor("#FFFFFF")
LIGHT_GRAY = HexColor("#F5F5F5")
DARK_GRAY = HexColor("#666666")
HAIRLINE = HexColor("#E3E3E3")

PAGE_W, PAGE_H = A4
MARGIN = 66                            # generous, premium margins (#12)
CONTENT_W = PAGE_W - 2 * MARGIN
LINE_HEIGHT = 1.6                      # requested 1.6 leading ratio


# --------------------------------------------------------------------------- #
# Font registration (Inter / Poppins / Source Sans Pro -> Helvetica fallback)
# --------------------------------------------------------------------------- #
FONT_REGULAR = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
FONT_ITALIC = "Helvetica-Oblique"
FONT_LIGHT = "Helvetica"

# Classic serif faces (base-14, always available) for the signature names.
SERIF = "Times-Roman"
SERIF_BOLD = "Times-Bold"

# Arial (registered from the OS if present; falls back to Helvetica, its
# metric-compatible twin). Used for the "ScaleOn" line in the signature.
ARIAL = "Helvetica"


def _register_arial() -> None:
    """Register Arial from the Windows fonts folder when available."""
    global ARIAL
    for path in (r"C:\Windows\Fonts\arial.ttf",
                 r"C:\Windows\Fonts\Arial.ttf"):
        p = Path(path)
        if p.exists():
            try:
                pdfmetrics.registerFont(TTFont("Arial", str(p)))
                ARIAL = "Arial"
            except Exception:
                pass
            break


def register_fonts(fonts_dir: Path) -> None:
    """Register bundled TTF fonts when available so they embed in the PDF."""
    global FONT_REGULAR, FONT_BOLD, FONT_ITALIC, FONT_LIGHT
    if not fonts_dir.exists():
        return

    # (registered-name, candidate filenames) ordered by preference.
    # Requirement: ONE consistent font for the whole PDF (no mixing) using
    # Inter / Source Sans Pro / IBM Plex Sans. IBM Plex Sans ships with the app.
    faces = {
        "Brand": ["IBMPlexSans-Regular.ttf", "Inter-Regular.ttf",
                  "SourceSansPro-Regular.ttf", "SourceSans3-Regular.ttf"],
        "Brand-Bold": ["IBMPlexSans-Bold.ttf", "Inter-Bold.ttf",
                       "SourceSansPro-Bold.ttf", "SourceSans3-Bold.ttf"],
        "Brand-Italic": ["IBMPlexSans-Italic.ttf", "Inter-Italic.ttf",
                         "SourceSansPro-It.ttf", "SourceSans3-Italic.ttf"],
        "Brand-Light": ["IBMPlexSans-Light.ttf", "Inter-Light.ttf",
                        "SourceSansPro-Light.ttf", "SourceSans3-Light.ttf"],
    }
    registered: Dict[str, bool] = {}
    for name, files in faces.items():
        for fname in files:
            fpath = fonts_dir / fname
            if fpath.exists():
                try:
                    pdfmetrics.registerFont(TTFont(name, str(fpath)))
                    registered[name] = True
                except Exception:
                    pass
                break

    if registered.get("Brand"):
        FONT_REGULAR = "Brand"
    if registered.get("Brand-Bold"):
        FONT_BOLD = "Brand-Bold"
    if registered.get("Brand-Italic"):
        FONT_ITALIC = "Brand-Italic"
    FONT_LIGHT = "Brand-Light" if registered.get("Brand-Light") else FONT_REGULAR

    # CRITICAL: register the font *family* so that <b>/<i> markup inside
    # Paragraph flowables maps to the correct bold / italic faces. Without
    # this, <b> tags silently render as regular weight.
    if FONT_REGULAR == "Brand":
        try:
            pdfmetrics.registerFontFamily(
                "Brand",
                normal="Brand",
                bold=FONT_BOLD,
                italic=FONT_ITALIC if registered.get("Brand-Italic") else "Brand",
                boldItalic=FONT_BOLD,
            )
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Image helpers
# --------------------------------------------------------------------------- #
def _resolve(base_dir: Path, rel: str) -> Path | None:
    """Resolve a possibly-relative asset path against the project root."""
    if not rel:
        return None
    p = Path(str(rel).replace("\\", "/"))
    if not p.is_absolute():
        p = base_dir / p
    return p if p.exists() else None


def _watermark_image(path: Path, opacity: float = 0.08,
                     tint=(95, 95, 95)) -> ImageReader | None:
    """Turn the logo into a faint, transparent-background watermark.

    The logo sits on a solid black background, so near-black pixels are made
    fully transparent and the remaining logo marks are recoloured to a soft
    neutral grey at low opacity - readable behind body text without clutter.
    """
    if Image is None:
        return None
    try:
        img = Image.open(path).convert("RGBA")
        alpha_val = int(255 * opacity)
        out_pixels = []
        for r, g, b, a in img.getdata():
            if a == 0 or (r < 45 and g < 45 and b < 45):
                out_pixels.append((0, 0, 0, 0))           # background -> clear
            else:
                out_pixels.append((tint[0], tint[1], tint[2], alpha_val))
        img.putdata(out_pixels)
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return ImageReader(buf)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Base template (shared ScaleOn chrome)
# --------------------------------------------------------------------------- #
class BaseTemplate:
    """Shared rendering for every document type.

    Subclasses override :attr:`title` and :meth:`build_body`.
    """

    title: str = "Document"

    def __init__(self, c: rl_canvas.Canvas, company: Dict, data: Dict,
                 base_dir: Path):
        self.c = c
        self.company = company
        self.data = data
        self.base_dir = base_dir
        self.regular = FONT_REGULAR
        self.bold = FONT_BOLD
        self.italic = FONT_ITALIC
        self.light = FONT_LIGHT

    # -- paragraph styles ------------------------------------------------- #
    def _style(self, size: int = 11, **kw) -> ParagraphStyle:
        defaults = dict(
            name="body", fontName=self.regular, fontSize=size,
            leading=size * LINE_HEIGHT, textColor=BLACK, alignment=TA_JUSTIFY,
            spaceAfter=13,
        )
        defaults.update(kw)
        return ParagraphStyle(**defaults)

    # ------------------------------------------------------------------ #
    # Watermark
    # ------------------------------------------------------------------ #
    def draw_watermark(self) -> None:
        wm = (_resolve(self.base_dir, self.company.get("watermark", ""))
              or _resolve(self.base_dir, self.company.get("logo", "")))
        reader = _watermark_image(wm, opacity=0.11) if wm else None
        if reader is None:
            return
        try:
            iw, ih = reader.getSize()
            target_w = PAGE_W * 0.62
            scale = target_w / iw
            target_h = ih * scale
            x = (PAGE_W - target_w) / 2
            y = (PAGE_H - target_h) / 2
            self.c.drawImage(reader, x, y, target_w, target_h, mask="auto")
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Header
    # ------------------------------------------------------------------ #
    def draw_header(self) -> float:
        c = self.c
        top = PAGE_H - MARGIN
        logo_bottom = top - 18

        # Logo is the sole branding element (no company name / tagline text).
        # Enlarged ~40% versus the previous size.
        logo = _resolve(self.base_dir, self.company.get("logo", ""))
        if logo:
            try:
                reader = ImageReader(str(logo))
                iw, ih = reader.getSize()
                logo_h = 64                       # larger ScaleOn logo
                logo_w = min(iw * (logo_h / ih), 280)
                c.drawImage(reader, MARGIN, top - logo_h, logo_w, logo_h,
                            preserveAspectRatio=True, mask="auto")
                logo_bottom = top - logo_h
            except Exception:
                pass

        # Corporate date block: small uppercase label + large bold date,
        # right aligned, no icons, no decoration.
        issue = format_long_date(self.data.get("issue_date", ""))
        right_y = top - 14
        if issue:
            c.setFillColor(DARK_GRAY)
            c.setFont(self.regular, 8.5)
            c.drawRightString(PAGE_W - MARGIN, right_y, "DATE OF ISSUE")
            c.setFillColor(BLACK)
            c.setFont(self.bold, 15)
            c.drawRightString(PAGE_W - MARGIN, top - 35, issue)
            right_y = top - 35

        # Intern ID, just below the issue date (label muted, ID bold).
        intern_id = str(self.data.get("intern_id", "")).strip()
        if intern_id:
            c.setFillColor(DARK_GRAY)
            c.setFont(self.regular, 8.5)
            c.drawRightString(PAGE_W - MARGIN, right_y - 18, "INTERN ID")
            c.setFillColor(BLACK)
            c.setFont(self.bold, 12)
            c.drawRightString(PAGE_W - MARGIN, right_y - 33, intern_id)

        # Thin light-grey divider beneath the header.
        line_y = logo_bottom - 24
        c.setStrokeColor(HAIRLINE)
        c.setLineWidth(1)
        c.line(MARGIN, line_y, PAGE_W - MARGIN, line_y)
        return line_y - 50                        # extra top spacing (#12)

    # ------------------------------------------------------------------ #
    # Title + gold accent rule
    # ------------------------------------------------------------------ #
    def draw_title(self, y: float) -> float:
        c = self.c
        c.setFillColor(BLACK)
        c.setFont(self.bold, 26)
        c.drawCentredString(PAGE_W / 2, y, self.title)
        new_y = y - 24

        # Optional small gold subtitle (e.g. the program name) - NOT a heading,
        # NO decorative underline beneath the title.
        if self.subtitle:
            c.setFillColor(GOLD)
            c.setFont(self.regular, 11)
            c.drawCentredString(PAGE_W / 2, new_y, self.subtitle)
            new_y -= 16
        return new_y - 26                          # paragraph spacing (#12)

    # ------------------------------------------------------------------ #
    # Paragraph rendering
    # ------------------------------------------------------------------ #
    def draw_paragraph(self, html: str, y: float,
                       style: ParagraphStyle | None = None,
                       width: float = CONTENT_W, x: float = MARGIN) -> float:
        style = style or self._style()
        para = Paragraph(html, style)
        _w, h = para.wrap(width, y)
        para.drawOn(self.c, x, y - h)
        return y - h - style.spaceAfter         # full gap = clear spacing (#12)

    # ------------------------------------------------------------------ #
    # Signature + auto-generated seal
    # ------------------------------------------------------------------ #
    def draw_signature(self, y: float) -> None:
        c = self.c
        block_top = max(min(y, MARGIN + 185), MARGIN + 170)

        # Order requested: signature image first, then "Sincerely,",
        # then HR Team, then ScaleOn - all in non-bold (regular) text.
        cursor = block_top
        sig = _resolve(self.base_dir, self.company.get("signature", ""))
        if sig:
            try:
                reader = ImageReader(str(sig))
                iw, ih = reader.getSize()
                sig_h = 60                       # larger signature image
                sig_w = min(iw * (sig_h / ih), 210)
                c.drawImage(reader, MARGIN, cursor - sig_h, sig_w, sig_h,
                            preserveAspectRatio=True, mask="auto")
                cursor -= sig_h + 6
            except Exception:
                cursor -= 6

        c.setFillColor(DARK_GRAY)
        c.setFont(self.regular, 11)
        c.drawString(MARGIN, cursor, "Sincerely,")

        # HR Team in serif; ScaleOn font is chosen in Company Settings.
        c.setFillColor(BLACK)
        c.setFont(SERIF, 11)                       # same size as "Sincerely,"
        hr_name = self.company.get("hr_name", "") or "HR Team"
        c.drawString(MARGIN, cursor - 20, hr_name)

        scaleon_font = {
            "Sans": self.regular, "Arial": ARIAL,
            "Serif": SERIF, "Helvetica": "Helvetica",
        }.get(self.company.get("scaleon_font", "Sans"), self.regular)
        c.setFont(scaleon_font, 17)
        c.drawString(MARGIN, cursor - 42, self.company.get("company_name", ""))

        # Blue ink-style seal on the right of the signature area.
        self.draw_seal(PAGE_W - MARGIN - 60, cursor - 4, radius=54)

    def draw_seal(self, cx: float, cy: float, radius: float = 54) -> None:
        """Draw the company stamp.

        If the user uploaded a stamp image in Company Settings it is used
        (rotated ~10°, semi-transparent). Otherwise a vector blue ink-seal is
        generated automatically as a fallback.
        """
        stamp_img = _resolve(self.base_dir, self.company.get("stamp", ""))
        if stamp_img:
            try:
                self._draw_stamp_image(stamp_img, cx, cy, radius)
                return
            except Exception:
                pass  # fall back to the vector seal below

        c = self.c
        c.saveState()
        # Rotate the whole stamp 8-12 degrees around its centre.
        c.translate(cx, cy)
        c.rotate(10)

        # Semi-transparent blue so it reads as real ink pressed onto paper.
        c.setStrokeAlpha(0.6)
        c.setFillAlpha(0.6)
        c.setStrokeColor(INK_BLUE)

        # Double circular border.
        c.setLineWidth(3.0)
        c.circle(0, 0, radius, stroke=1, fill=0)
        c.setLineWidth(1.3)
        c.circle(0, 0, radius - 5, stroke=1, fill=0)
        # Thin inner ring framing the centre.
        c.setLineWidth(0.9)
        c.circle(0, 0, radius - 20, stroke=1, fill=0)

        company = (self.company.get("company_name", "SCALEON") or "SCALEON").upper()
        tagline = (self.company.get("tagline", "") or "SCALE BEYOND LIMITS").upper()

        # Outer ring text only (no "OFFICIAL SEAL").
        self._arc_text(0, 0, radius - 11, f"{company}  \u2022  {tagline}",
                       6.2, INK_BLUE, top=True)

        # Centre: only a large "S".
        c.setFillColor(INK_BLUE)
        c.setFont(self.bold, 30)
        c.drawCentredString(0, -10, "S")
        c.restoreState()

    def _draw_stamp_image(self, path, cx: float, cy: float,
                          radius: float) -> None:
        """Place an uploaded stamp image centred at (cx, cy), rotated ~10°."""
        reader = ImageReader(str(path))
        iw, ih = reader.getSize()
        size = radius * 2.2
        if iw >= ih:
            w = size
            h = size * ih / iw
        else:
            h = size
            w = size * iw / ih
        c = self.c
        c.saveState()
        c.translate(cx, cy)
        c.rotate(10)
        c.setFillAlpha(0.9)
        c.drawImage(reader, -w / 2, -h / 2, w, h,
                    preserveAspectRatio=True, mask="auto")
        c.restoreState()

    def _arc_text(self, cx: float, cy: float, r: float, text: str,
                  size: float, color: Color, top: bool = True) -> None:
        """Render ``text`` along a circular arc (top or bottom of the seal)."""
        c = self.c
        c.setFont(self.bold, size)
        c.setFillColor(color)
        widths = [c.stringWidth(ch, self.bold, size) for ch in text]
        total_angle = sum(widths) / r  # radians spanned by the text

        if top:
            angle = math.pi / 2 + total_angle / 2
            step = -1
        else:
            angle = -math.pi / 2 - total_angle / 2
            step = 1

        for ch, w in zip(text, widths):
            char_angle = w / r
            a = angle + step * (char_angle / 2)
            x = cx + r * math.cos(a)
            y = cy + r * math.sin(a)
            c.saveState()
            c.translate(x, y)
            rot = math.degrees(a) + (-90 if top else 90)
            c.rotate(rot)
            c.drawCentredString(0, 0, ch)
            c.restoreState()
            angle += step * char_angle

    # ------------------------------------------------------------------ #
    # Footer
    # ------------------------------------------------------------------ #
    def draw_footer(self) -> None:
        c = self.c
        y = MARGIN - 24
        # Thin divider above the footer.
        c.setStrokeColor(HAIRLINE)
        c.setLineWidth(1)
        c.line(MARGIN, y + 16, PAGE_W - MARGIN, y + 16)

        # Footer: email | ScaleOn | website  (gold separators, black text).
        email = str(self.company.get("email", "")).strip()
        website = str(self.company.get("website", "")).strip()
        company = str(self.company.get("company_name", "")).strip()
        segments = [s for s in (email, company, website) if s]
        if not segments:
            return

        sep = "   |   "
        c.setFont(self.regular, 9)
        # Measure total width to center the whole line.
        total = 0.0
        for i, seg in enumerate(segments):
            total += c.stringWidth(seg, self.regular, 9)
            if i < len(segments) - 1:
                total += c.stringWidth(sep, self.regular, 9)
        x = PAGE_W / 2 - total / 2
        # Footer text colour comes from Company Settings (default black);
        # the separators stay gold as a subtle brand accent.
        try:
            text_color = HexColor(self.company.get("footer_color") or "#111111")
        except Exception:
            text_color = BLACK
        for i, seg in enumerate(segments):
            c.setFillColor(text_color)
            c.drawString(x, y, seg)
            x += c.stringWidth(seg, self.regular, 9)
            if i < len(segments) - 1:
                c.setFillColor(GOLD)
                c.drawString(x, y, sep)
                x += c.stringWidth(sep, self.regular, 9)

    # ------------------------------------------------------------------ #
    # Body (override me)
    # ------------------------------------------------------------------ #
    def build_body(self) -> List[str]:
        return ["<i>No content defined for this template.</i>"]

    # ------------------------------------------------------------------ #
    # Orchestration
    # ------------------------------------------------------------------ #
    def render(self) -> None:
        self.draw_watermark()
        y = self.draw_header()
        y = self.draw_title(y)

        # Greeting with auto-bold candidate name (skipped for certificates/NDA
        # that provide their own opening line).
        if self.greeting:
            y = self.draw_paragraph(self.greeting, y,
                                    self._style(size=11.5, spaceAfter=8))

        body_style = self._style()
        for para in self.build_body():
            y = self.draw_paragraph(para, y, body_style)

        self.draw_signature(y)
        self.draw_footer()
        self.c.showPage()

    # ------------------------------------------------------------------ #
    # Convenience accessors (dynamic values - never hardcoded)
    # ------------------------------------------------------------------ #
    @property
    def greeting(self) -> str:
        return f"Dear <b>{self.name}</b>,"

    @property
    def subtitle(self) -> str:
        """Optional small gold subtitle under the title (off by default)."""
        return ""

    @property
    def name(self) -> str:
        return self.data.get("candidate_name", "Candidate")

    @property
    def position(self) -> str:
        return self.data.get("position", "")

    @property
    def department(self) -> str:
        return self.data.get("department", "")

    @property
    def duration(self) -> str:
        return self.data.get("duration", "")

    @property
    def start(self) -> str:
        return format_long_date(self.data.get("start_date", ""))

    @property
    def end(self) -> str:
        return format_long_date(self.data.get("end_date", ""))

    @property
    def company_name(self) -> str:
        return self.company.get("company_name", "the company")


# --------------------------------------------------------------------------- #
# Concrete templates - all inherit the ScaleOn branding above
# --------------------------------------------------------------------------- #
class InternshipOfferTemplate(BaseTemplate):
    title = "Internship Offer Letter"

    @property
    def subtitle(self) -> str:
        # Small gold subtitle under the title, e.g. "ScaleOn Internship Program 2026".
        from utils import parse_date
        parsed = parse_date(self.data.get("issue_date", ""))
        year = parsed.year if parsed else datetime.now().year
        return f"{self.company_name} Internship Program {year}"

    def build_body(self) -> List[str]:
        # Exact approved template text - dynamic values only, nothing added.
        # Domain is universal: use department if given, else the position with
        # a trailing "Intern" stripped (e.g. "Social Media Marketing Intern"
        # -> "Social Media Marketing").
        import re
        role = self.department or self.position
        domain = re.sub(r"\s*intern\s*$", "", role, flags=re.IGNORECASE).strip()
        domain = domain or role or "your field"
        return [
            (f"We are pleased to offer you the opportunity to join "
             f"<b>{self.company_name}</b> as an Intern."),
            (f"We were impressed with your qualifications and your passion for "
             f"<b>{domain}</b>, and we believe that you will make a valuable "
             f"contribution to our team."),
            f"<b>Position: {self.position}</b>",
            f"<b>Duration: {self.start} to {self.end}</b>",
            (f"We look forward to welcoming you to the <b>{self.company_name}</b> "
             f"team and working together to achieve success. Thank you for "
             f"choosing <b>{self.company_name}</b> as the place to further your "
             f"career and professional growth."),
        ]


class JobOfferTemplate(BaseTemplate):
    title = "Full-Time Offer Letter"

    def build_body(self) -> List[str]:
        dept = f" in the <b>{self.department}</b> department" if self.department else ""
        return [
            (f"We are pleased to offer you the full-time position of "
             f"<b>{self.position}</b>{dept} at <b>{self.company_name}</b>. "
             f"Your skills, experience and professional outlook stood out "
             f"during our evaluation, and we are confident you will be a "
             f"valuable addition to our team."),
            (f"Your employment will be effective from <b>{self.start}</b>. The "
             f"complete details of your compensation, benefits and "
             f"responsibilities will be provided in your formal employment "
             f"agreement."),
            ("This offer is contingent upon successful background verification "
             "and submission of the required documentation. You will be "
             "governed by the policies and code of conduct of the company."),
            ("We are excited about the contribution you will make and look "
             "forward to a long and rewarding association."),
            ("Please sign and return a copy of this letter as confirmation of "
             "your acceptance."),
            (f"We warmly welcome you to the <b>{self.company_name}</b> family."),
        ]


class AppointmentLetterTemplate(BaseTemplate):
    title = "Appointment Letter"

    def build_body(self) -> List[str]:
        dept = f" in the <b>{self.department}</b> department" if self.department else ""
        return [
            (f"With reference to your application and subsequent interview, we "
             f"are pleased to appoint you as <b>{self.position}</b>{dept} at "
             f"<b>{self.company_name}</b>, effective <b>{self.start}</b>."),
            ("You will be governed by the rules, regulations and policies of "
             "the company as amended from time to time. A detailed description "
             "of your role, responsibilities and reporting structure will be "
             "shared separately."),
            ("You are expected to maintain the highest standards of "
             "professionalism, integrity and confidentiality throughout your "
             "tenure with the organization."),
            ("Please sign and return the duplicate copy of this letter as "
             "confirmation of your acceptance of this appointment."),
            (f"We warmly welcome you to the <b>{self.company_name}</b> family."),
        ]


class InternshipCertificateTemplate(BaseTemplate):
    title = "Internship Completion Certificate"

    @property
    def greeting(self) -> str:
        return ""

    def build_body(self) -> List[str]:
        return [
            (f"This is to certify that <b>{self.name}</b> has successfully "
             f"completed an internship as a <b>{self.position}</b> at "
             f"<b>{self.company_name}</b> from <b>{self.start}</b> to "
             f"<b>{self.end}</b>, for a total duration of <b>{self.duration}</b>."),
            (f"During this period, {self.name} was actively involved in live "
             f"projects and demonstrated excellent learning ability, "
             f"commitment, discipline and a collaborative attitude."),
            ("Their conduct and performance throughout the internship were "
             "found to be exemplary and in line with the values of the "
             "organization."),
            ("We appreciate the contribution made during the internship and "
             "wish them continued success in their professional career."),
        ]


class CompletionCertificateTemplate(BaseTemplate):
    title = "Certificate of Completion"

    @property
    def greeting(self) -> str:
        return ""

    def build_body(self) -> List[str]:
        program = self.data.get("course") or self.position
        return [
            (f"This certificate is proudly presented to <b>{self.name}</b> in "
             f"recognition of the successful completion of the "
             f"<b>{program}</b> program at <b>{self.company_name}</b>."),
            (f"The program was undertaken from <b>{self.start}</b> to "
             f"<b>{self.end}</b>, during which {self.name} displayed remarkable "
             f"dedication, skill and professionalism."),
            ("This achievement reflects a high standard of commitment and we "
             "extend our heartfelt congratulations on this accomplishment."),
        ]


class ExperienceLetterTemplate(BaseTemplate):
    title = "Experience Letter"

    @property
    def greeting(self) -> str:
        return "<b>To Whom It May Concern,</b>"

    def build_body(self) -> List[str]:
        return [
            (f"This is to certify that <b>{self.name}</b> was associated with "
             f"<b>{self.company_name}</b> as a <b>{self.position}</b> from "
             f"<b>{self.start}</b> to <b>{self.end}</b>."),
            (f"During the tenure with us, {self.name} demonstrated strong "
             f"professional competence, dedication and a collaborative "
             f"attitude. Their conduct and overall performance were found to "
             f"be satisfactory and commendable."),
            (f"We thank {self.name} for the valuable contribution to the "
             f"organization and wish them the very best for all future "
             f"endeavours."),
        ]


class RelievingLetterTemplate(BaseTemplate):
    title = "Relieving Letter"

    def build_body(self) -> List[str]:
        return [
            (f"This is to formally confirm that you have been relieved from "
             f"your duties as <b>{self.position}</b> at <b>{self.company_name}"
             f"</b> with effect from <b>{self.end}</b>."),
            (f"You served the organization from <b>{self.start}</b> to "
             f"<b>{self.end}</b>. We confirm that all your dues have been "
             f"settled and there are no pending obligations on either side."),
            ("Your contribution during your tenure is sincerely appreciated."),
            ("We wish you success and fulfilment in your future endeavours."),
        ]


class AppreciationCertificateTemplate(BaseTemplate):
    title = "Certificate of Appreciation"

    @property
    def greeting(self) -> str:
        return ""

    def build_body(self) -> List[str]:
        return [
            (f"This certificate of appreciation is proudly awarded to "
             f"<b>{self.name}</b> for outstanding performance and valuable "
             f"contribution as a <b>{self.position}</b> at "
             f"<b>{self.company_name}</b>."),
            (f"Your dedication, initiative and professionalism between "
             f"<b>{self.start}</b> and <b>{self.end}</b> have made a "
             f"meaningful difference to our team and our clients."),
            ("We thank you for your commitment and wish you continued success "
             "in all your future endeavours."),
        ]


class NDATemplate(BaseTemplate):
    title = "Non-Disclosure Agreement"

    @property
    def greeting(self) -> str:
        return ""

    def build_body(self) -> List[str]:
        issue = format_long_date(self.data.get("issue_date", ""))
        return [
            (f"This Non-Disclosure Agreement (\"Agreement\") is entered into on "
             f"<b>{issue}</b> between <b>{self.company_name}</b> (the "
             f"\"Disclosing Party\") and <b>{self.name}</b> (the \"Receiving "
             f"Party\")."),
            ("<b>1. Confidential Information.</b> The Receiving Party agrees to "
             "treat all proprietary, technical and business information "
             "disclosed during the engagement as strictly confidential."),
            ("<b>2. Obligations.</b> The Receiving Party shall not disclose, "
             "copy or use the Confidential Information for any purpose other "
             "than that for which it was provided."),
            ("<b>3. Term.</b> The obligations under this Agreement remain in "
             "effect during the engagement and for such period thereafter as "
             "is necessary to protect the Disclosing Party's interests."),
            ("By signing below, the Receiving Party acknowledges and agrees to "
             "the terms set forth in this Agreement."),
        ]


# --------------------------------------------------------------------------- #
# Registry + facade
# --------------------------------------------------------------------------- #
TEMPLATES: Dict[str, Type[BaseTemplate]] = {
    "Internship Offer": InternshipOfferTemplate,
    "Full-Time Offer": JobOfferTemplate,
    "Appointment Letter": AppointmentLetterTemplate,
    "Internship Certificate": InternshipCertificateTemplate,
    "Completion Certificate": CompletionCertificateTemplate,
    "Experience Letter": ExperienceLetterTemplate,
    "Relieving Letter": RelievingLetterTemplate,
    "Appreciation Certificate": AppreciationCertificateTemplate,
    "NDA": NDATemplate,
}

TEMPLATE_LABELS: List[str] = list(TEMPLATES.keys())


class PDFGenerator:
    """Public facade the rest of the app uses to create PDFs."""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        register_fonts(base_dir / "assets" / "fonts")
        _register_arial()

    def generate(self, output_path: Path, company: Dict, data: Dict,
                 template_label: str = "Internship Offer") -> Path:
        """Render ``data`` using ``template_label`` to ``output_path``."""
        template_cls = TEMPLATES.get(template_label, InternshipOfferTemplate)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        c = rl_canvas.Canvas(str(output_path), pagesize=A4)
        c.setTitle(f"{data.get('candidate_name', 'Candidate')} - {template_label}")
        c.setAuthor(company.get("company_name", "ScaleOn"))
        template = template_cls(c, company, data, self.base_dir)
        template.render()
        c.save()
        return output_path

"""
certificate_generator.py
------------------------
Standalone **Certificate of Completion** module.

This is an *additive* feature: it reuses the existing ScaleOn branding helpers
from :mod:`pdf_generator` (palette, font registration, watermark, asset
resolution) by importing them, and reuses the existing SQLite database file
(``company/interns.db``) by adding its own ``certificates`` table. It does not
modify any existing module, table or behaviour.

Contents
========
    * CertificateStore  - SQLite helper (new ``certificates`` + ``cert_counters``
                          tables in the existing DB file).
    * generate_certificate(...) - renders a premium A4 *landscape* certificate.
    * build_certificate_filename(...) - "{Name}_Certificate_of_Completion.pdf".
"""

from __future__ import annotations

import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import Paragraph

import pdf_generator as pg
import utils
from settings import store_path

# Landscape A4 canvas size.
PAGE_W, PAGE_H = landscape(A4)
MARGIN = 46


# --------------------------------------------------------------------------- #
# Database (reuses the existing interns.db file, adds new tables only)
# --------------------------------------------------------------------------- #
def _tune_connection(conn: sqlite3.Connection) -> None:
    """WAL + busy timeout: this connection shares the file with the intern DB
    and with the bulk generator's worker thread."""
    for pragma in ("PRAGMA journal_mode=WAL",
                   "PRAGMA busy_timeout=5000",
                   "PRAGMA synchronous=NORMAL"):
        try:
            conn.execute(pragma)
        except sqlite3.Error:
            pass


class CertificateStore:
    """SQLite helper for certificate numbers and records.

    Opens the *same* database file used by the offer-letter feature and adds a
    dedicated ``certificates`` table - existing tables are never touched.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        _tune_connection(self._conn)
        self._create_schema()

    def _create_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS certificates (
                    cert_no        TEXT PRIMARY KEY,
                    intern_id      TEXT,
                    candidate_name TEXT,
                    position       TEXT,
                    issue_date     TEXT,
                    start_date     TEXT,
                    end_date       TEXT,
                    duration       TEXT,
                    grade          TEXT,
                    remarks        TEXT,
                    pdf_path       TEXT,
                    status         TEXT,
                    created_at     TEXT
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cert_counters (
                    prefix   TEXT PRIMARY KEY,
                    last_num INTEGER NOT NULL
                )
                """
            )
        self._migrate()

    def _migrate(self) -> None:
        """Add columns introduced after the first release (idempotent).

        Existing ``interns.db`` files already contain a ``certificates`` table,
        so new fields have to be added rather than declared.
        """
        with self._lock, self._conn:
            existing = {r["name"] for r in self._conn.execute(
                "PRAGMA table_info(certificates)")}
            for column in ("cert_type", "program", "department"):
                if column not in existing:
                    self._conn.execute(
                        f"ALTER TABLE certificates ADD COLUMN {column} TEXT")
        self._migrate_paths()

    def _migrate_paths(self) -> None:
        """Convert absolute pdf_path values inside the project to relative."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT cert_no, pdf_path FROM certificates "
                "WHERE pdf_path IS NOT NULL AND pdf_path != ''").fetchall()
        updates = []
        for row in rows:
            stored = store_path(row["pdf_path"])
            if stored != row["pdf_path"]:
                updates.append((stored, row["cert_no"]))
        if not updates:
            return
        with self._lock, self._conn:
            self._conn.executemany(
                "UPDATE certificates SET pdf_path = ? WHERE cert_no = ?",
                updates)

    @staticmethod
    def _prefix_for(year: Optional[int], kind: str = "CERT") -> str:
        """Return the counter prefix, e.g. ``SO-CERT-26`` or ``SO-INT-26``.

        ``kind`` keeps each certificate family on its own sequence so an
        internship certificate never borrows a completion certificate's number.
        """
        year = year or datetime.now().year
        kind = (kind or "CERT").strip().upper() or "CERT"
        return f"SO-{kind}-{year % 100:02d}"

    def peek_next_cert_no(self, year: Optional[int] = None,
                          kind: str = "CERT") -> str:
        prefix = self._prefix_for(year, kind)
        with self._lock:
            row = self._conn.execute(
                "SELECT last_num FROM cert_counters WHERE prefix = ?",
                (prefix,)).fetchone()
        last = row["last_num"] if row else 0
        return f"{prefix}{last + 1:04d}"

    def next_cert_no(self, year: Optional[int] = None,
                     kind: str = "CERT") -> str:
        prefix = self._prefix_for(year, kind)
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT last_num FROM cert_counters WHERE prefix = ?",
                (prefix,)).fetchone()
            num = (row["last_num"] if row else 0) + 1
            while self._conn.execute(
                    "SELECT 1 FROM certificates WHERE cert_no = ?",
                    (f"{prefix}{num:04d}",)).fetchone():
                num += 1
            self._conn.execute(
                "INSERT INTO cert_counters(prefix, last_num) VALUES(?, ?) "
                "ON CONFLICT(prefix) DO UPDATE SET last_num = excluded.last_num",
                (prefix, num))
        return f"{prefix}{num:04d}"

    def reserve_counter(self, cert_no: str) -> None:
        """Ensure the allocator never tries to hand out ``cert_no`` again.

        ``next_cert_no`` already skips numbers present in the table, so this is
        not needed for correctness - but without it an import of a hundred
        certificates leaves the counter far behind, and every later allocation
        re-scans from the old value. Mirrors
        :meth:`InternDatabase.reserve_counter`.
        """
        text = (cert_no or "").strip().upper()
        if len(text) < 5 or not text[-4:].isdigit():
            return
        prefix, number = text[:-4], int(text[-4:])
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT last_num FROM cert_counters WHERE prefix = ?",
                (prefix,)).fetchone()
            if number > (row["last_num"] if row else 0):
                self._conn.execute(
                    "INSERT INTO cert_counters(prefix, last_num) "
                    "VALUES(?, ?) ON CONFLICT(prefix) DO UPDATE SET "
                    "last_num = excluded.last_num", (prefix, number))

    def add_record(self, record: Dict[str, str]) -> None:
        fields = ("cert_no", "intern_id", "candidate_name", "position",
                  "issue_date", "start_date", "end_date", "duration", "grade",
                  "remarks", "pdf_path", "status", "created_at", "cert_type",
                  "program", "department")
        values = [record.get(f, "") for f in fields]
        values[fields.index("pdf_path")] = store_path(record.get("pdf_path", ""))
        if not record.get("created_at"):
            values[fields.index("created_at")] = datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S")
        with self._lock, self._conn:
            self._conn.execute(
                f"INSERT OR REPLACE INTO certificates({', '.join(fields)}) "
                f"VALUES({', '.join('?' * len(fields))})", values)

    def get(self, cert_no: str) -> Optional[Dict[str, str]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM certificates WHERE UPPER(cert_no) = UPPER(?)",
                (cert_no.strip(),)).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------ #
    # Verification helpers
    # ------------------------------------------------------------------ #
    # Certificate families, in the order a bare number is guessed.
    KINDS = ("CERT", "INT")

    @staticmethod
    def candidate_numbers(value: str) -> List[str]:
        """Return the certificate numbers a typed ``value`` could mean.

        People rarely type the full ``SO-INT-260031``: they paste it with
        stray spaces, drop the dashes, or copy only the digits off the PDF.
        Every sensible spelling is tried before declaring the ID invalid.
        """
        raw = (value or "").strip()
        if not raw:
            return []
        squashed = re.sub(r"[\s_]+", "", raw).upper()
        options = [raw, squashed]
        no_dash = squashed.replace("-", "")
        # "SOINT260031" / "SO CERT 260001" -> canonical dashed form.
        for kind in CertificateStore.KINDS:
            token = f"SO{kind}"
            if no_dash.startswith(token):
                options.append(f"SO-{kind}-{no_dash[len(token):]}")
        # Bare digits ("260031" or even "0031") -> try each family.
        digits = re.sub(r"\D", "", squashed)
        if digits and digits == no_dash:
            yy = f"{datetime.now().year % 100:02d}"
            for kind in CertificateStore.KINDS:
                if len(digits) >= 6:
                    options.append(f"SO-{kind}-{digits}")
                elif len(digits) == 4:
                    options.append(f"SO-{kind}-{yy}{digits}")
        # De-duplicate while preserving order.
        seen, unique = set(), []
        for opt in options:
            key = opt.upper()
            if opt and key not in seen:
                seen.add(key)
                unique.append(opt)
        return unique

    def find(self, value: str) -> Optional[Dict[str, str]]:
        """Look up a certificate by number, tolerating formatting variations."""
        for candidate in self.candidate_numbers(value):
            record = self.get(candidate)
            if record:
                return record
        return None

    def certs_for_intern(self, intern_id: str) -> List[Dict[str, str]]:
        """Every certificate issued to an Intern ID (newest first)."""
        if not intern_id or not intern_id.strip():
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM certificates WHERE UPPER(intern_id) = UPPER(?) "
                "ORDER BY created_at DESC", (intern_id.strip(),)).fetchall()
        return [dict(r) for r in rows]

    def certs_for_name(self, name: str) -> List[Dict[str, str]]:
        """Certificates matched by candidate name (newest first).

        Used only as a fallback for legacy rows saved before Intern IDs were
        stored on certificates.
        """
        if not name or not name.strip():
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM certificates WHERE LOWER(candidate_name) = "
                "LOWER(?) ORDER BY created_at DESC", (name.strip(),)).fetchall()
        return [dict(r) for r in rows]

    def cert_of_type(self, intern_id: str, cert_type: str
                     ) -> Optional[Dict[str, str]]:
        """An existing certificate of ``cert_type`` for this intern, if any.

        Used to avoid issuing the same person two of the same certificate.
        Records written before the ``cert_type`` column existed were all
        completion certificates, hence the NULL/empty handling.
        """
        if not intern_id or not intern_id.strip() or not cert_type:
            return None
        legacy = cert_type == "Completion Certificate"
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM certificates WHERE UPPER(intern_id) = UPPER(?) "
                "AND (cert_type = ?" +
                (" OR cert_type IS NULL OR cert_type = ''" if legacy else "") +
                ") ORDER BY created_at DESC LIMIT 1",
                (intern_id.strip(), cert_type)).fetchone()
        return dict(row) if row else None

    def all_records(self) -> List[Dict[str, str]]:
        """Every certificate ever issued (newest first)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM certificates ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) AS n FROM certificates").fetchone()["n"]

    # ------------------------------------------------------------------ #
    # Corrections
    # ------------------------------------------------------------------ #
    def reassign_intern(self, old_id: str, new_id: str) -> int:
        """Point every certificate of ``old_id`` at ``new_id``.

        Returns the number of certificate records updated. Certificate numbers
        are never changed - only the Intern ID they belong to.
        """
        old = (old_id or "").strip()
        new = (new_id or "").strip()
        if not old or not new or old.upper() == new.upper():
            return 0
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE certificates SET intern_id = ? WHERE "
                "UPPER(intern_id) = UPPER(?)", (new, old))
        return cursor.rowcount

    def set_pdf_path(self, cert_no: str, pdf_path: str) -> bool:
        """Update where a certificate's PDF lives (after a re-render)."""
        if not cert_no or not str(cert_no).strip():
            return False
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE certificates SET pdf_path = ? WHERE UPPER(cert_no) = "
                "UPPER(?)", (store_path(pdf_path), str(cert_no).strip()))
        return cursor.rowcount > 0

    # ------------------------------------------------------------------ #
    # Deletion
    # ------------------------------------------------------------------ #
    def delete(self, cert_no: str) -> bool:
        """Delete one certificate record by number."""
        if not cert_no or not cert_no.strip():
            return False
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM certificates WHERE UPPER(cert_no) = UPPER(?)",
                (cert_no.strip(),))
        return cursor.rowcount > 0

    def close(self) -> None:
        """Release the connection. Mirrors :meth:`InternDatabase.close`."""
        with self._lock:
            self._conn.close()

    def delete_for_intern(self, intern_id: str) -> int:
        """Delete every certificate belonging to an Intern ID.

        Returns the number of certificate records removed. Counters are left
        untouched so a deleted certificate number is never reissued.
        """
        if not intern_id or not intern_id.strip():
            return 0
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM certificates WHERE UPPER(intern_id) = UPPER(?)",
                (intern_id.strip(),))
        return cursor.rowcount


# --------------------------------------------------------------------------- #
# Filename helper
# --------------------------------------------------------------------------- #
def build_certificate_filename(candidate_name: str) -> str:
    return f"{utils.sanitize_filename(candidate_name)}_Certificate_of_Completion"


# --------------------------------------------------------------------------- #
# Renderer
# --------------------------------------------------------------------------- #
class CertificateRenderer:
    """Renders a premium ScaleOn Certificate of Completion (A4 landscape)."""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        # Reuse the existing font registration so fonts embed consistently.
        pg.register_fonts(base_dir / "assets" / "fonts")
        pg._register_arial()

    # -- branding helpers (reuse pdf_generator) -------------------------- #
    def _watermark(self, c) -> None:
        wm = (pg._resolve(self.base_dir, self.company.get("watermark", ""))
              or pg._resolve(self.base_dir, self.company.get("logo", "")))
        reader = pg._watermark_image(wm, opacity=0.07) if wm else None
        if reader is None:
            return
        try:
            iw, ih = reader.getSize()
            target_w = PAGE_W * 0.46
            scale = target_w / iw
            target_h = ih * scale
            c.drawImage(reader, (PAGE_W - target_w) / 2,
                        (PAGE_H - target_h) / 2, target_w, target_h, mask="auto")
        except Exception:
            pass

    def _border(self, c) -> None:
        c.setStrokeColor(pg.GOLD)
        c.setLineWidth(3)
        c.rect(MARGIN, MARGIN, PAGE_W - 2 * MARGIN, PAGE_H - 2 * MARGIN)
        c.setLineWidth(1)
        inset = MARGIN + 8
        c.rect(inset, inset, PAGE_W - 2 * inset, PAGE_H - 2 * inset)

    def _logo(self, c, top_y: float) -> float:
        logo = pg._resolve(self.base_dir, self.company.get("logo", ""))
        if not logo:
            return top_y
        try:
            reader = ImageReader(str(logo))
            iw, ih = reader.getSize()
            logo_h = 58
            logo_w = min(iw * (logo_h / ih), 220)
            c.drawImage(reader, (PAGE_W - logo_w) / 2, top_y - logo_h,
                        logo_w, logo_h, preserveAspectRatio=True, mask="auto")
            return top_y - logo_h
        except Exception:
            return top_y

    def _seal(self, c, cx: float, cy: float, radius: float = 52) -> None:
        """Uploaded stamp image if present, else an auto gold vector seal."""
        stamp = pg._resolve(self.base_dir, self.company.get("stamp", ""))
        if stamp:
            try:
                reader = ImageReader(str(stamp))
                iw, ih = reader.getSize()
                size = radius * 2.2
                w, h = (size, size * ih / iw) if iw >= ih else (size * iw / ih, size)
                c.saveState()
                c.translate(cx, cy)
                c.rotate(8)
                c.setFillAlpha(0.9)
                c.drawImage(reader, -w / 2, -h / 2, w, h,
                            preserveAspectRatio=True, mask="auto")
                c.restoreState()
                return
            except Exception:
                pass
        # Vector fallback.
        c.saveState()
        c.translate(cx, cy)
        c.rotate(9)
        c.setStrokeAlpha(0.7)
        c.setFillAlpha(0.7)
        c.setStrokeColor(pg.GOLD)
        c.setLineWidth(2.6)
        c.circle(0, 0, radius, stroke=1, fill=0)
        c.setLineWidth(1.2)
        c.circle(0, 0, radius - 5, stroke=1, fill=0)
        company = (self.company.get("company_name", "SCALEON") or "SCALEON").upper()
        tagline = (self.company.get("tagline", "") or "SCALE BEYOND LIMITS").upper()
        self._arc_text(c, 0, 0, radius - 11, f"{company}  \u2022  {tagline}",
                       6.0, top=True)
        c.setFillColor(pg.GOLD)
        c.setFont(pg.FONT_BOLD, 26)
        c.drawCentredString(0, -10, "S")
        c.restoreState()

    def _arc_text(self, c, cx, cy, r, text, size, top=True) -> None:
        import math
        c.setFont(pg.FONT_BOLD, size)
        c.setFillColor(pg.GOLD)
        widths = [c.stringWidth(ch, pg.FONT_BOLD, size) for ch in text]
        total = sum(widths) / r
        angle = math.pi / 2 + total / 2 if top else -math.pi / 2 - total / 2
        step = -1 if top else 1
        for ch, w in zip(text, widths):
            ca = w / r
            a = angle + step * (ca / 2)
            c.saveState()
            c.translate(cx + r * math.cos(a), cy + r * math.sin(a))
            c.rotate(math.degrees(a) + (-90 if top else 90))
            c.drawCentredString(0, 0, ch)
            c.restoreState()
            angle += step * ca

    def _para(self, c, html, cx, cy, width, size=12, leading=18, bold=False):
        style = ParagraphStyle(
            "c", fontName=pg.FONT_BOLD if bold else pg.FONT_REGULAR,
            fontSize=size, leading=leading, textColor=pg.BLACK,
            alignment=TA_CENTER)
        p = Paragraph(html, style)
        _w, h = p.wrap(width, 200)
        p.drawOn(c, cx - width / 2, cy - h)
        return cy - h

    # -- main render ----------------------------------------------------- #
    def render(self, output_path: Path, company: Dict, data: Dict) -> Path:
        self.company = company
        output_path.parent.mkdir(parents=True, exist_ok=True)
        c = rl_canvas.Canvas(str(output_path), pagesize=landscape(A4))
        c.setTitle(f"{data.get('candidate_name','Candidate')} - Certificate of Completion")
        c.setAuthor(company.get("company_name", "ScaleOn"))

        self._watermark(c)
        self._border(c)

        y = self._logo(c, PAGE_H - MARGIN - 22)

        # Title + program subtitle.
        c.setFillColor(pg.BLACK)
        c.setFont(pg.FONT_BOLD, 34)
        c.drawCentredString(PAGE_W / 2, y - 34, "Certificate of Completion")
        # gold accent rule
        c.setStrokeColor(pg.GOLD)
        c.setLineWidth(2.5)
        c.line(PAGE_W / 2 - 120, y - 44, PAGE_W / 2 + 120, y - 44)

        issue = data.get("issue_date", "")
        parsed = utils.parse_date(issue)
        year = parsed.year if parsed else datetime.now().year
        company_name = company.get("company_name", "ScaleOn")
        c.setFillColor(pg.GOLD)
        c.setFont(pg.FONT_REGULAR, 12)
        c.drawCentredString(PAGE_W / 2, y - 62,
                            f"{company_name} Internship Program {year}")

        cy = y - 96
        name = data.get("candidate_name", "Candidate")
        position = data.get("position", "")
        start = utils.format_long_date(data.get("start_date", ""))
        end = utils.format_long_date(data.get("end_date", ""))
        duration = data.get("duration", "") or utils.duration_between(
            data.get("start_date", ""), data.get("end_date", ""))

        cy = self._para(c, "This certificate is proudly presented to",
                        PAGE_W / 2, cy, PAGE_W * 0.8, size=13, leading=18)

        # Candidate name (large, gold).
        c.setFillColor(pg.GOLD)
        c.setFont(pg.FONT_BOLD, 30)
        c.drawCentredString(PAGE_W / 2, cy - 34, name)
        cy = cy - 34 - 10

        body = (f"for successfully completing the <b>{position}</b> internship "
                f"at <b>{company_name}</b> from <b>{start}</b> to <b>{end}</b> "
                f"(<b>{duration}</b>) under the {company_name} Internship "
                f"Program {year}.")
        cy = self._para(c, body, PAGE_W / 2, cy - 6, PAGE_W * 0.78,
                        size=12.5, leading=19)

        appreciation = (
            f"We sincerely appreciate {name}'s dedication, professionalism and "
            f"valuable contribution throughout the internship. Their commitment "
            f"and enthusiasm reflect the highest standards of the "
            f"{company_name} community. We wish them continued success in all "
            f"future endeavours.")
        cy = self._para(c, appreciation, PAGE_W / 2, cy - 10, PAGE_W * 0.78,
                        size=11, leading=17)

        # Optional grade / remarks.
        grade = data.get("grade", "").strip()
        remarks = data.get("remarks", "").strip()
        extras = []
        if grade:
            extras.append(f"<b>Performance Grade:</b> {grade}")
        if remarks:
            extras.append(f"<b>Remarks:</b> {remarks}")
        if extras:
            cy = self._para(c, "&nbsp;&nbsp;&nbsp;".join(extras), PAGE_W / 2,
                            cy - 6, PAGE_W * 0.8, size=10.5, leading=15)

        # Bottom band: signature (left), seal (right), cert no (center).
        base_y = MARGIN + 64
        self._signature(c, MARGIN + 70, base_y)
        self._seal(c, PAGE_W - MARGIN - 100, base_y + 30, radius=50)

        # Certificate number + issue date (centered footer).
        cert_no = data.get("cert_no", "")
        c.setFillColor(pg.DARK_GRAY)
        c.setFont(pg.FONT_REGULAR, 9)
        footer_bits = []
        if cert_no:
            footer_bits.append(f"Certificate No: {cert_no}")
        if issue:
            footer_bits.append(f"Issued: {utils.format_long_date(issue)}")
        if data.get("intern_id"):
            footer_bits.append(f"Intern ID: {data['intern_id']}")
        c.drawCentredString(PAGE_W / 2, MARGIN + 18,
                            "      |      ".join(footer_bits))

        c.save()
        return output_path

    def _signature(self, c, x_center: float, base_y: float) -> None:
        sig = pg._resolve(self.base_dir, self.company.get("signature", ""))
        if sig:
            try:
                reader = ImageReader(str(sig))
                iw, ih = reader.getSize()
                sig_h = 42
                sig_w = min(iw * (sig_h / ih), 150)
                c.drawImage(reader, x_center - sig_w / 2, base_y + 10,
                            sig_w, sig_h, preserveAspectRatio=True, mask="auto")
            except Exception:
                pass
        c.setStrokeColor(pg.DARK_GRAY)
        c.setLineWidth(1)
        c.line(x_center - 80, base_y + 6, x_center + 80, base_y + 6)
        c.setFillColor(pg.BLACK)
        c.setFont(pg.FONT_REGULAR, 10)
        c.drawCentredString(x_center, base_y - 8,
                            self.company.get("hr_name", "") or "HR Team")
        c.setFont(pg.FONT_BOLD, 12)
        c.drawCentredString(x_center, base_y - 24,
                            self.company.get("company_name", "ScaleOn"))


def generate_certificate(output_path: Path, company: Dict, data: Dict,
                         base_dir: Path) -> Path:
    """Convenience wrapper used by the UI and bulk generator."""
    return CertificateRenderer(base_dir).render(output_path, company, data)

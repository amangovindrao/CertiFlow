"""
document_page.py
----------------
The single page for creating any ScaleOn document - offer letters and both
certificate types - with a live preview.

Deliberately intern-only: ScaleOn's logo, tagline, watermark, seal, signature
and HR block are fixed and come from the saved company profile, so this page
never asks for company details. Fields that only apply to one document type
(program name, grade, remarks) appear and disappear with the type.

Layout is a form on the left and a genuine live preview on the right. The
preview is rasterised from the very same PDF that gets exported, so what is on
screen is exactly what prints. Rendering happens on a worker thread with a
short debounce, keeping typing responsive.
"""

from __future__ import annotations

import queue
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import customtkinter as ctk

import bulk_generator as bg
import certificate_generator as cg
import doc_router
import internship_certificate as ic
import pdf_preview as pv
import utils
from settings import BASE_DIR, OUTPUT_DIR
from ui import (ACCENT, ACCENT_HOVER, CORNER, PAD, DatePicker, ModernDialog,
                SearchableCombo, _font, copy_button, copy_to_clipboard)

TEMPLATE = "Internship Certificate"
COMPLETION_TEMPLATE = "Completion Certificate"
PREVIEW_DEBOUNCE_MS = 400

# Fields that only make sense for certain document types.
TYPE_ONLY_FIELDS = {
    "program": {TEMPLATE},
    "grade": {COMPLETION_TEMPLATE},
    "remarks": {COMPLETION_TEMPLATE},
}


class CreateDocumentPage(ctk.CTkFrame):
    """Create any ScaleOn document, with a live preview of the real PDF."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.store = app.cert_store
        self.db = app.db

        # Preview plumbing.
        self._preview_pdf = (Path(tempfile.gettempdir()) /
                             "scaleon_internship_certificate_preview.pdf")
        self._preview_job: Optional[str] = None
        self._resize_job: Optional[str] = None
        self._queue: "queue.Queue" = queue.Queue()
        self._seq = 0
        self._polling = False
        self._preview_image = None          # keep a reference alive
        self._last_size = (0, 0)
        self._intern_id_touched = False
        self.template_var = ctk.StringVar(value=TEMPLATE)
        self._field_rows: Dict[str, list] = {}
        self._custom_fields: Dict[str, set] = {}

        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_form()
        self._build_preview()
        self._prefill()
        self._schedule_preview()

    # ================================================================== #
    # Header
    # ================================================================== #
    def _build_header(self) -> None:
        ctk.CTkLabel(self, text="Create Document",
                     font=_font(26, "bold"), anchor="w").grid(
            row=0, column=0, columnspan=2, sticky="w", padx=40, pady=(30, 0))
        ctk.CTkLabel(self, text="Pick a document type, fill in the intern's "
                     "details and watch it build. ScaleOn branding is fixed - "
                     "only intern details are needed.",
                     font=_font(13), text_color=("#6B7280", "#9CA3AF"),
                     anchor="w").grid(row=1, column=0, columnspan=2, sticky="w",
                                      padx=40, pady=(2, 12))

    # ================================================================== #
    # Form (intern-specific fields only)
    # ================================================================== #
    def _build_form(self) -> None:
        wrap = ctk.CTkScrollableFrame(self, fg_color="transparent", width=430)
        wrap.grid(row=2, column=0, sticky="nsew", padx=(40, 12), pady=(0, 24))
        wrap.grid_columnconfigure(0, weight=1)

        card = ctk.CTkFrame(wrap, corner_radius=18,
                            fg_color=("#FFFFFF", "#161616"), border_width=1,
                            border_color=("#ECECEC", "#242424"))
        card.grid(row=0, column=0, sticky="ew")
        card.grid_columnconfigure(0, weight=1)

        self.form: Dict[str, object] = {}
        row = 0

        def ent(m):
            return ctk.CTkEntry(m, height=40, corner_radius=CORNER,
                                font=_font(12))

        def labeled(label, factory, r, hint: str = "", key: str = ""):
            parts = []
            title = ctk.CTkLabel(card, text=label, font=_font(11, "bold"),
                                 text_color=("#374151", "#D1D5DB"), anchor="w")
            title.grid(row=r, column=0, sticky="ew", padx=PAD, pady=(12, 2))
            parts.append(title)
            widget = factory(card)
            widget.grid(row=r + 1, column=0, sticky="ew", padx=PAD,
                        pady=(0, 0))
            parts.append(widget)
            if hint:
                note = ctk.CTkLabel(card, text=hint, font=_font(10),
                                    text_color=("#9CA3AF", "#6B7280"),
                                    anchor="w")
                note.grid(row=r + 2, column=0, sticky="ew", padx=PAD,
                          pady=(2, 0))
                parts.append(note)
            if key:
                # Remembered so type-specific fields can be hidden and shown.
                self._field_rows[key] = parts
            return widget

        # -- document type ---------------------------------------------- #
        ctk.CTkLabel(card, text="Document", font=_font(15, "bold"),
                     anchor="w").grid(row=row, column=0, sticky="w",
                                      padx=PAD, pady=(PAD, 0))
        row += 1
        self.type_menu = labeled("Document Type *", lambda m: ctk.CTkOptionMenu(
            m, values=doc_router.all_doc_types(), variable=self.template_var,
            height=40, corner_radius=CORNER, font=_font(12), fg_color=ACCENT,
            button_color=ACCENT, button_hover_color=ACCENT_HOVER,
            command=lambda *_: self._on_type_change()), row)
        row += 2

        ctk.CTkLabel(card, text="Intern Details", font=_font(15, "bold"),
                     anchor="w").grid(row=row, column=0, sticky="w",
                                      padx=PAD, pady=(PAD, 0))
        row += 1

        # Pull an existing intern instead of retyping their details.
        ctk.CTkButton(card, text="\U0001F465  Select Existing Intern",
                      command=self._select_existing, height=38,
                      corner_radius=CORNER, font=_font(12, "bold"),
                      fg_color=("#F3F4F6", "#222222"),
                      hover_color=("#E5E7EB", "#2E2E2E"),
                      text_color=("#374151", "#D1D5DB")).grid(
            row=row, column=0, sticky="ew", padx=PAD, pady=(10, 0))
        row += 1

        self.form["candidate_name"] = labeled("Intern Full Name *", ent, row)
        row += 2
        self.form["position"] = labeled(
            "Internship Role *", lambda m: SearchableCombo(
                m, utils.POSITION_OPTIONS, height=40, corner_radius=CORNER,
                font=_font(12), button_color=ACCENT,
                button_hover_color=ACCENT_HOVER), row)
        row += 2
        self.form["intern_id"] = labeled(
            "Intern ID *", ent, row,
            hint="Auto-filled. Reused automatically for a known intern.")
        row += 3

        self.form["start_date"] = labeled(
            "Internship Start Date *", lambda m: DatePicker(m), row)
        row += 2
        self.form["duration"] = labeled(
            "Duration (fills End Date)", lambda m: ctk.CTkOptionMenu(
                m, values=list(utils.DURATION_OPTIONS.keys()), height=40,
                corner_radius=CORNER, font=_font(12), fg_color=ACCENT,
                button_color=ACCENT, button_hover_color=ACCENT_HOVER,
                command=lambda *_: self._apply_duration()), row)
        row += 2
        self.form["end_date"] = labeled(
            "Internship End Date *", lambda m: DatePicker(m), row)
        row += 2
        self.form["issue_date"] = labeled(
            "Certificate Issue Date *", lambda m: DatePicker(m), row)
        row += 2

        ctk.CTkLabel(card, text="Optional", font=_font(15, "bold"),
                     anchor="w").grid(row=row, column=0, sticky="w",
                                      padx=PAD, pady=(18, 0))
        row += 1
        self.form["department"] = labeled("Department", ent, row)
        row += 2
        self.form["program"] = labeled(
            "Program Name", ent, row, key="program",
            hint="Blank uses \u201cScaleOn Internship Program <year>\u201d.")
        row += 3
        self.form["grade"] = labeled("Performance Grade", ent, row,
                                     key="grade")
        row += 2
        self.form["remarks"] = labeled("Remarks", ent, row, key="remarks")
        row += 2

        # Live status strip (duration + ongoing state + certificate number).
        self.status_box = ctk.CTkFrame(card, corner_radius=12,
                                       fg_color=("#F9FAFB", "#1C1C1C"))
        self.status_box.grid(row=row, column=0, sticky="ew", padx=PAD,
                            pady=(16, 0))
        self.status_box.grid_columnconfigure(0, weight=1)
        self.status_lbl = ctk.CTkLabel(self.status_box, text="",
                                       font=_font(11, "bold"), anchor="w",
                                       justify="left")
        self.status_lbl.grid(row=0, column=0, sticky="ew", padx=12, pady=10)
        row += 1

        # Fixed-branding note (explains the absence of company fields).
        note = ctk.CTkFrame(card, corner_radius=12,
                            fg_color=("#FFFBEB", "#1F1B10"))
        note.grid(row=row, column=0, sticky="ew", padx=PAD, pady=(10, 0))
        note.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(note, text="\U0001F512  ScaleOn branding is fixed",
                     font=_font(11, "bold"), anchor="w",
                     text_color=("#92400E", "#D4AF37")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(10, 0))
        ctk.CTkLabel(note, text="Logo, tagline, watermark, seal and the HR "
                     "signature block come from the existing ScaleOn "
                     "certificate template and are never edited here.",
                     font=_font(10), anchor="w", justify="left",
                     wraplength=330,
                     text_color=("#92400E", "#A1855A")).grid(
            row=1, column=0, sticky="w", padx=12, pady=(2, 10))
        row += 1

        # Actions.
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=row, column=0, sticky="ew", padx=PAD, pady=(16, PAD))
        actions.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(actions, text="\u26a1  Generate PDF",
                      command=lambda: self._issue("pdf"), height=44,
                      corner_radius=CORNER, font=_font(13, "bold"),
                      fg_color=ACCENT, hover_color=ACCENT_HOVER).grid(
            row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self._secondary(actions, "\U0001F5BC  PNG",
                        lambda: self._issue("png"), 1, 0)
        self._secondary(actions, "\U0001F5BC  JPG",
                        lambda: self._issue("jpg"), 1, 1)
        self._secondary(actions, "\u21ba  Reset", self._reset, 2, 0)
        self._secondary(actions, "\U0001F441  Open PDF", self._open_preview,
                        2, 1)

        # Re-render the preview whenever anything changes.
        for key in ("candidate_name", "intern_id", "department", "program"):
            self.form[key].bind("<KeyRelease>",
                                lambda e, k=key: self._on_typed(k))
        # DatePicker fires on_change for typing, masking and calendar picks.
        self.form["start_date"].on_change = self._apply_duration
        self.form["end_date"].on_change = self._schedule_preview
        self.form["issue_date"].on_change = self._schedule_preview
        # Role: react to both dropdown selection and typing.
        self.form["position"].configure(
            command=lambda *_: self._schedule_preview())
        try:
            self.form["position"]._entry.bind(
                "<KeyRelease>", lambda e: self._schedule_preview(), add="+")
        except Exception:
            pass

        # Date chaining like the rest of the app.
        self.form["start_date"].next_widget = self.form["end_date"]
        self.form["end_date"].next_widget = self.form["issue_date"]

    def _secondary(self, master, text, cmd, r, col):
        ctk.CTkButton(master, text=text, command=cmd, height=40,
                      corner_radius=CORNER, font=_font(12, "bold"),
                      fg_color=("#F3F4F6", "#222222"),
                      hover_color=("#E5E7EB", "#2E2E2E"),
                      text_color=("#374151", "#D1D5DB")).grid(
            row=r, column=col, sticky="ew",
            padx=(0, 3) if col == 0 else (3, 0), pady=3)

    # ================================================================== #
    # Preview panel
    # ================================================================== #
    def _build_preview(self) -> None:
        panel = ctk.CTkFrame(self, corner_radius=18,
                             fg_color=("#FFFFFF", "#161616"), border_width=1,
                             border_color=("#ECECEC", "#242424"))
        panel.grid(row=2, column=1, sticky="nsew", padx=(12, 40), pady=(0, 24))
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(1, weight=1)

        bar = ctk.CTkFrame(panel, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(PAD, 4))
        bar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(bar, text="Live Preview", font=_font(15, "bold"),
                     anchor="w").grid(row=0, column=0, sticky="w")
        self.preview_state = ctk.CTkLabel(
            bar, text="", font=_font(11),
            text_color=("#6B7280", "#9CA3AF"))
        self.preview_state.grid(row=0, column=1, sticky="e")

        self.preview_holder = ctk.CTkFrame(panel, fg_color=("#F3F4F6", "#101010"),
                                           corner_radius=12)
        self.preview_holder.grid(row=1, column=0, sticky="nsew", padx=PAD,
                                 pady=(4, PAD))
        self.preview_holder.grid_columnconfigure(0, weight=1)
        self.preview_holder.grid_rowconfigure(0, weight=1)

        self.preview_label = ctk.CTkLabel(self.preview_holder, text="",
                                          font=_font(12))
        self.preview_label.grid(row=0, column=0)

        if not pv.AVAILABLE:
            self.preview_label.configure(
                text="Live preview needs the 'pypdfium2' package.\n\n"
                     "pip install pypdfium2\n\n"
                     "Generating and exporting PDFs still works.",
                text_color=("#6B7280", "#9CA3AF"))

        self.preview_holder.bind("<Configure>", self._on_panel_resize)

    def _on_panel_resize(self, event) -> None:
        size = (event.width, event.height)
        if abs(size[0] - self._last_size[0]) < 24 and \
                abs(size[1] - self._last_size[1]) < 24:
            return
        self._last_size = size
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(250, self._schedule_preview)

    # ================================================================== #
    # Data
    # ================================================================== #
    def _prefill(self) -> None:
        self.form["issue_date"].set(utils.format_date(datetime.now()))
        self.form["duration"].set("3 Months")
        self._apply_type_visibility()
        self._refresh_intern_id()

    def _year(self, data: Dict[str, str]) -> int:
        parsed = utils.parse_date(data.get("issue_date", ""))
        return parsed.year if parsed else datetime.now().year

    def _refresh_intern_id(self) -> None:
        """Suggest an Intern ID: the intern's existing one, else the next free."""
        if self._intern_id_touched:
            return
        name = self.form["candidate_name"].get().strip()
        suggestion = ""
        if name:
            existing = self.db.get_by_name(name)
            if existing and existing.get("intern_id"):
                suggestion = existing["intern_id"]
        if not suggestion:
            suggestion = self.db.peek_next_intern_id(
                self._year(self._collect(raw=True)))
        widget = self.form["intern_id"]
        if widget.get().strip() != suggestion:
            widget.delete(0, "end")
            widget.insert(0, suggestion)

    def _select_existing(self) -> None:
        """Fill the form from an intern already in the database."""
        import intern_directory as idir
        interns = idir.load_interns(self.db, self.store)
        if not interns:
            ModernDialog(self.app, "No Interns Yet",
                         "No interns are on record. Generate a document from "
                         "the New Letter page first.", icon="\u26a0")
            return
        idir.InternPickerDialog(
            self.app, interns, self._apply_existing, multi=False,
            title="Select an Existing Intern",
            confirm_label="\u2713  Use This Intern")

    def _apply_existing(self, records) -> None:
        if not records:
            return
        intern = records[0]

        def fill(key: str, value: str) -> None:
            widget = self.form[key]
            widget.delete(0, "end")
            widget.insert(0, value or "")

        fill("candidate_name", (intern.get("candidate_name") or "").strip())
        fill("department", intern.get("domain", ""))
        self.form["position"].set(intern.get("position", ""))
        # The whole point: keep their existing Intern ID, and stop the
        # name-based suggestion from overwriting it.
        fill("intern_id", (intern.get("intern_id") or "").strip())
        self._intern_id_touched = True
        if intern.get("start_date"):
            self.form["start_date"].set(intern["start_date"])
        if intern.get("end_date"):
            self.form["end_date"].set(intern["end_date"])
        self.form["issue_date"].set(utils.format_date(datetime.now()))

        already = ", ".join(sorted(intern.get("cert_types") or set()))
        note = (f"Loaded {intern.get('candidate_name','')} "
                f"({intern.get('intern_id','')}).")
        if already:
            note += f"\n\nAlready issued: {already}."
        ModernDialog(self.app, "Intern Loaded", note, icon="\U0001F465")
        self._schedule_preview()

    def refresh_types(self) -> None:
        """Pick up designs added or removed in the Template Designer."""
        types = doc_router.all_doc_types()
        self._custom_fields.clear()          # a design may have been edited
        try:
            self.type_menu.configure(values=types)
        except Exception:
            return
        if self.template_var.get() not in types:
            self.template_var.set(types[0])
        self._on_type_change()

    def _custom_placeholders(self, template: str) -> set:
        """Placeholders a custom design uses (cached; disk read is per name)."""
        if template in self._custom_fields:
            return self._custom_fields[template]
        used = set()
        try:
            import template_schema as ts

            design = ts.get_by_name(template)
            if design:
                used = ts.used_placeholders(design)
        except Exception:
            used = set()
        self._custom_fields[template] = used
        return used

    def _apply_type_visibility(self) -> None:
        """Show only the fields the selected document type actually uses."""
        template = self.template_var.get()
        custom = template not in doc_router.DOC_TYPES
        used = self._custom_placeholders(template) if custom else set()
        for key, applies_to in TYPE_ONLY_FIELDS.items():
            parts = self._field_rows.get(key) or []
            # For a custom design, offer the field only if the layout refers
            # to it - so a template that prints {grade} asks for a grade.
            show = (key in used) if custom else (template in applies_to)
            for widget in parts:
                if show:
                    widget.grid()
                else:
                    widget.grid_remove()

    def _on_type_change(self) -> None:
        self._apply_type_visibility()
        self._schedule_preview()

    def _on_typed(self, key: str) -> None:
        if key == "intern_id":
            self._intern_id_touched = True
        if key == "candidate_name":
            self._refresh_intern_id()
        self._schedule_preview()

    def _apply_duration(self) -> None:
        start = self.form["start_date"].get().strip()
        label = self.form["duration"].get()
        end = utils.calculate_end_date(start, label)
        if end:
            self.form["end_date"].set(end)
        self._schedule_preview()

    def _collect(self, raw: bool = False) -> Dict[str, str]:
        template = self.template_var.get()
        data = {
            "candidate_name": self.form["candidate_name"].get().strip(),
            "position": self.form["position"].get().strip(),
            "intern_id": self.form["intern_id"].get().strip(),
            "start_date": self.form["start_date"].get().strip(),
            "end_date": self.form["end_date"].get().strip(),
            "issue_date": self.form["issue_date"].get().strip(),
            "department": self.form["department"].get().strip(),
            "program": self.form["program"].get().strip(),
            "grade": self.form["grade"].get().strip(),
            "remarks": self.form["remarks"].get().strip(),
            "course": "", "college": "", "email": "",
            "template": template,
        }
        if not raw:
            # The ongoing certificate prints "3-Month Internship"; the other
            # documents use the legacy "3 Months" wording.
            data["duration"] = (
                utils.duration_phrase(data["start_date"], data["end_date"])
                if template == TEMPLATE
                else utils.duration_between(data["start_date"],
                                            data["end_date"]))
        return data

    def _reset(self) -> None:
        for key in ("candidate_name", "intern_id", "department", "program",
                    "grade", "remarks"):
            self.form[key].delete(0, "end")
        self.form["position"].set("")
        self.form["start_date"].set("")
        self.form["end_date"].set("")
        self._intern_id_touched = False
        self._prefill()
        self._schedule_preview()

    # ================================================================== #
    # Live preview
    # ================================================================== #
    def _schedule_preview(self, *_args) -> None:
        self._update_status()
        if not pv.AVAILABLE:
            return
        if self._preview_job:
            self.after_cancel(self._preview_job)
        self._preview_job = self.after(PREVIEW_DEBOUNCE_MS,
                                       self._start_preview)

    def _update_status(self) -> None:
        data = self._collect()
        template = data["template"]
        phrase = data.get("duration") or "\u2014"
        colour = ("#6B7280", "#9CA3AF")
        parts = [phrase]

        if template == TEMPLATE:
            if data["start_date"] and data["end_date"] and data["issue_date"]:
                ongoing = utils.is_internship_ongoing(data["end_date"],
                                                      data["issue_date"])
                parts.append("INTERNSHIP ONGOING" if ongoing
                             else "INTERNSHIP CONFIRMED")
                colour = "#16A34A" if ongoing else "#B45309"
            else:
                parts.append("Enter the dates")
        else:
            parts.append(template)

        text = "   \u2022   ".join(parts)
        if doc_router.is_certificate(template):
            try:
                text += ("\nNext certificate no: " +
                         self.store.peek_next_cert_no(
                             self._year(data), doc_router.cert_kind(template)))
            except Exception:
                pass

        errors = utils.validate_internship_dates(
            data["start_date"], data["end_date"], data["issue_date"])
        if errors:
            text += "\n" + "\n".join(e.lstrip("\u2022 ").strip()
                                     for e in errors)
            colour = "#DC2626"
        self.status_lbl.configure(text=text, text_color=colour)

    def _start_preview(self) -> None:
        self._preview_job = None
        # While the page is off-screen the panel has no real size, so rendering
        # would produce a throwaway thumbnail. The panel's <Configure> event
        # fires when it becomes visible and re-triggers this.
        if self.preview_holder.winfo_width() <= 1:
            return
        data = self._collect()
        if not data["candidate_name"]:
            self.preview_label.configure(
                image=None,
                text="Enter the intern's name to see the live preview.",
                text_color=("#6B7280", "#9CA3AF"))
            self._preview_image = None
            return

        # Preview must never consume a certificate number.
        data["cert_no"] = ""
        if doc_router.is_certificate(data["template"]):
            try:
                data["cert_no"] = self.store.peek_next_cert_no(
                    self._year(data), doc_router.cert_kind(data["template"]))
            except Exception:
                data["cert_no"] = ""

        # winfo_* are real device pixels; the raster is made larger than this
        # and downscaled for display, which is what keeps the preview sharp.
        width = max(self.preview_holder.winfo_width() - 24, 320)
        height = max(self.preview_holder.winfo_height() - 24, 220)
        self._seq += 1
        seq = self._seq
        self.preview_state.configure(text="rendering\u2026")

        company = dict(self.app.company.data)
        thread = threading.Thread(
            target=self._render_worker,
            args=(seq, data, company, width, height), daemon=True)
        thread.start()
        if not self._polling:
            self._polling = True
            self.after(90, self._poll_preview)

    def _render_worker(self, seq: int, data: Dict, company: Dict,
                       width: int, height: int) -> None:
        try:
            # The same router the export uses, so the preview is the real thing
            # for every document type - portrait letters included.
            doc_router.render(self._preview_pdf, company, data,
                              data["template"], BASE_DIR, self.app.pdf)
            image = pv.render_page_fit(self._preview_pdf, width, height)
            self._queue.put((seq, image, None))
        except Exception as exc:                     # pragma: no cover
            self._queue.put((seq, None, str(exc)))

    def _poll_preview(self) -> None:
        latest = None
        try:
            while True:                     # drain to the newest result
                latest = self._queue.get_nowait()
        except queue.Empty:
            pass

        if latest is not None:
            seq, image, error = latest
            if seq == self._seq:            # ignore superseded renders
                self._show_preview(image, error)
                self._polling = False
                return
        self.after(90, self._poll_preview)

    def _show_preview(self, image, error: Optional[str]) -> None:
        if error or image is None:
            self.preview_label.configure(
                image=None,
                text=error or "Preview unavailable.",
                text_color="#DC2626" if error else ("#6B7280", "#9CA3AF"))
            self._preview_image = None
            self.preview_state.configure(text="")
            return

        box_w = max(self.preview_holder.winfo_width() - 24, 320)
        box_h = max(self.preview_holder.winfo_height() - 24, 220)
        target_w, target_h = pv.fit_size(image.size, box_w, box_h)

        # CTkImage multiplies its `size` by the widget scaling factor, so the
        # size handed over must be *logical* - passing raw pixels makes
        # CustomTkinter upscale the bitmap and the preview looks soft.
        try:
            scaling = ctk.ScalingTracker.get_widget_scaling(self)
        except Exception:
            scaling = 1.0
        scaling = scaling or 1.0
        logical = (max(int(target_w / scaling), 1),
                   max(int(target_h / scaling), 1))

        self._preview_image = ctk.CTkImage(light_image=image, dark_image=image,
                                           size=logical)
        self.preview_label.configure(image=self._preview_image, text="")
        shape = ("A4 landscape" if image.size[0] >= image.size[1]
                 else "A4 portrait")
        self.preview_state.configure(
            text=f"{shape}  \u2022  rendered {image.size[0]}"
                 f"\u00d7{image.size[1]}")

    def _open_preview(self) -> None:
        """Open the current preview PDF in the system viewer."""
        data = self._collect()
        if not data["candidate_name"]:
            ModernDialog(self.app, "Nothing to Preview",
                         "Enter the intern's name first.", icon="\u26a0")
            return
        try:
            data["cert_no"] = self.store.peek_next_cert_no(
                self._year(data), "INT")
            ic.generate_internship_certificate(
                self._preview_pdf, self.app.company.data, data, BASE_DIR)
            utils.open_file(self._preview_pdf)
        except Exception as exc:
            ModernDialog(self.app, "Preview Failed", str(exc), icon="\u274c")

    # ================================================================== #
    # Validation + issue
    # ================================================================== #
    def _validate(self, data: Dict[str, str]) -> bool:
        errors = []
        required = [("candidate_name", "Intern Full Name"),
                    ("position", "Internship Role"),
                    ("intern_id", "Intern ID"),
                    ("start_date", "Internship Start Date"),
                    ("end_date", "Internship End Date"),
                    ("issue_date", "Certificate Issue Date")]
        for key, label in required:
            if not data.get(key, "").strip():
                errors.append(f"\u2022 {label} is required.")
        for key, label in (("start_date", "Internship Start Date"),
                           ("end_date", "Internship End Date"),
                           ("issue_date", "Certificate Issue Date")):
            value = data.get(key, "").strip()
            if value and utils.parse_date(value) is None:
                errors.append(f"\u2022 {label} must be DD-MM-YYYY.")
        errors.extend(utils.validate_internship_dates(
            data.get("start_date", ""), data.get("end_date", ""),
            data.get("issue_date", "")))
        if errors:
            ModernDialog(self.app, "Please Check the Form", "\n".join(errors),
                         icon="\u26a0")
            return False
        return True

    def _issue(self, fmt: str = "pdf") -> None:
        data = self._collect()
        if not self._validate(data):
            return
        if not self.app.company.is_configured():
            ModernDialog(self.app, "Company Not Configured",
                         "Please complete Company Settings first so the "
                         "ScaleOn branding is available.", icon="\U0001F3E2")
            return

        # This person already has a record: reuse it rather than creating a
        # second intern for the same name.
        existing = self._existing_person(data)
        if existing:
            def use_existing():
                data["intern_id"] = existing["intern_id"]
                self.form["intern_id"].delete(0, "end")
                self.form["intern_id"].insert(0, existing["intern_id"])
                self._intern_id_touched = True
                self._continue_issue(data, fmt)

            ModernDialog(
                self.app, "Intern Already Exists",
                f"{data['candidate_name']} is already on record as "
                f"{existing['intern_id']}"
                + (f" ({existing.get('position','')})"
                   if existing.get("position") else "") +
                f".\n\nUsing the existing record keeps one Intern ID per "
                f"person. Creating a new one makes a duplicate.",
                icon="\U0001F465",
                actions=[(f"Use {existing['intern_id']}", use_existing, True),
                         ("Create New Record",
                          lambda: self._continue_issue(data, fmt), False),
                         ("Cancel", None, False)])
            return

        self._continue_issue(data, fmt)

    def _continue_issue(self, data: Dict[str, str], fmt: str) -> None:
        # Only the ongoing-internship certificate contradicts itself once the
        # end date has passed, so only it prompts to switch. Offer letters and
        # completion certificates are unaffected.
        if data["template"] == TEMPLATE and \
                not utils.is_internship_ongoing(data["end_date"],
                                                data["issue_date"]):
            ModernDialog(
                self.app, "Internship Already Ended",
                f"The internship end date ({utils.format_long_date(data['end_date'])}) "
                f"has been reached on the issue date "
                f"({utils.format_long_date(data['issue_date'])}).\n\n"
                f"Would you like to generate the Certificate of Completion "
                f"instead?",
                icon="\U0001F4C5",
                actions=[
                    ("Generate Completion Certificate",
                     lambda: self._do_issue(data, fmt, COMPLETION_TEMPLATE),
                     True),
                    ("Keep Internship Certificate",
                     lambda: self._do_issue(data, fmt, TEMPLATE), False),
                    ("Cancel", None, False),
                ])
            return

        self._do_issue(data, fmt, data["template"])

    def _reserve_intern_id(self, entered: str, year: int) -> str:
        """Keep Intern IDs unique without stealing a manually typed one.

        The counter is only advanced when the app's own suggestion is accepted;
        an ID that already belongs to a record is simply reused.
        """
        entered = (entered or "").strip()
        if not entered:
            return self.db.next_intern_id(year)
        if self.db.get(entered):
            return entered                     # existing intern - reuse as-is
        if entered.upper() == self.db.peek_next_intern_id(year).upper():
            return self.db.next_intern_id(year)
        return entered                         # custom ID, left untouched

    def _existing_person(self, data: Dict[str, str]) -> Optional[Dict[str, str]]:
        """An intern already on record for this name under a different ID.

        Catches the case that creates duplicate people: a new Intern ID typed
        (or suggested) for someone who is already in the database.
        """
        name = data.get("candidate_name", "")
        entered = (data.get("intern_id") or "").strip()
        if entered and self.db.get(entered):
            return None                         # the ID itself is known
        existing = self.db.get_by_name(name)
        if not existing or not existing.get("intern_id"):
            return None
        if existing["intern_id"].upper() == entered.upper():
            return None
        return existing

    def _do_issue(self, data: Dict[str, str], fmt: str, template: str) -> None:
        # Don't quietly issue a second certificate of the same kind.
        import intern_directory as idir

        duplicate = idir.existing_certificate(
            self.app, (data.get("intern_id") or "").strip(), template)
        if duplicate:
            ModernDialog(
                self.app, "Certificate Already Issued",
                f"{data.get('candidate_name','')} already has a {template}:\n\n"
                f"{duplicate.get('cert_no','')}  \u2022  issued "
                f"{utils.format_long_date(duplicate.get('issue_date',''))}\n\n"
                f"Issuing another creates a second certificate number for the "
                f"same document.", icon="\u26a0",
                actions=[("Issue Another Anyway",
                          lambda: self._render_issue(data, fmt, template),
                          False),
                         ("Cancel", None, True)])
            return
        self._render_issue(data, fmt, template)

    def _render_issue(self, data: Dict[str, str], fmt: str,
                      template: str) -> None:
        data = dict(data)
        data["template"] = template
        year = self._year(data)

        data["intern_id"] = self._reserve_intern_id(data.get("intern_id", ""),
                                                    year)
        # Only certificates carry a certificate number.
        data["cert_no"] = (
            self.store.next_cert_no(year, doc_router.cert_kind(template))
            if doc_router.is_certificate(template) else "")

        folder = bg.output_dir_for(OUTPUT_DIR, template)
        base = doc_router.filename_base(template, data["candidate_name"])
        pdf_path = utils.unique_path(folder, base)

        try:
            doc_router.render(pdf_path, self.app.company.data, data, template,
                              BASE_DIR, self.app.pdf)
        except Exception as exc:
            ModernDialog(self.app, "Generation Failed", str(exc),
                         icon="\u274c")
            return

        image_path: Optional[Path] = None
        if fmt in ("png", "jpg"):
            try:
                image_path = pv.export_image(
                    pdf_path, pdf_path.with_suffix(f".{fmt}"), fmt.upper())
            except RuntimeError as exc:
                ModernDialog(self.app, "Image Export Unavailable", str(exc),
                             icon="\u26a0")
            except Exception as exc:
                ModernDialog(self.app, "Image Export Failed", str(exc),
                             icon="\u274c")

        self._record(data, pdf_path, template)
        if hasattr(self.app, "_refresh_generated"):
            self.app._refresh_generated()

        saved = pdf_path.name if image_path is None else \
            f"{pdf_path.name}\n{image_path.name}"
        details = f"Saved as {saved}\n\nIntern ID: {data['intern_id']}"
        clipboard = f"Intern ID: {data['intern_id']}"
        if data.get("cert_no"):
            details += f"\nCertificate No: {data['cert_no']}"
            clipboard += f"\nCertificate No: {data['cert_no']}"
        ModernDialog(
            self.app, f"{template} Generated", details, icon="\u2705",
            actions=[
                ("Open PDF", lambda: utils.open_file(pdf_path), True),
                ("Copy IDs",
                 lambda: copy_to_clipboard(self.app, clipboard), False),
                ("Open Folder", lambda: utils.reveal_in_folder(pdf_path),
                 False),
            ])
        self._schedule_preview()

    def _record(self, data: Dict[str, str], pdf_path: Path,
                template: str) -> None:
        """Persist the intern record, plus a certificate record when relevant."""
        ongoing = (template == TEMPLATE and
                   utils.is_internship_ongoing(data["end_date"],
                                               data["issue_date"]))
        duration = data.get("duration") or utils.duration_between(
            data["start_date"], data["end_date"])

        if doc_router.is_certificate(template):
            self.store.add_record({
                **data,
                "program": ic.program_name(data, self.app.company.data),
                "pdf_path": str(pdf_path),
                "cert_type": template,
                "status": "Internship Ongoing" if ongoing else "Completed",
            })
        self.db.add_record({
            "intern_id": data["intern_id"],
            "candidate_name": data["candidate_name"],
            "position": data["position"],
            "domain": data.get("department", ""),
            "issue_date": data["issue_date"],
            "start_date": data["start_date"],
            "end_date": data["end_date"],
            "duration": duration,
            "pdf_path": str(pdf_path),
            "status": "Internship Ongoing" if ongoing else "Generated",
        })

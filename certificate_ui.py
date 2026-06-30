"""
certificate_ui.py
-----------------
UI page for the **Certificate of Completion** feature.

Additive only: it reuses the existing shared widgets (DatePicker,
SearchableCombo, ModernDialog, design tokens) from :mod:`ui`, the existing
assets and company profile, and the existing database file (via
:class:`certificate_generator.CertificateStore`). It does not modify any
existing module.

The page mirrors the look of the Offer Letter pages: a centered form card for a
single certificate, plus a "Bulk Certificate Generation" card that imports an
Excel/CSV and generates every valid record in one click.
"""

from __future__ import annotations

import queue
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from tkinter import filedialog
from typing import Dict, List, Optional

import customtkinter as ctk

import bulk_generator as bg
import certificate_generator as cg
import utils
from settings import BASE_DIR, COMPANY_DIR, OUTPUT_DIR
from ui import (ACCENT, ACCENT_HOVER, CORNER, PAD, DatePicker, ModernDialog,
                SearchableCombo, _font)


class CertificatePage(ctk.CTkScrollableFrame):
    """Certificate of Completion generator (single + bulk)."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.store = cg.CertificateStore(COMPANY_DIR / "interns.db")
        self.bulk_rows: List[Dict[str, str]] = []
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self, text="Certificate of Completion",
                     font=_font(26, "bold"), anchor="w").grid(
            row=0, column=0, sticky="w", padx=40, pady=(30, 0))
        ctk.CTkLabel(self, text="Generate premium internship completion "
                     "certificates - individually or in bulk.", font=_font(13),
                     text_color=("#6B7280", "#9CA3AF"), anchor="w").grid(
            row=1, column=0, sticky="w", padx=40, pady=(2, 10))

        self._build_form()
        self._build_bulk()

    # ================================================================== #
    # Single certificate form
    # ================================================================== #
    def _build_form(self) -> None:
        card = ctk.CTkFrame(self, corner_radius=18,
                            fg_color=("#FFFFFF", "#161616"), border_width=1,
                            border_color=("#ECECEC", "#242424"))
        card.grid(row=2, column=0, sticky="ew", padx=40, pady=16)
        card.grid_columnconfigure(0, weight=1)
        card.grid_columnconfigure(1, weight=1)

        self.form: Dict[str, object] = {}

        left = ctk.CTkFrame(card, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew")
        left.grid_columnconfigure(0, weight=1)
        right = ctk.CTkFrame(card, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)

        def ent(m):
            return ctk.CTkEntry(m, height=40, corner_radius=CORNER, font=_font(12))

        # Left column
        self.form["candidate_name"] = self._labeled(left, "Candidate Name *", ent, 0)
        self.form["intern_id"] = self._labeled(left, "Intern ID", ent, 2)
        self.form["position"] = self._labeled(
            left, "Position *", lambda m: SearchableCombo(
                m, utils.POSITION_OPTIONS, height=40, corner_radius=CORNER,
                font=_font(12), button_color=ACCENT,
                button_hover_color=ACCENT_HOVER), 4)
        self.form["grade"] = self._labeled(
            left, "Performance Grade (optional)", ent, 6)

        # Right column
        self.form["issue_date"] = self._labeled(
            right, "Issue Date *", lambda m: DatePicker(m), 0)
        self.form["start_date"] = self._labeled(
            right, "Internship Start Date *",
            lambda m: DatePicker(m, self._recalc_duration), 2)
        self.form["end_date"] = self._labeled(
            right, "Internship End Date *",
            lambda m: DatePicker(m, self._recalc_duration), 4)
        self.form["duration"] = self._labeled(
            right, "Duration (auto)",
            lambda m: ctk.CTkEntry(m, height=40, corner_radius=CORNER,
                                   font=_font(12), state="readonly"), 6)

        self.form["issue_date"].set(utils.format_date(datetime.now()))
        self.form["issue_date"].next_widget = self.form["start_date"]
        self.form["start_date"].next_widget = self.form["end_date"]

        # Remarks (full width)
        ctk.CTkLabel(card, text="Remarks (optional)", font=_font(11, "bold"),
                     text_color=("#374151", "#D1D5DB"), anchor="w").grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=PAD, pady=(6, 2))
        self.form["remarks"] = ctk.CTkEntry(card, height=40, corner_radius=CORNER,
                                            font=_font(12))
        self.form["remarks"].grid(row=2, column=0, columnspan=2, sticky="ew",
                                  padx=PAD, pady=(0, 4))

        # Action buttons
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.grid(row=3, column=0, columnspan=2, sticky="ew",
                     padx=PAD, pady=(10, PAD))
        for i in range(3):
            actions.grid_columnconfigure(i, weight=1)
        self._btn(actions, "🎓  Generate Certificate", self._generate, 0, True)
        self._btn(actions, "👁  Preview", self._preview, 1)
        self._btn(actions, "↺  Reset", self._reset, 2)

    def _labeled(self, master, label, factory, row):
        ctk.CTkLabel(master, text=label, font=_font(11, "bold"),
                     text_color=("#374151", "#D1D5DB"), anchor="w").grid(
            row=row, column=0, sticky="ew", padx=PAD, pady=(10, 2))
        w = factory(master)
        w.grid(row=row + 1, column=0, sticky="ew", padx=PAD, pady=(0, 4))
        return w

    def _btn(self, master, text, cmd, col, primary=False):
        ctk.CTkButton(
            master, text=text, command=cmd, height=46, corner_radius=CORNER,
            font=_font(13, "bold"),
            fg_color=ACCENT if primary else ("#F3F4F6", "#222222"),
            hover_color=ACCENT_HOVER if primary else ("#E5E7EB", "#2E2E2E"),
            text_color="#FFFFFF" if primary else ("#374151", "#D1D5DB"),
        ).grid(row=0, column=col, sticky="ew", padx=5)

    def _set_duration(self, value: str) -> None:
        w = self.form["duration"]
        w.configure(state="normal")
        w.delete(0, "end")
        w.insert(0, value)
        w.configure(state="readonly")

    def _recalc_duration(self) -> None:
        dur = utils.duration_between(self.form["start_date"].get(),
                                     self.form["end_date"].get())
        self._set_duration(dur)

    def _collect(self) -> Dict[str, str]:
        return {
            "candidate_name": self.form["candidate_name"].get().strip(),
            "intern_id": self.form["intern_id"].get().strip(),
            "position": self.form["position"].get().strip(),
            "issue_date": self.form["issue_date"].get().strip(),
            "start_date": self.form["start_date"].get().strip(),
            "end_date": self.form["end_date"].get().strip(),
            "duration": self.form["duration"].get().strip(),
            "grade": self.form["grade"].get().strip(),
            "remarks": self.form["remarks"].get().strip(),
        }

    def _reset(self) -> None:
        for key in ("candidate_name", "intern_id", "grade", "remarks"):
            self.form[key].delete(0, "end")
        self.form["position"].set("")
        self.form["start_date"].set("")
        self.form["end_date"].set("")
        self.form["issue_date"].set(utils.format_date(datetime.now()))
        self._set_duration("")

    def _validate(self, data: Dict[str, str]) -> bool:
        ok, errors = utils.validate_form(data)
        if not ok:
            ModernDialog(self.app, "Please Check the Form", "\n".join(errors),
                         icon="⚠")
        return ok

    def _year(self, data: Dict[str, str]) -> int:
        parsed = utils.parse_date(data.get("issue_date", ""))
        return parsed.year if parsed else datetime.now().year

    def _generate(self) -> None:
        data = self._collect()
        if not data.get("duration"):
            data["duration"] = utils.duration_between(data["start_date"],
                                                      data["end_date"])
        if not self._validate(data):
            return
        data["cert_no"] = self.store.next_cert_no(self._year(data))
        path = utils.unique_path(
            OUTPUT_DIR, cg.build_certificate_filename(data["candidate_name"]))
        try:
            cg.generate_certificate(path, self.app.company.data, data, BASE_DIR)
        except Exception as exc:
            ModernDialog(self.app, "Generation Failed", str(exc), icon="❌")
            return
        self.store.add_record({**data, "pdf_path": str(path),
                               "status": "Completed"})
        if hasattr(self.app, "_refresh_generated"):
            self.app._refresh_generated()
        ModernDialog(
            self.app, "Certificate Generated",
            f"Saved as {path.name}\nCertificate No: {data['cert_no']}",
            icon="✅",
            actions=[("Open PDF", lambda: utils.open_file(path), True),
                     ("Open Folder", lambda: utils.reveal_in_folder(path), False),
                     ("Generate Another", self._reset, False)])

    def _preview(self) -> None:
        data = self._collect()
        if not data.get("duration"):
            data["duration"] = utils.duration_between(data["start_date"],
                                                      data["end_date"])
        if not self._validate(data):
            return
        data["cert_no"] = self.store.peek_next_cert_no(self._year(data))
        path = Path(tempfile.gettempdir()) / (
            "preview_" + cg.build_certificate_filename(data["candidate_name"])
            + ".pdf")
        try:
            cg.generate_certificate(path, self.app.company.data, data, BASE_DIR)
            utils.open_file(path)
        except Exception as exc:
            ModernDialog(self.app, "Preview Failed", str(exc), icon="❌")

    # ================================================================== #
    # Bulk certificate generation
    # ================================================================== #
    def _build_bulk(self) -> None:
        card = ctk.CTkFrame(self, corner_radius=18,
                            fg_color=("#FFFFFF", "#161616"), border_width=1,
                            border_color=("#ECECEC", "#242424"))
        card.grid(row=3, column=0, sticky="ew", padx=40, pady=(0, 30))
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card, text="Bulk Certificate Generation",
                     font=_font(15, "bold"), anchor="w").grid(
            row=0, column=0, sticky="w", padx=PAD, pady=(PAD, 0))
        ctk.CTkLabel(card, text="Import an Excel/CSV of interns "
                     "(Candidate Name · Position · Issue Date · Start Date · "
                     "End Date) and generate every certificate at once.",
                     font=_font(11), text_color=("#6B7280", "#9CA3AF"),
                     anchor="w", wraplength=800, justify="left").grid(
            row=1, column=0, sticky="w", padx=PAD, pady=(2, 8))

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=2, column=0, sticky="ew", padx=PAD, pady=(0, PAD))
        ctk.CTkButton(row, text="📥  Import Excel / CSV", command=self._bulk_import,
                      height=40, corner_radius=CORNER, font=_font(12, "bold"),
                      fg_color=("#F3F4F6", "#222222"),
                      hover_color=("#E5E7EB", "#2E2E2E"),
                      text_color=("#374151", "#D1D5DB")).pack(side="left")
        self.bulk_status = ctk.CTkLabel(row, text="No file imported.",
                                        font=_font(12),
                                        text_color=("#6B7280", "#9CA3AF"))
        self.bulk_status.pack(side="left", padx=14)
        ctk.CTkButton(row, text="🎓  Generate All", command=self._bulk_generate,
                      height=40, corner_radius=CORNER, font=_font(12, "bold"),
                      fg_color=ACCENT, hover_color=ACCENT_HOVER).pack(
            side="right")

    def _bulk_import(self) -> None:
        path = filedialog.askopenfilename(
            title="Import interns",
            filetypes=[("Spreadsheets", "*.xlsx *.csv")])
        if not path:
            return
        try:
            rows = bg.import_rows(path)
        except Exception as exc:
            ModernDialog(self.app, "Import Failed", str(exc), icon="❌")
            return
        self.bulk_rows = rows
        invalid = len(bg.validate_all(rows))
        self.bulk_status.configure(
            text=f"{len(rows)} imported  •  {len(rows) - invalid} valid  •  "
                 f"{invalid} invalid")

    def _bulk_generate(self) -> None:
        if not self.bulk_rows:
            ModernDialog(self.app, "Nothing to Generate",
                         "Import a spreadsheet first.", icon="⚠")
            return
        errors = bg.validate_all(self.bulk_rows)
        valid = [r for i, r in enumerate(self.bulk_rows) if i not in errors]
        if not valid:
            ModernDialog(self.app, "No Valid Rows",
                         "All imported rows have errors.", icon="⚠")
            return
        CertBulkProgress(self.app, valid, self.app.company.data, self.store,
                         on_done=getattr(self.app, "_refresh_generated", None))


# --------------------------------------------------------------------------- #
# Bulk progress window
# --------------------------------------------------------------------------- #
class CertBulkProgress(ctk.CTkToplevel):
    """Threaded progress window for bulk certificate generation."""

    def __init__(self, master, rows, company, store, on_done=None):
        super().__init__(master)
        self.title("Generating Certificates")
        self.resizable(False, False)
        self.geometry("420x250")
        self.configure(fg_color=("#FFFFFF", "#161616"))
        self.transient(master)
        self.grab_set()

        self.rows = rows
        self.company = company
        self.store = store
        self.on_done = on_done
        self.total = len(rows)
        self.queue: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.folder = OUTPUT_DIR / datetime.now().strftime("%Y-%m-%d") / "Certificates"

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=26, pady=22)
        self._running_view()

        threading.Thread(target=self._run, daemon=True).start()
        self.after(80, self._poll)
        self.update_idletasks()
        self.geometry(f"+{master.winfo_rootx()+ (master.winfo_width()-420)//2}"
                      f"+{master.winfo_rooty()+ (master.winfo_height()-250)//2}")

    def _running_view(self) -> None:
        for w in self.body.winfo_children():
            w.destroy()
        ctk.CTkLabel(self.body, text="🎓", font=_font(34)).pack(pady=(2, 4))
        ctk.CTkLabel(self.body, text="Generating certificates…",
                     font=_font(16, "bold")).pack()
        self.count = ctk.CTkLabel(self.body, text=f"0 / {self.total} completed",
                                  font=_font(12),
                                  text_color=("#6B7280", "#9CA3AF"))
        self.count.pack(pady=(2, 12))
        self.bar = ctk.CTkProgressBar(self.body, height=14, corner_radius=8,
                                      progress_color=ACCENT)
        self.bar.set(0)
        self.bar.pack(fill="x")
        ctk.CTkButton(self.body, text="Cancel", command=self.cancel.set,
                      height=36, corner_radius=CORNER, font=_font(12, "bold"),
                      fg_color=("#F3F4F6", "#222222"),
                      hover_color=("#E5E7EB", "#2E2E2E"),
                      text_color=("#374151", "#D1D5DB")).pack(pady=(16, 0))

    def _run(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        ok = fail = 0
        for i, row in enumerate(self.rows):
            if self.cancel.is_set():
                break
            try:
                issue = row.get("issue_date", "")
                parsed = utils.parse_date(issue)
                year = parsed.year if parsed else datetime.now().year
                data = {
                    "candidate_name": row.get("candidate_name", ""),
                    "intern_id": "",
                    "position": row.get("position", ""),
                    "issue_date": issue,
                    "start_date": row.get("start_date", ""),
                    "end_date": row.get("end_date", ""),
                    "duration": utils.duration_between(
                        row.get("start_date", ""), row.get("end_date", "")),
                    "grade": "", "remarks": "",
                    "cert_no": self.store.next_cert_no(year),
                }
                path = utils.unique_path(
                    self.folder,
                    cg.build_certificate_filename(data["candidate_name"]))
                cg.generate_certificate(path, self.company, data, BASE_DIR)
                self.store.add_record({**data, "pdf_path": str(path),
                                       "status": "Completed"})
                ok += 1
            except Exception:
                fail += 1
            self.queue.put(("p", i + 1, ok, fail))
        self.queue.put(("done", ok, fail))

    def _poll(self) -> None:
        try:
            while True:
                msg = self.queue.get_nowait()
                if msg[0] == "p":
                    done = msg[1]
                    self.bar.set(done / self.total if self.total else 0)
                    self.count.configure(text=f"{done} / {self.total} completed")
                elif msg[0] == "done":
                    self._done_view(msg[1], msg[2])
                    if self.on_done:
                        self.on_done()
                    return
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def _done_view(self, ok: int, fail: int) -> None:
        for w in self.body.winfo_children():
            w.destroy()
        ctk.CTkLabel(self.body, text="✅", font=_font(38)).pack(pady=(0, 4))
        ctk.CTkLabel(self.body, text="Certificates Generated",
                     font=_font(17, "bold")).pack()
        ctk.CTkLabel(self.body, text=f"{ok} created     •     {fail} failed",
                     font=_font(13),
                     text_color=("#4B5563", "#9CA3AF")).pack(pady=(6, 16))
        btns = ctk.CTkFrame(self.body, fg_color="transparent")
        btns.pack()
        ctk.CTkButton(btns, text="Open Folder",
                      command=lambda: utils.open_file(self.folder), height=38,
                      width=120, corner_radius=CORNER, font=_font(12, "bold"),
                      fg_color=ACCENT, hover_color=ACCENT_HOVER).pack(
            side="left", padx=4)
        ctk.CTkButton(btns, text="Close", command=self.destroy, height=38,
                      width=120, corner_radius=CORNER, font=_font(12, "bold"),
                      fg_color=("#F3F4F6", "#222222"),
                      hover_color=("#E5E7EB", "#2E2E2E"),
                      text_color=("#374151", "#D1D5DB")).pack(side="left", padx=4)

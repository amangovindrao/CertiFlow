"""
bulk_ui.py
----------
CustomTkinter page for the enterprise Bulk Generator.

Wired into the main window by ``ui.OfferLetterApp``.  It owns:
    * an Excel/CSV import + editable validation table (ttk.Treeview for speed),
    * search / sort / filter and inline row editing,
    * a threaded generation run with a live progress window (ETA + cancel),
    * a completion report with open-folder / export-report / generate-again.

All heavy lifting (parsing, validation, PDF generation, logging) lives in
``bulk_generator`` so this file only deals with presentation and threading.
"""

from __future__ import annotations

import queue
import threading
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Dict, List, Optional

import customtkinter as ctk

import bulk_generator as bg
import utils
from pdf_generator import TEMPLATE_LABELS
from settings import OUTPUT_DIR
# Shared design tokens / widgets from the main UI module (already imported by
# the time this page is constructed, so no circular import at runtime).
from ui import (ACCENT, ACCENT_HOVER, CORNER, PAD, DatePicker, ModernDialog,
                SearchableCombo, _font)


class BulkGeneratorPage(ctk.CTkFrame):
    """The Bulk Generator screen."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.rows: List[Dict[str, str]] = []
        self.errors: Dict[int, List[str]] = {}
        self.sort_col: Optional[str] = None
        self.sort_asc = True

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        self._build_header()
        self._build_toolbar()
        self._build_quick_add()
        self._build_table()
        self._build_statusbar()

    # ------------------------------------------------------------------ #
    # Layout
    # ------------------------------------------------------------------ #
    def _build_header(self) -> None:
        ctk.CTkLabel(self, text="Bulk Generator", font=_font(26, "bold"),
                     anchor="w").grid(row=0, column=0, sticky="w",
                                      padx=40, pady=(30, 0))
        ctk.CTkLabel(self, text="Import a spreadsheet or add candidates manually "
                     "below, review and fix rows, then generate every letter at "
                     "once.", font=_font(13),
                     text_color=("#6B7280", "#9CA3AF"), anchor="w").grid(
            row=1, column=0, sticky="w", padx=40, pady=(2, 10))

    def _build_toolbar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=40, pady=(0, 8))
        bar.grid_columnconfigure(6, weight=1)

        def tbtn(text, cmd, col, primary=False):
            ctk.CTkButton(
                bar, text=text, command=cmd, height=38, corner_radius=CORNER,
                font=_font(12, "bold"), width=10,
                fg_color=ACCENT if primary else ("#F3F4F6", "#222222"),
                hover_color=ACCENT_HOVER if primary else ("#E5E7EB", "#2E2E2E"),
                text_color="#FFFFFF" if primary else ("#374151", "#D1D5DB"),
            ).grid(row=0, column=col, padx=(0, 6), sticky="w")

        tbtn("📥  Import Excel / CSV", self._import, 0, primary=True)
        tbtn("☑  Select All", self._select_all, 1)
        tbtn("🗑  Delete", self._delete_selected, 2)

        # Template selector (which document to generate for the batch).
        self.template_var = ctk.StringVar(value=TEMPLATE_LABELS[0])
        ctk.CTkOptionMenu(
            bar, values=TEMPLATE_LABELS, variable=self.template_var,
            height=38, width=180, corner_radius=CORNER, font=_font(12),
            fg_color=ACCENT, button_color=ACCENT, button_hover_color=ACCENT_HOVER,
        ).grid(row=0, column=3, padx=(0, 6))

        # Filter
        self.filter_var = ctk.StringVar(value="All")
        ctk.CTkOptionMenu(
            bar, values=["All", "Valid", "Invalid"], variable=self.filter_var,
            command=lambda *_: self._render_table(), height=38, width=110,
            corner_radius=CORNER, font=_font(12), fg_color=("#F3F4F6", "#222222"),
            button_color=ACCENT, button_hover_color=ACCENT_HOVER,
            text_color=("#374151", "#D1D5DB"),
        ).grid(row=0, column=4, padx=(0, 6))

        # Search
        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._render_table())
        ctk.CTkEntry(bar, textvariable=self.search_var, height=38, width=200,
                     corner_radius=CORNER, font=_font(12),
                     placeholder_text="🔍  Search…").grid(
            row=0, column=5, padx=(0, 6))

        ctk.CTkButton(
            bar, text="⚡  Generate All", command=self._generate_all, height=38,
            corner_radius=CORNER, font=_font(13, "bold"), fg_color=ACCENT,
            hover_color=ACCENT_HOVER,
        ).grid(row=0, column=7, sticky="e")

    def _build_quick_add(self) -> None:
        """Inline form to type a candidate and add it straight to the list."""
        frame = ctk.CTkFrame(self, corner_radius=14,
                             fg_color=("#FFFFFF", "#161616"), border_width=1,
                             border_color=("#ECECEC", "#242424"))
        frame.grid(row=3, column=0, sticky="ew", padx=40, pady=(0, 8))
        for c in range(4):
            frame.grid_columnconfigure(c, weight=1)

        ctk.CTkLabel(frame, text="➕  Add Candidate Manually",
                     font=_font(12, "bold"), anchor="w").grid(
            row=0, column=0, columnspan=4, sticky="w", padx=PAD, pady=(10, 0))

        def cell(r, c, label, make):
            box = ctk.CTkFrame(frame, fg_color="transparent")
            box.grid(row=r, column=c, sticky="ew",
                     padx=(PAD if c == 0 else 6, PAD if c == 3 else 6), pady=4)
            box.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(box, text=label, font=_font(10, "bold"),
                         text_color=("#6B7280", "#9CA3AF"), anchor="w").grid(
                row=0, column=0, sticky="ew")
            w = make(box)
            w.grid(row=1, column=0, sticky="ew")
            return w

        def ent(m):
            return ctk.CTkEntry(m, height=36, corner_radius=CORNER, font=_font(11))

        self.qa_name = cell(1, 0, "Candidate Name", ent)
        self.qa_position = cell(
            1, 1, "Position", lambda m: SearchableCombo(
                m, utils.POSITION_OPTIONS, height=36, corner_radius=CORNER,
                font=_font(11), button_color=ACCENT,
                button_hover_color=ACCENT_HOVER))
        self.qa_issue = cell(1, 2, "Issue Date", lambda m: DatePicker(m))

        self.qa_start = cell(2, 0, "Start Date",
                             lambda m: DatePicker(m, self._qa_auto_end))
        self.qa_duration = cell(
            2, 1, "Duration", lambda m: ctk.CTkOptionMenu(
                m, values=list(utils.DURATION_OPTIONS.keys()), height=36,
                corner_radius=CORNER, font=_font(11), fg_color=ACCENT,
                button_color=ACCENT, button_hover_color=ACCENT_HOVER,
                command=lambda *_: self._qa_auto_end()))
        self.qa_end = cell(2, 2, "End Date", lambda m: DatePicker(m))

        # Add button (aligned with the field row).
        btn_box = ctk.CTkFrame(frame, fg_color="transparent")
        btn_box.grid(row=2, column=3, sticky="ew", padx=(6, PAD), pady=4)
        btn_box.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(btn_box, text=" ", font=_font(10)).grid(row=0, column=0)
        ctk.CTkButton(btn_box, text="Add to List", command=self._qa_add,
                      height=36, corner_radius=CORNER, font=_font(11, "bold"),
                      fg_color=ACCENT, hover_color=ACCENT_HOVER).grid(
            row=1, column=0, sticky="ew")

        # Sensible defaults + date-field focus chaining.
        self.qa_issue.set(utils.format_date(datetime.now()))
        self.qa_duration.set("1 Month")
        self.qa_issue.next_widget = self.qa_start
        self.qa_start.next_widget = self.qa_end

    def _qa_auto_end(self) -> None:
        end = utils.calculate_end_date(self.qa_start.get(),
                                       self.qa_duration.get())
        if end:
            self.qa_end.set(end)

    def _qa_add(self) -> None:
        row = {
            "candidate_name": self.qa_name.get().strip(),
            "position": self.qa_position.get().strip(),
            "domain": "",
            "issue_date": self.qa_issue.get().strip(),
            "start_date": self.qa_start.get().strip(),
            "end_date": self.qa_end.get().strip(),
        }
        if not row["candidate_name"]:
            ModernDialog(self.app, "Name Required",
                         "Please enter a candidate name before adding.",
                         icon="⚠")
            return
        self.rows.append(row)
        self._recompute()
        self._render_table()
        # Clear name for the next entry; keep dates/position/duration.
        self.qa_name.delete(0, "end")
        self.qa_name.focus_set()
        last = str(len(self.rows) - 1)
        if self.tree.exists(last):
            self.tree.see(last)
            self.tree.selection_set(last)

    def _build_table(self) -> None:
        container = ctk.CTkFrame(self, corner_radius=14,
                                 fg_color=("#FFFFFF", "#161616"),
                                 border_width=1,
                                 border_color=("#ECECEC", "#242424"))
        container.grid(row=4, column=0, sticky="nsew", padx=40, pady=(0, 6))
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        self._style_treeview()

        columns = [f for f, _ in bg.DISPLAY_COLUMNS]
        self.tree = ttk.Treeview(container, columns=columns, show="headings",
                                 selectmode="extended", style="Bulk.Treeview")
        for field_name, label in bg.DISPLAY_COLUMNS:
            self.tree.heading(field_name, text=label,
                              command=lambda c=field_name: self._sort_by(c))
            self.tree.column(field_name, width=150, anchor="w")

        self.tree.tag_configure("invalid", background="#FDE2E1",
                                foreground="#B91C1C")
        self.tree.tag_configure("valid", background="#FFFFFF")

        vsb = ttk.Scrollbar(container, orient="vertical",
                            command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        vsb.grid(row=0, column=1, sticky="ns", pady=6, padx=(0, 6))

        self.tree.bind("<Double-1>", self._on_double_click)

    def _style_treeview(self) -> None:
        dark = ctk.get_appearance_mode() == "Dark"
        bgc = "#161616" if dark else "#FFFFFF"
        fgc = "#E5E7EB" if dark else "#1F2933"
        head_bg = "#222222" if dark else "#F3F4F6"
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Bulk.Treeview", background=bgc, fieldbackground=bgc,
                        foreground=fgc, rowheight=30, borderwidth=0,
                        font=("Segoe UI", 10))
        style.configure("Bulk.Treeview.Heading", background=head_bg,
                        foreground=fgc, font=("Segoe UI", 10, "bold"),
                        relief="flat")
        style.map("Bulk.Treeview", background=[("selected", ACCENT)],
                  foreground=[("selected", "#FFFFFF")])
        # Re-apply valid tag bg for dark mode readability.
        if hasattr(self, "tree"):
            self.tree.tag_configure("valid", background=bgc, foreground=fgc)
            self.tree.tag_configure(
                "invalid", background="#5B1A1A" if dark else "#FDE2E1",
                foreground="#FCA5A5" if dark else "#B91C1C")

    def _build_statusbar(self) -> None:
        self.status = ctk.CTkLabel(self, text="No data imported yet.",
                                   font=_font(12),
                                   text_color=("#6B7280", "#9CA3AF"), anchor="w")
        self.status.grid(row=5, column=0, sticky="w", padx=40, pady=(0, 24))

    # ------------------------------------------------------------------ #
    # Data operations
    # ------------------------------------------------------------------ #
    def _recompute(self) -> None:
        self.errors = bg.validate_all(self.rows)

    def _render_table(self) -> None:
        self.tree.delete(*self.tree.get_children())
        term = self.search_var.get().lower().strip()
        mode = self.filter_var.get()

        view = list(enumerate(self.rows))
        if term:
            view = [(i, r) for i, r in view
                    if any(term in str(v).lower() for v in r.values())]
        if mode == "Valid":
            view = [(i, r) for i, r in view if i not in self.errors]
        elif mode == "Invalid":
            view = [(i, r) for i, r in view if i in self.errors]
        if self.sort_col:
            view.sort(key=lambda ir: str(ir[1].get(self.sort_col, "")).lower(),
                      reverse=not self.sort_asc)

        for idx, row in view:
            tag = "invalid" if idx in self.errors else "valid"
            values = [row.get(f, "") for f, _ in bg.DISPLAY_COLUMNS]
            self.tree.insert("", "end", iid=str(idx), values=values, tags=(tag,))

        self._update_status()

    def _update_status(self) -> None:
        total = len(self.rows)
        invalid = len(self.errors)
        valid = total - invalid
        if total == 0:
            self.status.configure(text="No data imported yet.")
            return
        msg = f"{total} rows  •  {valid} valid  •  {invalid} invalid"
        if invalid:
            msg += "   —  fix the highlighted rows before generating"
        self.status.configure(text=msg)

    def _sort_by(self, col: str) -> None:
        if self.sort_col == col:
            self.sort_asc = not self.sort_asc
        else:
            self.sort_col, self.sort_asc = col, True
        self._render_table()

    # ------------------------------------------------------------------ #
    # Toolbar actions
    # ------------------------------------------------------------------ #
    def _import(self) -> None:
        path = filedialog.askopenfilename(
            title="Import candidates",
            filetypes=[("Spreadsheets", "*.xlsx *.csv"), ("Excel", "*.xlsx"),
                       ("CSV", "*.csv")])
        if not path:
            return
        try:
            rows = bg.import_rows(path)
        except Exception as exc:
            ModernDialog(self.app, "Import Failed", str(exc), icon="❌")
            return
        if not rows:
            ModernDialog(self.app, "Nothing Imported",
                         "No candidate rows were found in that file.", icon="⚠")
            return
        self.rows = rows
        self._recompute()
        self._render_table()
        ModernDialog(self.app, "Import Complete",
                     f"Imported {len(rows)} candidate rows.", icon="✅")

    def _add_row(self) -> None:
        self.rows.append({f: "" for f, _ in bg.DISPLAY_COLUMNS})
        self._recompute()
        self._render_table()
        last = str(len(self.rows) - 1)
        if self.tree.exists(last):
            self.tree.see(last)
            self.tree.selection_set(last)

    def _select_all(self) -> None:
        """Select every visible row (for bulk delete / review)."""
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children)

    def _delete_selected(self) -> None:
        selected = self.tree.selection()
        if not selected:
            ModernDialog(self.app, "No Selection",
                         "Select one or more rows to delete (use Select All to "
                         "select everything).", icon="⚠")
            return
        indices = {int(iid) for iid in selected}

        def do_delete():
            self.rows = [r for i, r in enumerate(self.rows) if i not in indices]
            self._recompute()
            self._render_table()

        # Confirm when removing several at once.
        if len(indices) > 1:
            ModernDialog(
                self.app, "Delete Rows?",
                f"Remove {len(indices)} selected candidates from the list?",
                icon="🗑",
                actions=[("Delete", do_delete, True), ("Cancel", None, False)])
        else:
            do_delete()

    def _on_double_click(self, event) -> None:
        """Inline-edit the double-clicked cell with a floating entry."""
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        iid = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)  # like '#3'
        if not iid or not col:
            return
        col_index = int(col[1:]) - 1
        field_name = bg.DISPLAY_COLUMNS[col_index][0]
        x, y, w, h = self.tree.bbox(iid, col)

        editor = ttk.Entry(self.tree)
        editor.insert(0, self.rows[int(iid)].get(field_name, ""))
        editor.select_range(0, "end")
        editor.focus_set()
        editor.place(x=x, y=y, width=w, height=h)

        def commit(_=None):
            value = editor.get().strip()
            # Normalise date columns on commit for convenience.
            if field_name in ("issue_date", "start_date", "end_date") and value:
                value = bg._norm_date(value)
            self.rows[int(iid)][field_name] = value
            editor.destroy()
            self._recompute()
            self._render_table()

        editor.bind("<Return>", commit)
        editor.bind("<FocusOut>", commit)
        editor.bind("<Escape>", lambda e: editor.destroy())

    # ------------------------------------------------------------------ #
    # Generation
    # ------------------------------------------------------------------ #
    def _generate_all(self) -> None:
        if not self.rows:
            ModernDialog(self.app, "Nothing to Generate",
                         "Import or add some candidate rows first.", icon="⚠")
            return
        if not self.app.company.is_configured():
            ModernDialog(self.app, "Company Not Configured",
                         "Please complete Company Settings first.", icon="🏢")
            return
        self._recompute()
        if self.errors:
            ModernDialog(
                self.app, "Fix Invalid Rows",
                f"{len(self.errors)} row(s) have errors and are highlighted. "
                f"Use the 'Invalid' filter to review and fix them.", icon="⚠")
            self.filter_var.set("Invalid")
            self._render_table()
            return

        ProgressWindow(self.app, list(self.rows), self.app.company.data,
                       self.template_var.get(), on_done=self._after_run)

    def _after_run(self) -> None:
        # Refresh the Generated Letters page so new files show up.
        if hasattr(self.app, "_refresh_generated"):
            self.app._refresh_generated()


# --------------------------------------------------------------------------- #
# Progress + completion window
# --------------------------------------------------------------------------- #
class ProgressWindow(ctk.CTkToplevel):
    """Modal window that runs the batch in a thread and shows live progress."""

    def __init__(self, master, rows, company, template_label, on_done=None):
        super().__init__(master)
        self.title("Generating Letters")
        self.resizable(False, False)
        self.geometry("440x300")
        self.configure(fg_color=("#FFFFFF", "#161616"))
        self.transient(master)
        self.grab_set()

        self.on_done = on_done
        self.total = len(rows)
        self.started = datetime.now()
        self.queue: queue.Queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.report: Optional[bg.BulkReport] = None

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=28, pady=24)
        self._build_running_view()

        # Kick off the worker thread.
        generator = bg.BulkGenerator(master.pdf.base_dir, OUTPUT_DIR,
                                     db=getattr(master, "db", None))
        self.worker = threading.Thread(
            target=self._run, args=(generator, rows, company, template_label),
            daemon=True)
        self.worker.start()
        self.after(80, self._poll)
        self._center(master)

    # -- views ----------------------------------------------------------- #
    def _build_running_view(self) -> None:
        for w in self.body.winfo_children():
            w.destroy()
        ctk.CTkLabel(self.body, text="⚙", font=_font(34)).pack(pady=(4, 4))
        self.title_lbl = ctk.CTkLabel(self.body, text="Generating…",
                                      font=_font(17, "bold"))
        self.title_lbl.pack()
        self.count_lbl = ctk.CTkLabel(self.body, text=f"0 / {self.total} completed",
                                      font=_font(13),
                                      text_color=("#6B7280", "#9CA3AF"))
        self.count_lbl.pack(pady=(2, 12))

        self.bar = ctk.CTkProgressBar(self.body, height=14, corner_radius=8,
                                      progress_color=ACCENT)
        self.bar.set(0)
        self.bar.pack(fill="x")

        self.eta_lbl = ctk.CTkLabel(self.body, text="Estimating time…",
                                    font=_font(11),
                                    text_color=("#9CA3AF", "#6B7280"))
        self.eta_lbl.pack(pady=(10, 16))

        ctk.CTkButton(self.body, text="Cancel", command=self._cancel,
                      height=38, corner_radius=CORNER, font=_font(12, "bold"),
                      fg_color=("#F3F4F6", "#222222"),
                      hover_color=("#E5E7EB", "#2E2E2E"),
                      text_color=("#374151", "#D1D5DB")).pack()

    def _build_done_view(self) -> None:
        report = self.report
        for w in self.body.winfo_children():
            w.destroy()
        cancelled = report.cancelled if report else False
        icon = "🛑" if cancelled else "✅"
        ctk.CTkLabel(self.body, text=icon, font=_font(40)).pack(pady=(0, 4))
        head = "Cancelled" if cancelled else "Generation Complete"
        ctk.CTkLabel(self.body, text=head, font=_font(18, "bold")).pack()

        ok = report.success_count if report else 0
        fail = report.fail_count if report else 0
        secs = report.seconds if report else 0
        ctk.CTkLabel(
            self.body,
            text=f"{ok} PDFs Generated     •     {fail} Failed\n"
                 f"Completed in {secs:.1f}s",
            font=_font(13), justify="center",
            text_color=("#4B5563", "#9CA3AF")).pack(pady=(8, 16))

        btns = ctk.CTkFrame(self.body, fg_color="transparent")
        btns.pack()

        def b(text, cmd, primary=False):
            ctk.CTkButton(
                btns, text=text, command=cmd, height=38, width=120,
                corner_radius=CORNER, font=_font(12, "bold"),
                fg_color=ACCENT if primary else ("#F3F4F6", "#222222"),
                hover_color=ACCENT_HOVER if primary else ("#E5E7EB", "#2E2E2E"),
                text_color="#FFFFFF" if primary else ("#374151", "#D1D5DB"),
            ).pack(side="left", padx=4)

        folder = report.folder if report and report.folder else str(OUTPUT_DIR)
        b("Open Folder", lambda: utils.open_file(folder), primary=True)
        b("Export Report", self._export_report)
        b("Generate Again", self.destroy)

    # -- worker / polling ------------------------------------------------- #
    def _run(self, generator, rows, company, template_label) -> None:
        def progress(done, total, name):
            self.queue.put(("progress", done, total, name))

        report = generator.generate(rows, company, template_label,
                                    progress_cb=progress,
                                    cancel_event=self.cancel_event)
        self.queue.put(("done", report))

    def _poll(self) -> None:
        try:
            while True:
                msg = self.queue.get_nowait()
                if msg[0] == "progress":
                    self._on_progress(msg[1], msg[2], msg[3])
                elif msg[0] == "done":
                    self.report = msg[1]
                    self._build_done_view()
                    if self.on_done:
                        self.on_done()
                    return
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def _on_progress(self, done: int, total: int, name: str) -> None:
        self.bar.set(done / total if total else 0)
        self.count_lbl.configure(text=f"{done} / {total} completed")
        elapsed = (datetime.now() - self.started).total_seconds()
        if done:
            remaining = elapsed / done * (total - done)
            mm, ss = divmod(int(remaining), 60)
            self.eta_lbl.configure(
                text=f"{name}  •  about {mm:02d}:{ss:02d} remaining")

    def _cancel(self) -> None:
        self.cancel_event.set()
        self.title_lbl.configure(text="Cancelling…")

    def _export_report(self) -> None:
        if not self.report:
            return
        dest = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile="generation_report.csv",
            filetypes=[("CSV", "*.csv")])
        if dest:
            self.report.export_csv(Path(dest))
            ModernDialog(self, "Report Exported",
                         f"Saved to {Path(dest).name}", icon="📄")

    def _center(self, master) -> None:
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        x = master.winfo_rootx() + (master.winfo_width() - w) // 2
        y = master.winfo_rooty() + (master.winfo_height() - h) // 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

"""
backup_page.py
--------------
The Backup & Restore screen: export everything, bring it back, move it to
another computer.

Four actions, matching the four things people actually want to do:

    📦  Export / Create Backup    archive everything into one .zip
    📥  Import Backup             bring a .zip in from somewhere else
    ♻  Restore Data              apply a backup from the list
    📂  Open Backup Folder        find the files in Explorer

The list underneath shows every backup found in the ``Backup & Restore`` folder
with what each one holds, so restoring never means guessing which file is which.

All of the actual work lives in :mod:`data_transfer`; this module is only the
screen. Long operations run on a worker thread with a progress dialog, because
archiving a few hundred PDFs takes long enough to freeze the window otherwise.
"""

from __future__ import annotations

import threading
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Callable, Dict, List, Optional

import customtkinter as ctk

import data_transfer as dt
import settings
import utils
from intern_directory import style_tree
from ui import (ACCENT, ACCENT_HOVER, CORNER, PAD, ModernDialog, _font,
                copy_button)


def _mb(value: int) -> str:
    size = value / 1024 / 1024
    if size < 0.1:
        return f"{value / 1024:.0f} KB"
    return f"{size:.1f} MB"


class ProgressDialog(ctk.CTkToplevel):
    """Modal progress window for a background archive or restore."""

    def __init__(self, master, title: str):
        super().__init__(master)
        self.title(title)
        self.geometry("440x160")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", lambda: None)   # no cancel mid-write

        ctk.CTkLabel(self, text=title, font=_font(15, "bold")).pack(
            pady=(24, 6))
        self.detail = ctk.CTkLabel(self, text="Preparing\u2026",
                                   font=_font(11),
                                   text_color=("#6B7280", "#9CA3AF"))
        self.detail.pack()
        self.bar = ctk.CTkProgressBar(self, width=360, height=10,
                                      progress_color=ACCENT)
        self.bar.set(0)
        self.bar.pack(pady=(14, 20))
        self.update_idletasks()

    def step(self, done: int, total: int, label: str) -> None:
        if total > 0:
            self.bar.set(min(done / total, 1.0))
        text = str(label or "")
        self.detail.configure(text=text[:58] + ("\u2026" if len(text) > 58
                                                else ""))


class BackupPage(ctk.CTkFrame):
    """Backup, restore and migrate every piece of local data."""

    COLUMNS = [
        ("when", "Created", 130),
        ("name", "File", 250),
        ("contents", "Contents", 250),
        ("version", "Version", 70),
        ("size", "Size", 80),
    ]

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.backups: List[dt.BackupInfo] = []
        self.selected: Optional[dt.BackupInfo] = None
        self.include_output = ctk.BooleanVar(value=True)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(4, weight=1)

        self._build_header()
        self._build_state()
        self._build_actions()
        self._build_list()
        self.refresh()

    # ------------------------------------------------------------------ #
    # Layout
    # ------------------------------------------------------------------ #
    def _build_header(self) -> None:
        ctk.CTkLabel(self, text="Backup & Restore", font=_font(26, "bold"),
                     anchor="w").grid(row=0, column=0, sticky="w", padx=40,
                                      pady=(30, 0))
        ctk.CTkLabel(
            self,
            text="Save everything to one file, restore it with one click, or "
                 "move it to another computer. Nothing you have generated is "
                 "ever lost when upgrading or reinstalling.",
            font=_font(13), text_color=("#6B7280", "#9CA3AF"), anchor="w",
            justify="left", wraplength=900).grid(row=1, column=0, sticky="w",
                                                 padx=40, pady=(2, 10))

    def _build_state(self) -> None:
        card = ctk.CTkFrame(self, corner_radius=14,
                            fg_color=("#FFFFFF", "#161616"), border_width=1,
                            border_color=("#ECECEC", "#242424"))
        card.grid(row=2, column=0, sticky="ew", padx=40, pady=(0, 8))
        for column in range(4):
            card.grid_columnconfigure(column, weight=1)

        ctk.CTkLabel(card, text="What is on this computer right now",
                     font=_font(12, "bold"), anchor="w").grid(
            row=0, column=0, columnspan=4, sticky="w", padx=PAD, pady=(12, 6))

        self.state_labels: Dict[str, ctk.CTkLabel] = {}
        for index, (key, caption) in enumerate((
                ("interns", "Interns"),
                ("certificates", "Certificates"),
                ("documents", "Generated PDFs"),
                ("templates", "Saved Designs"))):
            box = ctk.CTkFrame(card, fg_color="transparent")
            box.grid(row=1, column=index, sticky="w", padx=PAD, pady=(0, 4))
            value = ctk.CTkLabel(box, text="0", font=_font(20, "bold"),
                                 anchor="w")
            value.pack(anchor="w")
            ctk.CTkLabel(box, text=caption, font=_font(11),
                         text_color=("#6B7280", "#9CA3AF"),
                         anchor="w").pack(anchor="w")
            self.state_labels[key] = value

        self.folder_label = ctk.CTkLabel(
            card, text="", font=_font(10), anchor="w",
            text_color=("#9CA3AF", "#6B7280"), justify="left")
        self.folder_label.grid(row=2, column=0, columnspan=3, sticky="w",
                               padx=PAD, pady=(0, 12))
        copy_button(card, lambda: str(dt.backup_root()), width=30, height=26,
                    tooltip="Copy the backup folder path").grid(
            row=2, column=3, sticky="e", padx=PAD, pady=(0, 12))

    def _build_actions(self) -> None:
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=3, column=0, sticky="ew", padx=40, pady=(0, 8))
        bar.grid_columnconfigure(5, weight=1)

        def button(text, command, column, primary=False, width=190):
            ctk.CTkButton(
                bar, text=text, command=command, height=40, width=width,
                corner_radius=CORNER, font=_font(12, "bold"),
                fg_color=ACCENT if primary else ("#F3F4F6", "#222222"),
                hover_color=ACCENT_HOVER if primary else ("#E5E7EB", "#2E2E2E"),
                text_color="#FFFFFF" if primary else ("#374151", "#D1D5DB"),
            ).grid(row=0, column=column, padx=(0, 8))

        button("\U0001F4E6  Export / Create Backup", self._export, 0,
               primary=True, width=215)
        button("\U0001F4E5  Import Backup", self._import, 1, width=165)
        self.restore_btn = ctk.CTkButton(
            bar, text="\u267b  Restore Data", command=self._restore_selected,
            height=40, width=155, corner_radius=CORNER, font=_font(12, "bold"),
            fg_color=("#F3F4F6", "#222222"),
            hover_color=("#DBEAFE", "#1E3A5F"),
            text_color=("#1D4ED8", "#93C5FD"), state="disabled")
        self.restore_btn.grid(row=0, column=2, padx=(0, 8))
        button("\U0001F4C2  Open Backup Folder", self._open_folder, 3,
               width=190)
        self.delete_btn = ctk.CTkButton(
            bar, text="\U0001F5D1", command=self._delete_selected, height=40,
            width=46, corner_radius=CORNER, font=_font(12, "bold"),
            fg_color=("#F3F4F6", "#222222"),
            hover_color=("#FEE2E2", "#3F1D1D"),
            text_color=("#B91C1C", "#FCA5A5"), state="disabled")
        self.delete_btn.grid(row=0, column=4, padx=(0, 8))

        ctk.CTkCheckBox(
            bar, text="Include generated PDFs", variable=self.include_output,
            font=_font(11), checkbox_width=18, checkbox_height=18,
            corner_radius=5, fg_color=ACCENT, hover_color=ACCENT_HOVER).grid(
            row=0, column=5, sticky="e")

    def _build_list(self) -> None:
        card = ctk.CTkFrame(self, corner_radius=14,
                            fg_color=("#FFFFFF", "#161616"), border_width=1,
                            border_color=("#ECECEC", "#242424"))
        card.grid(row=4, column=0, sticky="nsew", padx=40, pady=(0, 24))
        card.grid_rowconfigure(1, weight=1)
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card, text="Available backups", font=_font(12, "bold"),
                     anchor="w").grid(row=0, column=0, columnspan=2, sticky="w",
                                      padx=PAD, pady=(12, 6))

        style_tree("Backup.Treeview")
        self.tree = ttk.Treeview(card, columns=[c for c, _, _ in self.COLUMNS],
                                 show="headings", selectmode="browse",
                                 style="Backup.Treeview")
        for field, label, width in self.COLUMNS:
            self.tree.heading(field, text=label)
            self.tree.column(field, width=width, anchor="w")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda _e: self._restore_selected())

        scroll = ttk.Scrollbar(card, orient="vertical",
                               command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=1, column=0, sticky="nsew", padx=(6, 0), pady=(0, 6))
        scroll.grid(row=1, column=1, sticky="ns", padx=(0, 6), pady=(0, 6))

        self.status = ctk.CTkLabel(card, text="", font=_font(11), anchor="w",
                                   text_color=("#6B7280", "#9CA3AF"))
        self.status.grid(row=2, column=0, columnspan=2, sticky="w", padx=12,
                         pady=(0, 10))

    # ------------------------------------------------------------------ #
    # Data
    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        state = dt.current_state()
        for key, label in self.state_labels.items():
            label.configure(text=str(state.get(key, 0)))
        self.folder_label.configure(
            text=f"Backups are kept in:  {dt.backup_root()}")

        try:
            self.backups = dt.list_backups()
        except Exception:
            self.backups = []
        self._render()

    def _render(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, info in enumerate(self.backups):
            self.tree.insert("", "end", iid=str(index), values=[
                info.when, info.name, info.summary(),
                info.app_version or "\u2014", _mb(info.size_bytes)])
        total = sum(i.size_bytes for i in self.backups)
        if self.backups:
            self.status.configure(
                text=f"{len(self.backups)} backup(s) \u00b7 {_mb(total)} total"
                     f"   \u2022   double-click one to restore it")
        else:
            self.status.configure(
                text="No backups yet. Click \U0001F4E6 Export / Create Backup "
                     "to make your first one.")
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        has = self.selected is not None
        self.restore_btn.configure(state="normal" if has else "disabled")
        self.delete_btn.configure(state="normal" if has else "disabled")

    def _on_select(self, _event=None) -> None:
        selection = self.tree.selection()
        self.selected = (self.backups[int(selection[0])] if selection
                         else None)
        self._sync_buttons()

    # ------------------------------------------------------------------ #
    # Background work
    # ------------------------------------------------------------------ #
    def _run_with_progress(self, title: str, work: Callable,
                           done: Callable) -> None:
        """Run ``work(progress_cb)`` off the UI thread, then call ``done``."""
        dialog = ProgressDialog(self.app, title)
        outcome: Dict[str, object] = {}

        def report(current: int, total: int, label: str) -> None:
            self.after(0, lambda: dialog.step(current, total, label))

        def runner() -> None:
            try:
                outcome["value"] = work(report)
            except Exception as exc:                      # noqa: BLE001
                outcome["error"] = exc

            def finish() -> None:
                try:
                    dialog.grab_release()
                    dialog.destroy()
                except Exception:
                    pass
                done(outcome)

            self.after(0, finish)

        threading.Thread(target=runner, daemon=True).start()

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #
    def _export(self) -> None:
        """Archive everything into the Backup & Restore folder."""
        state = dt.current_state()
        include = bool(self.include_output.get())
        target = dt.backup_root() / dt.suggested_name()

        def work(report):
            return dt.create_backup(target, include_output=include,
                                    progress=report)

        def done(outcome):
            error = outcome.get("error")
            if error:
                ModernDialog(self.app, "Backup Failed", str(error),
                             icon="\u274c")
                return
            info: dt.BackupInfo = outcome["value"]      # type: ignore
            self.refresh()
            detail = (f"{info.name}\n\n{info.summary()}\n"
                      f"{info.file_count} file(s) \u00b7 {_mb(info.size_bytes)}")
            if not include:
                detail += ("\n\nGenerated PDFs were left out, so this restores "
                           "records but not the document files.")
            detail += ("\n\nCopy this single file to another computer and use "
                       "\U0001F4E5 Import Backup there \u2014 nothing else is "
                       "needed.")
            ModernDialog(
                self.app, "Backup Created", detail, icon="\U0001F4E6",
                actions=[("Open Backup Folder",
                          lambda: utils.reveal_in_folder(info.path), True),
                         ("Close", None, False)])

        summary = (f"{state['interns']} intern(s), "
                   f"{state['certificates']} certificate(s), "
                   f"{state['documents']} PDF(s)")
        if include and state["output_bytes"] > 50 * 1024 * 1024:
            ModernDialog(
                self.app, "Create Backup?",
                f"{summary}\n\nThe generated PDFs alone are "
                f"{_mb(state['output_bytes'])}, so this will take a moment and "
                f"produce a large file.\n\nUntick \u201cInclude generated "
                f"PDFs\u201d for a much smaller records-only backup.",
                icon="\U0001F4E6",
                actions=[("Create Backup",
                          lambda: self._run_with_progress(
                              "Creating backup", work, done), True),
                         ("Cancel", None, False)])
            return
        self._run_with_progress("Creating backup", work, done)

    def _import(self) -> None:
        """Bring a backup in from anywhere, then offer to restore it."""
        picked = filedialog.askopenfilename(
            title="Import a CertiFlow backup",
            initialdir=str(dt.backup_root()),
            filetypes=[("CertiFlow backup", "*.zip"), ("All files", "*.*")])
        if not picked:
            return
        source = Path(picked)
        try:
            info = dt.inspect(source, verify=False)
        except Exception as exc:
            ModernDialog(self.app, "Not a CertiFlow Backup", str(exc),
                         icon="\u274c")
            return

        # Copy it into the backup folder so it is kept with the others.
        target = dt.backup_root() / source.name
        copied = False
        if source.parent.resolve() != dt.backup_root().resolve():
            try:
                if target.exists():
                    target = dt.backup_root() / dt.suggested_name(
                        label="imported")
                import shutil

                shutil.copy2(source, target)
                copied = True
            except OSError:
                target = source
        else:
            target = source

        self.refresh()
        info = dt.inspect(target, verify=False)
        note = ("\n\nCopied into your Backup & Restore folder."
                if copied else "")
        ModernDialog(
            self.app, "Backup Imported",
            f"{info.name}\n\n{info.summary()}\nCreated {info.when}"
            f"{' on ' + info.created_on if info.created_on else ''}"
            f"{note}\n\nRestore it now?", icon="\U0001F4E5",
            actions=[("Restore Data", lambda: self._restore(info), True),
                     ("Later", None, False)])

    def _restore_selected(self) -> None:
        if self.selected is None:
            ModernDialog(self.app, "Nothing Selected",
                         "Pick a backup from the list first, or use "
                         "\U0001F4E5 Import Backup to bring one in.",
                         icon="\u26a0")
            return
        self._restore(self.selected)

    def _restore(self, info: dt.BackupInfo) -> None:
        """Confirm and apply a restore, choosing merge or replace."""
        state = dt.current_state()
        empty = dt.is_empty()
        include = bool(self.include_output.get())

        def go(mode: str):
            def start():
                def work(report):
                    return dt.restore(info.path, mode=mode,
                                      db=self.app.db,
                                      cert_store=self.app.cert_store,
                                      include_output=include,
                                      progress=report)

                self._run_with_progress(
                    "Restoring data", work,
                    lambda outcome: self._after_restore(outcome, info, mode))
            return start

        header = (f"{info.name}\n{info.summary()}\nCreated {info.when}"
                  f"{' on ' + info.created_on if info.created_on else ''}")

        if empty:
            # Nothing to lose: the obviously right choice should not be scary.
            ModernDialog(
                self.app, "Restore Everything?",
                f"{header}\n\nThere is no data on this computer yet, so this "
                f"backup will be restored in full.\n\nIntern IDs and "
                f"certificate numbers are preserved exactly, so certificates "
                f"you have already handed out keep verifying.", icon="\u267b",
                actions=[("Restore Everything", go("replace"), True),
                         ("Cancel", None, False)])
            return

        ModernDialog(
            self.app, "Restore Data",
            f"{header}\n\nThis computer already has {state['interns']} "
            f"intern(s) and {state['certificates']} certificate(s).\n\n"
            f"\u2022  Merge \u2014 keeps everything you have and adds only "
            f"what is missing. Nothing is overwritten.\n"
            f"\u2022  Replace \u2014 discards the current data and restores "
            f"this backup instead.\n\n"
            f"Either way your database is snapshotted first, so this can be "
            f"undone from company\\backups.", icon="\u267b",
            actions=[("Merge (recommended)", go("merge"), True),
                     ("Replace Everything", go("replace"), False),
                     ("Cancel", None, False)])

    def _after_restore(self, outcome: Dict, info: dt.BackupInfo,
                       mode: str) -> None:
        error = outcome.get("error")
        if error:
            ModernDialog(self.app, "Restore Failed", str(error), icon="\u274c")
            return
        result: dt.RestoreResult = outcome["value"]      # type: ignore

        lines = []
        if result.interns_added:
            lines.append(f"{result.interns_added} intern(s) restored")
        if result.certs_added:
            lines.append(f"{result.certs_added} certificate(s) restored")
        if result.files_copied:
            lines.append(f"{result.files_copied} file(s) copied")
        if result.templates_copied:
            lines.append(f"{result.templates_copied} saved design(s)")
        if result.company_restored:
            lines.append("company profile restored")
        if result.files_skipped:
            lines.append(f"{result.files_skipped} already present, left alone")
        detail = "\n".join(lines) or "Everything in the backup was already here."
        for note in result.notes:
            detail += f"\n\n{note}"
        if result.snapshot:
            detail += (f"\n\nYour previous database was snapshotted to:\n"
                       f"{result.snapshot.name}")

        self.refresh()
        # Bring every page back in sync with the restored data.
        for key in ("interns", "create", "bulk", "generated", "designer"):
            page = self.app.pages.get(key)
            for method in ("refresh", "refresh_types", "_refresh_generated"):
                if page is not None and hasattr(page, method):
                    try:
                        getattr(page, method)()
                    except Exception:
                        pass
        if hasattr(self.app, "_refresh_generated"):
            try:
                self.app._refresh_generated()
            except Exception:
                pass
        if hasattr(self.app, "refresh_document_types"):
            try:
                self.app.refresh_document_types()
            except Exception:
                pass

        if result.restart_required:
            ModernDialog(
                self.app, "Restore Complete \u2014 Restart Needed",
                f"{detail}\n\nThe database file was replaced, so CertiFlow "
                f"has to restart to open it.", icon="\u2705",
                actions=[("Close CertiFlow", self._quit, True)])
            return

        ModernDialog(self.app, "Restore Complete", detail, icon="\u2705",
                     actions=[("View Interns",
                               lambda: self.app.show_page("interns"), True),
                              ("Close", None, False)])

    def _quit(self) -> None:
        """Close the app so a replaced database is picked up on next launch."""
        try:
            self.app.db.close()
        except Exception:
            pass
        try:
            self.app.cert_store.close()
        except Exception:
            pass
        self.app.destroy()

    def _open_folder(self) -> None:
        folder = dt.backup_root()
        try:
            utils.reveal_in_folder(folder)
        except Exception:
            ModernDialog(self.app, "Could Not Open Folder", str(folder),
                         icon="\u26a0")

    def _delete_selected(self) -> None:
        info = self.selected
        if info is None:
            return

        def go():
            if dt.delete_backup(info.path):
                self.selected = None
                self.refresh()
            else:
                ModernDialog(self.app, "Could Not Delete",
                             f"{info.name} is in use or read-only.",
                             icon="\u26a0")

        ModernDialog(
            self.app, "Delete This Backup?",
            f"{info.name}\n{info.summary()}\n\nOnly the backup file is "
            f"deleted \u2014 your current data is not touched.\n\nThis cannot "
            f"be undone.", icon="\U0001F5D1",
            actions=[("Delete Backup", go, True), ("Cancel", None, False)])

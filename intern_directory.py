"""
intern_directory.py
-------------------
Everything about *existing* interns in one place:

    * :func:`load_interns`        - joins the intern records with every
                                    certificate issued to them,
    * :func:`issue_for_intern`    - generates another document for a specific
                                    intern, reusing their exact Intern ID,
    * :class:`InternPickerDialog` - the shared searchable multi/single select
                                    dialog used by the certificate page and the
                                    bulk generator,
    * :class:`InternDirectoryPage` - the Interns page: who exists, how many
                                    certificates they have, which ones, and a
                                    one-click way to issue another.

Interns are always keyed on **Intern ID**, never on name: two different people
can share a name (and do, in real data), so name keying would attach one
person's certificates to another.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Callable, Dict, List, Optional

import customtkinter as ctk

import backup
import bulk_generator as bg
import certificate_generator as cg
import doc_router
import intern_io
import internship_certificate as ic
import utils
from settings import BASE_DIR, OUTPUT_DIR, resolve_path
from ui import (ACCENT, ACCENT_HOVER, CORNER, PAD, ModernDialog, _font,
                copy_button)

# Short labels for the certificate summary column.
_SHORT_TYPES = {
    "Internship Certificate": "Internship",
    "Completion Certificate": "Completion",
}

# Certificates issued before the ``cert_type`` column existed were all
# completion certificates, so that is the correct label for a blank value.
LEGACY_CERT_TYPE = "Completion Certificate"


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
def cert_type_label(record: Dict[str, str]) -> str:
    """Certificate type, filling in the type for pre-existing records."""
    return (record.get("cert_type") or "").strip() or LEGACY_CERT_TYPE


def _summarise_types(certificates: List[Dict[str, str]]) -> str:
    """``"1 Internship, 2 Completion"`` for the list column."""
    counts: Dict[str, int] = {}
    for cert in certificates:
        label = cert_type_label(cert)
        key = _SHORT_TYPES.get(label, label)
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        return "\u2014"
    return ", ".join(f"{n} {name}" for name, n in sorted(counts.items()))


def load_interns(db, cert_store) -> List[Dict]:
    """Return every intern with their certificates attached (newest first).

    Certificates are matched on Intern ID. A certificate is additionally
    matched by name when its Intern ID does not belong to any current intern -
    either because it predates Intern IDs on certificates, or because the
    record it pointed at was a duplicate that has since been removed. That way
    cleaning up duplicates never makes certificate history disappear.
    """
    records = db.search("")
    known_ids = {(r.get("intern_id") or "").strip().upper()
                 for r in records if r.get("intern_id")}

    by_id: Dict[str, List[Dict]] = {}
    orphans_by_name: Dict[str, List[Dict]] = {}
    for cert in cert_store.all_records():
        intern_id = (cert.get("intern_id") or "").strip()
        if intern_id and intern_id.upper() in known_ids:
            by_id.setdefault(intern_id.upper(), []).append(cert)
        else:
            key = utils.normalize_name(cert.get("candidate_name", ""))
            if key:
                orphans_by_name.setdefault(key, []).append(cert)

    interns: List[Dict] = []
    for record in records:
        intern_id = (record.get("intern_id") or "").strip()
        name = (record.get("candidate_name") or "").strip()
        certificates = list(by_id.get(intern_id.upper(), []))
        certificates += orphans_by_name.get(utils.normalize_name(name), [])
        certificates.sort(key=lambda c: c.get("created_at") or "", reverse=True)
        interns.append({
            **record,
            "certificates": certificates,
            "cert_count": len(certificates),
            "cert_summary": _summarise_types(certificates),
            "cert_types": {cert_type_label(c) for c in certificates},
        })
    return interns


def summarise(interns: List[Dict]) -> Dict[str, int]:
    """Headline counts for the stats strip."""
    total_certs = sum(i["cert_count"] for i in interns)
    internship = completion = 0
    for intern in interns:
        for cert in intern["certificates"]:
            if cert_type_label(cert) == "Internship Certificate":
                internship += 1
            else:
                completion += 1
    return {
        "interns": len(interns),
        "certificates": total_certs,
        "internship": internship,
        "completion": completion,
        "with_certs": sum(1 for i in interns if i["cert_count"]),
    }


def intern_to_row(intern: Dict) -> Dict[str, str]:
    """Convert an intern record into a Bulk Generator table row."""
    return {
        "candidate_name": (intern.get("candidate_name") or "").strip(),
        "position": intern.get("position", ""),
        "domain": intern.get("domain", ""),
        # A fresh issue date - the stored one belongs to a previous document.
        "issue_date": utils.format_date(datetime.now()),
        "start_date": intern.get("start_date", ""),
        "end_date": intern.get("end_date", ""),
        "intern_id": (intern.get("intern_id") or "").strip(),
    }


# --------------------------------------------------------------------------- #
# Issuing a document for one existing intern
# --------------------------------------------------------------------------- #
def snapshot(app, reason: str) -> Optional[Path]:
    """Back up the database before a destructive change.

    Never raises and never blocks the action - a missing snapshot is worse than
    no snapshot, but not worth failing the user's request over.
    """
    try:
        path = getattr(app, "db_path", None) or app.db.db_path
        return backup.create_backup(Path(path), reason)
    except Exception:
        return None


def existing_certificate(app, intern_id: str, template: str
                         ) -> Optional[Dict[str, str]]:
    """A certificate of ``template`` already issued to this intern, if any."""
    if not doc_router.is_certificate(template):
        return None
    try:
        return app.cert_store.cert_of_type(intern_id, template)
    except Exception:
        return None


def issue_for_intern(app, intern: Dict, template: str,
                     issue_date: str = "") -> Path:
    """Generate ``template`` for ``intern``, keeping their exact Intern ID.

    Deliberately does not reuse the New Letter page's Intern ID resolution:
    that matches by name, which would pick the wrong person when two interns
    share a name.
    """
    intern_id = (intern.get("intern_id") or "").strip()
    name = (intern.get("candidate_name") or "").strip()
    start = intern.get("start_date", "")
    end = intern.get("end_date", "")
    issue = issue_date or utils.format_date(datetime.now())
    parsed = utils.parse_date(issue)
    year = parsed.year if parsed else datetime.now().year

    data: Dict[str, str] = {
        "candidate_name": name,
        "position": intern.get("position", ""),
        "department": intern.get("domain", ""),
        "course": "", "college": "", "email": "",
        "intern_id": intern_id,
        "issue_date": issue,
        "start_date": start,
        "end_date": end,
        "program": "",
        "template": template,
    }

    if template == "Internship Certificate":
        data["duration"] = utils.duration_phrase(start, end)
    else:
        data["duration"] = (intern.get("duration", "")
                            or utils.duration_between(start, end))

    if doc_router.is_certificate(template):
        data["cert_no"] = app.cert_store.next_cert_no(
            year, doc_router.cert_kind(template))

    folder = bg.output_dir_for(OUTPUT_DIR, template)
    path = utils.unique_path(folder, doc_router.filename_base(template, name))

    doc_router.render(path, app.company.data, data, template, BASE_DIR, app.pdf)

    ongoing = (template == "Internship Certificate"
               and utils.is_internship_ongoing(end, issue))
    status = "Internship Ongoing" if ongoing else "Generated"

    if doc_router.is_certificate(template):
        app.cert_store.add_record({
            **data,
            "program": ic.program_name(data, app.company.data),
            "pdf_path": str(path),
            "cert_type": template,
            "status": "Internship Ongoing" if ongoing else "Completed",
        })

    app.db.add_record({
        "intern_id": intern_id,
        "candidate_name": name,
        "position": data["position"],
        "domain": data["department"],
        "issue_date": issue,
        "start_date": start,
        "end_date": end,
        "duration": data["duration"],
        "pdf_path": str(path),
        "status": status,
    })
    return path


# --------------------------------------------------------------------------- #
# Correcting an Intern ID (including on certificates already issued)
# --------------------------------------------------------------------------- #
def regenerate_certificate(app, cert: Dict, intern_id: Optional[str] = None
                           ) -> Path:
    """Re-render an already-issued certificate from its stored record.

    The certificate **number is preserved** - this reissues the same document,
    it does not create a new one. ``intern_id`` overrides the stored ID, which
    is how a wrong Intern ID gets corrected on a PDF that was already handed
    out. The existing file is overwritten when it is still on disk.
    """
    template = cert_type_label(cert)
    data: Dict[str, str] = {
        "candidate_name": (cert.get("candidate_name") or "").strip(),
        "position": cert.get("position", "") or "",
        "department": cert.get("department", "") or "",
        "intern_id": (intern_id if intern_id is not None
                      else cert.get("intern_id", "")) or "",
        "issue_date": cert.get("issue_date", "") or "",
        "start_date": cert.get("start_date", "") or "",
        "end_date": cert.get("end_date", "") or "",
        "duration": cert.get("duration", "") or "",
        "grade": cert.get("grade", "") or "",
        "remarks": cert.get("remarks", "") or "",
        "program": cert.get("program", "") or "",
        "cert_no": cert.get("cert_no", "") or "",
        "course": "", "college": "", "email": "",
        "template": template,
    }

    stored = cert.get("pdf_path") or ""
    existing = resolve_path(stored) if stored else Path("")
    if existing.name and existing.parent.exists():
        path = existing                      # replace the file in place
    else:
        folder = bg.output_dir_for(OUTPUT_DIR, template)
        name = data["candidate_name"]
        if template == "Internship Certificate":
            base = ic.build_internship_certificate_filename(name)
        else:
            base = cg.build_certificate_filename(name)
        path = utils.unique_path(folder, base)

    doc_router.render(path, app.company.data, data, template, BASE_DIR, app.pdf)
    # cert_no is the primary key, so this updates the existing row rather than
    # adding one; created_at is preserved by add_record.
    app.cert_store.add_record({**cert, **data, "pdf_path": str(path)})
    return path


def change_intern_id(app, intern: Dict, new_id: str,
                     regenerate: bool = True) -> Dict:
    """Correct an intern's ID everywhere, optionally reissuing their PDFs.

    Returns ``{"old", "new", "certificates", "regenerated", "failed"}``.
    """
    old_id = (intern.get("intern_id") or "").strip()
    new_id = (new_id or "").strip()

    snapshot(app, "before-id-change")
    app.db.change_intern_id(old_id, new_id)          # raises on a clash
    moved = app.cert_store.reassign_intern(old_id, new_id)

    regenerated: List[str] = []
    failed: List[str] = []
    if regenerate:
        for cert in app.cert_store.certs_for_intern(new_id):
            try:
                regenerate_certificate(app, cert, intern_id=new_id)
                regenerated.append(cert.get("cert_no", ""))
            except Exception:
                failed.append(cert.get("cert_no", ""))
    return {"old": old_id, "new": new_id, "certificates": moved,
            "regenerated": regenerated, "failed": failed}


# --------------------------------------------------------------------------- #
# Deleting interns
# --------------------------------------------------------------------------- #
def _intern_id_key(intern_id: str):
    """Sort Intern IDs by their numeric tail (SO260009 before SO260027)."""
    digits = re.findall(r"\d+", str(intern_id or ""))
    return (int(digits[-1]) if digits else 0, str(intern_id or ""))


def duplicate_groups(db) -> List[Dict]:
    """Duplicate intern records grouped by person.

    Each entry is ``{"name", "primary", "extras"}`` where *primary* is the
    record to keep (the oldest / first-assigned Intern ID) and *extras* are the
    redundant copies.
    """
    groups = []
    for records in db.find_duplicate_groups():
        primary, extras = records[0], records[1:]
        groups.append({
            "name": (primary.get("candidate_name") or "").strip(),
            "primary": primary,
            "extras": extras,
        })
    groups.sort(key=lambda g: g["name"].casefold())
    return groups


def remove_duplicates(app, groups: List[Dict]) -> Dict[str, int]:
    """Delete the redundant copies in ``groups``, keeping each primary.

    Certificate records are deliberately kept: they stay verifiable, and
    :func:`load_interns` re-attaches them to the surviving record by name, so
    no certificate history is lost.
    """
    snapshot(app, "before-dedupe")
    extras = [record for group in groups for record in group["extras"]]
    result = delete_interns(app, extras, with_certificates=False)
    result["groups"] = len(groups)
    return result


def delete_interns(app, interns: List[Dict],
                   with_certificates: bool) -> Dict[str, int]:
    """Delete intern records, optionally their certificate records too.

    Generated PDF files on disk are never touched - the documents themselves
    are the deliverable, and the Generated Letters page already manages files.

    Returns ``{"interns": n, "certificates": n}``.
    """
    snapshot(app, "before-delete")
    removed_interns = removed_certs = 0
    for intern in interns:
        intern_id = (intern.get("intern_id") or "").strip()
        if not intern_id:
            continue
        if with_certificates:
            removed_certs += app.cert_store.delete_for_intern(intern_id)
        if app.db.delete(intern_id):
            removed_interns += 1
    return {"interns": removed_interns, "certificates": removed_certs}


# --------------------------------------------------------------------------- #
# Shared picker dialog
# --------------------------------------------------------------------------- #
def style_tree(style_name: str) -> None:
    """Apply the app's Treeview styling for ``style_name`` (theme aware)."""
    dark = ctk.get_appearance_mode() == "Dark"
    bgc = "#161616" if dark else "#FFFFFF"
    fgc = "#E5E7EB" if dark else "#1F2933"
    head_bg = "#222222" if dark else "#F3F4F6"
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure(style_name, background=bgc, fieldbackground=bgc,
                    foreground=fgc, rowheight=30, borderwidth=0,
                    font=("Segoe UI", 10))
    style.configure(f"{style_name}.Heading", background=head_bg,
                    foreground=fgc, font=("Segoe UI", 10, "bold"),
                    relief="flat")
    style.map(style_name, background=[("selected", ACCENT)],
              foreground=[("selected", "#FFFFFF")])


class ChangeInternIdDialog(ctk.CTkToplevel):
    """Ask for a corrected Intern ID and whether to reissue the PDFs."""

    def __init__(self, master, intern: Dict, suggestion: str, on_apply):
        super().__init__(master)
        self.title("Change Intern ID")
        self.resizable(False, False)
        self.configure(fg_color=("#FFFFFF", "#1A1A1A"))
        self.transient(master)

        self.intern = intern
        self.on_apply = on_apply
        self.certificates = intern.get("certificates", [])
        old_id = (intern.get("intern_id") or "").strip()

        wrap = ctk.CTkFrame(self, fg_color="transparent")
        wrap.pack(padx=28, pady=24, fill="both", expand=True)
        wrap.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(wrap, text="\U0001FAAA", font=_font(30)).grid(row=0,
                                                                  column=0)
        ctk.CTkLabel(wrap, text="Change Intern ID",
                     font=_font(17, "bold")).grid(row=1, column=0)
        ctk.CTkLabel(
            wrap,
            text=f"{intern.get('candidate_name', '')}\ncurrently {old_id}",
            font=_font(12), justify="center",
            text_color=("#4B5563", "#9CA3AF")).grid(row=2, column=0,
                                                    pady=(6, 14))

        ctk.CTkLabel(wrap, text="New Intern ID", font=_font(11, "bold"),
                     anchor="w", text_color=("#374151", "#D1D5DB")).grid(
            row=3, column=0, sticky="w")
        self.entry = ctk.CTkEntry(wrap, height=40, width=300,
                                  corner_radius=CORNER, font=_font(13))
        self.entry.grid(row=4, column=0, sticky="ew", pady=(2, 2))
        self.entry.insert(0, old_id)
        self.entry.select_range(0, "end")
        self.entry.focus_set()
        self.entry.bind("<Return>", lambda e: self._apply())

        ctk.CTkLabel(wrap, text=f"Next unused ID is {suggestion}",
                     font=_font(10), text_color=("#9CA3AF", "#6B7280"),
                     anchor="w").grid(row=5, column=0, sticky="w")

        self.regen_var = ctk.BooleanVar(value=bool(self.certificates))
        if self.certificates:
            box = ctk.CTkFrame(wrap, corner_radius=12,
                               fg_color=("#F9FAFB", "#1C1C1C"))
            box.grid(row=6, column=0, sticky="ew", pady=(12, 0))
            box.grid_columnconfigure(0, weight=1)
            ctk.CTkCheckBox(
                box, text=f"Re-create the {len(self.certificates)} certificate "
                          f"PDF(s) with the corrected ID",
                variable=self.regen_var, font=_font(11),
                checkbox_width=18, checkbox_height=18,
                fg_color=ACCENT, hover_color=ACCENT_HOVER).grid(
                row=0, column=0, sticky="w", padx=12, pady=(10, 2))
            numbers = ", ".join(c.get("cert_no", "")
                                for c in self.certificates[:4])
            if len(self.certificates) > 4:
                numbers += f", +{len(self.certificates) - 4} more"
            ctk.CTkLabel(
                box, text=f"Certificate numbers stay the same ({numbers}). "
                          f"The PDF files are rewritten in place.",
                font=_font(10), anchor="w", justify="left", wraplength=300,
                text_color=("#6B7280", "#9CA3AF")).grid(
                row=1, column=0, sticky="w", padx=12, pady=(0, 10))

        self.error = ctk.CTkLabel(wrap, text="", font=_font(11),
                                  text_color="#DC2626", wraplength=300,
                                  justify="left", anchor="w")
        self.error.grid(row=7, column=0, sticky="w", pady=(8, 0))

        buttons = ctk.CTkFrame(wrap, fg_color="transparent")
        buttons.grid(row=8, column=0, pady=(14, 0))
        ctk.CTkButton(buttons, text="Cancel", command=self.destroy, width=130,
                      height=38, corner_radius=CORNER, font=_font(12, "bold"),
                      fg_color="transparent", border_width=1,
                      border_color=("#D1D5DB", "#3A3A3A"),
                      hover_color=("#E5E7EB", "#2A2A2A"),
                      text_color=("#374151", "#D1D5DB")).pack(side="left",
                                                              padx=6)
        ctk.CTkButton(buttons, text="Save Changes", command=self._apply,
                      width=150, height=38, corner_radius=CORNER,
                      font=_font(12, "bold"), fg_color=ACCENT,
                      hover_color=ACCENT_HOVER).pack(side="left", padx=6)

        self.grab_set()
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _apply(self) -> None:
        new_id = self.entry.get().strip().upper()
        old_id = (self.intern.get("intern_id") or "").strip()
        if not new_id:
            self.error.configure(text="Enter an Intern ID.")
            return
        if new_id == old_id.upper():
            self.error.configure(text="That is already this intern's ID.")
            return
        regenerate = bool(self.regen_var.get())
        self.destroy()
        self.on_apply(new_id, regenerate)


class InternPickerDialog(ctk.CTkToplevel):
    """Searchable list of existing interns, single or multi select.

    Hands the caller the full intern records (including their certificates) so
    the stored Intern ID and details can be reused rather than re-entered.
    """

    COLUMNS = [
        ("intern_id", "Intern ID", 100),
        ("candidate_name", "Name", 180),
        ("position", "Role", 165),
        ("start_date", "Start", 90),
        ("end_date", "End", 90),
        ("cert_summary", "Certificates", 140),
    ]

    def __init__(self, master, interns: List[Dict],
                 on_select: Callable[[List[Dict]], None], multi: bool = True,
                 title: str = "Select Existing Interns",
                 confirm_label: str = "\u2795  Add Selected"):
        super().__init__(master)
        self.title(title)
        self.geometry("860x560")
        self.minsize(680, 430)
        self.configure(fg_color=("#FFFFFF", "#161616"))
        self.transient(master)

        self.interns = interns
        self.on_select = on_select
        self.multi = multi
        self.confirm_label = confirm_label
        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._render())
        self.only_without = ctk.BooleanVar(value=False)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        self._build_header(title)
        self._build_table()
        self._build_footer()
        self._render()

        self.grab_set()
        self._center(master)

    # -- layout ---------------------------------------------------------- #
    def _build_header(self, title: str) -> None:
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 0))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text=title, font=_font(19, "bold"),
                     anchor="w").grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(head, text=f"{len(self.interns)} interns in database",
                     font=_font(12),
                     text_color=("#6B7280", "#9CA3AF")).grid(row=0, column=1,
                                                             sticky="e")
        hint = ("Their Intern ID and saved details come along automatically."
                if not self.multi else
                "Ctrl+click or Shift+click to select several. Their Intern IDs "
                "come along automatically.")
        ctk.CTkLabel(head, text=hint, font=_font(12), anchor="w",
                     justify="left",
                     text_color=("#6B7280", "#9CA3AF")).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=24, pady=(12, 8))
        bar.grid_columnconfigure(0, weight=1)
        search_box = ctk.CTkFrame(bar, fg_color="transparent")
        search_box.grid(row=0, column=0, sticky="ew")
        search_box.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(search_box, text="\U0001F50D", font=_font(14),
                     text_color=("#6B7280", "#9CA3AF")).grid(row=0, column=0,
                                                             padx=(2, 6))
        # placeholder_text is ignored once a textvariable is bound, hence the
        # separate icon + caption.
        entry = ctk.CTkEntry(search_box, textvariable=self.search_var,
                             height=38, corner_radius=CORNER, font=_font(12))
        entry.grid(row=0, column=1, sticky="ew")
        entry.focus_set()

        col = 1
        ctk.CTkSwitch(bar, text="Without certificates only",
                      variable=self.only_without, onvalue=True, offvalue=False,
                      command=self._render, font=_font(11),
                      progress_color=ACCENT).grid(row=0, column=col,
                                                  padx=(12, 0))
        col += 1
        if self.multi:
            for label, cmd in (("\u2611  All", self._select_all),
                               ("Clear", self._clear)):
                ctk.CTkButton(
                    bar, text=label, command=cmd, height=38, width=88,
                    corner_radius=CORNER, font=_font(12, "bold"),
                    fg_color=("#F3F4F6", "#222222"),
                    hover_color=("#E5E7EB", "#2E2E2E"),
                    text_color=("#374151", "#D1D5DB"),
                ).grid(row=0, column=col, padx=(8, 0))
                col += 1

    def _build_table(self) -> None:
        container = ctk.CTkFrame(self, corner_radius=14,
                                 fg_color=("#FFFFFF", "#161616"),
                                 border_width=1,
                                 border_color=("#ECECEC", "#242424"))
        container.grid(row=2, column=0, sticky="nsew", padx=24)
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        style_tree("Picker.Treeview")
        self.tree = ttk.Treeview(
            container, columns=[c for c, _, _ in self.COLUMNS],
            show="headings", style="Picker.Treeview",
            selectmode="extended" if self.multi else "browse")
        for field, label, width in self.COLUMNS:
            self.tree.heading(field, text=label)
            self.tree.column(field, width=width, anchor="w")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._update_status())
        self.tree.bind("<Double-1>", lambda e: self._confirm())

        vsb = ttk.Scrollbar(container, orient="vertical",
                            command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        vsb.grid(row=0, column=1, sticky="ns", pady=6, padx=(0, 6))

    def _build_footer(self) -> None:
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=3, column=0, sticky="ew", padx=24, pady=(10, 20))
        foot.grid_columnconfigure(0, weight=1)
        self.status = ctk.CTkLabel(foot, text="", font=_font(12), anchor="w",
                                   text_color=("#6B7280", "#9CA3AF"))
        self.status.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(foot, text="Cancel", command=self.destroy, height=40,
                      width=110, corner_radius=CORNER, font=_font(12, "bold"),
                      fg_color=("#F3F4F6", "#222222"),
                      hover_color=("#E5E7EB", "#2E2E2E"),
                      text_color=("#374151", "#D1D5DB")).grid(row=0, column=1,
                                                              padx=(0, 8))
        ctk.CTkButton(foot, text=self.confirm_label, command=self._confirm,
                      height=40, width=160, corner_radius=CORNER,
                      font=_font(12, "bold"), fg_color=ACCENT,
                      hover_color=ACCENT_HOVER).grid(row=0, column=2)

    # -- behaviour -------------------------------------------------------- #
    def _visible(self):
        term = self.search_var.get().lower().strip()
        view = list(enumerate(self.interns))
        if term:
            view = [(i, r) for i, r in view
                    if any(term in str(r.get(f, "")).lower()
                           for f, _, _ in self.COLUMNS)]
        if self.only_without.get():
            view = [(i, r) for i, r in view if not r.get("cert_count")]
        return view

    def _render(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for idx, intern in self._visible():
            self.tree.insert("", "end", iid=str(idx),
                             values=[intern.get(f, "")
                                     for f, _, _ in self.COLUMNS])
        self._update_status()

    def _update_status(self) -> None:
        shown = len(self.tree.get_children())
        chosen = len(self.tree.selection())
        self.status.configure(
            text=(f"{chosen} selected  \u2022  {shown} shown" if chosen
                  else f"Nothing selected yet.  \u2022  {shown} shown"))

    def _select_all(self) -> None:
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children)

    def _clear(self) -> None:
        self.tree.selection_remove(*self.tree.selection())

    def _confirm(self) -> None:
        selected = self.tree.selection()
        if not selected:
            ModernDialog(self, "No Selection",
                         "Select an intern from the list first.", icon="\u26a0")
            return
        chosen = [self.interns[int(iid)] for iid in selected]
        if not self.multi:
            chosen = chosen[:1]
        self.destroy()
        self.on_select(chosen)

    def _center(self, master) -> None:
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        x = master.winfo_rootx() + (master.winfo_width() - w) // 2
        y = master.winfo_rooty() + (master.winfo_height() - h) // 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")


# --------------------------------------------------------------------------- #
# Interns page
# --------------------------------------------------------------------------- #
def _date_key(value: str, latest_first: bool = False):
    """Sortable key for a ``DD-MM-YYYY`` string; blanks always sort last."""
    parsed = utils.parse_date(value or "")
    if parsed is None:
        # datetime.min/max keeps unparseable or missing dates at the end.
        return datetime.max if not latest_first else datetime.min
    return parsed


class InternDirectoryPage(ctk.CTkFrame):
    """Existing interns, their certificate history, and issuing another."""

    LIST_COLUMNS = [
        ("intern_id", "Intern ID", 95),
        ("candidate_name", "Name", 160),
        ("start_date", "Joined", 88),
        ("cert_count", "Certs", 50),
        ("cert_summary", "Issued", 130),
    ]

    # label -> (key function, reverse)
    SORTS = {
        "Name (A \u2013 Z)":
            (lambda i: utils.normalize_name(i.get("candidate_name", "")), False),
        "Name (Z \u2013 A)":
            (lambda i: utils.normalize_name(i.get("candidate_name", "")), True),
        "Joining Date (newest)":
            (lambda i: _date_key(i.get("start_date", ""), True), True),
        "Joining Date (oldest)":
            (lambda i: _date_key(i.get("start_date", "")), False),
        "End Date (soonest)":
            (lambda i: _date_key(i.get("end_date", "")), False),
        "Intern ID (ascending)":
            (lambda i: _intern_id_key(i.get("intern_id", "")), False),
        "Intern ID (descending)":
            (lambda i: _intern_id_key(i.get("intern_id", "")), True),
        "Most Certificates": (lambda i: i.get("cert_count", 0), True),
        "Fewest Certificates": (lambda i: i.get("cert_count", 0), False),
        "Role (A \u2013 Z)":
            (lambda i: str(i.get("position", "")).casefold(), False),
        "Recently Added":
            (lambda i: str(i.get("created_at", "")), True),
    }
    DEFAULT_SORT = "Name (A \u2013 Z)"

    # Clicking a column header sorts by it (toggling direction).
    COLUMN_SORTS = {
        "intern_id": ("Intern ID (ascending)", "Intern ID (descending)"),
        "candidate_name": ("Name (A \u2013 Z)", "Name (Z \u2013 A)"),
        "start_date": ("Joining Date (oldest)", "Joining Date (newest)"),
        "cert_count": ("Fewest Certificates", "Most Certificates"),
    }

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.interns: List[Dict] = []
        self.selected: Optional[Dict] = None
        self.unlinked = 0
        self.duplicates: List[Dict] = []
        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._render_list())
        self.filter_var = ctk.StringVar(value="All")
        self.sort_var = ctk.StringVar(value=self.DEFAULT_SORT)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(5, weight=1)

        self._build_header()
        self._build_stats()
        self._build_toolbar()
        self._build_body()
        self.refresh()

    # -- layout ---------------------------------------------------------- #
    def _build_header(self) -> None:
        ctk.CTkLabel(self, text="Interns", font=_font(26, "bold"),
                     anchor="w").grid(row=0, column=0, sticky="w", padx=40,
                                      pady=(30, 0))
        ctk.CTkLabel(self, text="Every intern on record, the certificates they "
                     "already have, and one-click generation of another.",
                     font=_font(13), text_color=("#6B7280", "#9CA3AF"),
                     anchor="w").grid(row=1, column=0, sticky="w", padx=40,
                                      pady=(2, 10))

    def _build_stats(self) -> None:
        strip = ctk.CTkFrame(self, fg_color="transparent")
        strip.grid(row=2, column=0, sticky="ew", padx=40, pady=(0, 8))
        for c in range(4):
            strip.grid_columnconfigure(c, weight=1)
        self.stat_labels: Dict[str, ctk.CTkLabel] = {}
        for i, (key, caption) in enumerate((
                ("interns", "Interns"),
                ("certificates", "Certificates Issued"),
                ("internship", "Internship Certificates"),
                ("completion", "Completion Certificates"))):
            card = ctk.CTkFrame(strip, corner_radius=14,
                                fg_color=("#FFFFFF", "#161616"),
                                border_width=1,
                                border_color=("#ECECEC", "#242424"))
            card.grid(row=0, column=i, sticky="ew",
                      padx=(0 if i == 0 else 6, 0 if i == 3 else 6))
            value = ctk.CTkLabel(card, text="0", font=_font(22, "bold"),
                                 anchor="w")
            value.pack(anchor="w", padx=14, pady=(12, 0))
            ctk.CTkLabel(card, text=caption, font=_font(11),
                         text_color=("#6B7280", "#9CA3AF"), anchor="w").pack(
                anchor="w", padx=14, pady=(0, 12))
            self.stat_labels[key] = value

    def _build_toolbar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=3, column=0, sticky="ew", padx=40, pady=(0, 8))
        bar.grid_columnconfigure(0, weight=1)

        # A leading icon label: CustomTkinter hides placeholder_text whenever a
        # textvariable is bound, so the hint has to live outside the entry.
        search_box = ctk.CTkFrame(bar, fg_color="transparent")
        search_box.grid(row=0, column=0, sticky="ew")
        search_box.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(search_box, text="\U0001F50D", font=_font(14),
                     text_color=("#6B7280", "#9CA3AF")).grid(row=0, column=0,
                                                             padx=(2, 6))
        ctk.CTkEntry(search_box, textvariable=self.search_var, height=38,
                     corner_radius=CORNER, font=_font(12)).grid(
            row=0, column=1, sticky="ew")
        ctk.CTkLabel(bar, text="Search name, Intern ID or role",
                     font=_font(10), text_color=("#9CA3AF", "#6B7280")).grid(
            row=1, column=0, sticky="w", padx=(30, 0), pady=(2, 0))

        # Sort + filter sit beside the search box; actions go on the row below
        # so nothing gets squeezed.
        def dropdown(variable, values, width, col):
            return ctk.CTkOptionMenu(
                bar, values=values, variable=variable,
                command=lambda *_: self._render_list(), height=38, width=width,
                corner_radius=CORNER, font=_font(12),
                fg_color=("#F3F4F6", "#222222"), button_color=ACCENT,
                button_hover_color=ACCENT_HOVER,
                text_color=("#374151", "#D1D5DB")).grid(row=0, column=col,
                                                        padx=(8, 0))

        ctk.CTkLabel(bar, text="Sort", font=_font(10),
                     text_color=("#9CA3AF", "#6B7280")).grid(
            row=1, column=1, sticky="w", padx=(12, 0), pady=(2, 0))
        dropdown(self.sort_var, list(self.SORTS), 210, 1)
        ctk.CTkLabel(bar, text="Show", font=_font(10),
                     text_color=("#9CA3AF", "#6B7280")).grid(
            row=1, column=2, sticky="w", padx=(12, 0), pady=(2, 0))
        dropdown(self.filter_var,
                 ["All", "With certificates", "Without certificates"], 180, 2)

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=4, column=0, sticky="ew", padx=40, pady=(4, 8))
        actions.grid_columnconfigure(0, weight=1)

        def neutral(text, command, col, width):
            ctk.CTkButton(
                actions, text=text, command=command, height=38, width=width,
                corner_radius=CORNER, font=_font(12, "bold"),
                fg_color=("#F3F4F6", "#222222"),
                hover_color=("#E5E7EB", "#2E2E2E"),
                text_color=("#374151", "#D1D5DB")).grid(row=0, column=col,
                                                        padx=(8, 0))

        neutral("\U0001F4E4  Export", self._export, 1, 104)
        neutral("\U0001F4E5  Import", self._import, 2, 104)
        neutral("\u21bb  Refresh", self.refresh, 3, 100)
        # One-click duplicate cleanup; the label carries the count and the
        # button is disabled when there is nothing to clean.
        self.dedupe_btn = ctk.CTkButton(
            actions, text="\U0001F9F9  No Duplicates",
            command=self._remove_duplicates, height=38, width=185,
            corner_radius=CORNER, font=_font(12, "bold"),
            fg_color=("#F3F4F6", "#222222"),
            hover_color=("#FEF3C7", "#3F3212"),
            text_color=("#92400E", "#FBBF24"), state="disabled")
        self.dedupe_btn.grid(row=0, column=4, padx=(8, 0))
        # Destructive, so it stays visually quiet until something is selected.
        self.delete_btn = ctk.CTkButton(
            actions, text="\U0001F5D1  Delete", command=self._delete_selected,
            height=38, width=110, corner_radius=CORNER, font=_font(12, "bold"),
            fg_color=("#F3F4F6", "#222222"),
            hover_color=("#FEE2E2", "#3F1D1D"),
            text_color=("#B91C1C", "#FCA5A5"), state="disabled")
        self.delete_btn.grid(row=0, column=5, padx=(8, 0))
        ctk.CTkButton(
            actions, text="\u26a1  Bulk Certificates", command=self._bulk_all,
            height=38, width=175, corner_radius=CORNER, font=_font(12, "bold"),
            fg_color=ACCENT, hover_color=ACCENT_HOVER).grid(row=0, column=6,
                                                            padx=(8, 0))

    def _build_body(self) -> None:
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=5, column=0, sticky="nsew", padx=40, pady=(0, 24))
        body.grid_columnconfigure(0, weight=3, minsize=380)
        body.grid_columnconfigure(1, weight=4)
        body.grid_rowconfigure(0, weight=1)

        # -- left: intern list ------------------------------------------- #
        left = ctk.CTkFrame(body, corner_radius=14,
                            fg_color=("#FFFFFF", "#161616"), border_width=1,
                            border_color=("#ECECEC", "#242424"))
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left.grid_rowconfigure(0, weight=1)
        left.grid_columnconfigure(0, weight=1)

        style_tree("Dir.Treeview")
        # "extended" so several interns can be deleted at once; the detail
        # panel always follows the first row of the selection.
        self.tree = ttk.Treeview(left,
                                 columns=[c for c, _, _ in self.LIST_COLUMNS],
                                 show="headings", selectmode="extended",
                                 style="Dir.Treeview")
        for field, label, width in self.LIST_COLUMNS:
            self.tree.heading(
                field, text=label,
                command=lambda f=field: self._sort_by_column(f))
            self.tree.column(field, width=width, anchor="w")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        vsb = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        vsb.grid(row=0, column=1, sticky="ns", pady=6, padx=(0, 6))

        self.list_status = ctk.CTkLabel(left, text="", font=_font(11),
                                        text_color=("#6B7280", "#9CA3AF"),
                                        anchor="w")
        self.list_status.grid(row=1, column=0, columnspan=2, sticky="w",
                              padx=12, pady=(0, 8))

        # -- right: detail ----------------------------------------------- #
        self.detail = ctk.CTkScrollableFrame(
            body, corner_radius=14, fg_color=("#FFFFFF", "#161616"),
            border_color=("#ECECEC", "#242424"), border_width=1)
        self.detail.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self.detail.grid_columnconfigure(0, weight=1)
        self._detail_placeholder()

    # -- data ------------------------------------------------------------ #
    def refresh(self) -> None:
        self.interns = load_interns(self.app.db, self.app.cert_store)
        stats = summarise(self.interns)
        for key, label in self.stat_labels.items():
            label.configure(text=str(stats.get(key, 0)))
        # Certificates can outlive their intern record ("Delete Intern Only"),
        # in which case they stay verifiable but are no longer listed here.
        try:
            self.unlinked = max(self.app.cert_store.count()
                                - stats["certificates"], 0)
        except Exception:
            self.unlinked = 0
        self._update_delete_button()
        self._update_dedupe_button()
        self._render_list()

    def _update_dedupe_button(self) -> None:
        """Reflect how many duplicate records could be cleaned up."""
        try:
            self.duplicates = duplicate_groups(self.app.db)
        except Exception:
            self.duplicates = []
        extras = sum(len(g["extras"]) for g in self.duplicates)
        if extras:
            self.dedupe_btn.configure(
                text=f"\U0001F9F9  Remove {extras} Duplicate"
                     f"{'s' if extras != 1 else ''}", state="normal")
        else:
            self.dedupe_btn.configure(text="\U0001F9F9  No Duplicates",
                                      state="disabled")
        # Keep the open intern in sync after generating something.
        if self.selected:
            intern_id = self.selected.get("intern_id")
            match = next((i for i in self.interns
                          if i.get("intern_id") == intern_id), None)
            if match:
                self.selected = match
                self._render_detail(match)
            else:
                self.selected = None
                self._detail_placeholder()

    def _filtered(self):
        term = self.search_var.get().lower().strip()
        view = list(enumerate(self.interns))
        if term:
            view = [(i, r) for i, r in view
                    if term in str(r.get("candidate_name", "")).lower()
                    or term in str(r.get("intern_id", "")).lower()
                    or term in str(r.get("position", "")).lower()]
        mode = self.filter_var.get()
        if mode == "With certificates":
            view = [(i, r) for i, r in view if r["cert_count"]]
        elif mode == "Without certificates":
            view = [(i, r) for i, r in view if not r["cert_count"]]

        key, reverse = self.SORTS.get(self.sort_var.get(),
                                      self.SORTS[self.DEFAULT_SORT])
        try:
            view.sort(key=lambda pair: key(pair[1]), reverse=reverse)
        except TypeError:                       # mixed/unsortable values
            view.sort(key=lambda pair: str(key(pair[1])), reverse=reverse)
        return view

    def _render_list(self) -> None:
        if not hasattr(self, "tree"):
            return
        self.tree.delete(*self.tree.get_children())
        view = self._filtered()
        for idx, intern in view:
            self.tree.insert("", "end", iid=str(idx),
                             values=[intern.get(f, "")
                                     for f, _, _ in self.LIST_COLUMNS])
        status = f"{len(view)} of {len(self.interns)} interns"
        if getattr(self, "unlinked", 0):
            status += (f"   \u2022   {self.unlinked} certificate(s) still "
                       f"verifiable but not linked to a listed intern")
        self.list_status.configure(text=status)
        if self.selected:
            target = next((str(i) for i, r in view
                           if r.get("intern_id") == self.selected.get("intern_id")),
                          None)
            if target and self.tree.exists(target):
                self.tree.selection_set(target)

    def _sort_by_column(self, field: str) -> None:
        """Clicking a column header sorts by it, toggling the direction."""
        options = self.COLUMN_SORTS.get(field)
        if not options:
            return
        ascending, descending = options
        current = self.sort_var.get()
        self.sort_var.set(descending if current == ascending else ascending)
        self._render_list()

    def _selected_interns(self) -> List[Dict]:
        return [self.interns[int(iid)] for iid in self.tree.selection()]

    def _on_select(self, _event=None) -> None:
        selection = self.tree.selection()
        self._update_delete_button()
        if not selection:
            return
        intern = self.interns[int(selection[0])]
        self.selected = intern
        self._render_detail(intern)

    def _update_delete_button(self) -> None:
        count = len(self.tree.selection())
        if count == 0:
            self.delete_btn.configure(text="\U0001F5D1  Delete",
                                      state="disabled")
        elif count == 1:
            self.delete_btn.configure(text="\U0001F5D1  Delete", state="normal")
        else:
            self.delete_btn.configure(text=f"\U0001F5D1  Delete {count}",
                                      state="normal")

    # -- detail panel ---------------------------------------------------- #
    def _clear_detail(self) -> None:
        for widget in self.detail.winfo_children():
            widget.destroy()

    def _detail_placeholder(self) -> None:
        self._clear_detail()
        ctk.CTkLabel(self.detail, text="\U0001F464", font=_font(34)).grid(
            row=0, column=0, pady=(40, 6))
        ctk.CTkLabel(self.detail,
                     text="Select an intern to see their certificates.",
                     font=_font(13),
                     text_color=("#6B7280", "#9CA3AF")).grid(row=1, column=0,
                                                             pady=(0, 40))

    def _render_detail(self, intern: Dict) -> None:
        self._clear_detail()
        row = 0

        head = ctk.CTkFrame(self.detail, fg_color="transparent")
        head.grid(row=row, column=0, sticky="ew", padx=PAD, pady=(PAD, 0))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text=intern.get("candidate_name", "\u2014"),
                     font=_font(20, "bold"), anchor="w").grid(row=0, column=0,
                                                              sticky="w")

        # Intern ID with copy + correct actions.
        id_box = ctk.CTkFrame(head, fg_color="transparent")
        id_box.grid(row=0, column=1, sticky="e")
        intern_id = intern.get("intern_id", "")
        ctk.CTkLabel(id_box, text=intern_id, font=_font(13, "bold"),
                     text_color=ACCENT).pack(side="left", padx=(0, 6))
        copy_button(id_box, intern_id).pack(side="left")
        ctk.CTkButton(
            id_box, text="\u270E", width=30, height=26, corner_radius=6,
            font=_font(12), fg_color=("#F3F4F6", "#2A2A2A"),
            hover_color=("#E5E7EB", "#333333"),
            text_color=("#374151", "#D1D5DB"),
            command=lambda i=intern: self._change_intern_id(i)).pack(
            side="left", padx=(4, 0))

        ctk.CTkLabel(head, text=intern.get("position", ""), font=_font(12),
                     text_color=("#6B7280", "#9CA3AF"), anchor="w").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))
        row += 1

        # Facts grid.
        facts = ctk.CTkFrame(self.detail, fg_color="transparent")
        facts.grid(row=row, column=0, sticky="ew", padx=PAD, pady=(10, 0))
        facts.grid_columnconfigure((0, 1), weight=1)
        pairs = [
            ("Start Date", utils.format_long_date(intern.get("start_date", ""))),
            ("End Date", utils.format_long_date(intern.get("end_date", ""))),
            ("Duration", intern.get("duration", "")),
            ("Department", intern.get("domain", "")),
        ]
        for i, (label, value) in enumerate(pairs):
            r, c = divmod(i, 2)
            cell = ctk.CTkFrame(facts, fg_color="transparent")
            cell.grid(row=r, column=c, sticky="ew", padx=(0, 8), pady=5)
            ctk.CTkLabel(cell, text=label.upper(), font=_font(9, "bold"),
                         text_color=("#9CA3AF", "#6B7280"), anchor="w").pack(
                anchor="w")
            ctk.CTkLabel(cell, text=value or "\u2014", font=_font(12),
                         anchor="w").pack(anchor="w")
        row += 1

        # Certificates already issued.
        certificates = intern["certificates"]
        ctk.CTkLabel(self.detail,
                     text=f"Certificates ({len(certificates)})",
                     font=_font(15, "bold"), anchor="w").grid(
            row=row, column=0, sticky="w", padx=PAD, pady=(18, 4))
        row += 1

        if not certificates:
            empty = ctk.CTkFrame(self.detail, corner_radius=12,
                                 fg_color=("#F9FAFB", "#1C1C1C"))
            empty.grid(row=row, column=0, sticky="ew", padx=PAD)
            ctk.CTkLabel(empty, text="No certificates issued to this intern "
                         "yet.", font=_font(12),
                         text_color=("#6B7280", "#9CA3AF")).pack(padx=12,
                                                                 pady=12)
            row += 1
        else:
            for cert in certificates:
                self._cert_card(cert, row)
                row += 1

        # Generate another.
        ctk.CTkLabel(self.detail, text="Generate Another Document",
                     font=_font(15, "bold"), anchor="w").grid(
            row=row, column=0, sticky="w", padx=PAD, pady=(20, 4))
        row += 1

        gen = ctk.CTkFrame(self.detail, corner_radius=12,
                           fg_color=("#F9FAFB", "#1C1C1C"))
        gen.grid(row=row, column=0, sticky="ew", padx=PAD, pady=(0, PAD))
        gen.grid_columnconfigure(0, weight=1)
        template_var = ctk.StringVar(value="Internship Certificate")
        ctk.CTkOptionMenu(gen, values=doc_router.all_doc_types(),
                          variable=template_var, height=40,
                          corner_radius=CORNER, font=_font(12),
                          fg_color=ACCENT, button_color=ACCENT,
                          button_hover_color=ACCENT_HOVER).grid(
            row=0, column=0, sticky="ew", padx=(12, 8), pady=12)
        ctk.CTkButton(gen, text="\u26a1  Generate", height=40, width=140,
                      corner_radius=CORNER, font=_font(12, "bold"),
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      command=lambda: self._generate(intern,
                                                     template_var.get())).grid(
            row=0, column=1, padx=(0, 12), pady=12)
        existing = ", ".join(sorted(intern["cert_types"])) or "none yet"
        ctk.CTkLabel(gen, text=f"Reuses Intern ID {intern.get('intern_id','')} "
                     f"and the saved dates.  Already issued: {existing}.",
                     font=_font(10), anchor="w", justify="left",
                     wraplength=430,
                     text_color=("#6B7280", "#9CA3AF")).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 12))
        row += 1

    def _cert_card(self, cert: Dict, row: int) -> None:
        card = ctk.CTkFrame(self.detail, corner_radius=12,
                            fg_color=("#F9FAFB", "#1C1C1C"))
        card.grid(row=row, column=0, sticky="ew", padx=PAD, pady=3)
        card.grid_columnconfigure(0, weight=1)

        label = cert_type_label(cert)
        no_box = ctk.CTkFrame(card, fg_color="transparent")
        no_box.grid(row=0, column=0, sticky="w", padx=12, pady=(10, 0))
        cert_no = cert.get("cert_no", "\u2014")
        ctk.CTkLabel(no_box, text=cert_no, font=_font(12, "bold"),
                     anchor="w").pack(side="left", padx=(0, 6))
        copy_button(no_box, cert.get("cert_no", ""), width=26,
                    height=22).pack(side="left")
        badge = "#16A34A" if (cert.get("status") == "Internship Ongoing") \
            else ACCENT
        ctk.CTkLabel(card, text=label, font=_font(10, "bold"),
                     text_color=badge).grid(row=0, column=1, sticky="e",
                                            padx=12, pady=(10, 0))
        meta = " \u2022 ".join(x for x in (
            utils.format_long_date(cert.get("issue_date", "")),
            cert.get("status", ""),
            cert.get("duration", "")) if x)
        ctk.CTkLabel(card, text=meta or "\u2014", font=_font(10), anchor="w",
                     text_color=("#6B7280", "#9CA3AF")).grid(
            row=1, column=0, sticky="w", padx=12, pady=(0, 10))

        stored = cert.get("pdf_path", "")
        pdf_path = resolve_path(stored) if stored else None
        if pdf_path and pdf_path.exists():
            ctk.CTkButton(card, text="Open", width=72, height=28,
                          corner_radius=CORNER, font=_font(11, "bold"),
                          fg_color=("#FFFFFF", "#2A2A2A"),
                          hover_color=("#E5E7EB", "#333333"),
                          text_color=("#374151", "#D1D5DB"),
                          command=lambda p=pdf_path: utils.open_file(p)).grid(
                row=1, column=1, sticky="e", padx=12, pady=(0, 10))

    # -- actions --------------------------------------------------------- #
    def _generate(self, intern: Dict, template: str) -> None:
        if not self.app.company.is_configured():
            ModernDialog(self.app, "Company Not Configured",
                         "Please complete Company Settings first.",
                         icon="\U0001F3E2")
            return
        missing = [label for key, label in (("start_date", "Start Date"),
                                            ("end_date", "End Date"))
                   if not intern.get(key)]
        if missing:
            ModernDialog(self.app, "Missing Dates",
                         f"This intern has no {' and '.join(missing)} on "
                         f"record. Add it from the New Letter page first.",
                         icon="\u26a0")
            return
        # Never silently issue a second certificate of the same kind.
        duplicate = existing_certificate(self.app, intern.get("intern_id", ""),
                                         template)
        if duplicate:
            ModernDialog(
                self.app, "Certificate Already Issued",
                f"{intern.get('candidate_name','')} already has a "
                f"{template}:\n\n{duplicate.get('cert_no','')}  \u2022  issued "
                f"{utils.format_long_date(duplicate.get('issue_date',''))}\n\n"
                f"Issuing another creates a second certificate number for the "
                f"same document.", icon="\u26a0",
                actions=[("Issue Another Anyway",
                          lambda: self._do_generate(intern, template), False),
                         ("Cancel", None, True)])
            return
        self._do_generate(intern, template)

    def _do_generate(self, intern: Dict, template: str) -> None:
        try:
            path = issue_for_intern(self.app, intern, template)
        except Exception as exc:
            ModernDialog(self.app, "Generation Failed", str(exc),
                         icon="\u274c")
            return

        if hasattr(self.app, "_refresh_generated"):
            self.app._refresh_generated()
        self.refresh()
        ModernDialog(
            self.app, f"{template} Generated",
            f"Saved as {path.name}\nIntern: {intern.get('candidate_name','')} "
            f"({intern.get('intern_id','')})", icon="\u2705",
            actions=[("Open PDF", lambda: utils.open_file(path), True),
                     ("Open Folder", lambda: utils.reveal_in_folder(path),
                      False)])

    def _change_intern_id(self, intern: Dict) -> None:
        """Correct a wrong Intern ID, optionally reissuing their certificates."""
        try:
            suggestion = self.app.db.peek_next_intern_id()
        except Exception:
            suggestion = "\u2014"

        def apply(new_id: str, regenerate: bool) -> None:
            try:
                result = change_intern_id(self.app, intern, new_id,
                                          regenerate=regenerate)
            except ValueError as exc:
                ModernDialog(self.app, "Cannot Change Intern ID", str(exc),
                             icon="\u26a0")
                return
            except Exception as exc:
                ModernDialog(self.app, "Change Failed", str(exc),
                             icon="\u274c")
                return

            self.selected = None
            self.refresh()
            # Re-open the same person under their new ID.
            match = next((i for i in self.interns
                          if i.get("intern_id") == result["new"]), None)
            if match:
                index = self.interns.index(match)
                if self.tree.exists(str(index)):
                    self.tree.selection_set(str(index))
                else:
                    self.selected = match
                    self._render_detail(match)
            if hasattr(self.app, "_refresh_generated"):
                self.app._refresh_generated()

            detail = (f"{result['old']}  \u2192  {result['new']}\n\n"
                      f"{result['certificates']} certificate record(s) moved "
                      f"to the new ID.")
            if result["regenerated"]:
                detail += (f"\n{len(result['regenerated'])} certificate PDF(s) "
                           f"re-created with the corrected ID, keeping their "
                           f"original numbers.")
            if result["failed"]:
                detail += (f"\n\n{len(result['failed'])} could not be "
                           f"re-created: {', '.join(result['failed'])}")
            ModernDialog(self.app, "Intern ID Updated", detail, icon="\u2705")

        ChangeInternIdDialog(self.app, intern, suggestion, apply)

    def _remove_duplicates(self) -> None:
        """One click: drop every redundant copy, keeping one record per person."""
        groups = self.duplicates
        if not groups:
            ModernDialog(self.app, "No Duplicates",
                         "Every intern already has a single record.",
                         icon="\u2705")
            return

        extras = [r for g in groups for r in g["extras"]]
        lines = []
        for group in groups[:8]:
            keep = group["primary"].get("intern_id", "")
            drop = ", ".join(r.get("intern_id", "") for r in group["extras"])
            lines.append(f"{group['name']}:  keep {keep}   drop {drop}")
        if len(groups) > 8:
            lines.append(f"\u2026 and {len(groups) - 8} more")

        def go():
            try:
                result = remove_duplicates(self.app, groups)
            except Exception as exc:
                ModernDialog(self.app, "Cleanup Failed", str(exc),
                             icon="\u274c")
                return
            self.selected = None
            self.tree.selection_remove(*self.tree.selection())
            self.refresh()
            self._detail_placeholder()
            ModernDialog(
                self.app, "Duplicates Removed",
                f"Merged {result['groups']} duplicated intern(s) and removed "
                f"{result['interns']} extra record(s).\n\n"
                f"Certificates were kept and now appear under the remaining "
                f"record. PDF files on disk were not touched.",
                icon="\u2705")

        ModernDialog(
            self.app, f"Remove {len(extras)} Duplicate Record(s)?",
            f"{len(groups)} intern(s) have more than one record. The oldest "
            f"Intern ID is kept and the extra copies are removed:\n\n"
            + "\n".join(lines) +
            "\n\nCertificates stay verifiable and are re-attached to the "
            "record that is kept. PDF files on disk are not deleted.",
            icon="\U0001F9F9",
            actions=[(f"Remove {len(extras)} Duplicate(s)", go, True),
                     ("Cancel", None, False)])

    def _delete_selected(self) -> None:
        """Confirm, then delete the selected intern record(s)."""
        chosen = self._selected_interns()
        if not chosen:
            ModernDialog(self.app, "Nothing Selected",
                         "Select one or more interns in the list first.",
                         icon="\u26a0")
            return

        cert_total = sum(i["cert_count"] for i in chosen)
        if len(chosen) == 1:
            who = (f"{chosen[0].get('candidate_name', '')} "
                   f"({chosen[0].get('intern_id', '')})")
        else:
            who = f"{len(chosen)} interns"
            preview = ", ".join(f"{i.get('candidate_name','')}"
                                f" ({i.get('intern_id','')})"
                                for i in chosen[:4])
            if len(chosen) > 4:
                preview += f", +{len(chosen) - 4} more"
            who += f"\n{preview}"

        def run(with_certs: bool):
            def _do():
                try:
                    result = delete_interns(self.app, chosen, with_certs)
                except Exception as exc:
                    ModernDialog(self.app, "Delete Failed", str(exc),
                                 icon="\u274c")
                    return
                self.selected = None
                self.tree.selection_remove(*self.tree.selection())
                self.refresh()
                self._detail_placeholder()
                detail = f"Removed {result['interns']} intern record(s)."
                if result["certificates"]:
                    detail += (f"\n{result['certificates']} certificate "
                               f"record(s) also removed.")
                detail += "\n\nPDF files on disk were not touched."
                ModernDialog(self.app, "Deleted", detail, icon="\U0001F5D1")
            return _do

        title = ("Delete Intern Record?" if len(chosen) == 1
                 else f"Delete {len(chosen)} Intern Records?")
        if cert_total == 0:
            ModernDialog(
                self.app, title,
                f"{who}\n\nNo certificates are linked, so nothing else is "
                f"affected. Generated PDF files on disk are kept.\n\n"
                f"This cannot be undone.", icon="\U0001F5D1",
                actions=[("Delete", run(True), True),
                         ("Cancel", None, False)])
            return

        ModernDialog(
            self.app, title,
            f"{who}\n\n{cert_total} certificate record(s) are linked to "
            f"{'them' if len(chosen) > 1 else 'this intern'}.\n\n"
            f"Deleting the certificates means any already-issued certificate "
            f"number will no longer verify in the Intern Verification panel. "
            f"PDF files on disk are kept either way.\n\n"
            f"This cannot be undone.", icon="\u26a0",
            actions=[
                (f"Delete + {cert_total} Certificate(s)", run(True), True),
                ("Delete Intern Only", run(False), False),
                ("Cancel", None, False),
            ])

    # -- import / export ------------------------------------------------- #
    def _export(self) -> None:
        """Save interns and their certificates to a file.

        Exports the **current filter and selection**: whatever the list is
        showing is what gets written, so a search or a multi-select doubles as
        a way to export a subset.
        """
        # A multi-selection means "export just these". A single selection does
        # not: clicking one row is how you *view* an intern, so treating that
        # as a filter would surprise anyone who just browsed the list.
        chosen = self._selected_interns()
        picked = len(chosen) > 1
        records = chosen if picked else [i for _i, i in self._filtered()]
        if not records:
            ModernDialog(self.app, "Nothing to Export",
                         "There are no interns matching the current filter.",
                         icon="\u26a0")
            return

        cert_total = sum(len(r.get("certificates") or []) for r in records)
        stamp = datetime.now().strftime("%Y-%m-%d")
        dest = filedialog.asksaveasfilename(
            title="Export interns and certificates",
            defaultextension=".xlsx",
            initialfile=f"CertiFlow_interns_{stamp}.xlsx",
            filetypes=[("Excel workbook", "*.xlsx"), ("JSON", "*.json"),
                       ("CSV (interns only)", "*.csv")])
        if not dest:
            return

        def go():
            try:
                path, n_interns, n_certs = intern_io.export_from_db(dest,
                                                                    records)
            except Exception as exc:
                ModernDialog(self.app, "Export Failed", str(exc), icon="\u274c")
                return
            detail = (f"{path.name}\n\n{n_interns} "
                      f"{'selected ' if picked else ''}intern"
                      f"{'s' if n_interns != 1 else ''}")
            if intern_io.csv_holds_interns_only(path):
                detail += ("\n\nCSV holds one table, so the certificates were "
                           "not written. Export as .xlsx or .json to include "
                           "them.")
            else:
                detail += (f" and {n_certs} certificate"
                           f"{'s' if n_certs != 1 else ''}")
                detail += ("\n\nRe-import this file on any machine to restore "
                           "these records with their exact Intern IDs and "
                           "certificate numbers.")
            ModernDialog(
                self.app, "Export Complete", detail, icon="\U0001F4E4",
                actions=[("Open File", lambda: utils.open_file(path), True),
                         ("Open Folder",
                          lambda: utils.reveal_in_folder(path), False),
                         ("Close", None, False)])

        # Warn before a format silently drops the certificate history.
        if cert_total and intern_io.csv_holds_interns_only(dest):
            ModernDialog(
                self.app, "CSV Cannot Hold Certificates",
                f"{len(records)} intern(s) have {cert_total} certificate "
                f"record(s) between them.\n\nA CSV file is a single table, so "
                f"only the interns would be written and the certificate "
                f"history would be left behind.\n\nExcel (.xlsx) and JSON "
                f"keep both.", icon="\u26a0",
                actions=[("Export Interns Only", go, False),
                         ("Cancel", None, True)])
            return
        go()

    def _import(self) -> None:
        """Load interns and certificates from a file, after confirming."""
        path = filedialog.askopenfilename(
            title="Import interns and certificates",
            filetypes=[("CertiFlow export", "*.xlsx *.json *.csv"),
                       ("Excel workbook", "*.xlsx"), ("JSON", "*.json"),
                       ("CSV", "*.csv")])
        if not path:
            return
        try:
            archive = intern_io.read_archive(path)
        except Exception as exc:
            ModernDialog(self.app, "Import Failed", str(exc), icon="\u274c")
            return
        if not archive:
            ModernDialog(
                self.app, "Nothing to Import",
                "No intern or certificate rows were recognised in that "
                "file.\n\nUse \U0001F4E4 Export to see the expected columns, "
                "or check that the header row is present.", icon="\u26a0")
            return

        try:
            plan = intern_io.preview(self.app.db, self.app.cert_store, archive)
        except Exception as exc:
            ModernDialog(self.app, "Import Failed", str(exc), icon="\u274c")
            return

        lines = [f"From {archive.source}", ""]
        if plan.new_interns:
            lines.append(f"\u2022  {len(plan.new_interns)} new intern(s)")
        if plan.new_certs:
            lines.append(f"\u2022  {len(plan.new_certs)} new certificate(s)")
        if plan.existing_interns:
            lines.append(f"\u2022  {len(plan.existing_interns)} intern(s) "
                         f"already on record")
        if plan.existing_certs:
            lines.append(f"\u2022  {len(plan.existing_certs)} certificate(s) "
                         f"already on record")
        if plan.orphan_certs:
            lines.append(f"\u2022  {len(plan.orphan_certs)} certificate(s) "
                         f"belong to an Intern ID not in this file or on "
                         f"record - they will import and stay verifiable, but "
                         f"will not be listed under an intern")
        if plan.skipped:
            lines.append(f"\u2022  {len(plan.skipped)} row(s) will be skipped")
            for who, why in plan.skipped[:3]:
                lines.append(f"      {who} \u2014 {why}")
            if len(plan.skipped) > 3:
                lines.append(f"      +{len(plan.skipped) - 3} more")

        if not plan.total_new and not plan.total_existing:
            ModernDialog(self.app, "Nothing to Import", "\n".join(lines),
                         icon="\u26a0")
            return

        def run(overwrite: bool):
            def _do():
                snapshot(self.app, "import")
                try:
                    result = intern_io.apply_archive(
                        self.app.db, self.app.cert_store, archive,
                        overwrite=overwrite)
                except Exception as exc:
                    ModernDialog(self.app, "Import Failed", str(exc),
                                 icon="\u274c")
                    return
                self.selected = None
                self.tree.selection_remove(*self.tree.selection())
                self.refresh()
                self._detail_placeholder()
                if hasattr(self.app, "_refresh_generated"):
                    self.app._refresh_generated()
                self._import_report(result)
            return _do

        lines.append("")
        lines.append("A database snapshot is taken first, so this can be "
                     "rolled back.")
        message = "\n".join(lines)

        if plan.total_existing:
            ModernDialog(
                self.app, "Import Records?", message, icon="\U0001F4E5",
                actions=[
                    (f"Add {plan.total_new} New Only", run(False), True),
                    (f"Add + Overwrite {plan.total_existing}", run(True),
                     False),
                    ("Cancel", None, False)])
        else:
            ModernDialog(
                self.app, "Import Records?", message, icon="\U0001F4E5",
                actions=[(f"Import {plan.total_new}", run(False), True),
                         ("Cancel", None, False)])

    def _import_report(self, result: "intern_io.Result") -> None:
        """Summarise an import, offering to generate certificates next."""
        parts = []
        if result.interns_added:
            parts.append(f"{result.interns_added} intern(s) added")
        if result.interns_updated:
            parts.append(f"{result.interns_updated} intern(s) updated")
        if result.certs_added:
            parts.append(f"{result.certs_added} certificate(s) added")
        if result.certs_updated:
            parts.append(f"{result.certs_updated} certificate(s) updated")
        detail = "\n".join(parts) or "No records were changed."
        if result.skipped:
            detail += f"\n\n{len(result.skipped)} row(s) skipped."
        if result.failed:
            detail += f"\n{len(result.failed)} failed:"
            for who, why in result.failed[:3]:
                detail += f"\n   {who} \u2014 {why}"

        actions = [("Close", None, True)]
        # Imported interns are ordinary records, so certificates can be issued
        # for them straight away - offer the shortcut rather than make the user
        # find it.
        if result.interns_added or result.interns_updated:
            detail += ("\n\nImported interns can be issued certificates like "
                       "any other - individually from the list, or all at once.")
            actions.insert(0, ("Generate Certificates", self._bulk_all, False))
        ModernDialog(self.app, "Import Complete", detail, icon="\u2705",
                     actions=actions)

    def _bulk_all(self) -> None:
        """Send the currently filtered interns to the Bulk Generator."""
        view = [intern for _i, intern in self._filtered()]
        if not view:
            ModernDialog(self.app, "No Interns",
                         "There are no interns matching the current filter.",
                         icon="\u26a0")
            return
        bulk = self.app.pages.get("bulk")
        if bulk is None or not hasattr(bulk, "load_intern_rows"):
            ModernDialog(self.app, "Unavailable",
                         "The Bulk Generator page is not available.",
                         icon="\u26a0")
            return

        def go():
            bulk.load_intern_rows(view, template="Internship Certificate")
            self.app.show_page("bulk")

        ModernDialog(
            self.app, "Bulk Certificates",
            f"Send {len(view)} intern(s) to the Bulk Generator with their "
            f"existing Intern IDs, ready to generate certificates for all of "
            f"them?", icon="\u26a1",
            actions=[("Open Bulk Generator", go, True),
                     ("Cancel", None, False)])

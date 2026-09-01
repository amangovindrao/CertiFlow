"""
ui.py
-----
CustomTkinter user interface for the Offer Letter Generator.

The UI is intentionally split into small, reusable widgets and one page per
feature so the screen logic stays readable and future pages (HR dashboard,
bulk generation, etc.) can be added by registering a new page frame.

Layers stay separated:
    * UI (this file) collects input and shows results.
    * Persistence    -> settings.py
    * Business/maths -> utils.py
    * Rendering      -> pdf_generator.py
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from tkinter import filedialog
from typing import Callable, Dict, List, Optional

import customtkinter as ctk

try:
    from tkcalendar import Calendar
except Exception:  # pragma: no cover - calendar is optional at runtime
    Calendar = None  # type: ignore

import settings
import utils
from pdf_generator import PDFGenerator
from database import InternDatabase
import doc_router
from certificate_generator import CertificateStore
import backup
from settings import (
    BASE_DIR,
    COMPANY_DIR,
    OUTPUT_DIR,
    AppSettings,
    CompanyProfile,
    asset_dir,
    autoload_assets,
    resolve_path,
)

# --------------------------------------------------------------------------- #
# Design tokens
# --------------------------------------------------------------------------- #
FONT_FAMILY = "Segoe UI"
ACCENT = "#2563EB"
ACCENT_HOVER = "#1D4ED8"
CORNER = 12
PAD = 16


def _font(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=FONT_FAMILY, size=size, weight=weight)


# --------------------------------------------------------------------------- #
# Clipboard helpers (Intern IDs / certificate numbers are copied constantly)
# --------------------------------------------------------------------------- #
def copy_to_clipboard(widget, text: str) -> bool:
    """Put ``text`` on the system clipboard. Returns True on success."""
    value = str(text or "").strip()
    if not value:
        return False
    try:
        widget.clipboard_clear()
        widget.clipboard_append(value)
        widget.update_idletasks()      # keeps the clipboard after we exit
        return True
    except Exception:
        return False


def copy_button(master, text, width: int = 30, height: int = 26,
                tooltip: str = "Copy"):
    """A small button that copies ``text`` and briefly confirms.

    ``text`` may be a string or a zero-argument callable, so the value can be
    resolved at click time.
    """
    button = ctk.CTkButton(
        master, text="\U0001F4CB", width=width, height=height,
        corner_radius=6, font=_font(11),
        fg_color=("#F3F4F6", "#2A2A2A"), hover_color=("#E5E7EB", "#333333"),
        text_color=("#374151", "#D1D5DB"))

    def do_copy():
        value = text() if callable(text) else text
        if copy_to_clipboard(button, value):
            button.configure(text="\u2713", text_color="#16A34A")
            button.after(1000, lambda: button.configure(
                text="\U0001F4CB", text_color=("#374151", "#D1D5DB")))

    button.configure(command=do_copy)
    return button


# --------------------------------------------------------------------------- #
# Modern dialog (replaces ugly tk messagebox)
# --------------------------------------------------------------------------- #
class ModernDialog(ctk.CTkToplevel):
    """A rounded, centered modal dialog with optional action buttons."""

    def __init__(self, master, title: str, message: str,
                 icon: str = "ℹ", actions: Optional[List[tuple]] = None):
        super().__init__(master)
        self.title(title)
        self.resizable(False, False)
        self.configure(fg_color=("#FFFFFF", "#1A1A1A"))
        self.transient(master)
        self.grab_set()

        wrapper = ctk.CTkFrame(self, fg_color="transparent")
        wrapper.pack(padx=28, pady=24, fill="both", expand=True)

        ctk.CTkLabel(wrapper, text=icon, font=_font(34)).pack(pady=(0, 6))
        ctk.CTkLabel(wrapper, text=title, font=_font(17, "bold")).pack()
        ctk.CTkLabel(
            wrapper, text=message, font=_font(12), justify="center",
            wraplength=360, text_color=("#4B5563", "#9CA3AF"),
        ).pack(pady=(8, 18))

        btn_row = ctk.CTkFrame(wrapper, fg_color="transparent")
        btn_row.pack()

        actions = actions or [("OK", self.destroy, True)]
        for label, command, primary in actions:
            def _wrap(cmd=command):
                self.destroy()
                if cmd:
                    cmd()

            ctk.CTkButton(
                btn_row, text=label, command=_wrap, width=130, height=38,
                corner_radius=CORNER, font=_font(12, "bold"),
                fg_color=ACCENT if primary else "transparent",
                hover_color=ACCENT_HOVER if primary else ("#E5E7EB", "#2A2A2A"),
                text_color="#FFFFFF" if primary else ("#374151", "#D1D5DB"),
                border_width=0 if primary else 1,
                border_color=("#D1D5DB", "#3A3A3A"),
            ).pack(side="left", padx=6)

        self.update_idletasks()
        self._center(master)

    def _center(self, master) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        x = master.winfo_rootx() + (master.winfo_width() - w) // 2
        y = master.winfo_rooty() + (master.winfo_height() - h) // 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")


# --------------------------------------------------------------------------- #
# Searchable dropdown
# --------------------------------------------------------------------------- #
class SearchableCombo(ctk.CTkComboBox):
    """A CTkComboBox that filters its dropdown values as the user types."""

    def __init__(self, master, values: List[str], **kwargs):
        self._all_values = list(values)
        super().__init__(master, values=values, **kwargs)
        # ``_entry`` is the internal entry widget of the combobox.
        self._entry.bind("<KeyRelease>", self._on_key)

    def _on_key(self, event) -> None:
        if event.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
            return
        typed = self.get().lower()
        filtered = [v for v in self._all_values if typed in v.lower()]
        self.configure(values=filtered or self._all_values)


# --------------------------------------------------------------------------- #
# Date picker (modern CTk entry + tkcalendar popup)
# --------------------------------------------------------------------------- #
class DatePicker(ctk.CTkFrame):
    """An entry showing DD-MM-YYYY with a button that opens a calendar popup."""

    def __init__(self, master, on_change: Optional[Callable] = None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.on_change = on_change
        self.next_widget = None          # focus jumps here when fully typed
        self._masking = False
        self.grid_columnconfigure(0, weight=1)

        self.var = ctk.StringVar()
        self.entry = ctk.CTkEntry(
            self, textvariable=self.var, height=40, corner_radius=CORNER,
            font=_font(12), placeholder_text="DD-MM-YYYY",
        )
        self.entry.grid(row=0, column=0, sticky="ew")
        self.var.trace_add("write", lambda *_: self._fire())
        self.entry.bind("<KeyRelease>", self._on_type)

        ctk.CTkButton(
            self, text="📅", width=44, height=40, corner_radius=CORNER,
            font=_font(15), fg_color=("#F3F4F6", "#2A2A2A"),
            hover_color=("#E5E7EB", "#333333"),
            text_color=("#374151", "#D1D5DB"), command=self._open_calendar,
        ).grid(row=0, column=1, padx=(8, 0))

    # -- input mask: auto-insert dashes, jump on completion --------------- #
    def _on_type(self, event) -> None:
        if event.keysym in ("Left", "Right", "Up", "Down", "Tab",
                            "Shift_L", "Shift_R", "Return", "Escape"):
            return
        text = self.var.get()
        digits = "".join(ch for ch in text if ch.isdigit())[:8]
        out = digits[:2]
        if len(digits) >= 3:
            out += "-" + digits[2:4]
        if len(digits) >= 5:
            out += "-" + digits[4:8]
        if out != text:
            self._masking = True
            self.var.set(out)
            self.entry.icursor("end")
            self._masking = False
        # When a full DD-MM-YYYY is entered, jump to the next field.
        if len(digits) == 8 and self.next_widget is not None:
            target = getattr(self.next_widget, "entry", self.next_widget)
            try:
                target.focus_set()
            except Exception:
                pass

    # -- value api -------------------------------------------------------- #
    def get(self) -> str:
        return self.var.get().strip()

    def set(self, value: str) -> None:
        self.var.set(value)

    def _fire(self) -> None:
        if self.on_change:
            self.on_change()

    # -- popup ------------------------------------------------------------ #
    def _open_calendar(self) -> None:
        if Calendar is None:
            return
        top = ctk.CTkToplevel(self)
        top.title("Select Date")
        top.resizable(False, False)
        top.transient(self.winfo_toplevel())
        top.grab_set()

        current = utils.parse_date(self.get()) or datetime.now()
        cal = Calendar(
            top, selectmode="day", date_pattern="dd-mm-yyyy",
            year=current.year, month=current.month, day=current.day,
            background=ACCENT, headersbackground="#F3F4F6",
            selectbackground=ACCENT, borderwidth=0,
        )
        cal.pack(padx=16, pady=16)

        def _confirm():
            self.set(cal.get_date())
            top.destroy()

        ctk.CTkButton(
            top, text="Select", command=_confirm, height=36,
            corner_radius=CORNER, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            font=_font(12, "bold"),
        ).pack(padx=16, pady=(0, 16), fill="x")

        top.update_idletasks()
        x = self.winfo_rootx()
        y = self.winfo_rooty() + 44
        top.geometry(f"+{x}+{y}")


# --------------------------------------------------------------------------- #
# Labeled field helper
# --------------------------------------------------------------------------- #
def labeled(master, label: str, widget_factory: Callable, row: int,
            required: bool = False) -> object:
    """Place a label above a freshly created widget inside a grid."""
    text = label + ("  *" if required else "")
    ctk.CTkLabel(
        master, text=text, font=_font(11, "bold"),
        text_color=("#374151", "#D1D5DB"), anchor="w",
    ).grid(row=row, column=0, sticky="ew", padx=PAD, pady=(10, 2))
    widget = widget_factory(master)
    widget.grid(row=row + 1, column=0, sticky="ew", padx=PAD, pady=(0, 4))
    return widget


# --------------------------------------------------------------------------- #
# Main application window
# --------------------------------------------------------------------------- #
class OfferLetterApp(ctk.CTk):
    """Top level window orchestrating navigation, pages and shared state."""

    NAV_ITEMS = [
        ("📄  Create Document", "create"),
        ("🎨  Template Designer", "designer"),
        ("👥  Interns", "interns"),
        ("⚡  Bulk Generator", "bulk"),
        ("🔎  Verification", "verify"),
        ("📁  Generated Files", "generated"),
        ("💾  Backup & Restore", "backup"),
        ("🏢  Company Settings", "company"),
        ("ℹ  About", "about"),
    ]

    def __init__(self):
        super().__init__()

        # -- shared services ---------------------------------------------- #
        self.company = CompanyProfile()
        autoload_assets(self.company)  # auto-load logo/signature/watermark
        self.app_settings = AppSettings()
        self.pdf = PDFGenerator(BASE_DIR)
        self.db_path = COMPANY_DIR / "interns.db"
        # A snapshot per launch, before anything can be changed.
        backup.create_backup(self.db_path, "startup")
        self.db = InternDatabase(self.db_path)
        self.cert_store = CertificateStore(self.db_path)

        # -- window ------------------------------------------------------- #
        ctk.set_appearance_mode(self.app_settings.theme.lower())
        ctk.set_default_color_theme("blue")
        self.title("CertiFlow")
        self.geometry("1180x760")
        self.minsize(980, 640)
        self.configure(fg_color=("#F7F8FA", "#0F0F0F"))

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.pages: Dict[str, ctk.CTkFrame] = {}
        self.nav_buttons: Dict[str, ctk.CTkButton] = {}

        self._build_sidebar()
        self._build_pages()
        self._bind_shortcuts()

        # First-run: nudge the user to fill company settings.
        start = "create" if self.company.is_configured() else "company"
        self.show_page(start)
        if not self.company.is_configured():
            self.after(400, lambda: ModernDialog(
                self, "Welcome 👋",
                "Let's set up your company details first. They are saved and "
                "reused automatically for every letter.",
                icon="🏢",
            ))

    # ------------------------------------------------------------------ #
    # Sidebar
    # ------------------------------------------------------------------ #
    def _build_sidebar(self) -> None:
        bar = ctk.CTkFrame(self, width=240, corner_radius=0,
                           fg_color=("#FFFFFF", "#161616"))
        bar.grid(row=0, column=0, sticky="nsew")
        bar.grid_propagate(False)
        bar.grid_rowconfigure(99, weight=1)

        ctk.CTkLabel(
            bar, text="CertiFlow", font=_font(22, "bold"),
            text_color=ACCENT,
        ).grid(row=0, column=0, sticky="w", padx=24, pady=(26, 0))
        ctk.CTkLabel(
            bar, text=self.company.get("company_name", "") or "Documents",
            font=_font(12), text_color=("#6B7280", "#9CA3AF"),
        ).grid(row=1, column=0, sticky="w", padx=24, pady=(0, 18))

        ctk.CTkFrame(bar, height=1, fg_color=("#E5E7EB", "#2A2A2A")).grid(
            row=2, column=0, sticky="ew", padx=20, pady=(0, 12))

        for i, (label, key) in enumerate(self.NAV_ITEMS):
            btn = ctk.CTkButton(
                bar, text=label, anchor="w", height=44, corner_radius=CORNER,
                font=_font(13), fg_color="transparent",
                text_color=("#374151", "#D1D5DB"),
                hover_color=("#F3F4F6", "#222222"),
                command=lambda k=key: self.show_page(k),
            )
            btn.grid(row=3 + i, column=0, sticky="ew", padx=14, pady=3)
            self.nav_buttons[key] = btn

        # Theme switch at the bottom
        theme_frame = ctk.CTkFrame(bar, fg_color="transparent")
        theme_frame.grid(row=100, column=0, sticky="ew", padx=20, pady=20)
        ctk.CTkLabel(theme_frame, text="Theme", font=_font(11, "bold"),
                     text_color=("#6B7280", "#9CA3AF")).pack(anchor="w")
        self.theme_switch = ctk.CTkSegmentedButton(
            theme_frame, values=["☀ Light", "🌙 Dark"],
            command=self._on_theme_change, font=_font(12),
            selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
        )
        self.theme_switch.set("🌙 Dark" if self.app_settings.theme == "Dark"
                              else "☀ Light")
        self.theme_switch.pack(fill="x", pady=(6, 0))

    def _on_theme_change(self, value: str) -> None:
        theme = "Dark" if "Dark" in value else "Light"
        ctk.set_appearance_mode(theme.lower())
        self.app_settings.theme = theme

    # ------------------------------------------------------------------ #
    # Page management
    # ------------------------------------------------------------------ #
    def _build_pages(self) -> None:
        self.pages["create"] = self._build_create_page()
        self.pages["designer"] = self._build_designer_page()
        self.pages["bulk"] = self._build_bulk_page()
        self.pages["interns"] = self._build_interns_page()
        self.pages["verify"] = self._build_verify_page()
        self.pages["company"] = self._build_company_page()
        self.pages["generated"] = self._build_generated_page()
        self.pages["backup"] = self._build_backup_page()
        self.pages["about"] = self._build_about_page()

    def _build_bulk_page(self) -> ctk.CTkFrame:
        # Imported lazily to avoid a circular import at module load time.
        from bulk_ui import BulkGeneratorPage
        return BulkGeneratorPage(self, self)

    def _build_create_page(self) -> ctk.CTkFrame:
        # Lazy import: the page imports shared widgets from this module.
        from document_page import CreateDocumentPage
        return CreateDocumentPage(self, self)

    def _build_interns_page(self) -> ctk.CTkFrame:
        from intern_directory import InternDirectoryPage
        return InternDirectoryPage(self, self)

    def _build_designer_page(self) -> ctk.CTkFrame:
        from designer_page import DesignerPage
        return DesignerPage(self, self)

    def _build_backup_page(self) -> ctk.CTkFrame:
        from backup_page import BackupPage
        return BackupPage(self, self)

    def refresh_document_types(self) -> None:
        """Tell the generating pages that the custom template list changed."""
        for key in ("create", "bulk"):
            page = self.pages.get(key)
            refresh = getattr(page, "refresh_types", None)
            if refresh is not None:
                try:
                    refresh()
                except Exception:
                    pass

    def show_page(self, key: str) -> None:
        for page in self.pages.values():
            page.grid_forget()
        self.pages[key].grid(row=0, column=1, sticky="nsew")

        # Highlight the active nav button.
        for k, btn in self.nav_buttons.items():
            if k == key:
                btn.configure(fg_color=("#EFF3FF", "#1E293B"),
                              text_color=ACCENT)
            else:
                btn.configure(fg_color="transparent",
                              text_color=("#374151", "#D1D5DB"))

        if key == "generated":
            self._refresh_generated()
        elif key == "interns":
            # Certificates may have been issued from another page since the
            # directory was last shown.
            self.pages["interns"].refresh()

    # ------------------------------------------------------------------ #
    # Card helper
    # ------------------------------------------------------------------ #
    def _page_shell(self, title: str, subtitle: str = "") -> ctk.CTkScrollableFrame:
        page = ctk.CTkScrollableFrame(self, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(page, text=title, font=_font(26, "bold"),
                     anchor="w").grid(row=0, column=0, sticky="w",
                                      padx=40, pady=(30, 0))
        if subtitle:
            ctk.CTkLabel(page, text=subtitle, font=_font(13),
                         text_color=("#6B7280", "#9CA3AF"), anchor="w").grid(
                row=1, column=0, sticky="w", padx=40, pady=(2, 10))
        return page


    # ================================================================== #
    # PAGE: Company Settings
    # ================================================================== #
    def _build_company_page(self) -> ctk.CTkFrame:
        page = self._page_shell(
            "Company Settings",
            "Enter these once. They are saved and reused for every letter.")

        card = ctk.CTkFrame(page, corner_radius=18,
                            fg_color=("#FFFFFF", "#161616"),
                            border_width=1, border_color=("#ECECEC", "#242424"))
        card.grid(row=2, column=0, sticky="ew", padx=40, pady=16)
        card.grid_columnconfigure(0, weight=1)
        card.grid_columnconfigure(1, weight=1)

        left = ctk.CTkFrame(card, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew")
        left.grid_columnconfigure(0, weight=1)
        right = ctk.CTkFrame(card, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)

        self.company_form: Dict[str, object] = {}

        def field(master, key, label, row):
            def factory(m):
                return ctk.CTkEntry(m, height=40, corner_radius=CORNER,
                                    font=_font(12))
            w = labeled(master, label, factory, row)
            w.insert(0, self.company.get(key, ""))
            self.company_form[key] = w

        field(left, "company_name", "Company Name", 0)
        field(left, "tagline", "Company Tagline", 2)
        field(left, "email", "Email", 4)
        field(left, "phone", "Phone", 6)
        field(left, "website", "Website", 8)
        field(left, "footer_text", "Footer Text", 10)

        field(right, "hr_name", "HR Name", 0)
        field(right, "hr_designation", "HR Designation", 2)

        # Asset upload drop zones (now balanced: 6 boxes per column)
        self._upload_zone(right, "logo", "Company Logo", 4)
        self._upload_zone(right, "watermark", "Watermark", 6)
        self._upload_zone(right, "signature", "Signature", 8)
        self._upload_zone(right, "stamp", "Company Stamp", 10)

        # Footer text colour + ScaleOn signature font (full width row).
        self.FOOTER_COLORS = {
            "Black": "#111111", "Gold": "#D4AF37",
            "Dark Gray": "#666666", "Blue": "#1F4E9C",
        }
        # Display name -> stored key understood by the PDF generator.
        self.SCALEON_FONTS = {
            "IBM Plex Sans": "Sans", "Arial": "Arial",
            "Serif (Times)": "Serif", "Helvetica": "Helvetica",
        }
        current_hex = (self.company.get("footer_color", "") or "#111111").lower()
        current_name = next((n for n, h in self.FOOTER_COLORS.items()
                             if h.lower() == current_hex), "Black")
        current_font_key = self.company.get("scaleon_font", "") or "Sans"
        current_font_name = next((n for n, k in self.SCALEON_FONTS.items()
                                  if k == current_font_key), "IBM Plex Sans")

        opts = ctk.CTkFrame(card, fg_color="transparent")
        opts.grid(row=1, column=0, columnspan=2, sticky="ew", padx=PAD, pady=(6, 0))
        opts.grid_columnconfigure((0, 1), weight=1)

        fc_box = ctk.CTkFrame(opts, fg_color="transparent")
        fc_box.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkLabel(fc_box, text="Footer Text Color", font=_font(11, "bold"),
                     text_color=("#374151", "#D1D5DB"), anchor="w").pack(
            anchor="w", pady=(0, 4))
        self.footer_color_var = ctk.StringVar(value=current_name)
        ctk.CTkOptionMenu(
            fc_box, values=list(self.FOOTER_COLORS.keys()),
            variable=self.footer_color_var, height=40, corner_radius=CORNER,
            font=_font(12), fg_color=ACCENT, button_color=ACCENT,
            button_hover_color=ACCENT_HOVER).pack(anchor="w", fill="x")

        sf_box = ctk.CTkFrame(opts, fg_color="transparent")
        sf_box.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ctk.CTkLabel(sf_box, text="ScaleOn Font (signature)",
                     font=_font(11, "bold"),
                     text_color=("#374151", "#D1D5DB"), anchor="w").pack(
            anchor="w", pady=(0, 4))
        self.scaleon_font_var = ctk.StringVar(value=current_font_name)
        ctk.CTkOptionMenu(
            sf_box, values=list(self.SCALEON_FONTS.keys()),
            variable=self.scaleon_font_var, height=40, corner_radius=CORNER,
            font=_font(12), fg_color=ACCENT, button_color=ACCENT,
            button_hover_color=ACCENT_HOVER).pack(anchor="w", fill="x")

        ctk.CTkButton(
            card, text="💾  Save Company Settings", command=self._save_company,
            height=48, corner_radius=CORNER, font=_font(14, "bold"),
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
        ).grid(row=2, column=0, columnspan=2, sticky="ew", padx=PAD, pady=PAD)
        return page

    def _upload_zone(self, master, key: str, label: str, row: int) -> None:
        ctk.CTkLabel(master, text=label, font=_font(11, "bold"),
                     text_color=("#374151", "#D1D5DB"), anchor="w").grid(
            row=row, column=0, sticky="ew", padx=PAD, pady=(10, 2))

        zone = ctk.CTkFrame(master, height=46, corner_radius=CORNER,
                            fg_color=("#F9FAFB", "#1E1E1E"),
                            border_width=1, border_color=("#E5E7EB", "#2A2A2A"))
        zone.grid(row=row + 1, column=0, sticky="ew", padx=PAD, pady=(0, 4))
        zone.grid_columnconfigure(0, weight=1)
        zone.grid_propagate(False)

        current = self.company.get(key, "")
        label_text = Path(current).name if current else "Click to upload an image"
        lbl = ctk.CTkLabel(zone, text=f"  🖼  {label_text}", anchor="w",
                           font=_font(11), text_color=("#6B7280", "#9CA3AF"))
        lbl.grid(row=0, column=0, sticky="ew", padx=8)

        def browse():
            path = filedialog.askopenfilename(
                title=f"Select {label}",
                filetypes=[("Images", "*.png *.jpg *.jpeg *.gif *.bmp")])
            if not path:
                return
            rel = utils.copy_asset(path, asset_dir(key), key, root=BASE_DIR)
            if rel:
                self.company.update({key: rel})
                lbl.configure(text=f"  ✅  {Path(rel).name}")

        ctk.CTkButton(zone, text="Browse", width=78, height=34,
                      corner_radius=8, font=_font(11, "bold"),
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      command=browse).grid(row=0, column=1, padx=6)
        # Enable drag & drop if tkinterdnd2 is available (optional).
        self._try_enable_dnd(zone, key, lbl)

    def _try_enable_dnd(self, widget, key: str, lbl) -> None:
        """Best-effort drag & drop using tkinterdnd2 if it is installed."""
        try:
            from tkinterdnd2 import DND_FILES  # type: ignore

            def on_drop(event):
                path = event.data.strip("{}")
                rel = utils.copy_asset(path, asset_dir(key), key, root=BASE_DIR)
                if rel:
                    self.company.update({key: rel})
                    lbl.configure(text=f"  ✅  {Path(rel).name}")

            widget.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
            widget.dnd_bind("<<Drop>>", on_drop)     # type: ignore[attr-defined]
        except Exception:
            pass  # Drag & drop simply not available; browse still works.

    def _save_company(self) -> None:
        values = {k: w.get().strip() for k, w in self.company_form.items()}
        if not values.get("company_name"):
            ModernDialog(self, "Company Name Required",
                         "Please enter at least the company name.", icon="⚠")
            return
        # Footer text colour (chosen from the panel; default Black).
        values["footer_color"] = self.FOOTER_COLORS.get(
            self.footer_color_var.get(), "#111111")
        # ScaleOn signature font (chosen from the panel; default Sans).
        values["scaleon_font"] = self.SCALEON_FONTS.get(
            self.scaleon_font_var.get(), "Sans")
        self.company.update(values)
        self.company.save()
        ModernDialog(self, "Settings Saved",
                     "Your company details have been saved.", icon="✅")


    # ================================================================== #
    # PAGE: Intern Verification
    # ================================================================== #
    def _build_verify_page(self) -> ctk.CTkFrame:
        page = self._page_shell(
            "Intern Verification",
            "Look up any generated document by its Intern ID or Certificate "
            "No. Works fully offline.")

        # Search row
        bar = ctk.CTkFrame(page, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=40, pady=(6, 10))
        bar.grid_columnconfigure(0, weight=1)

        self.verify_var = ctk.StringVar()
        entry = ctk.CTkEntry(
            bar, textvariable=self.verify_var, height=44,
            corner_radius=CORNER, font=_font(13),
            placeholder_text="Enter Intern ID (e.g. SO260015) or Certificate "
                             "No (e.g. SO-CERT-260001)")
        entry.grid(row=0, column=0, sticky="ew")
        entry.bind("<Return>", lambda e: self._do_verify())

        ctk.CTkButton(bar, text="Verify", command=self._do_verify, height=44,
                      width=130, corner_radius=CORNER, font=_font(13, "bold"),
                      fg_color=ACCENT, hover_color=ACCENT_HOVER).grid(
            row=0, column=1, padx=(10, 0))

        # Result card
        self.verify_result = ctk.CTkFrame(
            page, corner_radius=18, fg_color=("#FFFFFF", "#161616"),
            border_width=1, border_color=("#ECECEC", "#242424"))
        self.verify_result.grid(row=3, column=0, sticky="ew", padx=40, pady=8)
        self.verify_result.grid_columnconfigure(0, weight=1)
        self._verify_placeholder()
        return page

    def _verify_placeholder(self) -> None:
        for w in self.verify_result.winfo_children():
            w.destroy()
        ctk.CTkLabel(self.verify_result, text="🔎", font=_font(34)).grid(
            row=0, column=0, pady=(28, 4))
        ctk.CTkLabel(self.verify_result,
                     text="Enter an Intern ID or Certificate No above and "
                          "click Verify.",
                     font=_font(13), text_color=("#6B7280", "#9CA3AF")).grid(
            row=1, column=0, pady=(0, 28))

    def _do_verify(self) -> None:
        query = self.verify_var.get().strip()
        for w in self.verify_result.winfo_children():
            w.destroy()
        if not query:
            self._verify_placeholder()
            return

        # Intern IDs (SO260015) and certificate numbers (SO-CERT-260001) live
        # in different tables, so try both before reporting a miss.
        record = self.db.get(query)
        if record:
            self._render_verify_record(record)
            return

        certificate = self.cert_store.find(query)
        if certificate:
            self._render_verify_record(certificate, is_certificate=True)
            return

        ctk.CTkLabel(self.verify_result, text="❌", font=_font(34)).grid(
            row=0, column=0, pady=(28, 4))
        ctk.CTkLabel(self.verify_result, text="Invalid ID",
                     font=_font(16, "bold"),
                     text_color="#DC2626").grid(row=1, column=0)
        ctk.CTkLabel(self.verify_result,
                     text=f"No intern or certificate record found for "
                          f"'{query}'.",
                     font=_font(12),
                     text_color=("#6B7280", "#9CA3AF")).grid(
            row=2, column=0, pady=(2, 28))

    def _render_verify_record(self, rec: Dict[str, str],
                              is_certificate: bool = False) -> None:
        # Header strip with verified badge.
        head = ctk.CTkFrame(self.verify_result, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=24, pady=(22, 6))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text=rec.get("candidate_name", "—"),
                     font=_font(20, "bold"), anchor="w").grid(
            row=0, column=0, sticky="w")
        badge = "✓ Certificate Verified" if is_certificate else "✓ Verified"
        ctk.CTkLabel(head, text=badge, font=_font(12, "bold"),
                     text_color="#16A34A").grid(row=0, column=1, sticky="e")

        grid = ctk.CTkFrame(self.verify_result, fg_color="transparent")
        grid.grid(row=1, column=0, sticky="ew", padx=24, pady=(4, 22))
        grid.grid_columnconfigure((0, 1), weight=1)

        if is_certificate:
            rows = [
                ("Certificate No", rec.get("cert_no", "")),
                ("Certificate Type", rec.get("cert_type", "")
                 or "Completion Certificate"),
                ("Intern ID", rec.get("intern_id", "")),
                ("Position", rec.get("position", "")),
                ("Issue Date", utils.format_long_date(rec.get("issue_date", ""))),
                ("Start Date", utils.format_long_date(rec.get("start_date", ""))),
                ("End Date", utils.format_long_date(rec.get("end_date", ""))),
                ("Duration", rec.get("duration", "")),
                ("Status", rec.get("status", "")),
            ]
            # Optional certificate details.
            for label, key in (("Program", "program"),
                               ("Department", "department"),
                               ("Grade", "grade"), ("Remarks", "remarks")):
                if rec.get(key):
                    rows.append((label, rec.get(key, "")))
            rows.append(("Issued On", rec.get("created_at", "")))
        else:
            rows = [
                ("Intern ID", rec.get("intern_id", "")),
                ("Position", rec.get("position", "")),
                ("Domain", rec.get("domain", "")),
                ("Start Date", utils.format_long_date(rec.get("start_date", ""))),
                ("End Date", utils.format_long_date(rec.get("end_date", ""))),
                ("Duration", rec.get("duration", "")),
                ("Status", rec.get("status", "")),
                ("Generated", rec.get("created_at", "")),
            ]
            # Surface any certificate numbers issued to this intern so they can
            # be verified directly.
            certs = self.cert_store.certs_for_intern(rec.get("intern_id", ""))
            if certs:
                numbers = ", ".join(
                    c.get("cert_no", "") for c in certs if c.get("cert_no"))
                label = ("Certificate No" if len(certs) == 1
                         else f"Certificates ({len(certs)})")
                rows.insert(1, (label, numbers))
        for i, (label, value) in enumerate(rows):
            r, col = divmod(i, 2)
            cell = ctk.CTkFrame(grid, fg_color="transparent")
            cell.grid(row=r, column=col, sticky="ew", padx=6, pady=8)
            ctk.CTkLabel(cell, text=label.upper(), font=_font(10, "bold"),
                         text_color=("#9CA3AF", "#6B7280"), anchor="w").pack(
                anchor="w")
            # IDs are the values people actually need to copy.
            if value and label in ("Intern ID", "Certificate No",
                                   "Certificates"):
                line = ctk.CTkFrame(cell, fg_color="transparent")
                line.pack(anchor="w")
                ctk.CTkLabel(line, text=value, font=_font(13),
                             anchor="w").pack(side="left", padx=(0, 6))
                copy_button(line, value, width=26, height=22).pack(side="left")
            else:
                ctk.CTkLabel(cell, text=value or "—", font=_font(13),
                             anchor="w").pack(anchor="w")

        # PDF file row with open button.
        stored = rec.get("pdf_path", "")
        pdf_path = resolve_path(stored) if stored else None
        foot = ctk.CTkFrame(self.verify_result, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 22))
        foot.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(foot, text=f"📄  {pdf_path.name if pdf_path else '—'}",
                     font=_font(12), text_color=("#6B7280", "#9CA3AF"),
                     anchor="w").grid(row=0, column=0, sticky="w")
        if pdf_path and pdf_path.exists():
            ctk.CTkButton(foot, text="Open PDF",
                          command=lambda: utils.open_file(pdf_path),
                          height=34, width=110, corner_radius=CORNER,
                          font=_font(12, "bold"), fg_color=ACCENT,
                          hover_color=ACCENT_HOVER).grid(row=0, column=1)

    # ================================================================== #
    # PAGE: Generated Letters
    # ================================================================== #
    def _build_generated_page(self) -> ctk.CTkFrame:
        page = self._page_shell(
            "Generated Letters", "Browse, search and manage your saved PDFs.")

        controls = ctk.CTkFrame(page, fg_color="transparent")
        controls.grid(row=2, column=0, sticky="ew", padx=40, pady=(6, 4))
        controls.grid_columnconfigure(0, weight=1)

        self.search_var = ctk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._refresh_generated())
        ctk.CTkEntry(controls, textvariable=self.search_var, height=40,
                     corner_radius=CORNER, font=_font(12),
                     placeholder_text="🔍  Search by file name…").grid(
            row=0, column=0, sticky="ew")

        self.sort_var = ctk.StringVar(value="Newest")
        ctk.CTkOptionMenu(controls, values=["Newest", "Oldest", "Name (A-Z)"],
                          variable=self.sort_var,
                          command=lambda *_: self._refresh_generated(),
                          height=40, width=150, corner_radius=CORNER,
                          font=_font(12), fg_color=ACCENT, button_color=ACCENT,
                          button_hover_color=ACCENT_HOVER).grid(
            row=0, column=1, padx=(8, 8))

        ctk.CTkButton(controls, text="📂 Output Folder", height=40, width=150,
                      corner_radius=CORNER, font=_font(12, "bold"),
                      fg_color=("#F3F4F6", "#222222"),
                      hover_color=("#E5E7EB", "#2E2E2E"),
                      text_color=("#374151", "#D1D5DB"),
                      command=lambda: utils.open_file(OUTPUT_DIR)).grid(
            row=0, column=2)

        self.generated_list = ctk.CTkScrollableFrame(
            page, fg_color="transparent", height=420)
        self.generated_list.grid(row=3, column=0, sticky="nsew", padx=40,
                                 pady=(8, 30))
        self.generated_list.grid_columnconfigure(0, weight=1)
        return page

    def _list_pdfs(self) -> List[Path]:
        folders = {OUTPUT_DIR}
        files: List[Path] = []
        for folder in folders:
            if folder.exists():
                # Recursive so bulk-generated letters in dated/category
                # sub-folders (output/<date>/<category>/) also show up here.
                files.extend(folder.rglob("*.pdf"))
        return list(set(files))

    def _refresh_generated(self) -> None:
        if "generated" not in self.pages:
            return
        for child in self.generated_list.winfo_children():
            child.destroy()

        query = self.search_var.get().lower() if hasattr(self, "search_var") else ""
        files = [f for f in self._list_pdfs() if query in f.name.lower()]

        sort = self.sort_var.get() if hasattr(self, "sort_var") else "Newest"
        if sort == "Newest":
            files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        elif sort == "Oldest":
            files.sort(key=lambda f: f.stat().st_mtime)
        else:
            files.sort(key=lambda f: f.name.lower())

        if not files:
            ctk.CTkLabel(self.generated_list, text="No letters found.",
                         font=_font(13), text_color=("#9CA3AF", "#6B7280")).grid(
                row=0, column=0, pady=40)
            return

        for i, f in enumerate(files):
            self._generated_row(f, i)

    def _generated_row(self, path: Path, row: int) -> None:
        card = ctk.CTkFrame(self.generated_list, corner_radius=14,
                            fg_color=("#FFFFFF", "#161616"),
                            border_width=1, border_color=("#ECECEC", "#242424"))
        card.grid(row=row, column=0, sticky="ew", pady=5)
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(card, text="📄", font=_font(22)).grid(
            row=0, column=0, rowspan=2, padx=(16, 8), pady=10)

        ctk.CTkLabel(card, text=path.name, font=_font(13, "bold"),
                     anchor="w").grid(row=0, column=1, sticky="w", pady=(10, 0))
        modified = datetime.fromtimestamp(path.stat().st_mtime).strftime(
            "%d %b %Y, %H:%M")
        try:
            folder = path.parent.relative_to(OUTPUT_DIR)
        except ValueError:
            folder = path.parent
        ctk.CTkLabel(card, text=f"{folder}   •   {modified}",
                     font=_font(11), text_color=("#6B7280", "#9CA3AF"),
                     anchor="w").grid(row=1, column=1, sticky="w", pady=(0, 10))

        right = ctk.CTkFrame(card, fg_color="transparent")
        right.grid(row=0, column=2, rowspan=2, padx=12, pady=8)

        btns = ctk.CTkFrame(right, fg_color="transparent")
        btns.pack(anchor="e")

        def mk(text, cmd, danger=False):
            ctk.CTkButton(
                btns, text=text, command=cmd, width=70, height=32,
                corner_radius=8, font=_font(11, "bold"),
                fg_color="#EF4444" if danger else ("#F3F4F6", "#222222"),
                hover_color="#DC2626" if danger else ("#E5E7EB", "#2E2E2E"),
                text_color="#FFFFFF" if danger else ("#374151", "#D1D5DB"),
            ).pack(side="left", padx=3)

        mk("Open", lambda: utils.open_file(path))
        mk("Reveal", lambda: utils.reveal_in_folder(path))
        mk("Delete", lambda: self._delete_pdf(path), danger=True)
        # Issuing another document for someone lives on the Interns page,
        # where it is keyed on the Intern ID instead of guessed from a
        # file name.

    def _delete_pdf(self, path: Path) -> None:
        def do_delete():
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            self._refresh_generated()

        ModernDialog(
            self, "Delete Letter?",
            f"This will permanently delete '{path.name}'.", icon="🗑",
            actions=[("Delete", do_delete, True), ("Cancel", None, False)])

    # ================================================================== #
    # PAGE: About
    # ================================================================== #
    def _build_about_page(self) -> ctk.CTkFrame:
        page = self._page_shell("About")
        card = ctk.CTkFrame(page, corner_radius=18,
                            fg_color=("#FFFFFF", "#161616"),
                            border_width=1, border_color=("#ECECEC", "#242424"))
        card.grid(row=2, column=0, sticky="ew", padx=40, pady=16)

        ctk.CTkLabel(card, text="📄", font=_font(48)).pack(pady=(28, 6))
        ctk.CTkLabel(card, text=settings.APP_NAME,
                     font=_font(22, "bold")).pack()
        ctk.CTkLabel(card, text=f"Version {settings.APP_VERSION}",
                     font=_font(11),
                     text_color=("#9CA3AF", "#6B7280")).pack(pady=(2, 0))
        ctk.CTkLabel(card, text="A polished, offline HR utility for generating "
                     "professional company documents in seconds.",
                     font=_font(13), wraplength=520, justify="center",
                     text_color=("#6B7280", "#9CA3AF")).pack(pady=(8, 16),
                                                             padx=30)

        shortcuts = ("Keyboard Shortcuts\n"
                     "Ctrl + G   Generate the current document\n"
                     "Ctrl + R   Reset the form\n"
                     f"Ctrl + 1…{len(self.NAV_ITEMS)}  Switch pages")
        ctk.CTkLabel(card, text=shortcuts, font=_font(12), justify="left",
                     text_color=("#374151", "#D1D5DB")).pack(pady=(0, 20))

        # Where the data lives. An installed copy keeps it in AppData rather
        # than beside the .exe, so it has to be discoverable - this is the
        # folder to back up, and the one to copy to a new machine.
        data_box = ctk.CTkFrame(card, fg_color="transparent")
        data_box.pack(pady=(0, 6), padx=30, fill="x")
        modes = {
            "installed": "Installed copy \u2014 your data is kept per user",
            "portable": "Portable copy \u2014 your data travels with this folder",
            "source": "Running from source",
            "custom": "Data folder set by CERTIFLOW_DATA",
        }
        ctk.CTkLabel(data_box,
                     text=modes.get(settings.DATA_MODE, settings.DATA_MODE),
                     font=_font(11, "bold"),
                     text_color=("#374151", "#D1D5DB")).pack()
        ctk.CTkLabel(data_box, text=str(settings.BASE_DIR), font=_font(10),
                     wraplength=520, justify="center",
                     text_color=("#9CA3AF", "#6B7280")).pack(pady=(2, 8))
        row = ctk.CTkFrame(data_box, fg_color="transparent")
        row.pack()
        ctk.CTkButton(
            row, text="\U0001F4C2  Open Data Folder",
            command=lambda: utils.reveal_in_folder(settings.BASE_DIR),
            height=32, width=160, corner_radius=CORNER, font=_font(11, "bold"),
            fg_color=("#F3F4F6", "#222222"),
            hover_color=("#E5E7EB", "#2E2E2E"),
            text_color=("#374151", "#D1D5DB")).grid(row=0, column=0, padx=4)
        copy_button(row, lambda: str(settings.BASE_DIR), width=32, height=32,
                    tooltip="Copy data folder path").grid(row=0, column=1,
                                                          padx=4)

        ctk.CTkLabel(card, text="Built with Python, CustomTkinter & ReportLab",
                     font=_font(11), text_color=("#9CA3AF", "#6B7280")).pack(
            pady=(16, 24))
        return page

    # ------------------------------------------------------------------ #
    # Keyboard shortcuts
    # ------------------------------------------------------------------ #
    def _bind_shortcuts(self) -> None:
        # Actions are delegated to whichever page owns them, so a shortcut can
        # never call into a page that no longer exists.
        self.bind("<Control-g>", lambda e: self._page_action("create", "_issue"))
        self.bind("<Control-r>", lambda e: self._page_action("create", "_reset"))
        for i, (_label, key) in enumerate(self.NAV_ITEMS, start=1):
            self.bind(f"<Control-Key-{i}>", lambda e, k=key: self.show_page(k))

    def _page_action(self, page_key: str, method: str) -> None:
        """Run an action on a page, switching to it first."""
        page = self.pages.get(page_key)
        handler = getattr(page, method, None)
        if handler is None:
            return
        self.show_page(page_key)
        handler()

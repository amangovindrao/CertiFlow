"""
designer_page.py
----------------
The visual Template Designer: a drag-and-drop canvas for building custom
document layouts.

Self-contained by design. It reads and writes JSON templates through
:mod:`template_schema` and renders them through :mod:`template_renderer`, so
none of the built-in documents share code with anything here. If you never save
a template, the rest of the app behaves exactly as it did before.

The canvas draws the page at a fitted scale and every element as ordinary
canvas items tagged with the element id, which is what makes hit-testing and
dragging straightforward.
"""

from __future__ import annotations

import copy
import json
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog
from typing import Any, Dict, List, Optional

import customtkinter as ctk

import template_renderer as tr
import template_schema as ts
import utils
from settings import BASE_DIR
from ui import (ACCENT, ACCENT_HOVER, CORNER, PAD, ModernDialog, _font)

try:
    from PIL import Image, ImageTk
except ImportError:                                   # pragma: no cover
    Image = ImageTk = None  # type: ignore

# Canvas chrome
PAGE_SHADOW = "#D8DCE3"
SELECT_COLOR = "#2563EB"
GUIDE_COLOR = "#F59E0B"
SNAP_TOLERANCE = 6.0            # points
MAX_UNDO = 40


class DesignerPage(ctk.CTkFrame):
    """Design custom document templates by dragging elements on a page."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.template: Dict[str, Any] = ts.starter_template("My Template")
        self.selected_id: Optional[str] = None
        self.scale = 1.0
        self.origin = (0, 0)
        self._drag: Optional[Dict[str, Any]] = None
        self._images: List[Any] = []          # keep PhotoImage refs alive
        self._undo: List[str] = []
        self._dirty = False
        self._prop_job: Optional[str] = None

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self._build_header()
        self._build_toolbar()
        self._build_body()
        self._refresh_template_list()
        self._redraw()

    # ================================================================== #
    # Layout
    # ================================================================== #
    def _build_header(self) -> None:
        ctk.CTkLabel(self, text="Template Designer", font=_font(26, "bold"),
                     anchor="w").grid(row=0, column=0, columnspan=3, sticky="w",
                                      padx=40, pady=(30, 0))
        ctk.CTkLabel(self, text="Drag anything on the page to move it. Custom "
                     "templates appear as extra document types once saved - the "
                     "built-in documents are never modified.",
                     font=_font(13), text_color=("#6B7280", "#9CA3AF"),
                     anchor="w").grid(row=1, column=0, columnspan=3, sticky="w",
                                      padx=40, pady=(2, 10))

    def _build_toolbar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, columnspan=3, sticky="ew", padx=40,
                 pady=(0, 8))
        bar.grid_columnconfigure(5, weight=1)      # spacer

        self.template_var = ctk.StringVar(value="")
        self.template_menu = ctk.CTkOptionMenu(
            bar, values=["(no saved templates)"], variable=self.template_var,
            command=self._on_pick_template, width=200, height=36,
            corner_radius=CORNER, font=_font(12),
            fg_color=("#F3F4F6", "#222222"), button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
            text_color=("#374151", "#D1D5DB"))
        self.template_menu.grid(row=0, column=0)

        def button(text, command, col, primary=False, width=104):
            ctk.CTkButton(
                bar, text=text, command=command, height=36, width=width,
                corner_radius=CORNER, font=_font(12, "bold"),
                fg_color=ACCENT if primary else ("#F3F4F6", "#222222"),
                hover_color=ACCENT_HOVER if primary else ("#E5E7EB", "#2E2E2E"),
                text_color="#FFFFFF" if primary else ("#374151", "#D1D5DB"),
            ).grid(row=0, column=col, padx=(8, 0))

        # "New from" builds an editable copy of any built-in document.
        self.base_var = ctk.StringVar(value="New from…")
        ctk.CTkOptionMenu(
            bar, values=list(ts.STARTERS) + ["Blank page"],
            variable=self.base_var, command=self._on_pick_base, width=190,
            height=36, corner_radius=CORNER, font=_font(12), fg_color=ACCENT,
            button_color=ACCENT, button_hover_color=ACCENT_HOVER).grid(
            row=0, column=1, padx=(8, 0))
        button("💾  Save", self._save, 2, primary=True, width=96)
        button("Save As", self._save_as, 3, width=92)
        button("🗑", self._delete_template, 4, width=44)
        button("👁  Preview PDF", self._preview_pdf, 6, width=140)

        self.page_var = ctk.StringVar(value=self.template.get("page",
                                                             ts.DEFAULT_PAGE))
        ctk.CTkOptionMenu(
            bar, values=list(ts.PAGE_SIZES), variable=self.page_var,
            command=self._on_page_change, width=150, height=36,
            corner_radius=CORNER, font=_font(12),
            fg_color=("#F3F4F6", "#222222"), button_color=ACCENT,
            button_hover_color=ACCENT_HOVER,
            text_color=("#374151", "#D1D5DB")).grid(row=0, column=7,
                                                    padx=(8, 0))

    def _build_body(self) -> None:
        # -- left: element list + add buttons ---------------------------- #
        left = ctk.CTkFrame(self, corner_radius=14,
                            fg_color=("#FFFFFF", "#161616"), border_width=1,
                            border_color=("#ECECEC", "#242424"), width=212)
        left.grid(row=3, column=0, sticky="nsew", padx=(40, 8), pady=(0, 24))
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)
        left.grid_propagate(False)

        ctk.CTkLabel(left, text="Elements", font=_font(13, "bold"),
                     anchor="w").grid(row=0, column=0, sticky="w", padx=12,
                                      pady=(12, 4))
        self.element_list = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self.element_list.grid(row=1, column=0, sticky="nsew", padx=6)
        self.element_list.grid_columnconfigure(0, weight=1)

        add = ctk.CTkFrame(left, fg_color="transparent")
        add.grid(row=2, column=0, sticky="ew", padx=8, pady=(6, 10))
        add.grid_columnconfigure((0, 1), weight=1)
        for i, (label, kind) in enumerate((("Text", "text"),
                                           ("Field", "field"),
                                           ("Image", "image"),
                                           ("Line", "line"),
                                           ("Box", "rect"),
                                           ("Badge", "pill"))):
            ctk.CTkButton(
                add, text=f"＋ {label}", command=lambda k=kind: self._add(k),
                height=30, corner_radius=8, font=_font(11),
                fg_color=("#F3F4F6", "#222222"),
                hover_color=("#E5E7EB", "#2E2E2E"),
                text_color=("#374151", "#D1D5DB"),
            ).grid(row=i // 2, column=i % 2, sticky="ew", padx=2, pady=2)

        # -- centre: canvas --------------------------------------------- #
        middle = ctk.CTkFrame(self, corner_radius=14,
                              fg_color=("#EEF1F5", "#0D0D0D"), border_width=1,
                              border_color=("#ECECEC", "#242424"))
        middle.grid(row=3, column=1, sticky="nsew", pady=(0, 24))
        middle.grid_rowconfigure(0, weight=1)
        middle.grid_columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(middle, highlightthickness=0, bd=0,
                                bg="#EEF1F5")
        self.canvas.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self.canvas.bind("<Configure>", lambda e: self._redraw())
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-1>", lambda e: self._focus_properties())

        self.hint = ctk.CTkLabel(middle, text="", font=_font(11),
                                 text_color=("#6B7280", "#9CA3AF"))
        self.hint.grid(row=1, column=0, sticky="w", padx=16, pady=(0, 8))

        # -- right: properties ------------------------------------------ #
        right = ctk.CTkFrame(self, corner_radius=14,
                             fg_color=("#FFFFFF", "#161616"), border_width=1,
                             border_color=("#ECECEC", "#242424"), width=292)
        right.grid(row=3, column=2, sticky="nsew", padx=(8, 40), pady=(0, 24))
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)
        right.grid_propagate(False)
        ctk.CTkLabel(right, text="Properties", font=_font(13, "bold"),
                     anchor="w").grid(row=0, column=0, sticky="w", padx=12,
                                      pady=(12, 4))
        self.props = ctk.CTkScrollableFrame(right, fg_color="transparent")
        self.props.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 10))
        self.props.grid_columnconfigure(0, weight=1)

        # Keyboard: nudge, delete, undo.
        for widget in (self.canvas,):
            widget.bind("<Left>", lambda e: self._nudge(-1, 0))
            widget.bind("<Right>", lambda e: self._nudge(1, 0))
            widget.bind("<Up>", lambda e: self._nudge(0, -1))
            widget.bind("<Down>", lambda e: self._nudge(0, 1))
            widget.bind("<Shift-Left>", lambda e: self._nudge(-10, 0))
            widget.bind("<Shift-Right>", lambda e: self._nudge(10, 0))
            widget.bind("<Shift-Up>", lambda e: self._nudge(0, -10))
            widget.bind("<Shift-Down>", lambda e: self._nudge(0, 10))
            widget.bind("<Delete>", lambda e: self._delete_selected())
            widget.bind("<Control-z>", lambda e: self._undo_last())

    # ================================================================== #
    # Template state
    # ================================================================== #
    def _elements(self) -> List[Dict[str, Any]]:
        return self.template.setdefault("elements", [])

    def _find(self, element_id: str) -> Optional[Dict[str, Any]]:
        for element in self._elements():
            if element.get("id") == element_id:
                return element
        return None

    def _selected(self) -> Optional[Dict[str, Any]]:
        return self._find(self.selected_id) if self.selected_id else None

    def _snapshot(self) -> None:
        """Remember the current state so Ctrl+Z can come back to it."""
        try:
            self._undo.append(json.dumps(self.template))
        except TypeError:
            return
        if len(self._undo) > MAX_UNDO:
            self._undo.pop(0)

    def _undo_last(self) -> None:
        if not self._undo:
            return
        self.template = json.loads(self._undo.pop())
        self.page_var.set(self.template.get("page", ts.DEFAULT_PAGE))
        if not self._find(self.selected_id or ""):
            self.selected_id = None
        self._dirty = True
        self._redraw()

    def _touch(self) -> None:
        self._dirty = True

    # ================================================================== #
    # Canvas drawing
    # ================================================================== #
    def _compute_scale(self) -> None:
        page_w, page_h = ts.page_size(self.template)
        avail_w = max(self.canvas.winfo_width() - 40, 80)
        avail_h = max(self.canvas.winfo_height() - 40, 80)
        self.scale = max(min(avail_w / page_w, avail_h / page_h), 0.05)
        draw_w, draw_h = page_w * self.scale, page_h * self.scale
        self.origin = ((self.canvas.winfo_width() - draw_w) / 2,
                       (self.canvas.winfo_height() - draw_h) / 2)

    def _to_canvas(self, x: float, y: float) -> tuple:
        return (self.origin[0] + x * self.scale, self.origin[1] + y * self.scale)

    def _to_points(self, cx: float, cy: float) -> tuple:
        return ((cx - self.origin[0]) / self.scale,
                (cy - self.origin[1]) / self.scale)

    def _canvas_font(self, element: Dict[str, Any]):
        named = str(element.get("font", "body"))
        size = max(int(float(element.get("size", 12) or 12) * self.scale), 6)
        family = {"serif": "Georgia", "display": "Poppins",
                  "display-bold": "Poppins"}.get(named, "Segoe UI")
        weight = "bold" if named.endswith("bold") else "normal"
        return (family, size, weight)

    def _redraw(self) -> None:
        if not self.canvas.winfo_exists():
            return
        self.canvas.delete("all")
        self._images.clear()
        self._compute_scale()

        page_w, page_h = ts.page_size(self.template)
        x0, y0 = self._to_canvas(0, 0)
        x1, y1 = self._to_canvas(page_w, page_h)
        self.canvas.create_rectangle(x0 + 4, y0 + 4, x1 + 4, y1 + 4,
                                     fill=PAGE_SHADOW, outline="")
        self.canvas.create_rectangle(
            x0, y0, x1, y1,
            fill=self.template.get("background") or "#FFFFFF",
            outline="#C9CFD8")

        values = ts.sample_values()
        for element in self._elements():
            if element.get("visible", True):
                self._draw_element(element, values)
        self._draw_selection()
        self._render_element_list()
        self._render_properties()
        self._update_hint()

    def _draw_element(self, element: Dict[str, Any],
                      values: Dict[str, str]) -> None:
        tag = f"el:{element.get('id')}"
        kind = element.get("type")
        x = float(element.get("x", 0) or 0)
        y = float(element.get("y", 0) or 0)
        colour = str(element.get("color") or "#111111")

        if kind in ("text", "field"):
            text = ts.fill(element.get("text", ""), values) or "(empty)"
            cx, cy = self._to_canvas(x, y)
            align = str(element.get("align", "center"))
            anchor = {"left": "nw", "center": "n", "right": "ne"}.get(align, "n")
            width = float(element.get("width", 0) or 0)
            self.canvas.create_text(
                cx, cy, text=text, fill=colour, font=self._canvas_font(element),
                anchor=anchor, tags=(tag, "element"),
                width=int(width * self.scale) if width > 0 else 0,
                justify={"left": "left", "center": "center",
                         "right": "right"}.get(align, "center"))
        elif kind == "pill":
            text = ts.fill(element.get("text", ""), values) or "BADGE"
            height = float(element.get("h", 22) or 22)
            pad = float(element.get("pad_x", 16) or 16)
            font = self._canvas_font(element)
            approx = max(len(text) * font[1] * 0.62, 40) + pad * 2 * self.scale
            cx, cy = self._to_canvas(x, y)
            half = approx / 2
            self.canvas.create_rectangle(
                cx - half, cy, cx + half, cy + height * self.scale,
                outline=colour, tags=(tag, "element"))
            self.canvas.create_text(cx, cy + height * self.scale / 2,
                                    text=text, fill=colour, font=font,
                                    tags=(tag, "element"))
        elif kind == "line":
            half = float(element.get("w", 0) or 0) / 2
            ax, ay = self._to_canvas(x - half, y)
            bx, _by = self._to_canvas(x + half, y)
            self.canvas.create_line(
                ax, ay, bx, ay, fill=colour,
                width=max(float(element.get("thickness", 1) or 1) * self.scale,
                          1),
                tags=(tag, "element"))
        elif kind == "rect":
            ax, ay = self._to_canvas(x, y)
            bx, by = self._to_canvas(x + float(element.get("w", 0) or 0),
                                     y + float(element.get("h", 0) or 0))
            self.canvas.create_rectangle(
                ax, ay, bx, by, outline=colour,
                fill=str(element.get("fill") or ""),
                width=max(float(element.get("thickness", 1) or 1) * self.scale,
                          1),
                tags=(tag, "element"))
        elif kind == "image":
            self._draw_image(element, tag)

    def _draw_image(self, element: Dict[str, Any], tag: str) -> None:
        x = float(element.get("x", 0) or 0)
        y = float(element.get("y", 0) or 0)
        w = float(element.get("w", 0) or 0)
        h = float(element.get("h", 0) or 0)
        ax, ay = self._to_canvas(x, y)
        bx, by = self._to_canvas(x + w, y + h)

        photo = None
        if Image is not None and ImageTk is not None:
            source = str(element.get("source") or "")
            raw = (self.app.company.get(source, "") if source
                   else element.get("path", ""))
            resolved = None
            if raw:
                candidate = Path(str(raw).replace("\\", "/"))
                if not candidate.is_absolute():
                    candidate = BASE_DIR / candidate
                resolved = candidate if candidate.exists() else None
            if resolved:
                try:
                    img = Image.open(resolved).convert("RGBA")
                    box = (max(int(bx - ax), 1), max(int(by - ay), 1))
                    img.thumbnail(box, Image.LANCZOS)
                    opacity = float(element.get("opacity", 1.0) or 1.0)
                    if opacity < 1.0:
                        alpha = img.split()[3].point(
                            lambda v: int(v * opacity))
                        img.putalpha(alpha)
                    photo = ImageTk.PhotoImage(img)
                except Exception:
                    photo = None
        if photo is not None:
            self._images.append(photo)
            self.canvas.create_image(ax, ay, image=photo, anchor="nw",
                                     tags=(tag, "element"))
        else:
            self.canvas.create_rectangle(ax, ay, bx, by, outline="#9CA3AF",
                                         dash=(3, 2), tags=(tag, "element"))
            self.canvas.create_text(
                (ax + bx) / 2, (ay + by) / 2,
                text=str(element.get("source") or "image"),
                fill="#9CA3AF", font=("Segoe UI", 9), tags=(tag, "element"))

    def _draw_selection(self) -> None:
        element = self._selected()
        if element is None:
            return
        items = self.canvas.find_withtag(f"el:{element['id']}")
        if not items:
            return
        box = self.canvas.bbox(f"el:{element['id']}")
        if not box:
            return
        pad = 4
        self.canvas.create_rectangle(box[0] - pad, box[1] - pad,
                                     box[2] + pad, box[3] + pad,
                                     outline=SELECT_COLOR, dash=(4, 3),
                                     tags="selection")
        for hx, hy in ((box[0] - pad, box[1] - pad), (box[2] + pad, box[1] - pad),
                       (box[0] - pad, box[3] + pad), (box[2] + pad, box[3] + pad)):
            self.canvas.create_rectangle(hx - 2, hy - 2, hx + 2, hy + 2,
                                         fill=SELECT_COLOR,
                                         outline=SELECT_COLOR, tags="selection")

    def _update_hint(self) -> None:
        page_w, page_h = ts.page_size(self.template)
        element = self._selected()
        where = ""
        if element:
            where = (f"   •   {ts.element_label(element)} at "
                     f"x {float(element.get('x', 0)):.0f}, "
                     f"y {float(element.get('y', 0)):.0f} pt")
        self.hint.configure(
            text=f"{self.template.get('name','Untitled')}"
                 f"{'*' if self._dirty else ''}   •   "
                 f"{page_w:.0f} × {page_h:.0f} pt   •   "
                 f"{len(self._elements())} elements   •   "
                 f"zoom {self.scale * 100:.0f}%{where}")

    # ================================================================== #
    # Element list
    # ================================================================== #
    def _render_element_list(self) -> None:
        for child in self.element_list.winfo_children():
            child.destroy()
        for index, element in enumerate(self._elements()):
            selected = element.get("id") == self.selected_id
            row = ctk.CTkFrame(
                self.element_list, corner_radius=8,
                fg_color=("#EFF3FF", "#1E293B") if selected else "transparent")
            row.grid(row=index, column=0, sticky="ew", pady=1)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkButton(
                row, text=ts.element_label(element), anchor="w", height=26,
                corner_radius=6, font=_font(11, "bold" if selected else "normal"),
                fg_color="transparent", hover_color=("#DCE6FF", "#27364B"),
                text_color=ACCENT if selected else ("#374151", "#D1D5DB"),
                command=lambda e=element: self._select(e.get("id")),
            ).grid(row=0, column=0, sticky="ew")
            ctk.CTkButton(
                row, text="◉" if element.get("visible", True) else "○",
                width=24, height=24, corner_radius=6, font=_font(10),
                fg_color="transparent", hover_color=("#DCE6FF", "#27364B"),
                text_color=("#6B7280", "#9CA3AF"),
                command=lambda e=element: self._toggle_visible(e),
            ).grid(row=0, column=1)

    def _toggle_visible(self, element: Dict[str, Any]) -> None:
        self._snapshot()
        element["visible"] = not element.get("visible", True)
        self._touch()
        self._redraw()

    def _select(self, element_id: Optional[str]) -> None:
        self.selected_id = element_id
        self._redraw()
        self.canvas.focus_set()

    # ================================================================== #
    # Properties panel
    # ================================================================== #
    def _render_properties(self) -> None:
        for child in self.props.winfo_children():
            child.destroy()
        element = self._selected()
        if element is None:
            ctk.CTkLabel(self.props,
                         text="Select an element on the page\nor in the list.",
                         font=_font(12), justify="left",
                         text_color=("#6B7280", "#9CA3AF")).grid(
                row=0, column=0, sticky="w", padx=8, pady=12)
            return

        row = 0
        kind = element.get("type", "text")
        ctk.CTkLabel(self.props, text=kind.upper(), font=_font(10, "bold"),
                     text_color=("#9CA3AF", "#6B7280"), anchor="w").grid(
            row=row, column=0, sticky="ew", padx=8)
        row += 1

        row = self._prop_text(element, "name", "Label (optional)", row)
        if kind in ("text", "field", "pill"):
            row = self._prop_text(element, "text", "Text", row, multiline=True)
            if kind == "field":
                row = self._prop_insert_field(element, row)
        if kind in ("text", "field", "pill"):
            row = self._prop_choice(element, "font", "Font", ts.FONT_CHOICES, row)
            row = self._prop_number(element, "size", "Font size", row)
            row = self._prop_number(element, "tracking", "Letter spacing", row)
        if kind in ("text", "field"):
            row = self._prop_choice(element, "align", "Align", ts.ALIGNMENTS, row)
            row = self._prop_number(element, "width", "Wrap width (0 = off)", row)
            row = self._prop_number(element, "leading", "Line height (0 = auto)",
                                    row)
        if kind == "image":
            row = self._prop_choice(element, "source", "Asset",
                                    ts.IMAGE_SOURCES, row)
            row = self._prop_number(element, "opacity", "Opacity (0-1)", row)
        if kind in ("image", "rect", "pill"):
            row = self._prop_number(element, "h", "Height", row)
        if kind in ("image", "rect", "line"):
            row = self._prop_number(element, "w", "Width", row)
        if kind in ("line", "rect", "pill"):
            row = self._prop_number(element, "thickness", "Line weight", row)
        if kind == "rect":
            row = self._prop_number(element, "radius", "Corner radius", row)
            row = self._prop_color(element, "fill", "Fill (blank = none)", row)
        if kind != "image":
            row = self._prop_color(element, "color", "Colour", row)

        row = self._prop_number(element, "x", "X (pt from left)", row)
        row = self._prop_number(element, "y", "Y (pt from top)", row)

        actions = ctk.CTkFrame(self.props, fg_color="transparent")
        actions.grid(row=row, column=0, sticky="ew", padx=8, pady=(14, 8))
        actions.grid_columnconfigure((0, 1), weight=1)
        for i, (label, command) in enumerate((
                ("Centre", self._centre_selected),
                ("Duplicate", self._duplicate_selected),
                ("Bring forward", lambda: self._reorder(1)),
                ("Send back", lambda: self._reorder(-1)))):
            ctk.CTkButton(actions, text=label, command=command, height=30,
                          corner_radius=8, font=_font(11),
                          fg_color=("#F3F4F6", "#222222"),
                          hover_color=("#E5E7EB", "#2E2E2E"),
                          text_color=("#374151", "#D1D5DB")).grid(
                row=i // 2, column=i % 2, sticky="ew", padx=2, pady=2)
        ctk.CTkButton(actions, text="🗑  Delete element",
                      command=self._delete_selected, height=30,
                      corner_radius=8, font=_font(11, "bold"),
                      fg_color=("#F3F4F6", "#222222"),
                      hover_color=("#FEE2E2", "#3F1D1D"),
                      text_color=("#B91C1C", "#FCA5A5")).grid(
            row=2, column=0, columnspan=2, sticky="ew", padx=2, pady=(6, 2))

    def _prop_label(self, text: str, row: int) -> None:
        ctk.CTkLabel(self.props, text=text, font=_font(10, "bold"),
                     text_color=("#6B7280", "#9CA3AF"), anchor="w").grid(
            row=row, column=0, sticky="ew", padx=8, pady=(8, 2))

    def _commit(self, element: Dict[str, Any], key: str, value: Any,
                redraw: bool = True) -> None:
        if element.get(key) == value:
            return
        element[key] = value
        self._touch()
        if redraw:
            # Debounced so typing does not redraw on every keystroke.
            if self._prop_job:
                self.after_cancel(self._prop_job)
            self._prop_job = self.after(180, self._redraw_keep_focus)

    def _redraw_keep_focus(self) -> None:
        self._prop_job = None
        focused = self.focus_get()
        self._redraw()
        try:
            if focused is not None and focused.winfo_exists():
                focused.focus_set()
        except Exception:
            pass

    def _prop_text(self, element, key, label, row, multiline: bool = False):
        self._prop_label(label, row)
        row += 1
        if multiline:
            box = ctk.CTkTextbox(self.props, height=74, corner_radius=CORNER,
                                 font=_font(11))
            box.grid(row=row, column=0, sticky="ew", padx=8)
            box.insert("1.0", str(element.get(key, "")))
            box.bind("<KeyRelease>",
                     lambda e: self._commit(element, key,
                                            box.get("1.0", "end-1c")))
        else:
            entry = ctk.CTkEntry(self.props, height=32, corner_radius=CORNER,
                                 font=_font(11))
            entry.grid(row=row, column=0, sticky="ew", padx=8)
            entry.insert(0, str(element.get(key, "")))
            entry.bind("<KeyRelease>",
                       lambda e: self._commit(element, key, entry.get()))
        return row + 1

    def _prop_number(self, element, key, label, row):
        self._prop_label(label, row)
        row += 1
        entry = ctk.CTkEntry(self.props, height=32, corner_radius=CORNER,
                             font=_font(11))
        entry.grid(row=row, column=0, sticky="ew", padx=8)
        entry.insert(0, f"{float(element.get(key, 0) or 0):g}")

        def apply(_event=None):
            try:
                self._commit(element, key, float(entry.get() or 0))
            except ValueError:
                pass

        entry.bind("<KeyRelease>", apply)
        return row + 1

    def _prop_choice(self, element, key, label, values, row):
        self._prop_label(label, row)
        row += 1
        var = ctk.StringVar(value=str(element.get(key, values[0])))
        ctk.CTkOptionMenu(
            self.props, values=list(values), variable=var, height=32,
            corner_radius=CORNER, font=_font(11), fg_color=("#F3F4F6", "#222222"),
            button_color=ACCENT, button_hover_color=ACCENT_HOVER,
            text_color=("#374151", "#D1D5DB"),
            command=lambda value: self._commit(element, key, value)).grid(
            row=row, column=0, sticky="ew", padx=8)
        return row + 1

    def _prop_color(self, element, key, label, row):
        self._prop_label(label, row)
        row += 1
        holder = ctk.CTkFrame(self.props, fg_color="transparent")
        holder.grid(row=row, column=0, sticky="ew", padx=8)
        holder.grid_columnconfigure(0, weight=1)
        entry = ctk.CTkEntry(holder, height=32, corner_radius=CORNER,
                             font=_font(11))
        entry.grid(row=0, column=0, sticky="ew")
        entry.insert(0, str(element.get(key, "")))
        entry.bind("<KeyRelease>",
                   lambda e: self._commit(element, key, entry.get().strip()))

        def pick():
            current = str(element.get(key, "") or "#111111")
            chosen = colorchooser.askcolor(
                color=current if current.startswith("#") else "#111111",
                parent=self)
            if chosen and chosen[1]:
                entry.delete(0, "end")
                entry.insert(0, chosen[1])
                self._commit(element, key, chosen[1])

        ctk.CTkButton(holder, text="🎨", width=36, height=32, corner_radius=8,
                      font=_font(12), command=pick,
                      fg_color=("#F3F4F6", "#222222"),
                      hover_color=("#E5E7EB", "#2E2E2E"),
                      text_color=("#374151", "#D1D5DB")).grid(row=0, column=1,
                                                              padx=(6, 0))
        return row + 1

    def _prop_insert_field(self, element, row):
        self._prop_label("Replace with field", row)
        row += 1
        var = ctk.StringVar(value="choose…")
        ctk.CTkOptionMenu(
            self.props, values=list(ts.PLACEHOLDERS), variable=var, height=32,
            corner_radius=CORNER, font=_font(11),
            fg_color=("#F3F4F6", "#222222"), button_color=ACCENT,
            button_hover_color=ACCENT_HOVER, text_color=("#374151", "#D1D5DB"),
            command=lambda label: self._commit(
                element, "text", "{" + ts.PLACEHOLDERS[label] + "}")).grid(
            row=row, column=0, sticky="ew", padx=8)
        return row + 1

    def _focus_properties(self) -> None:
        if self._selected() is not None:
            self.props.focus_set()

    # ================================================================== #
    # Mouse interaction
    # ================================================================== #
    def _element_at(self, cx: float, cy: float) -> Optional[str]:
        """Topmost element under the cursor, if any."""
        items = self.canvas.find_overlapping(cx - 2, cy - 2, cx + 2, cy + 2)
        for item in reversed(items):
            for tag in self.canvas.gettags(item):
                if tag.startswith("el:"):
                    return tag[3:]
        return None

    def _on_press(self, event) -> None:
        self.canvas.focus_set()
        element_id = self._element_at(event.x, event.y)
        if element_id != self.selected_id:
            self.selected_id = element_id
            self._redraw()
        element = self._selected()
        if element is None:
            self._drag = None
            return
        px, py = self._to_points(event.x, event.y)
        self._snapshot()
        self._drag = {"dx": px - float(element.get("x", 0) or 0),
                      "dy": py - float(element.get("y", 0) or 0),
                      "moved": False}

    def _on_drag(self, event) -> None:
        element = self._selected()
        if element is None or self._drag is None:
            return
        px, py = self._to_points(event.x, event.y)
        page_w, _page_h = ts.page_size(self.template)
        x = px - self._drag["dx"]
        y = py - self._drag["dy"]

        # Snap to the page centre for centred elements, and to whole points.
        self.canvas.delete("guide")
        centre = page_w / 2
        if str(element.get("align", "center")) == "center" or \
                element.get("type") in ("pill", "line"):
            if abs(x - centre) <= SNAP_TOLERANCE:
                x = centre
                gx, gy0 = self._to_canvas(centre, 0)
                _gx, gy1 = self._to_canvas(centre, ts.page_size(self.template)[1])
                self.canvas.create_line(gx, gy0, gx, gy1, fill=GUIDE_COLOR,
                                        dash=(3, 3), tags="guide")
        element["x"] = round(x, 1)
        element["y"] = round(y, 1)
        self._drag["moved"] = True
        self._touch()
        # Move the existing items instead of a full redraw: smooth dragging.
        self.canvas.delete("selection")
        self._reposition(element)
        self._draw_selection()
        self._update_hint()

    def _reposition(self, element: Dict[str, Any]) -> None:
        """Redraw just one element in place (cheap during a drag)."""
        tag = f"el:{element['id']}"
        self.canvas.delete(tag)
        self._draw_element(element, ts.sample_values())

    def _on_release(self, _event) -> None:
        if self._drag and self._drag.get("moved"):
            self.canvas.delete("guide")
            self._redraw()
        elif self._undo:
            # Nothing moved, so drop the snapshot taken on press.
            self._undo.pop()
        self._drag = None

    def _nudge(self, dx: float, dy: float) -> None:
        element = self._selected()
        if element is None:
            return
        self._snapshot()
        element["x"] = round(float(element.get("x", 0) or 0) + dx, 1)
        element["y"] = round(float(element.get("y", 0) or 0) + dy, 1)
        self._touch()
        self._redraw()

    # ================================================================== #
    # Element actions
    # ================================================================== #
    def _add(self, kind: str) -> None:
        self._snapshot()
        page_w, page_h = ts.page_size(self.template)
        element = ts.make_element(kind, x=round(page_w / 2, 1),
                                  y=round(page_h / 2, 1))
        self._elements().append(element)
        self.selected_id = element["id"]
        self._touch()
        self._redraw()

    def _delete_selected(self) -> None:
        element = self._selected()
        if element is None:
            return
        self._snapshot()
        elements = self._elements()
        index = elements.index(element)
        elements.remove(element)
        # Keep a neighbour selected so Delete can be pressed repeatedly and the
        # properties panel does not blank out after every removal.
        if elements:
            self.selected_id = elements[min(index, len(elements) - 1)].get("id")
        else:
            self.selected_id = None
        self._touch()
        self._redraw()

    def _duplicate_selected(self) -> None:
        element = self._selected()
        if element is None:
            return
        self._snapshot()
        clone = copy.deepcopy(element)
        clone["id"] = ts.new_id()
        clone["x"] = float(clone.get("x", 0) or 0) + 12
        clone["y"] = float(clone.get("y", 0) or 0) + 12
        self._elements().append(clone)
        self.selected_id = clone["id"]
        self._touch()
        self._redraw()

    def _centre_selected(self) -> None:
        element = self._selected()
        if element is None:
            return
        self._snapshot()
        page_w, _ = ts.page_size(self.template)
        if element.get("type") in ("rect", "image"):
            element["x"] = round(page_w / 2 - float(element.get("w", 0) or 0) / 2,
                                 1)
        else:
            element["x"] = round(page_w / 2, 1)
        self._touch()
        self._redraw()

    def _reorder(self, direction: int) -> None:
        element = self._selected()
        if element is None:
            return
        elements = self._elements()
        index = elements.index(element)
        target = index + direction
        if not 0 <= target < len(elements):
            return
        self._snapshot()
        elements[index], elements[target] = elements[target], elements[index]
        self._touch()
        self._redraw()

    # ================================================================== #
    # Template actions
    # ================================================================== #
    def _refresh_template_list(self) -> None:
        names = ts.list_names()
        self.template_menu.configure(
            values=names or ["(no saved templates)"])
        current = self.template.get("name", "")
        self.template_var.set(current if current in names
                              else (current or "(no saved templates)"))

    def _confirm_discard(self, then) -> None:
        if not self._dirty:
            then()
            return
        ModernDialog(
            self.app, "Discard Changes?",
            f"'{self.template.get('name','Untitled')}' has unsaved changes.",
            icon="⚠",
            actions=[("Discard", then, False),
                     ("Keep Editing", None, True)])

    def _on_pick_base(self, base: str) -> None:
        """Start a new template from a built-in document, or from nothing."""
        def go():
            if base == "Blank page":
                self.template = ts.blank_template("Untitled",
                                                  self.page_var.get())
            else:
                self.template = ts.starter_template(f"My {base}", base)
            self.selected_id = None
            self._undo.clear()
            self._dirty = True
            self.page_var.set(self.template.get("page", ts.DEFAULT_PAGE))
            self._refresh_template_list()
            self._redraw()

        self.base_var.set("New from…")
        self._confirm_discard(go)

    def _on_pick_template(self, name: str) -> None:
        if name == "(no saved templates)":
            return

        def go():
            loaded = ts.get_by_name(name)
            if not loaded:
                ModernDialog(self.app, "Could Not Open",
                             f"'{name}' could not be read.", icon="❌")
                return
            loaded.pop("_path", None)
            self.template = loaded
            self.selected_id = None
            self._undo.clear()
            self._dirty = False
            self.page_var.set(self.template.get("page", ts.DEFAULT_PAGE))
            self._redraw()

        self._confirm_discard(go)

    def _on_page_change(self, value: str) -> None:
        self._snapshot()
        self.template["page"] = value
        self._touch()
        self._redraw()

    def _save(self) -> None:
        name = str(self.template.get("name", "")).strip()
        if not name or name.lower() == "untitled":
            self._save_as()
            return
        self._write(name)

    def _save_as(self) -> None:
        dialog = ctk.CTkInputDialog(text="Template name", title="Save Template")
        name = (dialog.get_input() or "").strip()
        if not name:
            return
        if ts.get_by_name(name) and name != self.template.get("name"):
            ModernDialog(
                self.app, "Name Already Used",
                f"A template called '{name}' already exists. Saving will "
                f"replace it.", icon="⚠",
                actions=[("Replace", lambda: self._write(name), True),
                         ("Cancel", None, False)])
            return
        self._write(name)

    def _write(self, name: str) -> None:
        self.template["name"] = name
        try:
            path = ts.save(self.template)
        except Exception as exc:
            ModernDialog(self.app, "Save Failed", str(exc), icon="❌")
            return
        self._dirty = False
        self._refresh_template_list()
        self._update_hint()
        # Let the rest of the app pick up the new document type.
        if hasattr(self.app, "refresh_document_types"):
            self.app.refresh_document_types()
        ModernDialog(
            self.app, "Template Saved",
            f"{path.name}\n\n'{name}' is now available as a document type on "
            f"the Create Document and Bulk Generator pages.", icon="✅")

    def _delete_template(self) -> None:
        name = str(self.template.get("name", "")).strip()
        if not ts.get_by_name(name):
            ModernDialog(self.app, "Not Saved Yet",
                         "This template has not been saved, so there is "
                         "nothing to delete.", icon="⚠")
            return

        def go():
            ts.delete(name)
            self.template = ts.starter_template("My Template")
            self.selected_id = None
            self._undo.clear()
            self._dirty = False
            self._refresh_template_list()
            if hasattr(self.app, "refresh_document_types"):
                self.app.refresh_document_types()
            self._redraw()

        ModernDialog(
            self.app, "Delete Template?",
            f"'{name}' will be removed. Documents already generated from it "
            f"are not affected.", icon="🗑",
            actions=[("Delete", go, True), ("Cancel", None, False)])

    def _preview_pdf(self) -> None:
        """Render the design to a real PDF with sample data and open it."""
        path = Path(tempfile.gettempdir()) / "scaleon_template_preview.pdf"
        data = {
            "candidate_name": "Aakif Jawaid",
            "position": "AI Agent Developer Intern",
            "intern_id": "SO260009", "cert_no": "SO-INT-260031",
            "start_date": "01-07-2026", "end_date": "30-09-2026",
            "issue_date": "30-08-2026", "department": "Engineering",
            "program": "", "grade": "A", "remarks": "Excellent",
        }
        try:
            tr.render_template(self.template, path, self.app.company.data,
                               data, BASE_DIR)
            utils.open_file(path)
        except Exception as exc:
            ModernDialog(self.app, "Preview Failed", str(exc), icon="❌")

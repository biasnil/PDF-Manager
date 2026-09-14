"""
Script/edit_pdf/tool.py — the "Edit PDF" tab.

Two modes, switched by a toggle:
  Annotate — everything that adds something on top of the page: markup
             (Underline, Strikeout, Squiggly, Free Hand, Free Hand
             Highlight), Free Text, Insert Text, Replace Text, Shapes
             (Rectangle/Circle/Line/Arrow), Stamp, Signature, File
             Attachment, Add Paragraph, New Image. Every one of these is
             draggable to reposition (except Replace Text) — see
             MOVABLE_TYPES in constants.py.
  Edit     — actually editing existing text in place: every real text
             block on the page gets outlined automatically, and double-
             clicking one selects that specific line and lets you type
             right over it, iLovePDF-style. See text_edit.py — this mode
             just wires TextEditOverlay into the canvas; the detection
             and in-place-entry mechanics live there.

A left-hand panel lists every page as a thumbnail; clicking one jumps the
main canvas to that page. Hovering the slim gap above a thumbnail for a
moment reveals a "+" that inserts a real blank page right there — it's
added straight into the live document, so every annotate/edit tool works
on it immediately, same as any other page.

Every action is recorded as a "pending edit" and only actually written
into the PDF when "Save changes" is pressed — Undo just pops the last
pending edit, Eraser removes whichever one you click on. Inserted blank
pages are tracked separately (in blank_page_indices) and replayed into
the saved file the same way.
"""

import math
import os
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk
from typing import Optional

import pymupdf

from Config import config
from Script.base_tool import BaseTool
from Script.pdf_edit_operations import apply_edits_and_save, get_word_quads_in_rect, rasterize_shape
from Script.pdf_operations import PDFSplitter
from Script.threading_utils import run_in_background, threadsafe_progress
from Script.widgets import ProgressRow, ScrollableFrame

from .text_edit import TextEditOverlay
from .image_edit import ImageEditOverlay
from .constants import (
    ANNOTATE, EDIT, T_UNDERLINE, T_STRIKEOUT, T_SQUIGGLY, T_FREEHAND, T_HIGHLIGHT,
    T_FREETEXT, T_INSERT_TEXT, T_REPLACE_TEXT, T_ERASER, T_SHAPE, T_STAMP, T_SIGNATURE,
    T_FILE_ATTACHMENT, T_PARAGRAPH, T_IMAGE, T_EXISTING_IMAGE, T_SELECT,
    TEXT_SELECT_TOOLS, DRAG_PATH_TOOLS, CLICK_POINT_TOOLS, MOVABLE_TYPES, KEPT_SWATCH_NAMES,
    _hex_for, _rgb_for, _color_name_for_rgb,
)
from .dialogs import SignaturePadDialog, TextBoxDialog


class NavInsertGap(tk.Frame):
    """A slim horizontal strip in the page-nav list, sitting above a page
    thumbnail (one also trails the very last page). Hover it for a moment
    and a "+" fades in — click it to insert a blank page right there.
    `position` is the page index the new blank page lands at.

    This is the vertical-stack counterpart of Organize PDF's InsertGap —
    same hover-then-reveal idea, just packed as a full-width strip instead
    of gridded as a narrow column.
    """

    HOVER_DELAY_MS = 500
    HEIGHT = 14

    def __init__(self, master, position, on_insert):
        super().__init__(master, bg=config.PANEL_BG, height=self.HEIGHT)
        self.position = position
        self.on_insert = on_insert
        self._pending_after_id = None

        self.plus_label = tk.Label(
            self, text="+", bg=config.PANEL_BG, fg=config.ACCENT,
            font=(config.FONT_FAMILY, 11, "bold"), cursor="hand2",
        )
        # not shown until the hover delay elapses — see _show_plus

        for widget in (self, self.plus_label):
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)
        self.plus_label.bind("<Button-1>", self._on_click)

    def _on_enter(self, _event):
        if self._pending_after_id is not None:
            return
        self._pending_after_id = self.after(self.HOVER_DELAY_MS, self._show_plus)

    def _on_leave(self, _event):
        # Moving from the strip onto the "+" label (its own child) also
        # fires <Leave> on the strip — only actually hide once the pointer
        # is over neither.
        x, y = self.winfo_pointerxy()
        under = self.winfo_containing(x, y)
        if under in (self, self.plus_label):
            return
        if self._pending_after_id is not None:
            self.after_cancel(self._pending_after_id)
            self._pending_after_id = None
        self.plus_label.place_forget()

    def _show_plus(self):
        self._pending_after_id = None
        self.plus_label.place(relx=0.5, rely=0.5, anchor="center")

    def _on_click(self, _event):
        self.on_insert(self.position)
        return "break"


class PdfEditTool(BaseTool):
    title = "Edit PDF"
    description_short = (
        "Annotate a PDF, or switch to Edit mode to rewrite its existing text directly."
    )
    description = (
        "Annotate a PDF (markup, freehand, shapes, stamps, signatures, Free "
        "Text, Add Paragraph, New Image) or actually edit the text that's "
        "already there. Free Text, Add Paragraph, and every other placed "
        "annotation (except Replace Text) can be dragged to reposition it; "
        "Free Text and Add Paragraph also resize from their corner handle, "
        "and double-clicking either lets you change the text — pick a font "
        "and size before placing one. Switch to Edit mode to rewrite "
        "existing text: every text block is outlined automatically, and "
        "double-clicking one selects it and lets you type right over it. "
        "Hover the gap above a page in the left-hand list for a moment to "
        "insert a blank page there. Middle-click drag pans the page; plain "
        "scroll moves it; Ctrl+scroll zooms."
    )

    def __init__(self, master, root):
        super().__init__(master, root)

        self.input_path: Optional[str] = None
        self.doc: Optional["pymupdf.Document"] = None
        self.page_count = 0
        self.current_page_index = 0
        self.pending_edits: list[dict] = []
        self._page_words_cache: dict[int, list] = {}
        self._page_photo = None  # keep a reference to the current page image
        self._overlay_item_to_edit: dict[int, dict] = {}
        self._overlay_photos: list = []  # keeps highlight rasters alive — see _render_highlight_image
        self._nav_thumb_labels: dict[int, tk.Label] = {}

        # Page indices (in self.doc's current numbering) that are blank
        # pages inserted this session via a NavInsertGap "+" — not part of
        # the original file. Kept as the sole source of truth for what to
        # replay into the saved copy; see _insert_blank_at/_remove_blank_page.
        self.blank_page_indices: set[int] = set()

        self.mode_var = tk.StringVar(value=ANNOTATE)
        self.active_tool: Optional[str] = None
        self.current_shape_var = tk.StringVar(value=config.DEFAULT_SHAPE_TYPE)
        self.color_var = tk.StringVar(value=config.DEFAULT_ANNOT_COLOR_NAME)
        self.custom_rgb: tuple = (0.6, 0.6, 0.6)
        self.width_var = tk.IntVar(value=config.DEFAULT_STROKE_WIDTH)
        self.opacity_var = tk.IntVar(value=round(config.EDIT_HIGHLIGHT_OPACITY * 100))
        self.stamp_name_var = tk.StringVar(value=config.DEFAULT_STAMP_NAME)
        self.font_name_var = tk.StringVar(value=config.DEFAULT_FONT_NAME)
        self.font_size_var = tk.IntVar(value=config.EDIT_DEFAULT_FONT_SIZE)

        self._pending_signature: Optional[tuple[bytes, float]] = None  # (png_bytes, aspect)
        self._pending_image_path: Optional[str] = None

        self._drag_start_canvas: Optional[tuple[int, int]] = None
        self._drag_preview_ids: list[int] = []
        self._drag_preview_photo = None  # keeps the live highlight raster alive; see _render_highlight_image
        self._current_path_points: list[tuple[int, int]] = []

        self._tool_buttons: dict[str, tk.Button] = {}
        self.zoom_level = 1.0
        self.nav_visible = True
        self._page_x_offset = 0
        self._handle_to_edit: dict[int, dict] = {}
        self._rotate_handle_to_edit: dict[int, dict] = {}
        self._interaction: Optional[dict] = None
        self._redo_stack: list[dict] = []

        # Edit-mode style panel state (Font/Size/Bold/Italic/Color for
        # whichever line is currently being edited) — deliberately separate
        # from Annotate's color_var/custom_rgb, since this is a plain
        # current-color value with no preset-name concept, matching the
        # simpler "current color + custom" panel from the reference design.
        self.text_edit_font_var = tk.StringVar(value=config.DEFAULT_FONT_NAME)
        self.text_edit_size_var = tk.IntVar(value=config.EDIT_DEFAULT_FONT_SIZE)
        self.text_edit_bold = False
        self.text_edit_italic = False
        self.text_edit_rgb: tuple = (0.0, 0.0, 0.0)

        self._build_top_bar()
        self._build_layout()

        self._text_edit_overlay = TextEditOverlay(
            canvas=self.canvas,
            get_page=lambda: (self.doc[self.current_page_index]
                              if self.doc is not None and self.current_page_index < self.doc.page_count
                              else None),
            canvas_to_pdf=self._canvas_to_pdf,
            pdf_rect_to_canvas=self._pdf_rect_to_canvas,
            scale=self._scale,
            on_start_edit=self._on_text_edit_start,
            on_commit=self._commit_text_edit,
            is_style_control=lambda widget: widget in self._text_edit_style_widgets,
        )

        self._image_edit_overlay = ImageEditOverlay(
            canvas=self.canvas,
            get_page=lambda: (self.doc[self.current_page_index]
                              if self.doc is not None and self.current_page_index < self.doc.page_count
                              else None),
            pdf_rect_to_canvas=self._pdf_rect_to_canvas,
        )

    # ==================================================================
    # top bar: open file + mode toggle
    # ==================================================================

    def _build_top_bar(self):
        top_row = tk.Frame(self.body, bg=config.PANEL_BG)
        top_row.pack(fill=tk.X)

        from Script.widgets import AccentButton
        AccentButton(top_row, "Choose PDF", command=self._choose_file).pack(side=tk.RIGHT)
        self.file_label = tk.Label(
            top_row, text="No file selected", bg=config.PANEL_BG, fg=config.TEXT_MUTED,
            font=config.FONT_BODY, anchor="w",
        )
        self.file_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        from Script.dnd_support import register_drop_target
        if register_drop_target(self, self._on_files_dropped, extensions={".pdf"}):
            self.file_label.config(text="No file selected — or drag a PDF in")

        mode_row = tk.Frame(self.body, bg=config.PANEL_BG)
        mode_row.pack(fill=tk.X, pady=(8, 8))
        tk.Radiobutton(
            mode_row, text="Annotate", variable=self.mode_var, value=ANNOTATE,
            bg=config.PANEL_BG, fg=config.TEXT_MAIN, selectcolor=config.INPUT_BG,
            activebackground=config.PANEL_BG, activeforeground=config.TEXT_MAIN,
            font=config.FONT_BODY_BOLD, command=self._on_mode_change,
        ).pack(side=tk.LEFT)
        tk.Radiobutton(
            mode_row, text="Edit", variable=self.mode_var, value=EDIT,
            bg=config.PANEL_BG, fg=config.TEXT_MAIN, selectcolor=config.INPUT_BG,
            activebackground=config.PANEL_BG, activeforeground=config.TEXT_MAIN,
            font=config.FONT_BODY_BOLD, command=self._on_mode_change,
        ).pack(side=tk.LEFT, padx=(16, 0))

        self.toolbar_annotate = tk.Frame(self.body, bg=config.PANEL_BG)
        self.toolbar_edit = tk.Frame(self.body, bg=config.PANEL_BG)
        self._build_toolbar_annotate(self.toolbar_annotate)
        self._build_toolbar_edit(self.toolbar_edit)
        self.toolbar_annotate.pack(fill=tk.X)

    def _tool_button(self, parent, text, tool_id):
        btn = tk.Button(
            parent, text=text, command=lambda: self._select_tool(tool_id),
            bg=config.BUTTON_BG, fg=config.TEXT_MAIN, relief=tk.FLAT, bd=0,
            padx=8, pady=4, font=config.FONT_SMALL, cursor="hand2",
        )
        self._tool_buttons[tool_id] = btn
        return btn

    def _safe_int_var(self, var: tk.IntVar, default: int) -> int:
        """IntVars backing the now-editable numeric comboboxes (pt width,
        font size, opacity) can transiently hold an empty/invalid string
        while someone's mid-typing a custom value — .get() would raise
        TclError in that moment. Falls back to `default` instead."""
        try:
            return var.get()
        except tk.TclError:
            return default

    def _numeric_combobox(self, parent, textvariable, values, width=4):
        """An editable (not readonly) Combobox for a numeric value — pt
        width, font size, opacity % — so someone can type e.g. 30 or 1000
        instead of only picking from the preset list. Restricted to
        digits while typing so a stray letter can't leave the underlying
        IntVar holding something .get() would choke on."""
        vcmd = (self.register(lambda s: s == "" or s.isdigit()), "%P")
        return ttk.Combobox(
            parent, textvariable=textvariable, width=width, values=values,
            validate="key", validatecommand=vcmd,
        )

    def _build_toolbar_annotate(self, parent):
        row1 = tk.Frame(parent, bg=config.PANEL_BG)
        row1.pack(fill=tk.X, anchor="w")
        self._tool_button(row1, "Select", T_SELECT).pack(side=tk.LEFT, padx=(0, 10), pady=2)
        for text, tool_id in [
            ("Underline", T_UNDERLINE), ("Strikeout", T_STRIKEOUT),
            ("Squiggly", T_SQUIGGLY), ("Free Hand", T_FREEHAND),
            ("Free Hand Highlight", T_HIGHLIGHT), ("Free Text", T_FREETEXT),
            ("Insert Text", T_INSERT_TEXT), ("Replace Text", T_REPLACE_TEXT),
        ]:
            self._tool_button(row1, text, tool_id).pack(side=tk.LEFT, padx=(0, 4), pady=2)

        tk.Frame(row1, bg=config.DESELECTED_BORDER, width=1).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        tk.Button(
            row1, text="Undo", command=self._undo,
            bg=config.BUTTON_BG, fg=config.TEXT_MAIN, relief=tk.FLAT, bd=0,
            padx=8, pady=4, font=config.FONT_SMALL, cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 4))
        self._tool_button(row1, "Eraser", T_ERASER).pack(side=tk.LEFT)

        row2 = tk.Frame(parent, bg=config.PANEL_BG)
        row2.pack(fill=tk.X, anchor="w", pady=(6, 0))
        tk.Label(
            row2, text="Shapes:", bg=config.PANEL_BG, fg=config.TEXT_MAIN,
            font=config.FONT_SMALL,
        ).pack(side=tk.LEFT, padx=(0, 6))
        for shape in config.SHAPE_TYPES:
            btn = tk.Button(
                row2, text=shape, command=lambda s=shape: self._select_shape(s),
                bg=config.BUTTON_BG, fg=config.TEXT_MAIN, relief=tk.FLAT, bd=0,
                padx=8, pady=4, font=config.FONT_SMALL, cursor="hand2",
            )
            btn.pack(side=tk.LEFT, padx=(0, 4))
            self._tool_buttons[f"shape:{shape}"] = btn

        row3 = tk.Frame(parent, bg=config.PANEL_BG)
        row3.pack(fill=tk.X, anchor="w", pady=(6, 0))
        tk.Label(
            row3, text="Style:", bg=config.PANEL_BG, fg=config.TEXT_MAIN,
            font=config.FONT_SMALL,
        ).pack(side=tk.LEFT, padx=(0, 6))
        self._color_swatches: dict[str, tk.Label] = {}
        for name, hexval, _rgb in config.ANNOT_COLOR_PALETTE:
            if name not in KEPT_SWATCH_NAMES:
                continue
            sw = tk.Label(row3, bg=hexval, width=2, height=1, relief=tk.FLAT, cursor="hand2")
            sw.pack(side=tk.LEFT, padx=1)
            sw.bind("<Button-1>", lambda e, n=name: self._select_color(n))
            self._color_swatches[name] = sw
        self._custom_swatch = tk.Label(
            row3, bg=self._custom_hex(), width=2, height=1, relief=tk.FLAT, cursor="hand2",
        )
        self._custom_swatch.pack(side=tk.LEFT, padx=(4, 0))
        self._custom_swatch.bind("<Button-1>", lambda e: self._pick_custom_color())
        self._numeric_combobox(
            row3, self.width_var, config.STROKE_WIDTH_OPTIONS,
        ).pack(side=tk.LEFT, padx=(10, 0))
        tk.Label(
            row3, text="pt width", bg=config.PANEL_BG, fg=config.TEXT_MUTED,
            font=config.FONT_SMALL,
        ).pack(side=tk.LEFT, padx=(4, 0))
        self._font_size_frame = tk.Frame(row3, bg=config.PANEL_BG)
        tk.Label(
            self._font_size_frame, text="Font:", bg=config.PANEL_BG, fg=config.TEXT_MAIN,
            font=config.FONT_SMALL,
        ).pack(side=tk.LEFT, padx=(14, 4))
        ttk.Combobox(
            self._font_size_frame, textvariable=self.font_name_var, state="readonly", width=11,
            values=[name for name, _code in config.FONT_CHOICES],
        ).pack(side=tk.LEFT)
        self._numeric_combobox(
            self._font_size_frame, self.font_size_var, config.FONT_SIZE_OPTIONS,
        ).pack(side=tk.LEFT, padx=(6, 0))
        tk.Label(
            self._font_size_frame, text="size (Free Text)", bg=config.PANEL_BG, fg=config.TEXT_MUTED,
            font=config.FONT_SMALL,
        ).pack(side=tk.LEFT, padx=(4, 0))
        self._font_size_frame.pack(side=tk.LEFT)

        self._opacity_frame = tk.Frame(row3, bg=config.PANEL_BG)
        tk.Label(
            self._opacity_frame, text="Opacity:", bg=config.PANEL_BG, fg=config.TEXT_MAIN,
            font=config.FONT_SMALL,
        ).pack(side=tk.LEFT, padx=(14, 4))
        self._numeric_combobox(
            self._opacity_frame, self.opacity_var, [10, 20, 30, 35, 40, 50, 60, 70, 80, 90, 100],
        ).pack(side=tk.LEFT)
        tk.Label(
            self._opacity_frame, text="% (Free Hand Highlight)", bg=config.PANEL_BG, fg=config.TEXT_MUTED,
            font=config.FONT_SMALL,
        ).pack(side=tk.LEFT, padx=(4, 0))
        # not packed here — only shown in place of _font_size_frame while
        # the Highlight tool is active; see _select_tool

        row4 = tk.Frame(parent, bg=config.PANEL_BG)
        row4.pack(fill=tk.X, anchor="w", pady=(6, 0))
        tk.Label(
            row4, text="Insert:", bg=config.PANEL_BG, fg=config.TEXT_MAIN,
            font=config.FONT_SMALL,
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Combobox(
            row4, textvariable=self.stamp_name_var, state="readonly", width=14,
            values=config.STAMP_NAMES,
        ).pack(side=tk.LEFT)
        self._tool_button(row4, "Place Stamp", T_STAMP).pack(side=tk.LEFT, padx=(4, 10))
        tk.Button(
            row4, text="Draw Signature...", command=self._open_signature_pad,
            bg=config.BUTTON_BG, fg=config.TEXT_MAIN, relief=tk.FLAT, bd=0,
            padx=8, pady=4, font=config.FONT_SMALL, cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 10))
        tk.Button(
            row4, text="File Attachment...", command=self._choose_attachment_file,
            bg=config.BUTTON_BG, fg=config.TEXT_MAIN, relief=tk.FLAT, bd=0,
            padx=8, pady=4, font=config.FONT_SMALL, cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 10))
        self._tool_button(row4, "Add Paragraph", T_PARAGRAPH).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(
            row4, text="New Image...", command=self._choose_new_image,
            bg=config.BUTTON_BG, fg=config.TEXT_MAIN, relief=tk.FLAT, bd=0,
            padx=8, pady=4, font=config.FONT_SMALL, cursor="hand2",
        ).pack(side=tk.LEFT)

        self._update_color_swatches()

    def _build_toolbar_edit(self, parent):
        """The Edit-mode toolbar: every text block on the page is already
        outlined (see TextEditOverlay), so there's no tool to pick — just
        a style panel that applies to whichever line is currently being
        double-click-edited, sitting above Undo/Redo."""
        tk.Label(
            parent, text=(
                "Every text block on the page is outlined. Double-click one "
                "to edit it in place — select-all happens automatically, so "
                "typing just replaces it. Use the controls below to restyle "
                "it before you're done."
            ),
            bg=config.PANEL_BG, fg=config.TEXT_MUTED, font=config.FONT_SMALL,
            anchor="w", justify=tk.LEFT, wraplength=560,
        ).pack(fill=tk.X, anchor="w")

        style_row = tk.Frame(parent, bg=config.PANEL_BG)
        style_row.pack(fill=tk.X, anchor="w", pady=(8, 0))

        tk.Label(
            style_row, text="Font:", bg=config.PANEL_BG, fg=config.TEXT_MAIN,
            font=config.FONT_SMALL,
        ).pack(side=tk.LEFT, padx=(0, 4))
        font_box = ttk.Combobox(
            style_row, textvariable=self.text_edit_font_var, state="readonly", width=11,
            values=[name for name, _code in config.FONT_CHOICES],
        )
        font_box.pack(side=tk.LEFT)
        font_box.bind("<<ComboboxSelected>>", lambda e: self._on_text_edit_style_changed())

        size_box = self._numeric_combobox(style_row, self.text_edit_size_var, config.FONT_SIZE_OPTIONS)
        size_box.pack(side=tk.LEFT, padx=(6, 0))
        size_box.bind("<<ComboboxSelected>>", lambda e: self._on_text_edit_style_changed())
        size_box.bind("<Return>", lambda e: self._on_text_edit_style_changed())
        size_box.bind("<FocusOut>", lambda e: self._on_text_edit_style_changed())

        self._text_edit_bold_btn = tk.Button(
            style_row, text="B", command=self._toggle_text_edit_bold,
            bg=config.BUTTON_BG, fg=config.TEXT_MAIN, relief=tk.FLAT, bd=0,
            font=(config.FONT_FAMILY, 10, "bold"), width=2, cursor="hand2",
        )
        self._text_edit_bold_btn.pack(side=tk.LEFT, padx=(10, 2))
        self._text_edit_italic_btn = tk.Button(
            style_row, text="I", command=self._toggle_text_edit_italic,
            bg=config.BUTTON_BG, fg=config.TEXT_MAIN, relief=tk.FLAT, bd=0,
            font=(config.FONT_FAMILY, 10, "italic"), width=2, cursor="hand2",
        )
        self._text_edit_italic_btn.pack(side=tk.LEFT, padx=(0, 10))

        tk.Label(
            style_row, text="Color:", bg=config.PANEL_BG, fg=config.TEXT_MAIN,
            font=config.FONT_SMALL,
        ).pack(side=tk.LEFT, padx=(0, 6))
        self._text_edit_swatch = tk.Label(
            style_row, bg=self._text_edit_hex(), width=2, height=1, cursor="hand2",
            relief=tk.FLAT,
        )
        self._text_edit_swatch.pack(side=tk.LEFT)
        self._text_edit_swatch.bind("<Button-1>", lambda e: self._pick_text_edit_color())

        # Every widget in this style panel — losing Entry focus TO one of
        # these (clicking Size, tabbing to Bold, etc.) must NOT be treated
        # as "clicked away, finish the edit"; see TextEditOverlay's
        # is_style_control and _maybe_finish_after_focus_change.
        self._text_edit_style_widgets = {
            font_box, size_box, self._text_edit_bold_btn,
            self._text_edit_italic_btn, self._text_edit_swatch,
        }

        action_row = tk.Frame(parent, bg=config.PANEL_BG)
        action_row.pack(fill=tk.X, anchor="w", pady=(10, 0))
        tk.Button(
            action_row, text="Undo", command=self._undo,
            bg=config.BUTTON_BG, fg=config.TEXT_MAIN, relief=tk.FLAT, bd=0,
            padx=8, pady=4, font=config.FONT_SMALL, cursor="hand2",
        ).pack(side=tk.LEFT)
        tk.Button(
            action_row, text="Redo", command=self._redo,
            bg=config.BUTTON_BG, fg=config.TEXT_MAIN, relief=tk.FLAT, bd=0,
            padx=8, pady=4, font=config.FONT_SMALL, cursor="hand2",
        ).pack(side=tk.LEFT, padx=(6, 0))

    # -- Edit mode: the style panel above -------------------------------

    def _text_edit_hex(self) -> str:
        r, g, b = self.text_edit_rgb
        return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))

    def _pick_text_edit_color(self):
        rgb, hexval = colorchooser.askcolor(
            color=self._text_edit_hex(), title="Choose a color", parent=self,
        )
        if hexval is None:
            return
        self.text_edit_rgb = tuple(c / 255 for c in rgb)
        self._text_edit_swatch.config(bg=hexval)
        self._on_text_edit_style_changed()

    def _toggle_text_edit_bold(self):
        self.text_edit_bold = not self.text_edit_bold
        self._text_edit_bold_btn.config(bg=config.ACCENT if self.text_edit_bold else config.BUTTON_BG)
        self._on_text_edit_style_changed()

    def _toggle_text_edit_italic(self):
        self.text_edit_italic = not self.text_edit_italic
        self._text_edit_italic_btn.config(bg=config.ACCENT if self.text_edit_italic else config.BUTTON_BG)
        self._on_text_edit_style_changed()

    def _on_text_edit_style_changed(self):
        """Any style-panel control changed — push the new values into
        whatever line is currently being edited (a no-op if none is)."""
        self._text_edit_overlay.update_active_style(
            self.text_edit_font_var.get(),
            self._safe_int_var(self.text_edit_size_var, config.EDIT_DEFAULT_FONT_SIZE),
            self.text_edit_bold, self.text_edit_italic, self.text_edit_rgb,
        )

    def _existing_replace_text_edit(self, rect: tuple) -> Optional[dict]:
        """The pending replace_text edit already covering this exact
        line's rect on the current page, if the line has been edited
        before — used both to prefill reopening it and to update it in
        place instead of stacking a duplicate."""
        quad = pymupdf.Rect(rect).quad
        for e in self.pending_edits:
            if (e["type"] == T_REPLACE_TEXT and e["page"] == self.current_page_index
                    and e.get("quads") and e["quads"][0].rect == quad.rect):
                return e
        return None

    def _on_text_edit_start(self, line: dict) -> tuple:
        """TextEditOverlay's on_start_edit callback: prefill the style
        panel and hand back the starting text/style for this line. If
        this exact line already has a replace_text edit pending (the
        person is reopening a line they already edited), that edit's
        CURRENT text and style are used as the starting point instead of
        the original PDF text — otherwise reopening an already-edited
        line would look like it had reverted. Best-effort for a genuinely
        untouched line — we don't know the source PDF's actual font
        family, only its size and color, so font defaults to
        config.DEFAULT_FONT_NAME and bold/italic default off."""
        existing = self._existing_replace_text_edit(line["rect"])
        if existing is not None:
            self.text_edit_font_var.set(existing.get("tk_font", config.DEFAULT_FONT_NAME))
            self.text_edit_size_var.set(round(existing.get("fontsize", config.EDIT_DEFAULT_FONT_SIZE)))
            self.text_edit_bold = existing.get("bold", False)
            self.text_edit_italic = existing.get("italic", False)
            self.text_edit_rgb = existing.get("color", (0, 0, 0))
            start_text = existing["new_text"]
        else:
            self.text_edit_font_var.set(config.DEFAULT_FONT_NAME)
            self.text_edit_size_var.set(round(line["fontsize"]))
            self.text_edit_bold = False
            self.text_edit_italic = False
            self.text_edit_rgb = line["color"]
            start_text = line["text"]

        self._text_edit_bold_btn.config(bg=config.ACCENT if self.text_edit_bold else config.BUTTON_BG)
        self._text_edit_italic_btn.config(bg=config.ACCENT if self.text_edit_italic else config.BUTTON_BG)
        self._text_edit_swatch.config(bg=self._text_edit_hex())

        return (self.text_edit_font_var.get(),
                self._safe_int_var(self.text_edit_size_var, config.EDIT_DEFAULT_FONT_SIZE),
                self.text_edit_bold, self.text_edit_italic, self.text_edit_rgb, start_text)

    # ==================================================================
    # main layout: left nav + canvas + save bar
    # ==================================================================

    def _build_layout(self):
        self.main_row = tk.Frame(self.body, bg=config.PANEL_BG)
        self.main_row.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        self.nav_container = tk.Frame(self.main_row, bg=config.PANEL_BG, width=110)
        self.nav_container.pack(side=tk.LEFT, fill=tk.Y)
        self.nav_container.pack_propagate(False)
        self.nav_scroll = ScrollableFrame(self.nav_container, height=400)
        self.nav_scroll.pack(fill=tk.BOTH, expand=True)

        toggle_bar = tk.Frame(self.main_row, bg=config.APP_BG, width=18)
        toggle_bar.pack(side=tk.LEFT, fill=tk.Y)
        toggle_bar.pack_propagate(False)
        self.nav_toggle_btn = tk.Button(
            toggle_bar, text="\u25c0", command=self._toggle_nav_panel,
            bg=config.APP_BG, fg=config.TEXT_MUTED, relief=tk.FLAT, bd=0,
            font=config.FONT_SMALL, cursor="hand2",
        )
        self.nav_toggle_btn.pack(fill=tk.Y, expand=True)

        canvas_frame = tk.Frame(self.main_row, bg=config.PANEL_BG)
        canvas_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0))

        zoom_row = tk.Frame(canvas_frame, bg=config.PANEL_BG)
        zoom_row.pack(fill=tk.X, pady=(0, 4))
        tk.Button(
            zoom_row, text="\u2212", command=lambda: self._zoom_step(1 / 1.15),
            bg=config.BUTTON_BG, fg=config.TEXT_MAIN, relief=tk.FLAT, bd=0,
            width=2, font=config.FONT_SMALL, cursor="hand2",
        ).pack(side=tk.LEFT)
        self.zoom_label = tk.Label(
            zoom_row, text="100%", bg=config.PANEL_BG, fg=config.TEXT_MUTED,
            font=config.FONT_SMALL, width=5,
        )
        self.zoom_label.pack(side=tk.LEFT, padx=4)
        tk.Button(
            zoom_row, text="+", command=lambda: self._zoom_step(1.15),
            bg=config.BUTTON_BG, fg=config.TEXT_MAIN, relief=tk.FLAT, bd=0,
            width=2, font=config.FONT_SMALL, cursor="hand2",
        ).pack(side=tk.LEFT)
        tk.Button(
            zoom_row, text="Reset", command=self._zoom_reset,
            bg=config.BUTTON_BG, fg=config.TEXT_MAIN, relief=tk.FLAT, bd=0,
            padx=6, font=config.FONT_SMALL, cursor="hand2",
        ).pack(side=tk.LEFT, padx=(6, 0))
        tk.Label(
            zoom_row, text="(Ctrl+scroll to zoom · scroll to move · middle-drag to pan)",
            bg=config.PANEL_BG, fg=config.TEXT_MUTED, font=config.FONT_SMALL,
        ).pack(side=tk.LEFT, padx=(10, 0))

        h_scroll = tk.Scrollbar(canvas_frame, orient=tk.HORIZONTAL)
        v_scroll = tk.Scrollbar(canvas_frame, orient=tk.VERTICAL)
        self.canvas = tk.Canvas(
            canvas_frame, bg="#111218", highlightthickness=0,
            xscrollcommand=h_scroll.set, yscrollcommand=v_scroll.set,
        )
        h_scroll.config(command=self.canvas.xview)
        v_scroll.config(command=self.canvas.yview)
        v_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        h_scroll.pack(side=tk.BOTTOM, fill=tk.X)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self.canvas.bind("<Double-Button-1>", self._on_canvas_double_click)
        self.canvas.bind("<Control-MouseWheel>", self._on_ctrl_wheel)      # Windows / macOS
        self.canvas.bind("<Control-Button-4>", lambda e: self._zoom_step(1.15))   # Linux scroll up
        self.canvas.bind("<Control-Button-5>", lambda e: self._zoom_step(1 / 1.15))  # Linux scroll down

        # plain scroll wheel = scroll the page up/down; Shift+wheel = left/right
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        self.canvas.bind("<Shift-MouseWheel>", lambda e: self.canvas.xview_scroll(int(-1 * (e.delta / 120)), "units"))
        self.canvas.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-3, "units"))    # Linux scroll up
        self.canvas.bind("<Button-5>", lambda e: self.canvas.yview_scroll(3, "units"))     # Linux scroll down

        # middle-mouse-button drag pans the view (grab-and-drag, any direction)
        self.canvas.bind("<ButtonPress-2>", self._on_pan_start)
        self.canvas.bind("<B2-Motion>", self._on_pan_move)
        self.canvas.bind("<ButtonRelease-2>", self._on_pan_end)

        self.progress = ProgressRow(self.body, "Save changes", self._save_changes)
        self.progress.pack(fill=tk.X, pady=(10, 0))

    # -- left panel collapse --

    def _toggle_nav_panel(self):
        self.nav_visible = not self.nav_visible
        if self.nav_visible:
            self.nav_container.pack(side=tk.LEFT, fill=tk.Y, before=self.nav_toggle_btn.master)
            self.nav_toggle_btn.config(text="\u25c0")
        else:
            self.nav_container.pack_forget()
            self.nav_toggle_btn.config(text="\u25b6")

    # -- zoom --

    def _scale(self) -> float:
        return config.EDIT_PAGE_RENDER_SCALE * self.zoom_level

    def _on_ctrl_wheel(self, event):
        factor = 1.15 if event.delta > 0 else (1 / 1.15)
        self._zoom_step(factor)

    def _zoom_step(self, factor: float):
        if self.doc is None:
            return
        new_zoom = max(0.25, min(4.0, self.zoom_level * factor))
        if abs(new_zoom - self.zoom_level) < 1e-6:
            return
        self.zoom_level = new_zoom
        self.zoom_label.config(text=f"{round(self.zoom_level * 100)}%")
        self._render_current_page(preserve_view=True)

    def _zoom_reset(self):
        if self.doc is None:
            return
        self.zoom_level = 1.0
        self.zoom_label.config(text="100%")
        self._render_current_page(preserve_view=True)

    # -- middle-mouse-button pan --

    def _on_pan_start(self, event):
        self.canvas.scan_mark(event.x, event.y)
        self._pan_cursor_before = self.canvas.cget("cursor")
        self.canvas.config(cursor="fleur")

    def _on_pan_move(self, event):
        self.canvas.scan_dragto(event.x, event.y, gain=1)

    def _on_pan_end(self, event):
        self.canvas.config(cursor=getattr(self, "_pan_cursor_before", "arrow"))

    # ==================================================================
    # file loading
    # ==================================================================

    def _choose_file(self):
        path = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf")])
        if path:
            self._set_file(path)

    def _on_files_dropped(self, paths):
        if paths:
            self._set_file(paths[0])

    def _set_file(self, path):
        self.input_path = path
        self.file_label.config(text=os.path.basename(path), fg=config.TEXT_MAIN)
        self.pending_edits.clear()
        self._page_words_cache.clear()
        self.current_page_index = 0
        self._load_document_and_thumbnails()

    def _load_document_and_thumbnails(self):
        if self.doc is not None:
            self.doc.close()
        self.doc = pymupdf.open(self.input_path)
        self.page_count = self.doc.page_count
        self.blank_page_indices = set()

        self._rebuild_nav_panel()
        self._fit_zoom_to_width()
        self._select_page(0)

    def _rebuild_nav_panel(self):
        """Redraws the whole left-hand page list from self.doc — the live
        in-memory document, not the file on disk, so any blank pages
        inserted this session (which don't exist on disk yet) still show
        up. Called on initial load and again after every insert/remove."""
        for w in self.nav_scroll.inner.winfo_children():
            w.destroy()
        self._nav_thumb_labels.clear()

        thumbs = PDFSplitter.generate_thumbnails_from_doc(
            self.doc, max_dim=config.EDIT_THUMB_MAX_DIM
        )
        self._nav_photos = []

        NavInsertGap(self.nav_scroll.inner, 0, on_insert=self._insert_blank_at).pack(fill=tk.X)

        for i, png_bytes in enumerate(thumbs):
            photo = tk.PhotoImage(data=png_bytes)
            self._nav_photos.append(photo)
            frame = tk.Frame(self.nav_scroll.inner, bg=config.PANEL_BG)
            frame.pack(fill=tk.X, pady=4)
            lbl = tk.Label(frame, image=photo, bg=config.PANEL_BG, bd=2, relief=tk.FLAT)
            lbl.pack()
            is_blank = i in self.blank_page_indices
            num = tk.Label(
                frame, text=f"{i + 1} (blank)" if is_blank else str(i + 1),
                bg=config.PANEL_BG, fg=config.TEXT_MUTED, font=config.FONT_SMALL,
            )
            num.pack()
            lbl.bind("<Button-1>", lambda e, idx=i: self._select_page(idx))
            self._nav_thumb_labels[i] = lbl

            if is_blank:
                remove_lbl = tk.Label(
                    frame, text="Remove", bg=config.PANEL_BG, fg=config.ACCENT,
                    font=config.FONT_SMALL, cursor="hand2",
                )
                remove_lbl.pack()
                remove_lbl.bind("<Button-1>", lambda e, idx=i: self._remove_blank_page(idx))

            NavInsertGap(
                self.nav_scroll.inner, i + 1, on_insert=self._insert_blank_at
            ).pack(fill=tk.X)

    def _insert_blank_at(self, position):
        """Called from a NavInsertGap's "+". Adds a real, empty page into
        the live document at `position` (sized to match page 0), shifts
        every pending edit on a later page along by one so nothing jumps
        to the wrong page, and refreshes the nav list."""
        if self.doc is None:
            return

        if self.doc.page_count:
            ref = self.doc[0].rect
            width, height = ref.width, ref.height
        else:
            width, height = pymupdf.paper_size("a4")
        self.doc.new_page(pno=position, width=width, height=height)

        self.blank_page_indices = {
            (i + 1 if i >= position else i) for i in self.blank_page_indices
        }
        self.blank_page_indices.add(position)

        for e in self.pending_edits:
            if e["page"] >= position:
                e["page"] += 1

        self.page_count += 1
        self._page_words_cache.clear()  # keyed by page index; indices just shifted
        self._rebuild_nav_panel()
        self._select_page(position)

    def _remove_blank_page(self, index):
        """Removes a page inserted via the "+" gap. Only ever wired up for
        an index this session marked blank — pages from the source PDF
        aren't removable from this tab."""
        if index not in self.blank_page_indices:
            return
        if self.page_count <= 1:
            messagebox.showwarning("Edit PDF", "A PDF needs at least one page.")
            return

        self.doc.delete_page(index)
        self.blank_page_indices.discard(index)
        self.blank_page_indices = {
            (i - 1 if i > index else i) for i in self.blank_page_indices
        }

        self.pending_edits = [e for e in self.pending_edits if e["page"] != index]
        for e in self.pending_edits:
            if e["page"] > index:
                e["page"] -= 1

        self.page_count -= 1
        self._page_words_cache.clear()
        if self.current_page_index >= self.page_count:
            self.current_page_index = self.page_count - 1
        self._rebuild_nav_panel()
        self._select_page(self.current_page_index)

    def _fit_zoom_to_width(self):
        """Set the initial zoom so the page width roughly fills the visible
        canvas — avoids always opening a page zoomed in past what's useful."""
        self.canvas.update_idletasks()
        viewport_w = self.canvas.winfo_width()
        if viewport_w < 50:
            viewport_w = 650  # canvas not realized yet; a reasonable guess
        page_w_pt = self.doc[0].rect.width or 1
        natural_px_w = page_w_pt * config.EDIT_PAGE_RENDER_SCALE
        fit_zoom = viewport_w / natural_px_w
        self.zoom_level = max(0.25, min(1.0, fit_zoom))
        self.zoom_label.config(text=f"{round(self.zoom_level * 100)}%")

    def _select_page(self, index: int):
        self.current_page_index = index
        for i, lbl in self._nav_thumb_labels.items():
            lbl.config(bd=2, relief=tk.SOLID if i == index else tk.FLAT,
                       highlightbackground=config.ACCENT if i == index else config.PANEL_BG)
        self._render_current_page()

    def _render_current_page(self, preserve_view: bool = False):
        x_frac, y_frac = None, None
        if preserve_view:
            try:
                x_frac = self.canvas.xview()[0]
                y_frac = self.canvas.yview()[0]
            except tk.TclError:
                pass

        page = self.doc[self.current_page_index]
        scale = self._scale()
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale))
        self._page_photo = tk.PhotoImage(data=pix.tobytes("png"))
        self.canvas.delete("all")

        self.canvas.update_idletasks()
        viewport_w = max(self.canvas.winfo_width(), pix.width)
        self._page_x_offset = max(0, (viewport_w - pix.width) / 2)

        self.canvas.create_image(
            self._page_x_offset, 0, image=self._page_photo, anchor="nw", tags="page"
        )
        self.canvas.config(scrollregion=(0, 0, viewport_w, pix.height))
        self._redraw_overlay()
        if self.mode_var.get() == EDIT:
            self._text_edit_overlay.refresh()
            self._image_edit_overlay.refresh(self._promoted_image_xrefs())

        if x_frac is not None:
            self.canvas.xview_moveto(x_frac)
            self.canvas.yview_moveto(y_frac)

    def _page_words(self, page_index: int) -> list:
        if page_index not in self._page_words_cache:
            self._page_words_cache[page_index] = self.doc[page_index].get_text("words")
        return self._page_words_cache[page_index]

    def _promoted_image_xrefs(self) -> set:
        """xrefs on the CURRENT page that are already live pending edits
        — passed to ImageEditOverlay.refresh() so it doesn't re-outline
        (and thus re-offer-for-promotion) something already promoted."""
        return {
            e["xref"] for e in self.pending_edits
            if e["type"] == T_EXISTING_IMAGE and e["page"] == self.current_page_index
        }

    # ==================================================================
    # mode / tool selection
    # ==================================================================

    def _on_mode_change(self):
        if self.mode_var.get() == ANNOTATE:
            self.toolbar_edit.pack_forget()
            self.toolbar_annotate.pack(fill=tk.X, before=self.main_row)
            self._text_edit_overlay.clear()
            self._image_edit_overlay.clear()
        else:
            self.toolbar_annotate.pack_forget()
            self.toolbar_edit.pack(fill=tk.X, before=self.main_row)
            self._text_edit_overlay.refresh()
            self._image_edit_overlay.refresh(self._promoted_image_xrefs())
        self._select_tool(None)

    def _select_tool(self, tool_id: Optional[str]):
        # T_SELECT and None both mean "no drawing tool" (select/move mode) —
        # T_SELECT is just the button-visible spelling of that same state.
        actual_tool = None if tool_id == T_SELECT else tool_id
        self.active_tool = actual_tool
        highlight_key = T_SELECT if actual_tool is None else actual_tool

        for tid, btn in self._tool_buttons.items():
            is_selected = (tid == highlight_key) or (
                actual_tool == T_SHAPE and tid == f"shape:{self.current_shape_var.get()}"
            )
            btn.config(bg=config.ACCENT if is_selected else config.BUTTON_BG)
        cursor = "crosshair" if actual_tool else "arrow"
        self.canvas.config(cursor=cursor)

        if actual_tool == T_HIGHLIGHT:
            self._font_size_frame.pack_forget()
            self._opacity_frame.pack(side=tk.LEFT)
        else:
            self._opacity_frame.pack_forget()
            self._font_size_frame.pack(side=tk.LEFT)

    def _select_shape(self, shape_name: str):
        self.current_shape_var.set(shape_name)
        self._select_tool(T_SHAPE)

    def _select_color(self, color_name: str):
        self.color_var.set(color_name)
        self._update_color_swatches()

    def _custom_hex(self) -> str:
        r, g, b = self.custom_rgb
        return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))

    def _pick_custom_color(self):
        rgb, hexval = colorchooser.askcolor(
            color=self._custom_hex(), title="Choose a color", parent=self,
        )
        if hexval is None:
            return
        self.custom_rgb = tuple(c / 255 for c in rgb)
        self._custom_swatch.config(bg=hexval)
        self.color_var.set("Custom")
        self._update_color_swatches()

    def _current_rgb(self) -> tuple:
        if self.color_var.get() == "Custom":
            return self.custom_rgb
        return _rgb_for(self.color_var.get())

    def _current_hex(self) -> str:
        if self.color_var.get() == "Custom":
            return self._custom_hex()
        return _hex_for(self.color_var.get())

    def _render_highlight_image(self, points: list[tuple[float, float]], width: int,
                                 rgb: tuple, opacity: float):
        """Rasterizes a highlighter stroke as a real RGBA image (alpha =
        opacity) instead of a flat-colored canvas line. A canvas line can
        only fake transparency by blending its fill toward a guessed
        background color — it still fully paints over (and hides)
        whatever text is underneath. A canvas IMAGE with a genuine alpha
        channel is properly composited by Tk against whatever's already
        drawn, so the text actually shows through, same as the real PDF.

        Returns (photo, x, y) — (x, y) is where to place it on the canvas
        (anchor="nw"). Caller must keep `photo` referenced for as long as
        it's shown, or Tk garbage-collects it out from under the canvas.
        """
        from io import BytesIO
        from PIL import Image, ImageDraw

        pad = max(2, width // 2 + 2)
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        x0, y0 = min(xs) - pad, min(ys) - pad
        x1, y1 = max(xs) + pad, max(ys) + pad
        w, h = max(1, round(x1 - x0)), max(1, round(y1 - y0))

        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        local_points = [(px - x0, py - y0) for px, py in points]
        alpha = round(max(0.0, min(1.0, opacity)) * 255)
        color = tuple(round(c * 255) for c in rgb) + (alpha,)
        if len(local_points) == 1:
            lx, ly = local_points[0]
            r = width / 2
            draw.ellipse([lx - r, ly - r, lx + r, ly + r], fill=color)
        else:
            draw.line(local_points, fill=color, width=width, joint="curve")

        buf = BytesIO()
        img.save(buf, format="PNG")
        photo = tk.PhotoImage(data=buf.getvalue())
        return photo, x0, y0

    def _render_image_thumbnail(self, source, w: int, h: int, rotation: float = 0):
        """Loads the chosen image and fits it (preserving aspect ratio)
        within a w x h box, optionally rotated by `rotation` degrees
        (expand=True, so corners aren't clipped — the returned image is
        simply larger as the angle approaches 45°). `source` is either a
        filepath (str, from the New Image tool) or raw encoded bytes
        (from an existing_image edit, extracted straight out of the
        PDF). Returns a tk.PhotoImage, or None if it can't be read so
        the caller can fall back to a plain placeholder instead of
        crashing the redraw."""
        from io import BytesIO
        from PIL import Image

        try:
            img = Image.open(BytesIO(source)) if isinstance(source, (bytes, bytearray)) else Image.open(source)
            img = img.convert("RGBA")
        except Exception:
            return None
        img.thumbnail((max(1, round(w)), max(1, round(h))))
        if rotation:
            # Canvas y grows downward, so negate to make a positive
            # rotation value feel clockwise when dragging the handle.
            img = img.rotate(-rotation, expand=True)
        buf = BytesIO()
        img.save(buf, format="PNG")
        return tk.PhotoImage(data=buf.getvalue())

    def _update_color_swatches(self):
        current = self.color_var.get()
        for name, sw in self._color_swatches.items():
            sw.config(highlightthickness=2 if name == current else 0,
                       highlightbackground=config.TEXT_MAIN)
        self._custom_swatch.config(
            highlightthickness=2 if current == "Custom" else 0,
            highlightbackground=config.TEXT_MAIN,
        )

    # ==================================================================
    # canvas <-> pdf coordinate helpers
    # ==================================================================

    def _canvas_to_pdf(self, cx: float, cy: float) -> tuple[float, float]:
        scale = self._scale()
        return (cx - self._page_x_offset) / scale, cy / scale

    def _pdf_rect_to_canvas(self, rect) -> tuple[float, float, float, float]:
        scale = self._scale()
        off = self._page_x_offset
        return rect[0] * scale + off, rect[1] * scale, rect[2] * scale + off, rect[3] * scale

    # ==================================================================
    # mouse handling
    # ==================================================================

    def _on_canvas_press(self, event):
        self.canvas.focus_set()
        cx, cy = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        if self.active_tool is None:
            self._try_start_interaction(cx, cy)
            return

        if self.active_tool == T_ERASER:
            self._erase_at(cx, cy)
            return

        if self.active_tool in CLICK_POINT_TOOLS:
            self._handle_click_point(cx, cy)
            return

        # everything else starts a drag
        self._drag_start_canvas = (cx, cy)
        self._current_path_points = [(cx, cy)]

    def _on_canvas_drag(self, event):
        cx, cy = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)

        if self._interaction is not None:
            self._update_interaction(cx, cy)
            return

        if self.active_tool is None or self._drag_start_canvas is None:
            return

        for iid in self._drag_preview_ids:
            self.canvas.delete(iid)
        self._drag_preview_ids = []

        if self.active_tool in DRAG_PATH_TOOLS:
            self._current_path_points.append((cx, cy))
            width = config.EDIT_HIGHLIGHT_WIDTH if self.active_tool == T_HIGHLIGHT else self._safe_int_var(self.width_var, config.DEFAULT_STROKE_WIDTH)
            if self.active_tool == T_HIGHLIGHT:
                photo, ix, iy = self._render_highlight_image(
                    self._current_path_points, width, self._current_rgb(),
                    self._safe_int_var(self.opacity_var, round(config.EDIT_HIGHLIGHT_OPACITY * 100)) / 100.0,
                )
                self._drag_preview_photo = photo  # keep it referenced — see _render_highlight_image
                iid = self.canvas.create_image(ix, iy, anchor="nw", image=photo)
            else:
                flat = [c for p in self._current_path_points for c in p]
                color = self._current_hex()
                iid = self.canvas.create_line(*flat, fill=color, width=width, smooth=True)
            self._drag_preview_ids = [iid]
        else:
            x0, y0 = self._drag_start_canvas
            color = self._current_hex()
            iid = self.canvas.create_rectangle(x0, y0, cx, cy, outline=color, dash=(3, 2))
            self._drag_preview_ids = [iid]

    def _on_canvas_release(self, event):
        if self._interaction is not None:
            self._interaction = None
            return

        if self.active_tool is None or self._drag_start_canvas is None:
            self._drag_start_canvas = None
            return
        cx, cy = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        x0, y0 = self._drag_start_canvas

        for iid in self._drag_preview_ids:
            self.canvas.delete(iid)
        self._drag_preview_ids = []

        if self.active_tool in DRAG_PATH_TOOLS:
            self._finish_path_tool()
        elif self.active_tool in TEXT_SELECT_TOOLS:
            self._finish_text_select_tool(x0, y0, cx, cy)
        elif self.active_tool == T_SHAPE:
            self._finish_shape_tool(x0, y0, cx, cy)
        elif self.active_tool == T_FREETEXT:
            self._finish_rect_dialog_tool(x0, y0, cx, cy)
        elif self.active_tool == T_SIGNATURE:
            self._finish_signature_placement(x0, y0, cx, cy)
        elif self.active_tool == T_PARAGRAPH:
            self._finish_paragraph_tool(x0, y0, cx, cy)
        elif self.active_tool == T_IMAGE:
            self._finish_image_placement(x0, y0, cx, cy)

        self._drag_start_canvas = None
        self._current_path_points = []

    # -- select / move / resize (active_tool is None => "select mode") --

    def _edit_bounds_pdf(self, edit: dict) -> tuple:
        """(x0, y0, x1, y1) in PDF space for any rect- or two-point-based
        edit — Line/Arrow shapes only have "points", everything else
        rotatable has "rect". Used to find a rotation center/handle
        position generically."""
        if "rect" in edit:
            return edit["rect"]
        if "points" in edit and len(edit["points"]) == 2:
            (x0, y0), (x1, y1) = edit["points"]
            return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        return (0, 0, 0, 0)

    def _try_start_interaction(self, cx, cy):
        for iid in self.canvas.find_overlapping(cx - 6, cy - 6, cx + 6, cy + 6):
            if "rotate_handle" in self.canvas.gettags(iid) and iid in self._rotate_handle_to_edit:
                edit = self._rotate_handle_to_edit[iid]
                x0, y0, x1, y1 = self._edit_bounds_pdf(edit)
                ccx, ccy = self._pdf_point_to_canvas((x0 + x1) / 2, (y0 + y1) / 2)
                start_angle = math.degrees(math.atan2(cy - ccy, cx - ccx))
                self._interaction = {
                    "mode": "rotate", "edit": edit, "center": (ccx, ccy),
                    "start_angle": start_angle, "orig_rotation": edit.get("rotation", 0),
                }
                return
        for iid in self.canvas.find_overlapping(cx - 5, cy - 5, cx + 5, cy + 5):
            if "resize_handle" in self.canvas.gettags(iid) and iid in self._handle_to_edit:
                edit = self._handle_to_edit[iid]
                self._interaction = {"mode": "resize", "edit": edit, "start": (cx, cy), "orig_rect": edit["rect"]}
                return
        for iid in self.canvas.find_overlapping(cx - 2, cy - 2, cx + 2, cy + 2):
            tags = self.canvas.gettags(iid)
            if "movable" in tags and iid in self._overlay_item_to_edit:
                edit = self._overlay_item_to_edit[iid]
                if edit["type"] in MOVABLE_TYPES:
                    self._interaction = {
                        "mode": "move", "edit": edit, "start": (cx, cy),
                        "orig_geom": self._capture_geometry(edit),
                    }
                    return

        # Nothing else was hit — in Edit mode, check whether the click
        # landed on an existing (not-yet-promoted) embedded image, and
        # if so promote it into a real pending edit right now so the
        # same drag that selected it can also move it.
        if self.mode_var.get() == EDIT:
            px, py = self._canvas_to_pdf(cx, cy)
            block = self._image_edit_overlay.hit_test(px, py)
            if block is not None:
                edit = {
                    "type": T_EXISTING_IMAGE, "page": self.current_page_index,
                    "rect": block["rect"], "original_rect": block["rect"],
                    "rotation": 0, "image_bytes": block["image_bytes"],
                    "ext": block["ext"], "xref": block["xref"],
                }
                self._add_edit(edit)
                self._image_edit_overlay.remove(block["xref"])
                self._interaction = {
                    "mode": "move", "edit": edit, "start": (cx, cy),
                    "orig_geom": self._capture_geometry(edit),
                }

    def _capture_geometry(self, edit: dict) -> dict:
        """A snapshot of whatever positional data this edit type actually
        has, taken once when a drag starts — _update_interaction applies
        the accumulated (dx, dy) against this snapshot rather than
        mutating incrementally, same as the original resize logic did."""
        if "quads" in edit:
            return {"quads": list(edit["quads"])}
        if "points" in edit:
            return {"points": list(edit["points"])}
        if "point" in edit:
            return {"point": edit["point"]}
        if "rect" in edit:
            return {"rect": edit["rect"]}
        return {}

    def _update_interaction(self, cx, cy):
        inter = self._interaction
        edit = inter["edit"]
        scale = self._scale()

        if inter["mode"] == "rotate":
            ccx, ccy = inter["center"]
            current_angle = math.degrees(math.atan2(cy - ccy, cx - ccx))
            delta = current_angle - inter["start_angle"]
            edit["rotation"] = (inter["orig_rotation"] + delta) % 360
            self._redraw_overlay()
            return

        dx = (cx - inter["start"][0]) / scale
        dy = (cy - inter["start"][1]) / scale

        if inter["mode"] == "resize":
            x0, y0, x1, y1 = inter["orig_rect"]
            edit["rect"] = (x0, y0, max(x0 + 20, x1 + dx), max(y0 + 14, y1 + dy))
        else:  # move — shift whatever geometry this edit type has
            geom = inter["orig_geom"]
            if "rect" in geom:
                x0, y0, x1, y1 = geom["rect"]
                edit["rect"] = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
            elif "points" in geom:
                edit["points"] = [(x + dx, y + dy) for x, y in geom["points"]]
            elif "point" in geom:
                x, y = geom["point"]
                edit["point"] = (x + dx, y + dy)
            elif "quads" in geom:
                shifted = []
                for q in geom["quads"]:
                    r = q.rect
                    shifted.append(pymupdf.Rect(r.x0 + dx, r.y0 + dy, r.x1 + dx, r.y1 + dy).quad)
                edit["quads"] = shifted

        self._redraw_overlay()

    def _on_canvas_double_click(self, event):
        cx, cy = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        if self.mode_var.get() == EDIT:
            self._text_edit_overlay.handle_double_click(cx, cy)
            return
        for iid in self.canvas.find_overlapping(cx - 2, cy - 2, cx + 2, cy + 2):
            if iid not in self._overlay_item_to_edit:
                continue
            edit = self._overlay_item_to_edit[iid]
            if edit["type"] in (T_FREETEXT, T_PARAGRAPH):
                title = "Edit Free Text" if edit["type"] == T_FREETEXT else "Edit Paragraph"

                def on_save(text, font_display, size, rgb, e=edit):
                    e["text"] = text
                    e["tk_font"] = font_display
                    e["fontsize"] = size
                    e["color"] = rgb
                    self._redraw_overlay()

                def on_delete(e=edit):
                    if e in self.pending_edits:
                        self.pending_edits.remove(e)
                    self._redraw_overlay()

                stored_rgb = edit.get("color", (0, 0, 0))
                preset_name = _color_name_for_rgb(stored_rgb)
                TextBoxDialog(
                    self, title, initial_text=edit["text"],
                    initial_font_name=edit.get("tk_font", config.DEFAULT_FONT_NAME),
                    initial_font_size=edit.get("fontsize", config.EDIT_DEFAULT_FONT_SIZE),
                    initial_color_name=preset_name or "Custom",
                    initial_custom_rgb=None if preset_name else stored_rgb,
                    allow_delete=True, on_save=on_save, on_delete=on_delete,
                )
                return
            elif edit["type"] == T_INSERT_TEXT:
                new_text = simpledialog.askstring(
                    "Edit Note", "Note text:", initialvalue=edit["text"], parent=self,
                )
                if new_text is not None:
                    edit["text"] = new_text
                    self._redraw_overlay()
                return

    # -- click-point tools --

    def _handle_click_point(self, cx, cy):
        px, py = self._canvas_to_pdf(cx, cy)
        if self.active_tool == T_INSERT_TEXT:
            text = simpledialog.askstring("Insert Text", "Note text:", parent=self)
            if text:
                self._add_edit({
                    "type": T_INSERT_TEXT, "page": self.current_page_index,
                    "point": (px, py), "text": text,
                })
        elif self.active_tool == T_STAMP:
            w, h = 130, 45
            self._add_edit({
                "type": T_STAMP, "page": self.current_page_index,
                "rect": (px, py, px + w, py + h), "name": self.stamp_name_var.get(),
            })
        elif self.active_tool == T_FILE_ATTACHMENT:
            if not self._pending_attachment_path:
                messagebox.showwarning("File Attachment", "Choose a file first.")
                return
            self._add_edit({
                "type": T_FILE_ATTACHMENT, "page": self.current_page_index,
                "point": (px, py), "filepath": self._pending_attachment_path,
            })

    _pending_attachment_path: Optional[str] = None

    def _choose_attachment_file(self):
        path = filedialog.askopenfilename(title="Choose a file to attach")
        if path:
            self._pending_attachment_path = path
            self._select_tool(T_FILE_ATTACHMENT)
            messagebox.showinfo(
                "File Attachment", "Now click on the page where the attachment icon should go."
            )

    # -- path tools (freehand / highlight) --

    def _finish_path_tool(self):
        if len(self._current_path_points) < 2:
            return
        points = [self._canvas_to_pdf(cx, cy) for cx, cy in self._current_path_points]
        color = self._current_rgb()
        width = config.EDIT_HIGHLIGHT_WIDTH if self.active_tool == T_HIGHLIGHT else self._safe_int_var(self.width_var, config.DEFAULT_STROKE_WIDTH)
        edit = {
            "type": self.active_tool, "page": self.current_page_index,
            "points": points, "color": color, "width": width,
        }
        if self.active_tool == T_HIGHLIGHT:
            edit["opacity"] = self._safe_int_var(self.opacity_var, round(config.EDIT_HIGHLIGHT_OPACITY * 100)) / 100.0
        self._add_edit(edit)

    # -- text-selection tools (underline/strikeout/squiggly/replace) --

    def _finish_text_select_tool(self, x0, y0, x1, y1):
        rect_pdf = self._canvas_rect_to_pdf(x0, y0, x1, y1)
        quads = get_word_quads_in_rect(self.doc[self.current_page_index], rect_pdf)
        if not quads:
            return
        color = self._current_rgb()
        if self.active_tool == T_REPLACE_TEXT:
            new_text = simpledialog.askstring("Replace Text", "Replacement text:", parent=self)
            if not new_text:
                return
            self._add_edit({
                "type": T_REPLACE_TEXT, "page": self.current_page_index,
                "quads": quads, "new_text": new_text, "color": color,
                "fontsize": config.EDIT_DEFAULT_FONT_SIZE, "tk_font": self.font_name_var.get(),
            })
        else:
            self._add_edit({
                "type": self.active_tool, "page": self.current_page_index,
                "quads": quads, "color": color,
            })

    def _canvas_rect_to_pdf(self, x0, y0, x1, y1):
        px0, py0 = self._canvas_to_pdf(min(x0, x1), min(y0, y1))
        px1, py1 = self._canvas_to_pdf(max(x0, x1), max(y0, y1))
        return (px0, py0, px1, py1)

    # -- Edit mode: committing an in-place text-block edit --

    def _commit_text_edit(self, rect: tuple, new_text: str, style: dict):
        """Called by TextEditOverlay once a line's inline entry is
        confirmed. Produces (or updates) a plain replace_text pending
        edit at that exact rect — editing the SAME line a second time
        before saving updates that edit in place instead of stacking a
        second one on top of it (which would double-draw the text)."""
        existing = self._existing_replace_text_edit(rect)
        if existing is not None:
            existing["new_text"] = new_text
            existing["color"] = style["color"]
            existing["fontsize"] = style["fontsize"]
            existing["tk_font"] = style["font_display"]
            existing["bold"] = style["bold"]
            existing["italic"] = style["italic"]
            self._redo_stack.clear()
            self._redraw_overlay()
            return
        quad = pymupdf.Rect(rect).quad
        self._add_edit({
            "type": T_REPLACE_TEXT, "page": self.current_page_index,
            "quads": [quad], "new_text": new_text, "color": style["color"],
            "fontsize": style["fontsize"], "tk_font": style["font_display"],
            "bold": style["bold"], "italic": style["italic"],
        })

    # -- shapes --

    def _finish_shape_tool(self, x0, y0, x1, y1):
        if abs(x1 - x0) < 3 and abs(y1 - y0) < 3:
            return
        shape = self.current_shape_var.get()
        color = self._current_rgb()
        width = self._safe_int_var(self.width_var, config.DEFAULT_STROKE_WIDTH)
        if shape in ("Rectangle", "Circle"):
            rect = self._canvas_rect_to_pdf(x0, y0, x1, y1)
            self._add_edit({
                "type": "shape", "page": self.current_page_index, "shape": shape,
                "rect": rect, "color": color, "width": width,
            })
        else:  # Line / Arrow
            p1 = self._canvas_to_pdf(x0, y0)
            p2 = self._canvas_to_pdf(x1, y1)
            self._add_edit({
                "type": "shape", "page": self.current_page_index, "shape": shape,
                "points": (p1, p2), "color": color, "width": width,
            })

    # -- free text / paragraph (drag rect then popup) --

    def _normalize_box(self, x0, y0, x1, y1) -> tuple[float, float, float, float]:
        """A plain click (no meaningful drag) places a default-size box
        anchored at the click point; an actual drag uses the dragged size."""
        if (abs(x1 - x0) < config.EDIT_CLICK_VS_DRAG_THRESHOLD
                and abs(y1 - y0) < config.EDIT_CLICK_VS_DRAG_THRESHOLD):
            return x0, y0, x0 + config.EDIT_DEFAULT_BOX_WIDTH, y0 + config.EDIT_DEFAULT_BOX_HEIGHT
        return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)

    def _finish_rect_dialog_tool(self, x0, y0, x1, y1):
        x0, y0, x1, y1 = self._normalize_box(x0, y0, x1, y1)
        rect = self._canvas_rect_to_pdf(x0, y0, x1, y1)

        def on_save(text, font_display, size, rgb):
            self._add_edit({
                "type": T_FREETEXT, "page": self.current_page_index,
                "rect": rect, "text": text, "color": rgb,
                "fontsize": size, "tk_font": font_display,
            })

        TextBoxDialog(
            self, "Free Text",
            initial_font_name=self.font_name_var.get(),
            initial_font_size=self._safe_int_var(self.font_size_var, config.EDIT_DEFAULT_FONT_SIZE),
            initial_color_name=self.color_var.get(),
            initial_custom_rgb=self.custom_rgb,
            allow_delete=False, on_save=on_save,
        )

    def _finish_paragraph_tool(self, x0, y0, x1, y1):
        x0, y0, x1, y1 = self._normalize_box(x0, y0, x1, y1)
        rect = self._canvas_rect_to_pdf(x0, y0, x1, y1)

        def on_save(text, font_display, size, rgb):
            self._add_edit({
                "type": T_PARAGRAPH, "page": self.current_page_index,
                "rect": rect, "text": text, "color": rgb,
                "fontsize": size, "tk_font": font_display,
            })

        TextBoxDialog(
            self, "Add Paragraph",
            initial_font_name=self.font_name_var.get(),
            initial_font_size=self._safe_int_var(self.font_size_var, config.EDIT_DEFAULT_FONT_SIZE),
            initial_color_name=self.color_var.get(),
            initial_custom_rgb=self.custom_rgb,
            allow_delete=False, on_save=on_save,
        )

    # -- signature / image placement (drag rect, already-chosen asset) --

    def _open_signature_pad(self):
        def on_done(png_bytes, aspect):
            self._pending_signature = (png_bytes, aspect)
            self._select_tool(T_SIGNATURE)
            messagebox.showinfo(
                "Signature", "Now click (or drag to size it) on the page to place your signature."
            )

        SignaturePadDialog(self, on_done)

    def _finish_signature_placement(self, x0, y0, x1, y1):
        if self._pending_signature is None:
            messagebox.showwarning("Signature", "Draw a signature first.")
            return
        x0, y0, x1, y1 = self._normalize_box(x0, y0, x1, y1)
        rect = self._canvas_rect_to_pdf(x0, y0, x1, y1)
        png_bytes, _aspect = self._pending_signature
        self._add_edit({
            "type": T_SIGNATURE, "page": self.current_page_index,
            "rect": rect, "png_bytes": png_bytes,
        })

    def _choose_new_image(self):
        path = filedialog.askopenfilename(
            title="Choose an image",
            filetypes=[("Image files", "*.jpg *.jpeg *.png *.webp *.bmp *.gif")],
        )
        if path:
            self._pending_image_path = path
            self._select_tool(T_IMAGE)
            messagebox.showinfo("New Image", "Now click (or drag to size it) on the page to place the image.")

    def _finish_image_placement(self, x0, y0, x1, y1):
        if not self._pending_image_path:
            messagebox.showwarning("New Image", "Choose an image first.")
            return
        x0, y0, x1, y1 = self._normalize_box(x0, y0, x1, y1)
        rect = self._canvas_rect_to_pdf(x0, y0, x1, y1)
        self._add_edit({
            "type": T_IMAGE, "page": self.current_page_index,
            "rect": rect, "filepath": self._pending_image_path,
        })

    # ==================================================================
    # pending-edit bookkeeping: add / undo / erase / redraw
    # ==================================================================

    # tools that place a single object and then hand off to select-mode so
    # the person can immediately move/resize/edit what they just placed
    SINGLE_SHOT_TYPES = (T_FREETEXT, T_PARAGRAPH, T_INSERT_TEXT, T_STAMP,
                         T_SIGNATURE, T_FILE_ATTACHMENT, T_IMAGE)

    def _add_edit(self, edit: dict):
        self.pending_edits.append(edit)
        self._redo_stack.clear()
        self._redraw_overlay()
        if edit["type"] in self.SINGLE_SHOT_TYPES:
            self._select_tool(None)

    def _undo(self):
        if not self.pending_edits:
            return
        self._redo_stack.append(self.pending_edits.pop())
        self._redraw_overlay()
        if self.mode_var.get() == EDIT:
            self._image_edit_overlay.refresh(self._promoted_image_xrefs())

    def _redo(self):
        if not self._redo_stack:
            return
        self.pending_edits.append(self._redo_stack.pop())
        self._redraw_overlay()
        if self.mode_var.get() == EDIT:
            self._image_edit_overlay.refresh(self._promoted_image_xrefs())

    def _erase_at(self, cx, cy):
        best_id = None
        best_dist = 12  # pixel tolerance
        for iid in self.canvas.find_all():
            if "overlay" not in self.canvas.gettags(iid):
                continue
            bbox = self.canvas.bbox(iid)
            if not bbox:
                continue
            x0, y0, x1, y1 = bbox
            if x0 - best_dist <= cx <= x1 + best_dist and y0 - best_dist <= cy <= y1 + best_dist:
                best_id = iid
                break
        if best_id is not None and best_id in self._overlay_item_to_edit:
            edit = self._overlay_item_to_edit[best_id]
            if edit in self.pending_edits:
                self.pending_edits.remove(edit)
            self._redraw_overlay()
            if self.mode_var.get() == EDIT:
                self._image_edit_overlay.refresh(self._promoted_image_xrefs())

    def _redraw_overlay(self):
        self.canvas.delete("overlay")
        self._overlay_item_to_edit = {}
        self._handle_to_edit = {}
        self._rotate_handle_to_edit = {}
        self._overlay_photos = []  # keeps this pass's highlight/image rasters alive
        for edit in self.pending_edits:
            if edit["page"] != self.current_page_index:
                continue
            for iid in self._draw_edit_preview(edit):
                self._overlay_item_to_edit[iid] = edit

    RESIZABLE_TYPES = (T_FREETEXT, T_PARAGRAPH, T_IMAGE, T_EXISTING_IMAGE)

    def _pdf_point_to_canvas(self, x, y) -> tuple[float, float]:
        scale = self._scale()
        return x * scale + self._page_x_offset, y * scale

    def _draw_edit_preview(self, edit: dict) -> list[int]:
        t = edit["type"]
        scale = self._scale()
        color_hex = "#%02x%02x%02x" % tuple(int(c * 255) for c in edit.get("color", (0, 0, 0)))
        ids = []

        if t in ("underline", "strikeout", "squiggly"):
            for q in edit["quads"]:
                r = q.rect
                y_pdf = r.y1 if t != "strikeout" else (r.y0 + r.y1) / 2
                x0, y0 = self._pdf_point_to_canvas(r.x0, y_pdf)
                x1, _ = self._pdf_point_to_canvas(r.x1, y_pdf)
                iid = self.canvas.create_line(
                    x0, y0, x1, y0, fill=color_hex, width=2, tags=("overlay", "movable"),
                )
                ids.append(iid)

        elif t in (T_FREEHAND, T_HIGHLIGHT):
            width = edit["width"]
            canvas_points = [self._pdf_point_to_canvas(*p) for p in edit["points"]]
            if t == T_HIGHLIGHT:
                # Real alpha compositing via a rasterized image — a canvas
                # line can't do transparency, only fake it with a flat
                # blended color that still fully hides the text beneath.
                # See _render_highlight_image. Saved-PDF opacity is exact
                # either way; this only affects the on-screen preview.
                opacity = edit.get("opacity", config.EDIT_HIGHLIGHT_OPACITY)
                photo, ix, iy = self._render_highlight_image(
                    canvas_points, width, edit.get("color", (0, 0, 0)), opacity,
                )
                self._overlay_photos.append(photo)
                iid = self.canvas.create_image(
                    ix, iy, anchor="nw", image=photo, tags=("overlay", "movable"),
                )
            else:
                flat = [c for p in canvas_points for c in p]
                iid = self.canvas.create_line(
                    *flat, fill=color_hex, width=width, smooth=True, tags=("overlay", "movable"),
                )
            ids.append(iid)

        elif t == T_FREETEXT:
            r = self._pdf_rect_to_canvas(edit["rect"])
            rotation = edit.get("rotation", 0)
            ccx, ccy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
            rotated_corners, resize_point, rotate_point = self._rotated_box_geometry(r, rotation)

            if rotation:
                flat_corners = [c for pt in rotated_corners for c in pt]
                ids.append(self.canvas.create_polygon(
                    *flat_corners, outline=color_hex, dash=(3, 2), fill="",
                    tags=("overlay", "movable"),
                ))
            else:
                ids.append(self.canvas.create_rectangle(
                    *r, outline=color_hex, dash=(3, 2), fill="white", stipple="gray25",
                    tags=("overlay", "movable"),
                ))

            preview_size = max(6, round(edit.get("fontsize", 12) * scale))
            text_x, text_y = self._rotate_point_around(r[0] + 3, r[1] + 3, ccx, ccy, rotation)
            text_kwargs = {"angle": (-rotation) % 360} if rotation else {}
            ids.append(self.canvas.create_text(
                text_x, text_y, text=edit["text"], anchor="nw", fill=color_hex,
                font=(edit.get("tk_font", "Helvetica"), preview_size),
                tags=("overlay", "movable"), **text_kwargs,
            ))
            ids += self._draw_resize_handle(resize_point, edit)
            ids += self._draw_rotate_handle(rotate_point, edit)

        elif t == T_INSERT_TEXT:
            x, y = self._pdf_point_to_canvas(*edit["point"])
            ids.append(self.canvas.create_rectangle(
                x, y, x + 18, y + 18, fill="#f7d716", outline="#8a7000", tags=("overlay", "movable"),
            ))
            # a couple of short lines to read as a "note" rather than a blank square
            ids.append(self.canvas.create_line(x + 4, y + 6, x + 14, y + 6, fill="#8a7000", tags=("overlay", "movable")))
            ids.append(self.canvas.create_line(x + 4, y + 10, x + 14, y + 10, fill="#8a7000", tags=("overlay", "movable")))
            ids.append(self.canvas.create_line(x + 4, y + 14, x + 10, y + 14, fill="#8a7000", tags=("overlay", "movable")))

        elif t == T_REPLACE_TEXT:
            # Deliberately NOT movable — it's anchored to a redaction of
            # the original words, so dragging it wouldn't mean anything.
            for q in edit["quads"]:
                r = self._pdf_rect_to_canvas(q.rect)
                ids.append(self.canvas.create_rectangle(
                    *r, fill="white", outline=color_hex, tags="overlay",
                ))
            r0 = self._pdf_rect_to_canvas(edit["quads"][0].rect)
            ids.append(self.canvas.create_text(
                r0[0], r0[1], text=edit["new_text"], anchor="nw",
                fill=color_hex, tags="overlay",
            ))

        elif t == "shape":
            shape = edit["shape"]
            width = edit["width"]
            rotation = edit.get("rotation", 0)
            bounds_pdf = self._edit_bounds_pdf(edit)
            bounds_canvas = self._pdf_rect_to_canvas(bounds_pdf)

            if rotation:
                # Same rasterize-then-rotate approach as the save path
                # (rasterize_shape, shared with pdf_edit_operations.py) —
                # native canvas primitives can't rotate arbitrarily.
                w_px = max(1, round(bounds_canvas[2] - bounds_canvas[0]))
                h_px = max(1, round(bounds_canvas[3] - bounds_canvas[1]))
                shape_color = edit.get("color", (0, 0, 0))
                if shape in ("Rectangle", "Circle"):
                    img = rasterize_shape(shape, w_px, h_px, shape_color, width)
                else:
                    p1, p2 = edit["points"]
                    c1 = self._pdf_point_to_canvas(*p1)
                    c2 = self._pdf_point_to_canvas(*p2)
                    local_p1 = (c1[0] - bounds_canvas[0], c1[1] - bounds_canvas[1])
                    local_p2 = (c2[0] - bounds_canvas[0], c2[1] - bounds_canvas[1])
                    img = rasterize_shape(shape, w_px, h_px, shape_color, width, (local_p1, local_p2))
                rotated = img.rotate(-rotation, expand=True)
                from io import BytesIO
                buf = BytesIO()
                rotated.save(buf, format="PNG")
                photo = tk.PhotoImage(data=buf.getvalue())
                self._overlay_photos.append(photo)
                ccx = (bounds_canvas[0] + bounds_canvas[2]) / 2
                ccy = (bounds_canvas[1] + bounds_canvas[3]) / 2
                ids.append(self.canvas.create_image(
                    ccx, ccy, anchor="center", image=photo, tags=("overlay", "movable"),
                ))
            elif shape == "Rectangle":
                r = self._pdf_rect_to_canvas(edit["rect"])
                ids.append(self.canvas.create_rectangle(*r, outline=color_hex, width=width, tags=("overlay", "movable")))
            elif shape == "Circle":
                r = self._pdf_rect_to_canvas(edit["rect"])
                ids.append(self.canvas.create_oval(*r, outline=color_hex, width=width, tags=("overlay", "movable")))
            elif shape in ("Line", "Arrow"):
                p1, p2 = edit["points"]
                x0, y0 = self._pdf_point_to_canvas(*p1)
                x1, y1 = self._pdf_point_to_canvas(*p2)
                arrow = tk.LAST if shape == "Arrow" else None
                ids.append(self.canvas.create_line(
                    x0, y0, x1, y1, fill=color_hex, width=width, arrow=arrow, tags=("overlay", "movable"),
                ))

            _corners, _resize_pt, rotate_point = self._rotated_box_geometry(bounds_canvas, rotation)
            ids += self._draw_rotate_handle(rotate_point, edit)

        elif t == T_STAMP:
            r = self._pdf_rect_to_canvas(edit["rect"])
            ids.append(self.canvas.create_rectangle(*r, outline="#e5533c", width=2, tags=("overlay", "movable")))
            ids.append(self.canvas.create_text(
                (r[0] + r[2]) / 2, (r[1] + r[3]) / 2, text=edit["name"],
                fill="#e5533c", tags=("overlay", "movable"),
            ))

        elif t == T_SIGNATURE:
            r = self._pdf_rect_to_canvas(edit["rect"])
            ids.append(self.canvas.create_rectangle(*r, outline="#3b82f6", dash=(2, 2), tags=("overlay", "movable")))
            ids.append(self.canvas.create_text(
                (r[0] + r[2]) / 2, (r[1] + r[3]) / 2, text="Signature", fill="#3b82f6", tags=("overlay", "movable"),
            ))

        elif t == T_FILE_ATTACHMENT:
            x, y = self._pdf_point_to_canvas(*edit["point"])
            ids.append(self.canvas.create_text(
                x, y, text="[file]", anchor="nw", fill="#9a9ba5", tags=("overlay", "movable"),
            ))

        elif t == T_PARAGRAPH:
            r = self._pdf_rect_to_canvas(edit["rect"])
            ids.append(self.canvas.create_rectangle(
                *r, outline=color_hex, dash=(3, 2), fill="white", stipple="gray25",
                tags=("overlay", "movable"),
            ))
            preview_size = max(6, round(edit.get("fontsize", 12) * scale))
            ids.append(self.canvas.create_text(
                r[0] + 3, r[1] + 3, text=edit["text"][:80], anchor="nw", fill=color_hex,
                font=(edit.get("tk_font", "Helvetica"), preview_size),
                width=max(r[2] - r[0] - 6, 10), tags=("overlay", "movable"),
            ))
            ids += self._draw_resize_handle((r[2], r[3]), edit)

        elif t in (T_IMAGE, T_EXISTING_IMAGE):
            if t == T_EXISTING_IMAGE:
                # Mask out where the image used to sit on the actual
                # page — the canvas render underneath is the real PDF
                # page as a bitmap, which still has the original image
                # baked in until Save actually rewrites the file. Without
                # this, dragging the image away leaves the untouched
                # original visible at its old spot, duplicated.
                mask = self._pdf_rect_to_canvas(edit["original_rect"])
                ids.append(self.canvas.create_rectangle(*mask, fill="white", outline="", tags="overlay"))

            r = self._pdf_rect_to_canvas(edit["rect"])
            w, h = max(1, round(r[2] - r[0])), max(1, round(r[3] - r[1]))
            rotation = edit.get("rotation", 0)
            rotated_corners, resize_point, rotate_point = self._rotated_box_geometry(r, rotation)

            # Cached per (box size, rotation) — New Image's box size only
            # changes via its own resize handle now, and rotation only via
            # its rotate handle, so a plain move/drag redraw always hits
            # this cache instead of re-decoding the file/bytes every frame.
            cache = edit.get("_thumb_cache")
            cache_key = (w, h, round(rotation, 1))
            if cache is not None and cache[0] == cache_key:
                photo = cache[1]
            else:
                source = edit["image_bytes"] if t == T_EXISTING_IMAGE else edit.get("filepath", "")
                photo = self._render_image_thumbnail(source, w, h, rotation)
                edit["_thumb_cache"] = (cache_key, photo)

            outline_kwargs = dict(outline="#2ecc71", dash=(2, 2), tags=("overlay", "movable"))
            if photo is not None:
                self._overlay_photos.append(photo)
                cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
                ids.append(self.canvas.create_image(
                    cx, cy, anchor="center", image=photo, tags=("overlay", "movable"),
                ))
                # outline of the placed/resizable box too, so the full
                # area stays visible even where the (aspect-fitted)
                # thumbnail doesn't fully cover it — rotated to match if
                # the box is rotated, so it actually tracks the content
                if rotation:
                    ids.append(self.canvas.create_polygon(
                        *[c for pt in rotated_corners for c in pt], fill="", **outline_kwargs,
                    ))
                else:
                    ids.append(self.canvas.create_rectangle(*r, **outline_kwargs))
            else:
                if rotation:
                    ids.append(self.canvas.create_polygon(
                        *[c for pt in rotated_corners for c in pt], fill="", **outline_kwargs,
                    ))
                else:
                    ids.append(self.canvas.create_rectangle(*r, **outline_kwargs))
                ids.append(self.canvas.create_text(
                    (r[0] + r[2]) / 2, (r[1] + r[3]) / 2, text="Image", fill="#2ecc71", tags=("overlay", "movable"),
                ))

            ids += self._draw_resize_handle(resize_point, edit)
            ids += self._draw_rotate_handle(rotate_point, edit)

        return ids

    def _rotate_point_around(self, px, py, ccx, ccy, rotation_deg):
        """Rotates canvas point (px, py) around canvas center (ccx, ccy)
        by rotation_deg, using the same sign convention the rendered
        content itself uses (Tk's create_text angle=, and the rasterized
        image rotate calls) — so a box/handle rotated with this actually
        lines up with what's drawn, instead of staying frozen in place."""
        theta = math.radians(rotation_deg)
        dx, dy = px - ccx, py - ccy
        rx = dx * math.cos(theta) - dy * math.sin(theta)
        ry = dx * math.sin(theta) + dy * math.cos(theta)
        return (ccx + rx, ccy + ry)

    def _rotated_box_geometry(self, canvas_rect, rotation):
        """For a rotatable box (Free Text, Image): the 4 corners rotated
        around the box's own center, plus where the resize handle
        (rotated bottom-right corner) and rotate handle (rotated
        top-center, floated outward) should sit. With rotation=0 this is
        just the plain axis-aligned corners — same numbers as before."""
        x0, y0, x1, y1 = canvas_rect
        ccx, ccy = (x0 + x1) / 2, (y0 + y1) / 2
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        rotated_corners = [self._rotate_point_around(px, py, ccx, ccy, rotation) for px, py in corners]
        resize_point = rotated_corners[2]  # bottom-right, rotated
        rotate_point = self._rotate_point_around((x0 + x1) / 2, y0 - 18, ccx, ccy, rotation)
        return rotated_corners, resize_point, rotate_point

    def _draw_resize_handle(self, point: tuple, edit) -> list[int]:
        """A small square at `point` (normally a box's bottom-right
        corner, rotated to match if the box is rotated) — drag it to
        resize, drag the box itself to move, double-click to edit the
        text."""
        x1, y1 = point
        size = 6
        iid = self.canvas.create_rectangle(
            x1 - size, y1 - size, x1 + size, y1 + size,
            fill=config.ACCENT, outline="white", tags=("overlay", "resize_handle"),
        )
        self._handle_to_edit[iid] = edit
        return [iid]

    def _draw_rotate_handle(self, point: tuple, edit: dict) -> list[int]:
        """A small circular handle centered at `point` (normally floating
        above a box's top-center, rotated to match if the box is
        rotated) — drag it around the box's center to rotate. Distinct
        from the resize handle: dragging this never changes size/
        position, only edit["rotation"]."""
        cx, cy = point
        r = 8
        ids = []
        ids.append(self.canvas.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            fill=config.PANEL_BG, outline=config.ACCENT, width=2,
            tags=("overlay", "rotate_handle"),
        ))
        ids.append(self.canvas.create_text(
            cx, cy, text="\u27f3", fill=config.ACCENT, font=(config.FONT_FAMILY, 11),
            tags=("overlay", "rotate_handle"),
        ))
        for iid in ids:
            self._rotate_handle_to_edit[iid] = edit
        return ids

    # ==================================================================
    # save
    # ==================================================================

    def _save_changes(self):
        if not self.input_path:
            messagebox.showwarning("Edit PDF", "Choose a PDF first.")
            return
        if not self.pending_edits and not self.blank_page_indices:
            messagebox.showwarning("Edit PDF", "No changes to save yet.")
            return

        out_path = filedialog.asksaveasfilename(
            defaultextension=".pdf", initialfile="edited.pdf",
            filetypes=[("PDF files", "*.pdf")],
        )
        if not out_path:
            return

        edits = list(self.pending_edits)
        blank_pages = sorted(self.blank_page_indices)
        self.progress.set_running(True)

        def work():
            return apply_edits_and_save(
                self.input_path, out_path, edits, blank_pages=blank_pages,
                progress=threadsafe_progress(self.root, self.progress),
            )

        def done(result):
            self.progress.set_running(False)
            self.progress.reset()
            messagebox.showinfo("Edit PDF", f"Saved to:\n{result}")

        def error(e):
            self.progress.set_running(False)
            self.progress.reset()
            messagebox.showerror("Edit PDF", f"Something went wrong:\n{e}")

        run_in_background(self.root, work, done, error)
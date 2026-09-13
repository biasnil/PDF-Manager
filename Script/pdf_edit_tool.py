"""
Script/pdf_edit_tool.py — the "Edit PDF" tab.

Two modes, switched by a toggle:
  Annotate — Underline, Strikeout, Squiggly, Free Hand, Free Hand Highlight,
             Free Text, Insert Text, Replace Text, Undo, Eraser, Shapes
             (Rectangle/Circle/Line/Arrow with color+width style), and
             Insert (Stamp, Signature, File Attachment).
  Edit     — Add Paragraph, New Image (burned directly into the page content,
             not removable annotations).

A left-hand panel lists every page as a thumbnail; clicking one jumps the
main canvas to that page. Every action is recorded as a "pending edit" and
only actually written into the PDF when "Save changes" is pressed — Undo
just pops the last pending edit, Eraser removes whichever one you click on.
"""

import io
import os
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk
from typing import Optional

from Config import config
from Script.base_tool import BaseTool
from Script.pdf_edit_operations import apply_edits_and_save, get_word_quads_in_rect
from Script.pdf_operations import PDFSplitter
from Script.threading_utils import run_in_background, threadsafe_progress
from Script.widgets import ProgressRow, ScrollableFrame

import pymupdf

ANNOTATE = "annotate"
EDIT = "edit"

# tool ids
T_UNDERLINE = "underline"
T_STRIKEOUT = "strikeout"
T_SQUIGGLY = "squiggly"
T_FREEHAND = "freehand"
T_HIGHLIGHT = "highlight_freehand"
T_FREETEXT = "freetext"
T_INSERT_TEXT = "insert_text_note"
T_REPLACE_TEXT = "replace_text"
T_ERASER = "eraser"
T_SHAPE = "shape"
T_STAMP = "stamp"
T_SIGNATURE = "signature"
T_FILE_ATTACHMENT = "file_attachment"
T_PARAGRAPH = "paragraph"
T_IMAGE = "image"
T_SELECT = "select"

TEXT_SELECT_TOOLS = (T_UNDERLINE, T_STRIKEOUT, T_SQUIGGLY, T_REPLACE_TEXT)
DRAG_RECT_TOOLS = (T_FREETEXT, T_SIGNATURE, T_PARAGRAPH, T_IMAGE, T_SHAPE)
DRAG_PATH_TOOLS = (T_FREEHAND, T_HIGHLIGHT)
CLICK_POINT_TOOLS = (T_INSERT_TEXT, T_STAMP, T_FILE_ATTACHMENT)


def _hex_for(color_name: str) -> str:
    for name, hexval, _rgb in config.ANNOT_COLOR_PALETTE:
        if name == color_name:
            return hexval
    return "#000000"


def _rgb_for(color_name: str) -> tuple:
    for name, _hexval, rgb in config.ANNOT_COLOR_PALETTE:
        if name == color_name:
            return rgb
    return (0, 0, 0)


def _font_code_for(display_name: str) -> str:
    for name, code in config.FONT_CHOICES:
        if name == display_name:
            return code
    return "helv"


def _color_name_for_rgb(rgb) -> str:
    for name, _hexval, c in config.ANNOT_COLOR_PALETTE:
        if all(abs(a - b) < 0.01 for a, b in zip(c, rgb)):
            return name
    return config.DEFAULT_ANNOT_COLOR_NAME


class SignaturePadDialog(tk.Toplevel):
    """Popup for drawing a signature freehand; hands back PNG bytes
    (transparent background, trimmed to the drawn strokes) on confirm."""

    def __init__(self, master, on_done):
        super().__init__(master)
        self.title("Draw your signature")
        self.configure(bg=config.PANEL_BG)
        self.on_done = on_done
        self.strokes: list[list[tuple[int, int]]] = []
        self._current: Optional[list[tuple[int, int]]] = None

        self.canvas = tk.Canvas(self, width=420, height=160, bg="white",
                                 cursor="pencil", highlightthickness=1,
                                 highlightbackground="#888")
        self.canvas.pack(padx=12, pady=12)
        self.canvas.bind("<Button-1>", self._start)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._end)

        btn_row = tk.Frame(self, bg=config.PANEL_BG)
        btn_row.pack(pady=(0, 12))
        tk.Button(btn_row, text="Clear", command=self._clear,
                  bg=config.BUTTON_BG, fg=config.TEXT_MAIN, relief=tk.FLAT,
                  padx=10, pady=5).pack(side=tk.LEFT, padx=4)
        tk.Button(btn_row, text="Cancel", command=self.destroy,
                  bg=config.BUTTON_BG, fg=config.TEXT_MAIN, relief=tk.FLAT,
                  padx=10, pady=5).pack(side=tk.LEFT, padx=4)
        tk.Button(btn_row, text="Use this signature", command=self._confirm,
                  bg=config.ACCENT, fg="white", relief=tk.FLAT,
                  padx=10, pady=5).pack(side=tk.LEFT, padx=4)

    def _start(self, event):
        self._current = [(event.x, event.y)]

    def _move(self, event):
        if self._current is None:
            return
        last = self._current[-1]
        self.canvas.create_line(
            last[0], last[1], event.x, event.y, width=3, fill="black",
            capstyle=tk.ROUND, smooth=True, tags="stroke",
        )
        self._current.append((event.x, event.y))

    def _end(self, event):
        if self._current and len(self._current) > 1:
            self.strokes.append(self._current)
        self._current = None

    def _clear(self):
        self.canvas.delete("stroke")
        self.strokes = []

    def _confirm(self):
        if not self.strokes:
            messagebox.showwarning("Signature", "Draw a signature first.")
            return
        from PIL import Image, ImageDraw

        all_points = [p for stroke in self.strokes for p in stroke]
        xs = [p[0] for p in all_points]
        ys = [p[1] for p in all_points]
        pad = 8
        x0, x1 = max(0, min(xs) - pad), max(xs) + pad
        y0, y1 = max(0, min(ys) - pad), max(ys) + pad
        w, h = int(x1 - x0), int(y1 - y0)

        img = Image.new("RGBA", (w, h), (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)
        for stroke in self.strokes:
            pts = [(x - x0, y - y0) for x, y in stroke]
            if len(pts) > 1:
                draw.line(pts, fill=(0, 0, 0, 255), width=3, joint="curve")

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        aspect = w / h if h else 1.0
        self.on_done(buf.getvalue(), aspect)
        self.destroy()


class TextBoxDialog(tk.Toplevel):
    """Full editor popup for a Free Text or Add Paragraph box: text, font,
    size, and color — with an optional Delete button when editing an
    existing box (not shown when creating a new one)."""

    def __init__(self, master, title, initial_text="",
                 initial_font_name=None, initial_font_size=None,
                 initial_color_name=None, allow_delete=False,
                 on_save=None, on_delete=None):
        super().__init__(master)
        self.title(title)
        self.configure(bg=config.PANEL_BG)
        self.on_save = on_save
        self.on_delete = on_delete

        tk.Label(
            self, text="Text:", bg=config.PANEL_BG, fg=config.TEXT_MAIN,
            font=config.FONT_SMALL, anchor="w",
        ).pack(fill=tk.X, padx=12, pady=(12, 2))
        self.text_widget = tk.Text(
            self, width=48, height=5, wrap=tk.WORD,
            bg=config.INPUT_BG, fg=config.TEXT_MAIN, insertbackground=config.TEXT_MAIN,
            font=config.FONT_BODY,
        )
        self.text_widget.pack(padx=12)
        self.text_widget.insert("1.0", initial_text)
        self.text_widget.focus_set()

        style_row = tk.Frame(self, bg=config.PANEL_BG)
        style_row.pack(fill=tk.X, padx=12, pady=(10, 0))
        tk.Label(
            style_row, text="Font:", bg=config.PANEL_BG, fg=config.TEXT_MAIN,
            font=config.FONT_SMALL,
        ).pack(side=tk.LEFT)
        self.font_var = tk.StringVar(value=initial_font_name or config.DEFAULT_FONT_NAME)
        ttk.Combobox(
            style_row, textvariable=self.font_var, state="readonly", width=11,
            values=[name for name, _code in config.FONT_CHOICES],
        ).pack(side=tk.LEFT, padx=(4, 12))
        tk.Label(
            style_row, text="Size:", bg=config.PANEL_BG, fg=config.TEXT_MAIN,
            font=config.FONT_SMALL,
        ).pack(side=tk.LEFT)
        self.size_var = tk.IntVar(value=initial_font_size or config.EDIT_DEFAULT_FONT_SIZE)
        ttk.Combobox(
            style_row, textvariable=self.size_var, state="readonly", width=4,
            values=config.FONT_SIZE_OPTIONS,
        ).pack(side=tk.LEFT, padx=(4, 0))

        color_row = tk.Frame(self, bg=config.PANEL_BG)
        color_row.pack(fill=tk.X, padx=12, pady=(10, 0))
        tk.Label(
            color_row, text="Font color:", bg=config.PANEL_BG, fg=config.TEXT_MAIN,
            font=config.FONT_SMALL,
        ).pack(side=tk.LEFT, padx=(0, 6))
        self.color_var = tk.StringVar(value=initial_color_name or config.DEFAULT_ANNOT_COLOR_NAME)
        self._swatches: dict[str, tk.Label] = {}
        for name, hexval, _rgb in config.ANNOT_COLOR_PALETTE:
            sw = tk.Label(color_row, bg=hexval, width=2, height=1, cursor="hand2")
            sw.pack(side=tk.LEFT, padx=1)
            sw.bind("<Button-1>", lambda e, n=name: self._pick_color(n))
            self._swatches[name] = sw
        self._refresh_swatches()

        btn_row = tk.Frame(self, bg=config.PANEL_BG)
        btn_row.pack(fill=tk.X, padx=12, pady=12)
        if allow_delete:
            tk.Button(
                btn_row, text="Delete", command=self._confirm_delete,
                bg="#7a2a2a", fg="white", relief=tk.FLAT, padx=10, pady=5,
            ).pack(side=tk.LEFT)
        tk.Button(
            btn_row, text="Cancel", command=self.destroy,
            bg=config.BUTTON_BG, fg=config.TEXT_MAIN, relief=tk.FLAT, padx=10, pady=5,
        ).pack(side=tk.RIGHT, padx=(4, 0))
        tk.Button(
            btn_row, text="Save", command=self._confirm_save,
            bg=config.ACCENT, fg="white", relief=tk.FLAT, padx=10, pady=5,
        ).pack(side=tk.RIGHT)

    def _pick_color(self, name):
        self.color_var.set(name)
        self._refresh_swatches()

    def _refresh_swatches(self):
        for name, sw in self._swatches.items():
            sw.config(highlightthickness=2 if name == self.color_var.get() else 0,
                      highlightbackground=config.TEXT_MAIN)

    def _confirm_save(self):
        text = self.text_widget.get("1.0", tk.END).strip()
        if not text:
            self.destroy()
            return
        if self.on_save:
            self.on_save(text, self.font_var.get(), self.size_var.get(), self.color_var.get())
        self.destroy()

    def _confirm_delete(self):
        if self.on_delete:
            self.on_delete()
        self.destroy()


class PdfEditTool(BaseTool):
    title = "Edit PDF"
    description = (
        "Annotate a PDF (markup, freehand, shapes, stamps, signatures) or "
        "edit its content directly (new paragraphs, new images), then save "
        "the result as a new file. Free Text and Add Paragraph: click for a "
        "default-size box or drag to size it yourself; once placed, drag it "
        "to move, drag its corner handle to resize, or double-click to "
        "change the text — pick a font and size before placing one. "
        "Middle-click drag pans the page; plain scroll moves it; Ctrl+scroll "
        "zooms."
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
        self._nav_thumb_labels: dict[int, tk.Label] = {}

        self.mode_var = tk.StringVar(value=ANNOTATE)
        self.active_tool: Optional[str] = None
        self.current_shape_var = tk.StringVar(value=config.DEFAULT_SHAPE_TYPE)
        self.color_var = tk.StringVar(value=config.DEFAULT_ANNOT_COLOR_NAME)
        self.width_var = tk.IntVar(value=config.DEFAULT_STROKE_WIDTH)
        self.stamp_name_var = tk.StringVar(value=config.DEFAULT_STAMP_NAME)
        self.font_name_var = tk.StringVar(value=config.DEFAULT_FONT_NAME)
        self.font_size_var = tk.IntVar(value=config.EDIT_DEFAULT_FONT_SIZE)

        self._pending_signature: Optional[tuple[bytes, float]] = None  # (png_bytes, aspect)
        self._pending_image_path: Optional[str] = None

        self._drag_start_canvas: Optional[tuple[int, int]] = None
        self._drag_preview_ids: list[int] = []
        self._current_path_points: list[tuple[int, int]] = []

        self._tool_buttons: dict[str, tk.Button] = {}
        self.zoom_level = 1.0
        self.nav_visible = True
        self._page_x_offset = 0
        self._handle_to_edit: dict[int, dict] = {}
        self._interaction: Optional[dict] = None

        self._build_top_bar()
        self._build_layout()

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
            sw = tk.Label(row3, bg=hexval, width=2, height=1, relief=tk.FLAT, cursor="hand2")
            sw.pack(side=tk.LEFT, padx=1)
            sw.bind("<Button-1>", lambda e, n=name: self._select_color(n))
            self._color_swatches[name] = sw
        ttk.Combobox(
            row3, textvariable=self.width_var, state="readonly", width=4,
            values=config.STROKE_WIDTH_OPTIONS,
        ).pack(side=tk.LEFT, padx=(10, 0))
        tk.Label(
            row3, text="pt width", bg=config.PANEL_BG, fg=config.TEXT_MUTED,
            font=config.FONT_SMALL,
        ).pack(side=tk.LEFT, padx=(4, 0))
        tk.Label(
            row3, text="Font:", bg=config.PANEL_BG, fg=config.TEXT_MAIN,
            font=config.FONT_SMALL,
        ).pack(side=tk.LEFT, padx=(14, 4))
        ttk.Combobox(
            row3, textvariable=self.font_name_var, state="readonly", width=11,
            values=[name for name, _code in config.FONT_CHOICES],
        ).pack(side=tk.LEFT)
        ttk.Combobox(
            row3, textvariable=self.font_size_var, state="readonly", width=4,
            values=config.FONT_SIZE_OPTIONS,
        ).pack(side=tk.LEFT, padx=(6, 0))
        tk.Label(
            row3, text="size (Free Text)", bg=config.PANEL_BG, fg=config.TEXT_MUTED,
            font=config.FONT_SMALL,
        ).pack(side=tk.LEFT, padx=(4, 0))

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
        ).pack(side=tk.LEFT)

        self._update_color_swatches()

    def _build_toolbar_edit(self, parent):
        row = tk.Frame(parent, bg=config.PANEL_BG)
        row.pack(fill=tk.X, anchor="w")
        self._tool_button(row, "Select", T_SELECT).pack(side=tk.LEFT, padx=(0, 10))
        self._tool_button(row, "Add Paragraph", T_PARAGRAPH).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(
            row, text="New Image...", command=self._choose_new_image,
            bg=config.BUTTON_BG, fg=config.TEXT_MAIN, relief=tk.FLAT, bd=0,
            padx=8, pady=4, font=config.FONT_SMALL, cursor="hand2",
        ).pack(side=tk.LEFT)

        font_row = tk.Frame(parent, bg=config.PANEL_BG)
        font_row.pack(fill=tk.X, anchor="w", pady=(6, 0))
        tk.Label(
            font_row, text="Font:", bg=config.PANEL_BG, fg=config.TEXT_MAIN,
            font=config.FONT_SMALL,
        ).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Combobox(
            font_row, textvariable=self.font_name_var, state="readonly", width=11,
            values=[name for name, _code in config.FONT_CHOICES],
        ).pack(side=tk.LEFT)
        ttk.Combobox(
            font_row, textvariable=self.font_size_var, state="readonly", width=4,
            values=config.FONT_SIZE_OPTIONS,
        ).pack(side=tk.LEFT, padx=(6, 0))
        tk.Label(
            font_row, text="size (Add Paragraph)", bg=config.PANEL_BG, fg=config.TEXT_MUTED,
            font=config.FONT_SMALL,
        ).pack(side=tk.LEFT, padx=(4, 0))

        tk.Frame(parent, bg=config.DESELECTED_BORDER, width=1).pack(fill=tk.X, pady=6)
        tk.Button(
            parent, text="Undo", command=self._undo,
            bg=config.BUTTON_BG, fg=config.TEXT_MAIN, relief=tk.FLAT, bd=0,
            padx=8, pady=4, font=config.FONT_SMALL, cursor="hand2",
        ).pack(anchor="w")

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

        for w in self.nav_scroll.inner.winfo_children():
            w.destroy()
        self._nav_thumb_labels.clear()

        thumbs = PDFSplitter.generate_thumbnails(
            self.input_path, max_dim=config.EDIT_THUMB_MAX_DIM
        )
        self._nav_photos = []
        for i, png_bytes in enumerate(thumbs):
            photo = tk.PhotoImage(data=png_bytes)
            self._nav_photos.append(photo)
            frame = tk.Frame(self.nav_scroll.inner, bg=config.PANEL_BG)
            frame.pack(fill=tk.X, pady=4)
            lbl = tk.Label(frame, image=photo, bg=config.PANEL_BG, bd=2, relief=tk.FLAT)
            lbl.pack()
            num = tk.Label(
                frame, text=str(i + 1), bg=config.PANEL_BG, fg=config.TEXT_MUTED,
                font=config.FONT_SMALL,
            )
            num.pack()
            lbl.bind("<Button-1>", lambda e, idx=i: self._select_page(idx))
            self._nav_thumb_labels[i] = lbl

        self._fit_zoom_to_width()
        self._select_page(0)

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

        if x_frac is not None:
            self.canvas.xview_moveto(x_frac)
            self.canvas.yview_moveto(y_frac)

    def _page_words(self, page_index: int) -> list:
        if page_index not in self._page_words_cache:
            self._page_words_cache[page_index] = self.doc[page_index].get_text("words")
        return self._page_words_cache[page_index]

    # ==================================================================
    # mode / tool selection
    # ==================================================================

    def _on_mode_change(self):
        if self.mode_var.get() == ANNOTATE:
            self.toolbar_edit.pack_forget()
            self.toolbar_annotate.pack(fill=tk.X, before=self.main_row)
        else:
            self.toolbar_annotate.pack_forget()
            self.toolbar_edit.pack(fill=tk.X, before=self.main_row)
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

    def _select_shape(self, shape_name: str):
        self.current_shape_var.set(shape_name)
        self._select_tool(T_SHAPE)

    def _select_color(self, color_name: str):
        self.color_var.set(color_name)
        self._update_color_swatches()

    def _update_color_swatches(self):
        for name, sw in self._color_swatches.items():
            sw.config(highlightthickness=2 if name == self.color_var.get() else 0,
                       highlightbackground=config.TEXT_MAIN)

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
            color = _hex_for(self.color_var.get())
            width = config.EDIT_HIGHLIGHT_WIDTH if self.active_tool == T_HIGHLIGHT else self.width_var.get()
            flat = [c for p in self._current_path_points for c in p]
            iid = self.canvas.create_line(*flat, fill=color, width=width, smooth=True)
            self._drag_preview_ids = [iid]
        else:
            x0, y0 = self._drag_start_canvas
            color = _hex_for(self.color_var.get())
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

    def _try_start_interaction(self, cx, cy):
        for iid in self.canvas.find_overlapping(cx - 5, cy - 5, cx + 5, cy + 5):
            if "resize_handle" in self.canvas.gettags(iid) and iid in self._handle_to_edit:
                edit = self._handle_to_edit[iid]
                self._interaction = {"mode": "resize", "edit": edit, "start": (cx, cy), "orig_rect": edit["rect"]}
                return
        for iid in self.canvas.find_overlapping(cx - 2, cy - 2, cx + 2, cy + 2):
            tags = self.canvas.gettags(iid)
            if "movable" in tags and iid in self._overlay_item_to_edit:
                edit = self._overlay_item_to_edit[iid]
                if edit["type"] in self.RESIZABLE_TYPES:
                    self._interaction = {"mode": "move", "edit": edit, "start": (cx, cy), "orig_rect": edit["rect"]}
                    return

    def _update_interaction(self, cx, cy):
        inter = self._interaction
        edit = inter["edit"]
        x0, y0, x1, y1 = inter["orig_rect"]
        scale = self._scale()
        dx = (cx - inter["start"][0]) / scale
        dy = (cy - inter["start"][1]) / scale

        if inter["mode"] == "move":
            edit["rect"] = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
        else:  # resize — drag the bottom-right corner, keep top-left fixed
            edit["rect"] = (x0, y0, max(x0 + 20, x1 + dx), max(y0 + 14, y1 + dy))

        self._redraw_overlay()

    def _on_canvas_double_click(self, event):
        cx, cy = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        for iid in self.canvas.find_overlapping(cx - 2, cy - 2, cx + 2, cy + 2):
            if iid not in self._overlay_item_to_edit:
                continue
            edit = self._overlay_item_to_edit[iid]
            if edit["type"] in (T_FREETEXT, T_PARAGRAPH):
                title = "Edit Free Text" if edit["type"] == T_FREETEXT else "Edit Paragraph"

                def on_save(text, font_display, size, color_display, e=edit):
                    e["text"] = text
                    e["fontname"] = _font_code_for(font_display)
                    e["tk_font"] = font_display
                    e["fontsize"] = size
                    e["color"] = _rgb_for(color_display)
                    self._redraw_overlay()

                def on_delete(e=edit):
                    if e in self.pending_edits:
                        self.pending_edits.remove(e)
                    self._redraw_overlay()

                TextBoxDialog(
                    self, title, initial_text=edit["text"],
                    initial_font_name=edit.get("tk_font", config.DEFAULT_FONT_NAME),
                    initial_font_size=edit.get("fontsize", config.EDIT_DEFAULT_FONT_SIZE),
                    initial_color_name=_color_name_for_rgb(edit.get("color", (0, 0, 0))),
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
        color = _rgb_for(self.color_var.get())
        width = config.EDIT_HIGHLIGHT_WIDTH if self.active_tool == T_HIGHLIGHT else self.width_var.get()
        self._add_edit({
            "type": self.active_tool, "page": self.current_page_index,
            "points": points, "color": color, "width": width,
        })

    # -- text-selection tools (underline/strikeout/squiggly/replace) --

    def _finish_text_select_tool(self, x0, y0, x1, y1):
        rect_pdf = self._canvas_rect_to_pdf(x0, y0, x1, y1)
        quads = get_word_quads_in_rect(self.doc[self.current_page_index], rect_pdf)
        if not quads:
            return
        color = _rgb_for(self.color_var.get())
        if self.active_tool == T_REPLACE_TEXT:
            new_text = simpledialog.askstring("Replace Text", "Replacement text:", parent=self)
            if not new_text:
                return
            self._add_edit({
                "type": T_REPLACE_TEXT, "page": self.current_page_index,
                "quads": quads, "new_text": new_text, "color": color,
                "fontsize": config.EDIT_DEFAULT_FONT_SIZE,
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

    # -- shapes --

    def _finish_shape_tool(self, x0, y0, x1, y1):
        if abs(x1 - x0) < 3 and abs(y1 - y0) < 3:
            return
        shape = self.current_shape_var.get()
        color = _rgb_for(self.color_var.get())
        width = self.width_var.get()
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

        def on_save(text, font_display, size, color_display):
            self._add_edit({
                "type": T_FREETEXT, "page": self.current_page_index,
                "rect": rect, "text": text, "color": _rgb_for(color_display),
                "fontsize": size, "fontname": _font_code_for(font_display),
                "tk_font": font_display,
            })

        TextBoxDialog(
            self, "Free Text",
            initial_font_name=self.font_name_var.get(),
            initial_font_size=self.font_size_var.get(),
            initial_color_name=self.color_var.get(),
            allow_delete=False, on_save=on_save,
        )

    def _finish_paragraph_tool(self, x0, y0, x1, y1):
        x0, y0, x1, y1 = self._normalize_box(x0, y0, x1, y1)
        rect = self._canvas_rect_to_pdf(x0, y0, x1, y1)

        def on_save(text, font_display, size, color_display):
            self._add_edit({
                "type": T_PARAGRAPH, "page": self.current_page_index,
                "rect": rect, "text": text, "color": _rgb_for(color_display),
                "fontsize": size, "fontname": _font_code_for(font_display),
                "tk_font": font_display,
            })

        TextBoxDialog(
            self, "Add Paragraph",
            initial_font_name=self.font_name_var.get(),
            initial_font_size=self.font_size_var.get(),
            initial_color_name=self.color_var.get(),
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
        self._redraw_overlay()
        if edit["type"] in self.SINGLE_SHOT_TYPES:
            self._select_tool(None)

    def _undo(self):
        if not self.pending_edits:
            return
        self.pending_edits.pop()
        self._redraw_overlay()

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

    def _redraw_overlay(self):
        self.canvas.delete("overlay")
        self._overlay_item_to_edit = {}
        self._handle_to_edit = {}
        for edit in self.pending_edits:
            if edit["page"] != self.current_page_index:
                continue
            for iid in self._draw_edit_preview(edit):
                self._overlay_item_to_edit[iid] = edit

    RESIZABLE_TYPES = (T_FREETEXT, T_PARAGRAPH)

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
                    x0, y0, x1, y0, fill=color_hex, width=2, tags="overlay",
                )
                ids.append(iid)

        elif t in (T_FREEHAND, T_HIGHLIGHT):
            flat = [c for p in edit["points"] for c in self._pdf_point_to_canvas(*p)]
            width = edit["width"]
            iid = self.canvas.create_line(
                *flat, fill=color_hex, width=width, smooth=True, tags="overlay",
            )
            ids.append(iid)

        elif t == T_FREETEXT:
            r = self._pdf_rect_to_canvas(edit["rect"])
            ids.append(self.canvas.create_rectangle(
                *r, outline=color_hex, dash=(3, 2), fill="white", stipple="gray25",
                tags=("overlay", "movable"),
            ))
            preview_size = max(6, round(edit.get("fontsize", 12) * scale))
            ids.append(self.canvas.create_text(
                r[0] + 3, r[1] + 3, text=edit["text"], anchor="nw", fill=color_hex,
                font=(edit.get("tk_font", "Helvetica"), preview_size),
                tags=("overlay", "movable"),
            ))
            ids += self._draw_resize_handle(r, edit)

        elif t == T_INSERT_TEXT:
            x, y = self._pdf_point_to_canvas(*edit["point"])
            ids.append(self.canvas.create_rectangle(
                x, y, x + 18, y + 18, fill="#f7d716", outline="#8a7000", tags="overlay",
            ))
            # a couple of short lines to read as a "note" rather than a blank square
            ids.append(self.canvas.create_line(x + 4, y + 6, x + 14, y + 6, fill="#8a7000", tags="overlay"))
            ids.append(self.canvas.create_line(x + 4, y + 10, x + 14, y + 10, fill="#8a7000", tags="overlay"))
            ids.append(self.canvas.create_line(x + 4, y + 14, x + 10, y + 14, fill="#8a7000", tags="overlay"))

        elif t == T_REPLACE_TEXT:
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
            if shape == "Rectangle":
                r = self._pdf_rect_to_canvas(edit["rect"])
                ids.append(self.canvas.create_rectangle(*r, outline=color_hex, width=width, tags="overlay"))
            elif shape == "Circle":
                r = self._pdf_rect_to_canvas(edit["rect"])
                ids.append(self.canvas.create_oval(*r, outline=color_hex, width=width, tags="overlay"))
            elif shape in ("Line", "Arrow"):
                p1, p2 = edit["points"]
                x0, y0 = self._pdf_point_to_canvas(*p1)
                x1, y1 = self._pdf_point_to_canvas(*p2)
                arrow = tk.LAST if shape == "Arrow" else None
                ids.append(self.canvas.create_line(
                    x0, y0, x1, y1, fill=color_hex, width=width, arrow=arrow, tags="overlay",
                ))

        elif t == T_STAMP:
            r = self._pdf_rect_to_canvas(edit["rect"])
            ids.append(self.canvas.create_rectangle(*r, outline="#e5533c", width=2, tags="overlay"))
            ids.append(self.canvas.create_text(
                (r[0] + r[2]) / 2, (r[1] + r[3]) / 2, text=edit["name"],
                fill="#e5533c", tags="overlay",
            ))

        elif t == T_SIGNATURE:
            r = self._pdf_rect_to_canvas(edit["rect"])
            ids.append(self.canvas.create_rectangle(*r, outline="#3b82f6", dash=(2, 2), tags="overlay"))
            ids.append(self.canvas.create_text(
                (r[0] + r[2]) / 2, (r[1] + r[3]) / 2, text="Signature", fill="#3b82f6", tags="overlay",
            ))

        elif t == T_FILE_ATTACHMENT:
            x, y = self._pdf_point_to_canvas(*edit["point"])
            ids.append(self.canvas.create_text(
                x, y, text="[file]", anchor="nw", fill="#9a9ba5", tags="overlay",
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
            ids += self._draw_resize_handle(r, edit)

        elif t == T_IMAGE:
            r = self._pdf_rect_to_canvas(edit["rect"])
            ids.append(self.canvas.create_rectangle(*r, outline="#2ecc71", dash=(2, 2), tags="overlay"))
            ids.append(self.canvas.create_text(
                (r[0] + r[2]) / 2, (r[1] + r[3]) / 2, text="Image", fill="#2ecc71", tags="overlay",
            ))

        return ids

    def _draw_resize_handle(self, canvas_rect, edit) -> list[int]:
        """A small square at the bottom-right corner of a movable box —
        drag it to resize, drag the box itself to move, double-click to
        edit the text."""
        x1, y1 = canvas_rect[2], canvas_rect[3]
        size = 6
        iid = self.canvas.create_rectangle(
            x1 - size, y1 - size, x1 + size, y1 + size,
            fill=config.ACCENT, outline="white", tags=("overlay", "resize_handle"),
        )
        self._handle_to_edit[iid] = edit
        return [iid]

    # ==================================================================
    # save
    # ==================================================================

    def _save_changes(self):
        if not self.input_path:
            messagebox.showwarning("Edit PDF", "Choose a PDF first.")
            return
        if not self.pending_edits:
            messagebox.showwarning("Edit PDF", "No changes to save yet.")
            return

        out_path = filedialog.asksaveasfilename(
            defaultextension=".pdf", initialfile="edited.pdf",
            filetypes=[("PDF files", "*.pdf")],
        )
        if not out_path:
            return

        edits = list(self.pending_edits)
        self.progress.set_running(True)

        def work():
            return apply_edits_and_save(
                self.input_path, out_path, edits,
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
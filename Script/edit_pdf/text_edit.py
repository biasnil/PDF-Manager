"""
Script/edit_pdf/text_edit.py

The engine behind the new "Edit" mode: every real text block on the page
gets a persistent dashed outline (get_text_blocks), and double-clicking
inside one finds the specific line under the cursor (get_text_lines) and
turns it into a live, in-place tk.Entry pre-filled and pre-selected with
that line's text — type over it, press Enter (or click away), done.

Deliberately produces a plain "replace_text" pending edit on commit — the
exact same edit type Annotate's manual Replace Text tool already creates
— so pdf_edit_operations.py needed no changes at all: redacting the
original words and reinserting new text at that spot was already solved.
This module is just a smarter, auto-targeting front end for it.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional


def get_text_lines(page) -> list[dict]:
    """One entry per real line of text on the page: its bounding rect (pdf
    points), text, and enough style info (fontsize, color) to reinsert a
    lightly-matched replacement. Blank/whitespace-only lines are skipped.
    Order follows PyMuPDF's own block/line order (roughly reading order).
    """
    lines = []
    raw = page.get_text("dict")
    for block in raw.get("blocks", []):
        if block.get("type") != 0:  # 0 = text block, 1 = image block
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(s.get("text", "") for s in spans).strip()
            bbox = line.get("bbox")
            if not text or not bbox:
                continue
            first = spans[0]
            fontsize = first.get("size", 12)
            color_int = first.get("color", 0)
            color = (
                ((color_int >> 16) & 255) / 255,
                ((color_int >> 8) & 255) / 255,
                (color_int & 255) / 255,
            )
            lines.append({"rect": tuple(bbox), "text": text,
                          "fontsize": fontsize, "color": color})
    return lines


def get_text_blocks(page) -> list[tuple]:
    """Bounding rect of every text block (paragraph/text-frame) on the
    page — coarser than get_text_lines, one rect per block. This is what
    draws the persistent "here's everything you can edit" outline; which
    LINE actually gets edited on a double-click is resolved separately,
    against get_text_lines."""
    raw = page.get_text("dict")
    rects = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        bbox = block.get("bbox")
        if bbox and any(line.get("spans") for line in block.get("lines", [])):
            rects.append(tuple(bbox))
    return rects


class TextEditOverlay:
    """Owns the Edit-mode canvas state: the block outlines, the cached
    line list they're hit-tested against, and the single live edit-in-
    -progress Entry widget (if any). Talks to PdfEditTool only through the
    callables passed in — it doesn't know about pending_edits, tool
    selection, or anything else outside "what text is on this page and
    what did the person just type over it"."""

    ENTRY_MIN_FONT = 8
    OUTLINE_COLOR = "#3b82f6"
    WIDTH_HANDLE_SIZE = 6   # px, canvas-space
    MIN_ENTRY_WIDTH = 40    # px, canvas-space — floor so a handle can't invert the box

    def __init__(self, canvas: tk.Canvas, get_page: Callable[[], Optional[object]],
                 canvas_to_pdf: Callable[[float, float], tuple[float, float]],
                 pdf_rect_to_canvas: Callable[[tuple], tuple[float, float, float, float]],
                 scale: Callable[[], float],
                 on_start_edit: Callable[[dict], tuple],
                 on_commit: Callable[[tuple, str, dict], None],
                 is_style_control: Callable[[object], bool] = lambda widget: False):
        self.canvas = canvas
        self.get_page = get_page
        self.canvas_to_pdf = canvas_to_pdf
        self.pdf_rect_to_canvas = pdf_rect_to_canvas
        self.scale = scale
        # on_start_edit(line) -> (font_display, fontsize, bold, italic, rgb):
        # called right before an entry is built, so the tool can both hand
        # back sensible starting values AND update its own style-panel
        # widgets (font/size dropdowns, bold/italic buttons, color swatch)
        # to match — see PdfEditTool._on_text_edit_start.
        self.on_start_edit = on_start_edit
        # on_commit(rect, new_text, style) — style is the dict below.
        self.on_commit = on_commit
        # is_style_control(widget) -> bool: identifies the tool's own
        # Font/Size/Bold/Italic/Color panel widgets, so losing focus TO
        # one of them (clicking Size, tabbing to Bold, etc.) is treated
        # as "still restyling this line", not "the person clicked away
        # and is done" — see _maybe_finish_after_focus_change.
        self.is_style_control = is_style_control

        self._lines: list[dict] = []
        self._outline_ids: list[int] = []
        self._entry: Optional[tk.Entry] = None
        self._entry_window_id: Optional[int] = None
        self._editing_line: Optional[dict] = None
        self._active_style: Optional[dict] = None
        self._original_style: Optional[dict] = None
        self._start_text: Optional[str] = None
        self._edit_session = 0
        self._entry_geom: Optional[dict] = None
        self._width_handle_ids: list[int] = []
        self._width_drag_side: Optional[str] = None

    def refresh(self):
        """Recompute and redraw the block outlines for the current page.
        Call this whenever the page is (re)rendered while Edit mode is
        active — the canvas gets fully cleared on every render, so the
        outlines need to be redrawn every time too."""
        self._finish_editing(commit=True)
        self._clear_outlines()

        page = self.get_page()
        if page is None:
            self._lines = []
            return
        self._lines = get_text_lines(page)
        for rect in get_text_blocks(page):
            r = self.pdf_rect_to_canvas(rect)
            iid = self.canvas.create_rectangle(
                *r, outline=self.OUTLINE_COLOR, dash=(2, 2), tags="text_edit_outline",
            )
            self._outline_ids.append(iid)

    def clear(self):
        """Switching out of Edit mode — commit any in-progress edit and
        drop the outlines entirely."""
        self._finish_editing(commit=True)
        self._clear_outlines()
        self._lines = []

    def _clear_outlines(self):
        for iid in self._outline_ids:
            self.canvas.delete(iid)
        self._outline_ids = []

    def handle_double_click(self, cx: float, cy: float) -> bool:
        """Returns True if the click landed on a line and editing started."""
        px, py = self.canvas_to_pdf(cx, cy)
        for line in self._lines:
            x0, y0, x1, y1 = line["rect"]
            if x0 <= px <= x1 and y0 <= py <= y1:
                self._start_editing(line)
                return True
        return False

    def _start_editing(self, line: dict):
        self._finish_editing(commit=True)  # in case another line was mid-edit
        self._edit_session += 1
        session = self._edit_session

        font_display, fontsize, bold, italic, rgb, start_text = self.on_start_edit(line)
        self._active_style = {
            "font_display": font_display, "fontsize": fontsize,
            "bold": bold, "italic": italic, "color": rgb,
        }
        self._original_style = dict(self._active_style)
        self._start_text = start_text

        r = self.pdf_rect_to_canvas(line["rect"])
        w, h = max(24, r[2] - r[0]), max(16, r[3] - r[1])
        self._entry_geom = {"x0": r[0], "y0": r[1], "x1": r[0] + w, "y1": r[1] + h}

        entry = tk.Entry(
            self.canvas, bg="white",
            relief=tk.FLAT, bd=0, highlightthickness=1,
            highlightbackground=self.OUTLINE_COLOR, highlightcolor=self.OUTLINE_COLOR,
            selectbackground=self.OUTLINE_COLOR, selectforeground="white",
        )
        entry.insert(0, start_text)
        entry.select_range(0, tk.END)
        entry.icursor(tk.END)
        entry.bind("<Return>", lambda e: self._finish_editing(commit=True))
        entry.bind("<Escape>", lambda e: self._finish_editing(commit=False))
        entry.bind("<FocusOut>", lambda e: self._on_entry_focus_out(session))

        self._entry_window_id = self.canvas.create_window(
            r[0], r[1], anchor="nw", window=entry, width=w, height=h,
        )
        self._entry = entry
        self._editing_line = line
        self._apply_style_to_entry()
        entry.focus_set()
        self._create_width_handles()

    def _create_width_handles(self):
        """Small drag grips on the left and right edges of the entry box
        — widens (or narrows) it so a replacement that runs longer than
        the original line stays visible instead of auto-scrolling out of
        view while typing. This only resizes the live editing box; the
        saved replacement text isn't wrapped or width-constrained (see
        pdf_edit_operations.py's replace_text — it's a single insert_text
        call at a point, same as before), so it doesn't change anything
        about the output."""
        self._clear_width_handles()
        g = self._entry_geom
        hw = self.WIDTH_HANDLE_SIZE
        left = self.canvas.create_rectangle(
            g["x0"] - hw, g["y0"], g["x0"], g["y1"],
            fill=self.OUTLINE_COLOR, outline="", tags="text_edit_width_handle",
        )
        right = self.canvas.create_rectangle(
            g["x1"], g["y0"], g["x1"] + hw, g["y1"],
            fill=self.OUTLINE_COLOR, outline="", tags="text_edit_width_handle",
        )
        # "break" on every binding stops the event from also reaching the
        # canvas-widget-level bindings (which would call canvas.focus_set()
        # and steal focus from the entry mid-drag, ending the edit).
        for iid, side in ((left, "left"), (right, "right")):
            self.canvas.tag_bind(iid, "<ButtonPress-1>", lambda e, s=side: self._start_width_drag(s))
            self.canvas.tag_bind(iid, "<B1-Motion>", self._on_width_drag)
            self.canvas.tag_bind(iid, "<ButtonRelease-1>", self._end_width_drag)
        self._width_handle_ids = [left, right]

    def _clear_width_handles(self):
        for iid in self._width_handle_ids:
            self.canvas.delete(iid)
        self._width_handle_ids = []

    def _start_width_drag(self, side: str):
        self._width_drag_side = side
        return "break"

    def _on_width_drag(self, event):
        if self._entry is None or self._entry_geom is None or self._width_drag_side is None:
            return "break"
        cx = self.canvas.canvasx(event.x)
        g = self._entry_geom
        if self._width_drag_side == "left":
            g["x0"] = min(cx, g["x1"] - self.MIN_ENTRY_WIDTH)
        else:
            g["x1"] = max(cx, g["x0"] + self.MIN_ENTRY_WIDTH)
        self._apply_entry_geometry()
        return "break"

    def _apply_entry_geometry(self):
        """Push self._entry_geom out to the actual canvas items: the
        entry's window (position + size) and, if they've been created
        yet, the two width-drag handles. Guarded for "not created yet"
        because this runs once during _start_editing (via
        _apply_style_to_entry) BEFORE _create_width_handles() has run."""
        if self._entry is None or self._entry_geom is None:
            return
        g = self._entry_geom
        w, h = g["x1"] - g["x0"], g["y1"] - g["y0"]
        self.canvas.coords(self._entry_window_id, g["x0"], g["y0"])
        self.canvas.itemconfigure(self._entry_window_id, width=w, height=h)
        if self._width_handle_ids:
            hw = self.WIDTH_HANDLE_SIZE
            left_id, right_id = self._width_handle_ids
            self.canvas.coords(left_id, g["x0"] - hw, g["y0"], g["x0"], g["y1"])
            self.canvas.coords(right_id, g["x1"], g["y0"], g["x1"] + hw, g["y1"])

    def _end_width_drag(self, event):
        self._width_drag_side = None
        return "break"

    def _on_entry_focus_out(self, session: int):
        """The Entry just lost keyboard focus. Right when FocusOut fires,
        the widget about to receive focus hasn't necessarily taken it yet
        on every platform — checking on the next idle tick instead lets
        us tell "focus moved to a restyle control" (keep the edit open)
        apart from "focus moved somewhere else entirely" (the person
        clicked away — finish it), instead of treating every focus loss
        as the person being done. `session` pins this check to the exact
        edit it was scheduled for: if the person manages to click
        straight onto ANOTHER line before this callback runs, a new
        edit session will already be under way by the time it fires —
        without this guard it would silently act on that new session
        (closing the line the person JUST opened) instead of being a
        no-op, which looks exactly like the edit "reverting"."""
        if self._entry is None or session != self._edit_session:
            return
        self.canvas.after_idle(lambda: self._maybe_finish_after_focus_change(session))

    def _maybe_finish_after_focus_change(self, session: int):
        if self._entry is None or session != self._edit_session:
            return  # a different edit has started since this was scheduled
        focused = self._entry.focus_get()
        if focused is not None and self.is_style_control(focused):
            return  # focus moved to Font/Size/Bold/Italic/Color — keep editing
        self._finish_editing(commit=True)

    def update_active_style(self, font_display: str, fontsize: float,
                             bold: bool, italic: bool, rgb: tuple):
        """Called by the tool's style panel (Font/Size/Bold/Italic/Color
        controls) while an edit is in progress — updates the live entry's
        look immediately and is what gets used at commit time. A no-op if
        nothing is currently being edited."""
        if self._entry is None:
            return
        self._active_style = {
            "font_display": font_display, "fontsize": fontsize,
            "bold": bold, "italic": italic, "color": rgb,
        }
        self._apply_style_to_entry()

    def _apply_style_to_entry(self):
        style = self._active_style
        fontsize = max(self.ENTRY_MIN_FONT, round(style["fontsize"] * self.scale()))
        weight = "bold" if style["bold"] else "normal"
        slant = "italic" if style["italic"] else "roman"
        color_hex = "#%02x%02x%02x" % tuple(round(c * 255) for c in style["color"])
        self._entry.config(font=(style["font_display"], fontsize, weight, slant), fg=color_hex)
        self._resize_entry_to_fit_font()

    def _resize_entry_to_fit_font(self):
        """The entry's canvas window got a fixed height back when editing
        started, taken from the ORIGINAL line's (small) height — Tk
        doesn't grow that automatically as the font size changes, so
        bumping the size up just clips the bigger glyphs inside the old
        small box instead of actually showing them larger. Grow the box
        downward from its top edge to fit whatever font is active now."""
        if self._entry is None or self._entry_geom is None:
            return
        self._entry.update_idletasks()
        needed_h = self._entry.winfo_reqheight()
        g = self._entry_geom
        g["y1"] = g["y0"] + max(needed_h, 16)
        self._apply_entry_geometry()

    def _finish_editing(self, commit: bool):
        entry, line, style = self._entry, self._editing_line, self._active_style
        original_style = self._original_style
        start_text = self._start_text
        if entry is None:
            return
        # Clear these first: destroying the entry can synchronously fire
        # another <FocusOut>, and this is what makes that reentrant call
        # a no-op instead of double-committing.
        self._entry = None
        self._editing_line = None
        self._active_style = None
        self._original_style = None
        self._start_text = None

        try:
            new_text = entry.get()
        except tk.TclError:
            new_text = None

        if self._entry_window_id is not None:
            self.canvas.delete(self._entry_window_id)
            self._entry_window_id = None
        entry.destroy()
        self._clear_width_handles()
        self._entry_geom = None

        text_changed = new_text is not None and new_text != start_text
        style_changed = style != original_style
        if commit and new_text is not None and new_text.strip() and (text_changed or style_changed):
            self.on_commit(line["rect"], new_text, style)
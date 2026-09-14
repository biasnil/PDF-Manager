"""
Script/edit_pdf/dialogs.py

Popup windows used by the Edit PDF tool. Both are self-contained — they
know nothing about PdfEditTool internals, just hand results back through
an on_save/on_done callback.
"""

import io
import tkinter as tk
from tkinter import colorchooser, messagebox, ttk
from typing import Optional

from Config import config

from .constants import KEPT_SWATCH_NAMES


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
                 initial_color_name=None, initial_custom_rgb=None,
                 allow_delete=False, on_save=None, on_delete=None):
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
        size_vcmd = (self.register(lambda s: s == "" or s.isdigit()), "%P")
        ttk.Combobox(
            style_row, textvariable=self.size_var, width=4, values=config.FONT_SIZE_OPTIONS,
            validate="key", validatecommand=size_vcmd,
        ).pack(side=tk.LEFT, padx=(4, 0))

        color_row = tk.Frame(self, bg=config.PANEL_BG)
        color_row.pack(fill=tk.X, padx=12, pady=(10, 0))
        tk.Label(
            color_row, text="Font color:", bg=config.PANEL_BG, fg=config.TEXT_MAIN,
            font=config.FONT_SMALL,
        ).pack(side=tk.LEFT, padx=(0, 6))
        self.color_var = tk.StringVar(value=initial_color_name or config.DEFAULT_ANNOT_COLOR_NAME)
        self.custom_rgb = initial_custom_rgb or (0.6, 0.6, 0.6)
        self._swatches: dict[str, tk.Label] = {}
        for name, hexval, _rgb in config.ANNOT_COLOR_PALETTE:
            if name not in KEPT_SWATCH_NAMES:
                continue
            sw = tk.Label(color_row, bg=hexval, width=2, height=1, cursor="hand2")
            sw.pack(side=tk.LEFT, padx=1)
            sw.bind("<Button-1>", lambda e, n=name: self._pick_color(n))
            self._swatches[name] = sw
        self._custom_swatch = tk.Label(
            color_row, bg=self._custom_hex(), width=2, height=1, cursor="hand2",
        )
        self._custom_swatch.pack(side=tk.LEFT, padx=(4, 0))
        self._custom_swatch.bind("<Button-1>", lambda e: self._pick_custom_color())
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
        self._refresh_swatches()

    def _current_rgb(self) -> tuple:
        if self.color_var.get() == "Custom":
            return self.custom_rgb
        for name, _hexval, rgb in config.ANNOT_COLOR_PALETTE:
            if name == self.color_var.get():
                return rgb
        return (0, 0, 0)

    def _refresh_swatches(self):
        current = self.color_var.get()
        for name, sw in self._swatches.items():
            sw.config(highlightthickness=2 if name == current else 0,
                      highlightbackground=config.TEXT_MAIN)
        self._custom_swatch.config(
            highlightthickness=2 if current == "Custom" else 0,
            highlightbackground=config.TEXT_MAIN,
        )

    def _confirm_save(self):
        text = self.text_widget.get("1.0", tk.END).strip()
        if not text:
            self.destroy()
            return
        try:
            size = self.size_var.get()
        except tk.TclError:
            size = config.EDIT_DEFAULT_FONT_SIZE
        if self.on_save:
            self.on_save(text, self.font_var.get(), size, self._current_rgb())
        self.destroy()

    def _confirm_delete(self):
        if self.on_delete:
            self.on_delete()
        self.destroy()
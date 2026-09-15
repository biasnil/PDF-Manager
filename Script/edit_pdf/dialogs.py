"""
Script/edit_pdf/dialogs.py

Popup windows used by the Edit PDF tool. Both are self-contained — they
know nothing about PdfEditTool internals, just hand results back through
an on_save/on_done callback.
"""

import io
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk
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

        # An uploaded signature image, if the user picks one instead of
        # drawing — takes priority over strokes on confirm. _uploaded_photo
        # is the Tk-side preview and must be kept referenced or Tk garbage
        # collects it out from under the canvas.
        self._uploaded_image = None  # PIL.Image, full-res RGBA
        self._uploaded_photo = None
        self._canvas_image_id: Optional[int] = None

        self.canvas = tk.Canvas(self, width=420, height=160, bg="white",
                                 cursor="pencil", highlightthickness=1,
                                 highlightbackground="#888")
        self.canvas.pack(padx=12, pady=12)
        self.canvas.bind("<Button-1>", self._start)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._end)

        btn_row = tk.Frame(self, bg=config.PANEL_BG)
        btn_row.pack(pady=(0, 12))
        tk.Button(btn_row, text="Upload Image...", command=self._upload_image,
                  bg=config.BUTTON_BG, fg=config.TEXT_MAIN, relief=tk.FLAT,
                  padx=10, pady=5).pack(side=tk.LEFT, padx=4)
        tk.Button(btn_row, text="Clear", command=self._clear,
                  bg=config.BUTTON_BG, fg=config.TEXT_MAIN, relief=tk.FLAT,
                  padx=10, pady=5).pack(side=tk.LEFT, padx=4)
        tk.Button(btn_row, text="Cancel", command=self.destroy,
                  bg=config.BUTTON_BG, fg=config.TEXT_MAIN, relief=tk.FLAT,
                  padx=10, pady=5).pack(side=tk.LEFT, padx=4)
        tk.Button(btn_row, text="Use this signature", command=self._confirm,
                  bg=config.ACCENT, fg="white", relief=tk.FLAT,
                  padx=10, pady=5).pack(side=tk.LEFT, padx=4)

        # Force this to the front, centered over the main window — without
        # this it can end up opening behind the main window or off in a
        # corner (especially on multi-monitor setups), so it's easy to miss
        # entirely and look like nothing happened when "Draw Signature..."
        # was clicked.
        self.transient(master.winfo_toplevel())
        self.update_idletasks()
        self._center_on(master)
        self.lift()
        self.attributes("-topmost", True)
        self.focus_force()
        self.grab_set()

    def _center_on(self, master):
        mx, my = master.winfo_rootx(), master.winfo_rooty()
        mw = master.winfo_width() or master.winfo_reqwidth()
        mh = master.winfo_height() or master.winfo_reqheight()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = max(0, mx + (mw - w) // 2)
        y = max(0, my + (mh - h) // 2)
        self.geometry(f"+{x}+{y}")

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
        if self._canvas_image_id is not None:
            self.canvas.delete(self._canvas_image_id)
            self._canvas_image_id = None
        self._uploaded_image = None
        self._uploaded_photo = None

    def _upload_image(self):
        """Lets the user pick an existing signature image (e.g. a photo of
        their signature on paper, or an already-transparent PNG) instead of
        drawing one. Near-white background is made transparent and the
        image is cropped to the ink, same as the drawn path's output, so it
        composites onto the PDF the same way either way."""
        path = filedialog.askopenfilename(
            title="Choose a signature image",
            filetypes=[("Image files", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"), ("All files", "*.*")],
        )
        if not path:
            return

        try:
            from PIL import Image

            img = Image.open(path).convert("RGBA")
            # Cap resolution before any pixel processing — a phone photo
            # can easily be 10+ megapixels, which the (already fast,
            # vectorized) thresholding below would still needlessly chew
            # through, and it's far more than a signature needs anyway.
            if max(img.size) > 1000:
                img.thumbnail((1000, 1000))

            img = self._trim_white_background(img)

            # An upload replaces any strokes drawn so far — confirm uses
            # whichever of the two was set most recently.
            self.canvas.delete("stroke")
            self.strokes = []
            self._current = None
            self._uploaded_image = img

            cw = self.canvas.winfo_width() or 420
            ch = self.canvas.winfo_height() or 160
            preview = img.copy()
            preview.thumbnail((max(1, cw - 16), max(1, ch - 16)))

            # PIL -> PNG bytes -> tk.PhotoImage, same as _render_highlight_image
            # / _render_image_thumbnail in tool.py — NOT PIL.ImageTk, which
            # can silently fail to work in a PyInstaller-frozen build.
            buf = io.BytesIO()
            preview.save(buf, format="PNG")
            self._uploaded_photo = tk.PhotoImage(data=buf.getvalue())

            if self._canvas_image_id is not None:
                self.canvas.delete(self._canvas_image_id)
            self._canvas_image_id = self.canvas.create_image(cw // 2, ch // 2, image=self._uploaded_photo)
        except Exception as exc:
            messagebox.showerror("Signature", f"Couldn't use that image:\n{exc}")

    @staticmethod
    def _trim_white_background(img, threshold: int = 245):
        """Makes near-white pixels transparent and crops to the remaining
        ink's bounding box, with a little padding — mirrors what the
        hand-drawn path already produces (transparent background, tight
        crop) so uploaded and drawn signatures place onto the PDF the
        same way. Done with vectorized PIL band ops (not a per-pixel
        Python loop), so it stays fast even on a full-size photo."""
        from PIL import ImageChops

        r, g, b, a = img.split()
        white = ImageChops.multiply(ImageChops.multiply(
            r.point(lambda v: 255 if v >= threshold else 0),
            g.point(lambda v: 255 if v >= threshold else 0),
        ), b.point(lambda v: 255 if v >= threshold else 0))
        img.putalpha(ImageChops.subtract(a, white))

        bbox = img.getbbox()
        if bbox:
            pad = 6
            l, t, r2, b2 = bbox
            l, t = max(0, l - pad), max(0, t - pad)
            r2, b2 = min(img.width, r2 + pad), min(img.height, b2 + pad)
            img = img.crop((l, t, r2, b2))
        return img

    def _confirm(self):
        if self._uploaded_image is not None:
            buf = io.BytesIO()
            self._uploaded_image.save(buf, format="PNG")
            w, h = self._uploaded_image.size
            aspect = w / h if h else 1.0
            self.on_done(buf.getvalue(), aspect)
            self.destroy()
            return

        if not self.strokes:
            messagebox.showwarning("Signature", "Draw a signature, or upload an image, first.")
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
                 initial_align="left", allow_justify=False,
                 allow_delete=False, on_save=None, on_delete=None):
        super().__init__(master)
        self.title(title)
        self.configure(bg=config.PANEL_BG)
        self.on_save = on_save
        self.on_delete = on_delete
        self.align_var = tk.StringVar(value=initial_align or "left")

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
        size_box = ttk.Combobox(
            style_row, textvariable=self.size_var, width=4, values=config.FONT_SIZE_OPTIONS,
            validate="key", validatecommand=size_vcmd,
        )
        size_box.pack(side=tk.LEFT, padx=(4, 0))

        align_row = tk.Frame(self, bg=config.PANEL_BG)
        align_row.pack(fill=tk.X, padx=12, pady=(10, 0))
        tk.Label(
            align_row, text="Align:", bg=config.PANEL_BG, fg=config.TEXT_MAIN,
            font=config.FONT_SMALL,
        ).pack(side=tk.LEFT, padx=(0, 6))
        self._align_buttons: dict[str, tk.Button] = {}
        align_choices = [("left", "Left"), ("center", "Center"), ("right", "Right")]
        if allow_justify:
            align_choices.append(("justify", "Justify"))
        for value, label in align_choices:
            btn = tk.Button(
                align_row, text=label, command=lambda v=value: self._pick_align(v),
                relief=tk.FLAT, bd=0, padx=8, pady=3, font=config.FONT_SMALL, cursor="hand2",
            )
            btn.pack(side=tk.LEFT, padx=(0, 4))
            self._align_buttons[value] = btn
        self._refresh_align_buttons()

        bullets_row = tk.Frame(self, bg=config.PANEL_BG)
        bullets_row.pack(fill=tk.X, padx=12, pady=(10, 0))
        tk.Button(
            bullets_row, text="\u2022 Bullets", command=self._toggle_bullets,
            bg=config.BUTTON_BG, fg=config.TEXT_MAIN, relief=tk.FLAT, bd=0,
            padx=8, pady=3, font=config.FONT_SMALL, cursor="hand2",
        ).pack(side=tk.LEFT)

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

    def _pick_align(self, value):
        self.align_var.set(value)
        self._refresh_align_buttons()

    def _refresh_align_buttons(self):
        current = self.align_var.get()
        for value, btn in self._align_buttons.items():
            btn.config(bg=config.ACCENT if value == current else config.BUTTON_BG,
                       fg="white" if value == current else config.TEXT_MAIN)

    def _toggle_bullets(self):
        """Adds a '• ' prefix to every non-blank line, or removes it if
        every non-blank line already has one — plain text either way, so
        it needs no special handling anywhere else (preview or save)."""
        raw = self.text_widget.get("1.0", tk.END)
        lines = raw.split("\n")
        # trailing entry from the Text widget's own final newline
        had_trailing_newline = raw.endswith("\n")
        if had_trailing_newline:
            lines = lines[:-1]

        non_blank = [ln for ln in lines if ln.strip()]
        all_bulleted = bool(non_blank) and all(ln.lstrip().startswith("\u2022") for ln in non_blank)

        new_lines = []
        for ln in lines:
            if not ln.strip():
                new_lines.append(ln)
            elif all_bulleted:
                stripped = ln.lstrip()
                new_lines.append(stripped[1:].lstrip() if stripped.startswith("\u2022") else ln)
            else:
                new_lines.append(f"\u2022 {ln}")

        self.text_widget.delete("1.0", tk.END)
        self.text_widget.insert("1.0", "\n".join(new_lines))

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
            self.on_save(text, self.font_var.get(), size, self._current_rgb(), self.align_var.get())
        self.destroy()

    def _confirm_delete(self):
        if self.on_delete:
            self.on_delete()
        self.destroy()
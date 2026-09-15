"""
Script/widgets.py

Small reusable tkinter widgets shared by more than one tool tab:
AccentButton, FileListPanel, ScrollableFrame, PageThumb, ProgressRow.
All styling comes from Config/config.py — nothing here hardcodes a color.
"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from Config import config

from Script import print_utils


class AccentButton(tk.Button):
    """A flat, colored button matching the app's visual style."""

    def __init__(self, master, text, command=None, enabled=True, **kw):
        state = tk.NORMAL if enabled else tk.DISABLED
        bg = config.ACCENT if enabled else config.DESELECTED_BORDER
        fg = "white" if enabled else config.TEXT_MUTED
        super().__init__(
            master, text=text, command=command, state=state,
            bg=bg, fg=fg, activebackground=config.ACCENT_HOVER, activeforeground="white",
            relief=tk.FLAT, bd=0, padx=18, pady=8,
            font=config.FONT_BODY_BOLD, cursor="hand2" if enabled else "arrow",
            **kw,
        )


class FileListPanel(tk.Frame):
    """A listbox of chosen files with add/remove/reorder controls. Also
    accepts files dragged in from the OS file explorer, dropped anywhere
    on the panel."""

    def __init__(self, master, on_change=None, multiple=True, filetypes=None,
                 on_mismatched_drop=None):
        super().__init__(master, bg=config.PANEL_BG)
        self.on_change = on_change
        self.multiple = multiple
        self.filetypes = filetypes or [("PDF files", "*.pdf")]
        self.on_mismatched_drop = on_mismatched_drop
        self.paths: list[str] = []

        self.listbox = tk.Listbox(
            self, bg=config.INPUT_BG, fg=config.TEXT_MAIN, selectbackground=config.ACCENT,
            font=config.FONT_BODY, height=8, activestyle="none",
            highlightthickness=0, bd=0,
        )
        self.listbox.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        if multiple:
            hint = tk.Label(
                self, text="Drag an item to reorder it.", bg=config.PANEL_BG,
                fg=config.TEXT_MUTED, font=config.FONT_SMALL, anchor="w",
            )
            hint.pack(fill=tk.X, pady=(2, 0))
            self._drag_index = None
            self.listbox.bind("<Button-1>", self._drag_start)
            self.listbox.bind("<B1-Motion>", self._drag_move)
            self.listbox.bind("<ButtonRelease-1>", self._drag_end)

        btn_row = tk.Frame(self, bg=config.PANEL_BG)
        btn_row.pack(fill=tk.X, pady=(6, 0))

        tk.Button(
            btn_row, text="+ Add file(s)", command=self.add_files,
            bg=config.BUTTON_BG, fg=config.TEXT_MAIN, relief=tk.FLAT, bd=0,
            padx=10, pady=5, font=config.FONT_SMALL, cursor="hand2",
        ).pack(side=tk.LEFT)

        tk.Button(
            btn_row, text="Remove selected", command=self.remove_selected,
            bg=config.BUTTON_BG, fg=config.TEXT_MAIN, relief=tk.FLAT, bd=0,
            padx=10, pady=5, font=config.FONT_SMALL, cursor="hand2",
        ).pack(side=tk.LEFT, padx=(6, 0))

        from Script.dnd_support import register_drop_target
        # No extension filter registered here — self.filetypes can change
        # later (e.g. a mode toggle), so the filtering has to happen at
        # drop-time in _on_drop, not be baked in once at construction.
        drop_enabled = register_drop_target(self, self._on_drop)
        if drop_enabled:
            register_drop_target(self.listbox, self._on_drop)
            tk.Label(
                btn_row, text="or drag files here", bg=config.PANEL_BG,
                fg=config.TEXT_MUTED, font=config.FONT_SMALL,
            ).pack(side=tk.LEFT, padx=(8, 0))

    def _on_drop(self, paths):
        """Split dropped files into ones matching the current filetypes
        and ones that don't — the matches get added normally; anything
        else goes to on_mismatched_drop if the tool provided one (used to
        warn about a mode mismatch), otherwise it's just ignored."""
        from Script.dnd_support import extensions_from_filetypes
        allowed = extensions_from_filetypes(self.filetypes)
        matched = [p for p in paths if os.path.splitext(p)[1].lower() in allowed]
        mismatched = [p for p in paths if p not in matched]

        if matched:
            self._add_paths(matched)
        if mismatched and self.on_mismatched_drop:
            self.on_mismatched_drop(mismatched)

    # -- drag to reorder (multi-file mode only) -----------------------

    def _drag_start(self, event):
        self._drag_index = self.listbox.nearest(event.y)

    def _drag_move(self, event):
        if self._drag_index is None:
            return
        new_index = self.listbox.nearest(event.y)
        if new_index == self._drag_index or new_index < 0:
            return
        # swap in both the backing list and the visible listbox
        self.paths[self._drag_index], self.paths[new_index] = (
            self.paths[new_index], self.paths[self._drag_index]
        )
        text_a, text_b = self.listbox.get(self._drag_index), self.listbox.get(new_index)
        self.listbox.delete(self._drag_index)
        self.listbox.insert(self._drag_index, text_b)
        self.listbox.delete(new_index)
        self.listbox.insert(new_index, text_a)
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(new_index)
        self._drag_index = new_index

    def _drag_end(self, event):
        if self._drag_index is not None:
            self._drag_index = None
            self._changed()

    def add_files(self):
        filetypes = self.filetypes
        if self.multiple:
            new_paths = filedialog.askopenfilenames(filetypes=filetypes)
        else:
            single = filedialog.askopenfilename(filetypes=filetypes)
            new_paths = [single] if single else []
        self._add_paths(new_paths)

    def _add_paths(self, new_paths):
        """Add files to the list — shared by the file dialog and by files
        dragged in from the OS file explorer."""
        for p in new_paths:
            if p and p not in self.paths:
                if not self.multiple:
                    self.paths.clear()
                    self.listbox.delete(0, tk.END)
                self.paths.append(p)
                self.listbox.insert(tk.END, os.path.basename(p))
        self._changed()

    def remove_selected(self):
        for i in reversed(self.listbox.curselection()):
            self.listbox.delete(i)
            del self.paths[i]
        self._changed()

    def _changed(self):
        if self.on_change:
            self.on_change(self.paths)


class ScrollableFrame(tk.Frame):
    """A vertically-scrollable area. Put widgets in `.inner` (grid or pack
    onto it as normal) and this handles the canvas/scrollbar/mousewheel
    plumbing.

    center_content=True keeps `.inner` at its natural (content) width and
    centers it horizontally in the visible area instead of stretching it
    to fill the full width — used by the Split grid so the thumbnails stay
    centered instead of hugging the left edge on a wide/maximized window.
    """

    def __init__(self, master, bg=None, height=config.THUMB_GRID_HEIGHT,
                 center_content=False):
        bg = bg or config.PANEL_BG
        super().__init__(master, bg=bg)
        self.center_content = center_content
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, height=height)
        self.scrollbar = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)

        self.inner.bind("<Configure>", self._on_inner_configure)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._on_wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _on_inner_configure(self, event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        if self.center_content:
            self._recenter(self.canvas.winfo_width())

    def _on_resize(self, event):
        if self.center_content:
            self._recenter(event.width)
        else:
            self.canvas.itemconfig(self._window, width=event.width)

    def _recenter(self, canvas_width):
        content_width = self.inner.winfo_reqwidth()
        x = max(0, (canvas_width - content_width) // 2)
        self.canvas.coords(self._window, x, 0)

    def _on_wheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


class PageThumb(tk.Frame):
    """One page preview in the Split grid: a thumbnail with a colored
    border and a checkmark badge when selected (click anywhere to toggle),
    and the page number underneath — the iLovePDF-style page picker."""

    def __init__(self, master, page_num, photo, selected=True, on_toggle=None):
        super().__init__(master, bg=config.PANEL_BG)
        self.page_num = page_num
        self.photo = photo  # keep a reference so it isn't garbage-collected
        self.selected = selected
        self.on_toggle = on_toggle

        self.border = tk.Frame(self, bg=config.ACCENT if selected else config.DESELECTED_BORDER)
        self.border.pack(padx=2, pady=2)
        self.img_label = tk.Label(self.border, image=photo, bg="white", bd=0, cursor="hand2")
        self.img_label.pack(padx=2, pady=2)

        self.check_label = tk.Label(
            self.img_label, text="\u2713", bg=config.ACCENT, fg="white",
            font=(config.FONT_FAMILY, 8, "bold"), width=2, height=1,
        )
        self.num_label = tk.Label(
            self, text=str(page_num), bg=config.PANEL_BG,
            fg=config.TEXT_MAIN if selected else config.TEXT_MUTED, font=config.FONT_SMALL,
        )
        self.num_label.pack(pady=(3, 0))

        for widget in (self, self.border, self.img_label, self.num_label):
            widget.bind("<Button-1>", self._toggle)
        self._refresh()

    def _toggle(self, event=None):
        self.set_selected(not self.selected)
        if self.on_toggle:
            self.on_toggle(self.page_num, self.selected)

    def set_selected(self, value: bool):
        self.selected = value
        self._refresh()

    def _refresh(self):
        self.border.config(bg=config.ACCENT if self.selected else config.DESELECTED_BORDER)
        self.num_label.config(fg=config.TEXT_MAIN if self.selected else config.TEXT_MUTED)
        if self.selected:
            self.check_label.place(x=2, y=2)
        else:
            self.check_label.place_forget()


class PagePreview(tk.Canvas):
    """A small live preview of how the current page-layout options (size,
    orientation, margin) will place an image on the page — so the person
    can see the effect before converting, like iLovePDF's own preview."""

    PAGE_COLOR = "white"
    MARGIN_LINE_COLOR = config.ACCENT
    IMAGE_COLOR = "#b7c6e6"

    def __init__(self, master, width=config.IMAGE_PDF_PREVIEW_WIDTH,
                 height=config.IMAGE_PDF_PREVIEW_HEIGHT):
        super().__init__(
            master, width=width, height=height, bg=config.PANEL_BG,
            highlightthickness=0, bd=0,
        )
        self._box_w = width
        self._box_h = height

    def update_preview(self, page_width_pt, page_height_pt, image_rect):
        """image_rect is (x0, y0, x1, y1) in the same point units as the
        page dimensions, as returned by compute_page_layout()."""
        self.delete("all")
        pad = 10
        avail_w = self._box_w - 2 * pad
        avail_h = self._box_h - 2 * pad
        scale = min(avail_w / page_width_pt, avail_h / page_height_pt)

        page_w = page_width_pt * scale
        page_h = page_height_pt * scale
        px0 = (self._box_w - page_w) / 2
        py0 = (self._box_h - page_h) / 2

        # drop shadow + page
        self.create_rectangle(
            px0 + 3, py0 + 3, px0 + page_w + 3, py0 + page_h + 3,
            fill="#101116", outline="",
        )
        self.create_rectangle(
            px0, py0, px0 + page_w, py0 + page_h,
            fill=self.PAGE_COLOR, outline="#555", width=1,
        )

        # image placeholder, positioned the same way it will really sit
        ix0, iy0, ix1, iy1 = image_rect
        self.create_rectangle(
            px0 + ix0 * scale, py0 + iy0 * scale,
            px0 + ix1 * scale, py0 + iy1 * scale,
            fill=self.IMAGE_COLOR, outline="",
        )


class PrinterPickerDialog(tk.Toplevel):
    """Small popup listing the system's installed printers so the person
    can pick one and print `filepath` to it — opened by ProgressRow's
    Print button, present on every tool tab (see print_utils.py)."""

    def __init__(self, master, filepath: str):
        super().__init__(master)
        self.title("Print")
        self.configure(bg=config.PANEL_BG)
        self.resizable(False, False)
        self.filepath = filepath

        tk.Label(
            self, text=f"Print:\n{os.path.basename(filepath)}",
            bg=config.PANEL_BG, fg=config.TEXT_MAIN, font=config.FONT_SMALL,
            justify=tk.LEFT, anchor="w", wraplength=280,
        ).pack(fill=tk.X, padx=14, pady=(14, 8))

        printers = print_utils.list_printers()
        default = print_utils.get_default_printer()
        initial = default if default in printers else (printers[0] if printers else "")
        self.printer_var = tk.StringVar(value=initial)

        if not print_utils.is_supported():
            message = "Printing isn't available on this platform yet."
        elif not printers:
            message = "No printers found — add one in Windows Settings first."
        else:
            message = None

        if message:
            tk.Label(
                self, text=message, bg=config.PANEL_BG, fg=config.TEXT_MUTED,
                font=config.FONT_SMALL, wraplength=280, justify=tk.LEFT,
            ).pack(fill=tk.X, padx=14, pady=(0, 10))
        else:
            ttk.Combobox(
                self, textvariable=self.printer_var, state="readonly",
                values=printers, width=32,
            ).pack(fill=tk.X, padx=14, pady=(0, 10))

        btn_row = tk.Frame(self, bg=config.PANEL_BG)
        btn_row.pack(fill=tk.X, padx=14, pady=(0, 14))
        tk.Button(
            btn_row, text="Cancel", command=self.destroy,
            bg=config.BUTTON_BG, fg=config.TEXT_MAIN, relief=tk.FLAT, bd=0,
            padx=10, pady=5, cursor="hand2",
        ).pack(side=tk.RIGHT)
        tk.Button(
            btn_row, text="Print", command=self._do_print,
            bg=config.ACCENT, fg="white", relief=tk.FLAT, bd=0,
            padx=10, pady=5, cursor="hand2",
            state=tk.NORMAL if printers else tk.DISABLED,
        ).pack(side=tk.RIGHT, padx=(0, 6))

        # Same front-and-center treatment as the Edit PDF signature pad —
        # a Print dialog opening behind the main window is easy to miss.
        self.transient(master.winfo_toplevel())
        self.update_idletasks()
        self.lift()
        self.attributes("-topmost", True)
        self.focus_force()
        self.grab_set()

    def _do_print(self):
        printer = self.printer_var.get()
        if not printer:
            return
        try:
            print_utils.print_file(self.filepath, printer)
        except Exception as exc:
            messagebox.showerror("Print", f"Couldn't print:\n{exc}")
            return
        self.destroy()


class ProgressRow(tk.Frame):
    """Progress bar + status label + Run button + Print button, shared
    across tool tabs. Print stays clickable at all times — rather than
    just going disabled with no explanation, it shows a clear warning if
    there's nothing to print yet or a run is still in progress. Call
    set_output_path() with the run's resulting file once it succeeds;
    pass None (or just don't call it) when a run produces zero or
    multiple files, since Print only ever targets a single file."""

    def __init__(self, master, run_label, on_run):
        super().__init__(master, bg=config.PANEL_BG)
        self.status_var = tk.StringVar(value="")
        self.bar = ttk.Progressbar(self, mode="determinate")
        self.bar.pack(fill=tk.X, pady=(10, 4))

        bottom = tk.Frame(self, bg=config.PANEL_BG)
        bottom.pack(fill=tk.X)
        tk.Label(
            bottom, textvariable=self.status_var, bg=config.PANEL_BG, fg=config.TEXT_MUTED,
            font=config.FONT_SMALL, anchor="w",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.run_btn = AccentButton(bottom, run_label, command=on_run)
        self.run_btn.pack(side=tk.RIGHT)

        self._output_path = None
        self._running = False
        self.print_btn = tk.Button(
            bottom, text="Print...", command=self._open_print_dialog,
            bg=config.BUTTON_BG, fg=config.TEXT_MUTED, relief=tk.FLAT, bd=0,
            padx=12, pady=8, font=config.FONT_BODY, cursor="hand2",
        )
        self.print_btn.pack(side=tk.RIGHT, padx=(0, 8))

    def set_progress(self, current, total, message):
        self.bar["maximum"] = max(total, 1)
        self.bar["value"] = current
        self.status_var.set(message)
        self.update_idletasks()

    def reset(self):
        self.bar["value"] = 0
        self.status_var.set("")

    def set_running(self, running: bool):
        self.run_btn.config(state=tk.DISABLED if running else tk.NORMAL)
        self._running = running
        if running:
            # A new run is about to (over)write things — whatever Print
            # was pointed at is stale until this run's done() calls
            # set_output_path() again.
            self._output_path = None
            self.print_btn.config(fg=config.TEXT_MUTED)

    def set_run_label(self, text: str):
        self.run_btn.config(text=text)

    def set_output_path(self, path):
        self._output_path = path
        self.print_btn.config(fg=config.TEXT_MAIN if path else config.TEXT_MUTED)

    def _open_print_dialog(self):
        if self._running:
            messagebox.showwarning("Print", "Still working — wait for this to finish, then try Print again.")
            return
        if not self._output_path:
            messagebox.showwarning("Print", "Save your changes first — there's nothing to print yet.")
            return
        PrinterPickerDialog(self, self._output_path)
"""
Script/organize_pdf_tool.py — the "Organize PDF" tab.

Choose a PDF, see every page as a thumbnail, drag a thumbnail to move it
to a new position, click its × to drop that page, or hover the slim gap
between two thumbnails for a moment to reveal a "+" that inserts a blank
page right there — then save the result in the new order.
"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox

from Config import config
from Script.base_tool import BaseTool
from Script.pdf_operations import PDFOrganizer, PDFSplitter
from Script.threading_utils import run_in_background, threadsafe_progress
from Script.widgets import ProgressRow, ScrollableFrame


class OrganizeTile(tk.Frame):
    """One page thumbnail in the Organize grid: shows the page image, its
    current position number, a × to remove it, and is itself draggable to
    reorder — click and drag it over another tile to swap positions."""

    def __init__(self, master, original_index, photo, on_drag_over, on_drop, on_remove,
                 is_blank=False):
        super().__init__(master, bg=config.PANEL_BG)
        self.original_index = original_index
        self.photo = photo  # keep a reference so it isn't garbage-collected
        self.is_blank = is_blank
        self.on_drag_over = on_drag_over
        self.on_drop = on_drop
        self.on_remove = on_remove

        self.border = tk.Frame(self, bg=config.DESELECTED_BORDER)
        self.border.pack(padx=2, pady=2)
        self.img_label = tk.Label(self.border, image=photo, bg="white", bd=0, cursor="hand2")
        self.img_label.pack(padx=2, pady=2)

        if is_blank:
            # A page inserted via the "+" gap, not a real page from the
            # source PDF yet — just a plain white sheet until it's saved.
            tk.Label(
                self.img_label, text="Blank\npage", bg="white", justify="center",
                fg=config.TEXT_MUTED, font=config.FONT_SMALL,
            ).place(relx=0.5, rely=0.5, anchor="center")

        self.remove_btn = tk.Label(
            self.img_label, text="\u00d7", bg=config.ACCENT, fg="white",
            font=(config.FONT_FAMILY, 9, "bold"), width=2, cursor="hand2",
        )
        self.remove_btn.place(x=2, y=2)
        self.remove_btn.bind("<Button-1>", self._on_remove_click)

        self.num_label = tk.Label(
            self, text="", bg=config.PANEL_BG, fg=config.TEXT_MAIN, font=config.FONT_SMALL,
        )
        self.num_label.pack(pady=(3, 0))

        for widget in (self, self.border, self.img_label, self.num_label):
            widget.bind("<Button-1>", self._on_press)
            widget.bind("<B1-Motion>", self._on_motion)
            widget.bind("<ButtonRelease-1>", self._on_release)

    def _on_remove_click(self, event):
        self.on_remove(self.original_index)
        return "break"  # don't let the drag handlers on the parent also fire

    def set_position_label(self, position: int):
        self.num_label.config(text=str(position))

    def _on_press(self, event):
        self.border.config(bg=config.ACCENT)

    def _on_motion(self, event):
        abs_x = self.winfo_rootx() + event.x
        abs_y = self.winfo_rooty() + event.y
        self.on_drag_over(self.original_index, abs_x, abs_y)

    def _on_release(self, event):
        self.border.config(bg=config.DESELECTED_BORDER)
        self.on_drop()


class InsertGap(tk.Frame):
    """The slim strip that sits before a page tile (one also trails the
    very last tile). Normally it's just blank space the same color as the
    background — hover over it for a moment and a "+" fades in; click it
    to insert a blank page right there.

    `position` is where the new page lands in page_order (0 = before the
    very first page, len(page_order) = after the very last one).
    """

    HOVER_DELAY_MS = 500
    WIDTH = 18

    def __init__(self, master, position, on_insert, is_dragging=lambda: False):
        super().__init__(master, bg=config.PANEL_BG, width=self.WIDTH)
        self.position = position
        self.on_insert = on_insert
        self.is_dragging = is_dragging
        self._pending_after_id = None

        self.plus_label = tk.Label(
            self, text="+", bg=config.PANEL_BG, fg=config.ACCENT,
            font=(config.FONT_FAMILY, 14, "bold"), cursor="hand2",
        )
        # not shown until the hover delay elapses — see _show_plus

        for widget in (self, self.plus_label):
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)
        self.plus_label.bind("<Button-1>", self._on_click)

    def _on_enter(self, _event):
        if self.is_dragging() or self._pending_after_id is not None:
            return  # don't tempt-tease a "+" mid-drag, or double-schedule it
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


class OrganizePdfTool(BaseTool):
    title = "Organize PDF"
    description = (
        "Choose a PDF, then drag any page thumbnail to reorder it, click "
        "its × to remove that page, or hover the gap between two pages "
        "for a moment to insert a blank page there. Save when you're "
        "happy with the new order."
    )

    def __init__(self, master, root):
        super().__init__(master, root)

        top_row = tk.Frame(self.body, bg=config.PANEL_BG)
        top_row.pack(fill=tk.X)
        self.file_label = tk.Label(
            top_row, text="No file selected", bg=config.PANEL_BG, fg=config.TEXT_MUTED,
            font=config.FONT_BODY, anchor="w",
        )
        self.file_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        from Script.widgets import AccentButton
        AccentButton(top_row, "Choose PDF", command=self._choose_file).pack(side=tk.RIGHT)

        from Script.dnd_support import register_drop_target
        if register_drop_target(self, self._on_files_dropped, extensions={".pdf"}):
            self.file_label.config(text="No file selected — or drag a PDF in")

        self.status_label = tk.Label(
            self.body, text="", bg=config.PANEL_BG, fg=config.TEXT_MUTED, font=config.FONT_SMALL,
        )

        self.grid_container: "ScrollableFrame | None" = None
        self.input_path: "str | None" = None
        self.tiles: dict[int, OrganizeTile] = {}
        self.page_order: list[int] = []  # original page indices, in the new order

        # Blank pages inserted via the "+" gap don't exist in the source
        # PDF, so they can't have a real (>= 0) original_index. They get a
        # unique negative id instead — reorder_and_save() treats any
        # negative entry in page_order as "insert one blank page here".
        self._next_blank_id = -1
        self._gap_widgets: list[InsertGap] = []
        self._dragging = False
        self.blank_size = (config.ORGANIZE_THUMB_MAX_DIM, int(config.ORGANIZE_THUMB_MAX_DIM * 1.414))

        self.progress = ProgressRow(self.body, "Save changes", self._save)
        self.progress.pack(fill=tk.X, pady=(12, 0))

    # -- loading -----------------------------------------------------

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
        self._load_thumbnails()

    def _load_thumbnails(self):
        if self.grid_container is not None:
            self.grid_container.destroy()
            self.grid_container = None
        self.tiles.clear()
        self.page_order = []
        self._next_blank_id = -1
        self._gap_widgets = []

        self.status_label.config(text="Loading previews…")
        self.status_label.pack(fill=tk.X, pady=(8, 0))
        self.progress.set_running(True)

        path = self.input_path

        def work():
            return PDFSplitter.generate_thumbnails(
                path, max_dim=config.ORGANIZE_THUMB_MAX_DIM,
                progress=threadsafe_progress(self.root, self.progress),
            )

        def done(thumbs):
            self.progress.set_running(False)
            self.progress.reset()
            self.status_label.pack_forget()
            self._build_grid(thumbs)
            self._reposition_progress()

        def error(e):
            self.progress.set_running(False)
            self.progress.reset()
            self.status_label.pack_forget()
            messagebox.showerror("Organize PDF", f"Couldn't read that PDF:\n{e}")

        run_in_background(self.root, work, done, error)

    def _reposition_progress(self):
        self.progress.pack_forget()
        self.progress.pack(fill=tk.X, pady=(12, 0))

    def _build_grid(self, thumbs: list[bytes]):
        self.grid_container = ScrollableFrame(
            self.body, height=config.ORGANIZE_GRID_HEIGHT, center_content=True
        )
        self.grid_container.pack(fill=tk.BOTH, expand=True)

        self.photos = []
        for i, png_bytes in enumerate(thumbs):
            photo = tk.PhotoImage(data=png_bytes)
            self.photos.append(photo)
            tile = OrganizeTile(
                self.grid_container.inner, i, photo,
                on_drag_over=self._on_drag_over, on_drop=self._on_drop,
                on_remove=self._on_remove,
            )
            self.tiles[i] = tile
            self.page_order.append(i)

        if self.photos:
            self.blank_size = (self.photos[0].width(), self.photos[0].height())

        self._relayout()

    # -- drag to reorder -----------------------------------------------

    def _relayout(self):
        cols = config.ORGANIZE_GRID_COLS
        n = len(self.page_order)

        # Grid columns alternate gap, tile, gap, tile, ..., gap: a tile at
        # row-column `col` sits at grid column 2*col + 1, leaving the even
        # columns free for the slim InsertGap strip that goes before it.
        # One extra gap trails the very last tile in the whole grid (for
        # inserting at the end), even though it lands in a row of its own
        # when the last row is exactly full — a minor cosmetic quirk.
        #
        # Rebuilt from scratch every relayout (positions shift on every
        # drag/insert/remove), so the previous batch has to go first —
        # otherwise they'd pile up as stale, still-clickable widgets.
        for gap in self._gap_widgets:
            gap.destroy()
        self._gap_widgets = []
        for position, original_index in enumerate(self.page_order):
            tile = self.tiles[original_index]
            row, col = divmod(position, cols)
            tile.set_position_label(position + 1)
            tile.grid(row=row, column=2 * col + 1, padx=4, pady=4, sticky="n")

            gap = InsertGap(self.grid_container.inner, position,
                             on_insert=self._insert_blank_at, is_dragging=lambda: self._dragging)
            gap.grid(row=row, column=2 * col, sticky="ns")
            self._gap_widgets.append(gap)

        row, col = divmod(n, cols)
        gap = InsertGap(self.grid_container.inner, n,
                         on_insert=self._insert_blank_at, is_dragging=lambda: self._dragging)
        gap.grid(row=row, column=2 * col, sticky="ns")
        self._gap_widgets.append(gap)

    def _on_drag_over(self, dragged_original_index, abs_x, abs_y):
        self._dragging = True

        # find which tile's screen area the pointer is currently over
        target_original_index = None
        for original_index, tile in self.tiles.items():
            if original_index == dragged_original_index:
                continue
            tx0, ty0 = tile.winfo_rootx(), tile.winfo_rooty()
            tx1, ty1 = tx0 + tile.winfo_width(), ty0 + tile.winfo_height()
            if tx0 <= abs_x <= tx1 and ty0 <= abs_y <= ty1:
                target_original_index = original_index
                break

        if target_original_index is None:
            return

        old_pos = self.page_order.index(dragged_original_index)
        new_pos = self.page_order.index(target_original_index)
        if old_pos == new_pos:
            return
        self.page_order.pop(old_pos)
        self.page_order.insert(new_pos, dragged_original_index)
        self._relayout()

    def _on_drop(self):
        self._dragging = False  # position is already committed live during the drag

    # -- insert a blank page -------------------------------------------

    def _insert_blank_at(self, position):
        """Called when the "+" on a gap is clicked. Adds a plain white
        tile at `position` — it isn't a real page yet, just a marker that
        reorder_and_save() will turn into an actual blank page on save."""
        blank_id = self._next_blank_id
        self._next_blank_id -= 1

        photo = self._make_blank_photo()
        self.photos.append(photo)
        tile = OrganizeTile(
            self.grid_container.inner, blank_id, photo,
            on_drag_over=self._on_drag_over, on_drop=self._on_drop,
            on_remove=self._on_remove, is_blank=True,
        )
        self.tiles[blank_id] = tile
        self.page_order.insert(position, blank_id)
        self._relayout()

    def _make_blank_photo(self):
        w, h = self.blank_size
        photo = tk.PhotoImage(width=w, height=h)
        photo.put("white", to=(0, 0, w, h))
        return photo

    def _on_remove(self, original_index):
        if len(self.page_order) <= 1:
            messagebox.showwarning("Organize PDF", "A PDF needs at least one page.")
            return
        if original_index in self.page_order:
            self.page_order.remove(original_index)
        tile = self.tiles.pop(original_index, None)
        if tile is not None:
            tile.destroy()
        self._relayout()

    # -- save -------------------------------------------------------------

    def _save(self):
        if not self.input_path or not self.page_order:
            messagebox.showwarning("Organize PDF", "Choose a PDF first.")
            return

        out_path = filedialog.asksaveasfilename(
            defaultextension=".pdf", initialfile="organized.pdf",
            filetypes=[("PDF files", "*.pdf")],
        )
        if not out_path:
            return

        order = list(self.page_order)
        self.progress.set_running(True)

        def work():
            return PDFOrganizer.reorder_and_save(
                self.input_path, out_path, order,
                progress=threadsafe_progress(self.root, self.progress),
            )

        def done(result):
            self.progress.set_running(False)
            self.progress.reset()
            messagebox.showinfo("Organize PDF", f"Saved to:\n{result}")

        def error(e):
            self.progress.set_running(False)
            self.progress.reset()
            messagebox.showerror("Organize PDF", f"Something went wrong:\n{e}")

        run_in_background(self.root, work, done, error)
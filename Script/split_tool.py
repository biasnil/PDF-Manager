"""
Script/split_tool.py — the "Split PDF" tab.

Choose a PDF, preview every page as a thumbnail, tick which ones you want,
and either extract each as its own file or merge the ticked pages into one.
"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Optional

from Config import config
from Script.base_tool import BaseTool
from Script.pdf_operations import PDFSplitter
from Script.threading_utils import run_in_background, threadsafe_progress
from Script.widgets import PageThumb, ProgressRow, ScrollableFrame


class SplitTool(BaseTool):
    title = "Split PDF"
    description = (
        "Choose a PDF, preview every page as a thumbnail, and tick the ones "
        "you want. Untick a page to leave it out."
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
        AccentButton(top_row, "Choose PDF", command=self.choose_file).pack(side=tk.RIGHT)

        from Script.dnd_support import register_drop_target
        if register_drop_target(self, self._on_files_dropped, extensions={".pdf"}):
            self.file_label.config(text="No file selected — or drag a PDF in")

        self.status_label = tk.Label(
            self.body, text="", bg=config.PANEL_BG, fg=config.TEXT_MUTED, font=config.FONT_SMALL,
        )

        self.controls_row = tk.Frame(self.body, bg=config.PANEL_BG)
        self.merge_var = tk.BooleanVar(value=False)

        self.grid_container: Optional[ScrollableFrame] = None
        self.thumb_widgets: dict[int, PageThumb] = {}
        self.photos: list[tk.PhotoImage] = []
        self.input_path: Optional[str] = None

        self.progress = ProgressRow(self.body, "Split PDF", self.run)
        self.progress.pack(fill=tk.X, pady=(12, 0))

    # -- loading -----------------------------------------------------

    def choose_file(self):
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
        self.controls_row.pack_forget()
        for w in self.controls_row.winfo_children():
            w.destroy()
        self.thumb_widgets.clear()
        self.photos.clear()

        self.status_label.config(text="Loading previews…")
        self.status_label.pack(fill=tk.X, pady=(8, 0))
        self.progress.set_running(True)

        path = self.input_path

        def work():
            return PDFSplitter.generate_thumbnails(
                path, max_dim=config.THUMB_MAX_DIM,
                progress=threadsafe_progress(self.root, self.progress),
            )

        def done(thumbs):
            self.progress.set_running(False)
            self.progress.reset()
            self.status_label.pack_forget()
            self._build_controls()
            self._build_grid(thumbs)
            self._reposition_progress()

        def error(e):
            self.progress.set_running(False)
            self.progress.reset()
            self.status_label.pack_forget()
            messagebox.showerror("Split PDF", f"Couldn't read that PDF:\n{e}")

        run_in_background(self.root, work, done, error)

    def _reposition_progress(self):
        # keep the progress/run row pinned below the grid no matter what
        # order things were (re)built in
        self.progress.pack_forget()
        self.progress.pack(fill=tk.X, pady=(12, 0))

    # -- building the UI once a file is loaded ------------------------

    def _build_controls(self):
        self.controls_row.pack(fill=tk.X, pady=(10, 4))
        tk.Button(
            self.controls_row, text="Select all", command=self.select_all,
            bg=config.BUTTON_BG, fg=config.TEXT_MAIN, relief=tk.FLAT, bd=0, padx=10, pady=5,
            font=config.FONT_SMALL, cursor="hand2",
        ).pack(side=tk.LEFT)
        tk.Button(
            self.controls_row, text="Deselect all", command=self.deselect_all,
            bg=config.BUTTON_BG, fg=config.TEXT_MAIN, relief=tk.FLAT, bd=0, padx=10, pady=5,
            font=config.FONT_SMALL, cursor="hand2",
        ).pack(side=tk.LEFT, padx=(6, 0))
        tk.Checkbutton(
            self.controls_row, text="Merge selected pages into one PDF",
            variable=self.merge_var, bg=config.PANEL_BG, fg=config.TEXT_MAIN,
            selectcolor=config.INPUT_BG, activebackground=config.PANEL_BG,
            activeforeground=config.TEXT_MAIN, font=config.FONT_SMALL,
        ).pack(side=tk.RIGHT)

    def _build_grid(self, thumbs: list[bytes]):
        self.grid_container = ScrollableFrame(
            self.body, height=config.THUMB_GRID_HEIGHT, center_content=True
        )
        self.grid_container.pack(fill=tk.BOTH, expand=True)

        for i, png_bytes in enumerate(thumbs):
            page_num = i + 1
            photo = tk.PhotoImage(data=png_bytes)
            self.photos.append(photo)
            row, col = divmod(i, config.THUMB_GRID_COLS)
            thumb = PageThumb(self.grid_container.inner, page_num, photo, selected=True)
            thumb.grid(row=row, column=col, padx=4, pady=4)
            self.thumb_widgets[page_num] = thumb

    # -- selection helpers ---------------------------------------------

    def select_all(self):
        for w in self.thumb_widgets.values():
            w.set_selected(True)

    def deselect_all(self):
        for w in self.thumb_widgets.values():
            w.set_selected(False)

    def _selected_pages(self) -> list[int]:
        return sorted(p for p, w in self.thumb_widgets.items() if w.selected)

    # -- run -------------------------------------------------------------

    def run(self):
        if not self.input_path or not self.thumb_widgets:
            messagebox.showwarning("Split PDF", "Choose a PDF first.")
            return
        pages = self._selected_pages()
        if not pages:
            messagebox.showwarning("Split PDF", "Tick at least one page.")
            return

        merge = self.merge_var.get()
        if merge:
            target = filedialog.asksaveasfilename(
                defaultextension=".pdf", initialfile="extracted.pdf",
                filetypes=[("PDF files", "*.pdf")],
            )
            mode = "single"
        else:
            target = filedialog.askdirectory(title="Choose a folder for the split files")
            mode = "separate"
        if not target:
            return

        self.progress.set_running(True)

        def work():
            return PDFSplitter.split_selected_pages(
                self.input_path, target, pages, mode=mode,
                progress=threadsafe_progress(self.root, self.progress),
            )

        def done(result):
            self.progress.set_running(False)
            self.progress.reset()
            if mode == "single":
                messagebox.showinfo("Split PDF", f"Saved {len(pages)} page(s) to:\n{result[0]}")
            else:
                messagebox.showinfo("Split PDF", f"Created {len(result)} file(s) in:\n{target}")

        def error(e):
            self.progress.set_running(False)
            self.progress.reset()
            messagebox.showerror("Split PDF", f"Something went wrong:\n{e}")

        run_in_background(self.root, work, done, error)
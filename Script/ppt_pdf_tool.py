"""
Script/ppt_pdf_tool.py — the "PDF <-> PowerPoint" tab.

One tab, one direction at a time: a toggle switches between converting a
PDF to .pptx (with an "editable text" tick box), or a .pptx back to PDF.
"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox

from Config import config
from Script.base_tool import BaseTool
from Script.office_operations import PdfToPptConverter, PptToPdfConverter
from Script.threading_utils import run_in_background, threadsafe_progress
from Script.widgets import FileListPanel, ProgressRow
from Warning.mode_mismatch import check_mode_mismatch
from Error.dependency_checks import check_libreoffice, check_output_path

PDF_TO_PPT = "pdf_to_ppt"
PPT_TO_PDF = "ppt_to_pdf"
PDF_EXTS = {".pdf"}
PPTX_EXTS = {".pptx"}


class PptPdfTool(BaseTool):
    title = "PDF <-> PowerPoint"
    description = (
        "Convert a PDF to PowerPoint, or a PowerPoint back to PDF. For PDF "
        "to PowerPoint, tick \"Editable text\" to get real, selectable text "
        "boxes instead of a flat image of each page — layout is only "
        "approximate in that mode, and vector graphics aren't reconstructed, "
        "but every word can be edited afterward like a normal PowerPoint."
    )

    def __init__(self, master, root):
        super().__init__(master, root)

        mode_row = tk.Frame(self.body, bg=config.PANEL_BG)
        mode_row.pack(fill=tk.X, pady=(0, 10))
        self.mode_var = tk.StringVar(value=PDF_TO_PPT)
        tk.Radiobutton(
            mode_row, text="PDF to PowerPoint", variable=self.mode_var, value=PDF_TO_PPT,
            bg=config.PANEL_BG, fg=config.TEXT_MAIN, selectcolor=config.INPUT_BG,
            activebackground=config.PANEL_BG, activeforeground=config.TEXT_MAIN,
            font=config.FONT_BODY, command=self._on_mode_change,
        ).pack(side=tk.LEFT)
        tk.Radiobutton(
            mode_row, text="PowerPoint to PDF", variable=self.mode_var, value=PPT_TO_PDF,
            bg=config.PANEL_BG, fg=config.TEXT_MAIN, selectcolor=config.INPUT_BG,
            activebackground=config.PANEL_BG, activeforeground=config.TEXT_MAIN,
            font=config.FONT_BODY, command=self._on_mode_change,
        ).pack(side=tk.LEFT, padx=(16, 0))

        self.file_panel = FileListPanel(
            self.body, multiple=False, filetypes=[("PDF files", "*.pdf")],
            on_mismatched_drop=self._on_mismatched_drop,
        )
        self.file_panel.listbox.config(height=4)
        self.file_panel.pack(fill=tk.X)

        self.editable_var = tk.BooleanVar(value=False)
        self.editable_check = tk.Checkbutton(
            self.body, text="Editable text (approximate layout, real PowerPoint text)",
            variable=self.editable_var, bg=config.PANEL_BG, fg=config.TEXT_MAIN,
            selectcolor=config.INPUT_BG, activebackground=config.PANEL_BG,
            activeforeground=config.TEXT_MAIN, font=config.FONT_BODY,
        )
        self.editable_check.pack(anchor="w", pady=(10, 0))

        self.progress = ProgressRow(self.body, "Convert to PowerPoint", self.run)
        self.progress.pack(fill=tk.X, pady=(12, 0))

    def _on_mode_change(self):
        self.file_panel.paths.clear()
        self.file_panel.listbox.delete(0, tk.END)

        if self.mode_var.get() == PDF_TO_PPT:
            self.file_panel.filetypes = [("PDF files", "*.pdf")]
            self.editable_check.pack(anchor="w", pady=(10, 0), before=self.progress)
            self.progress.set_run_label("Convert to PowerPoint")
        else:
            self.file_panel.filetypes = [("PowerPoint files", "*.pptx")]
            self.editable_check.pack_forget()
            self.progress.set_run_label("Convert to PDF")

    def _on_mismatched_drop(self, mismatched_paths):
        path = mismatched_paths[0]
        current_mode = self.mode_var.get()
        if current_mode == PDF_TO_PPT:
            mismatch = check_mode_mismatch(
                path, PDF_TO_PPT, PDF_EXTS, PPT_TO_PDF, PPTX_EXTS,
                "PDF to PowerPoint", "PowerPoint to PDF",
            )
        else:
            mismatch = check_mode_mismatch(
                path, PPT_TO_PDF, PPTX_EXTS, PDF_TO_PPT, PDF_EXTS,
                "PowerPoint to PDF", "PDF to PowerPoint",
            )
        if mismatch is None:
            messagebox.showwarning(
                "PDF <-> PowerPoint",
                f"\"{os.path.basename(path)}\" isn't a file this tool can use here.",
            )
            return
        if messagebox.askyesno("PDF <-> PowerPoint", mismatch.message):
            self.mode_var.set(mismatch.switch_to_mode)
            self._on_mode_change()
            self.file_panel._add_paths([path])

    def run(self):
        if not self.file_panel.paths:
            messagebox.showwarning("PDF <-> PowerPoint", "Add a file to convert.")
            return
        input_path = self.file_panel.paths[0]
        mode = self.mode_var.get()

        if mode == PPT_TO_PDF:
            problem = check_libreoffice()
            if problem:
                messagebox.showerror("PDF <-> PowerPoint", problem)
                return

        if mode == PDF_TO_PPT:
            default_name = os.path.splitext(os.path.basename(input_path))[0] + ".pptx"
            out_path = filedialog.asksaveasfilename(
                defaultextension=".pptx", initialfile=default_name,
                filetypes=[("PowerPoint files", "*.pptx")],
            )
        else:
            default_name = os.path.splitext(os.path.basename(input_path))[0] + ".pdf"
            out_path = filedialog.asksaveasfilename(
                defaultextension=".pdf", initialfile=default_name,
                filetypes=[("PDF files", "*.pdf")],
            )
        if not out_path:
            return

        path_problem = check_output_path(out_path)
        if path_problem:
            messagebox.showerror("PDF <-> PowerPoint", path_problem)
            return

        editable = self.editable_var.get()
        self.progress.set_running(True)

        def work():
            if mode == PDF_TO_PPT:
                return PdfToPptConverter.convert(
                    input_path, out_path, editable=editable,
                    progress=threadsafe_progress(self.root, self.progress),
                )
            return PptToPdfConverter.convert(
                input_path, out_path, progress=threadsafe_progress(self.root, self.progress)
            )

        def done(result):
            self.progress.set_running(False)
            self.progress.reset()
            kind = "PowerPoint presentation" if mode == PDF_TO_PPT else "PDF"
            messagebox.showinfo("PDF <-> PowerPoint", f"Saved {kind} to:\n{result}")

        def error(e):
            self.progress.set_running(False)
            self.progress.reset()
            messagebox.showerror("PDF <-> PowerPoint", f"Something went wrong:\n{e}")

        run_in_background(self.root, work, done, error)
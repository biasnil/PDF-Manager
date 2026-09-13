import os
import tkinter as tk
from tkinter import filedialog, messagebox

from Config import config
from Script.base_tool import BaseTool
from Script.office_operations import PdfToWordConverter, WordToPdfConverter
from Script.threading_utils import run_in_background, threadsafe_progress
from Script.widgets import FileListPanel, ProgressRow
from Warning.mode_mismatch import check_mode_mismatch
from Error.dependency_checks import check_output_path

WORD_TO_PDF = "word_to_pdf"
PDF_TO_WORD = "pdf_to_word"
WORD_EXTS = {".docx"}
PDF_EXTS = {".pdf"}


class WordPdfTool(BaseTool):
    title = "Word <-> PDF"
    description = (
        "Convert a Word document to PDF, or a PDF back to an editable Word "
        "document. PDF to Word reconstructs real text and images (not just "
        "a picture of the page), but very complex layouts may not carry "
        "over perfectly."
    )

    def __init__(self, master, root):
        super().__init__(master, root)

        mode_row = tk.Frame(self.body, bg=config.PANEL_BG)
        mode_row.pack(fill=tk.X, pady=(0, 10))
        self.mode_var = tk.StringVar(value=WORD_TO_PDF)
        tk.Radiobutton(
            mode_row, text="Word to PDF", variable=self.mode_var, value=WORD_TO_PDF,
            bg=config.PANEL_BG, fg=config.TEXT_MAIN, selectcolor=config.INPUT_BG,
            activebackground=config.PANEL_BG, activeforeground=config.TEXT_MAIN,
            font=config.FONT_BODY, command=self._on_mode_change,
        ).pack(side=tk.LEFT)
        tk.Radiobutton(
            mode_row, text="PDF to Word", variable=self.mode_var, value=PDF_TO_WORD,
            bg=config.PANEL_BG, fg=config.TEXT_MAIN, selectcolor=config.INPUT_BG,
            activebackground=config.PANEL_BG, activeforeground=config.TEXT_MAIN,
            font=config.FONT_BODY, command=self._on_mode_change,
        ).pack(side=tk.LEFT, padx=(16, 0))

        self.file_panel = FileListPanel(
            self.body, multiple=False, filetypes=[("Word documents", "*.docx")],
            on_mismatched_drop=self._on_mismatched_drop,
        )
        self.file_panel.listbox.config(height=4)
        self.file_panel.pack(fill=tk.X)

        self.progress = ProgressRow(self.body, "Convert to PDF", self.run)
        self.progress.pack(fill=tk.X, pady=(12, 0))

    def _on_mode_change(self):
        # switching direction changes which file type we're picking, and
        # any file already chosen was for the other direction — clear it
        self.file_panel.paths.clear()
        self.file_panel.listbox.delete(0, tk.END)

        if self.mode_var.get() == WORD_TO_PDF:
            self.file_panel.filetypes = [("Word documents", "*.docx")]
            self.progress.set_run_label("Convert to PDF")
        else:
            self.file_panel.filetypes = [("PDF files", "*.pdf")]
            self.progress.set_run_label("Convert to Word")

    def _on_mismatched_drop(self, mismatched_paths):
        """A file that doesn't match the current mode was dropped — if it
        matches the OTHER mode instead, offer to switch to it."""
        path = mismatched_paths[0]
        current_mode = self.mode_var.get()
        if current_mode == WORD_TO_PDF:
            mismatch = check_mode_mismatch(
                path, WORD_TO_PDF, WORD_EXTS, PDF_TO_WORD, PDF_EXTS,
                "Word to PDF", "PDF to Word",
            )
        else:
            mismatch = check_mode_mismatch(
                path, PDF_TO_WORD, PDF_EXTS, WORD_TO_PDF, WORD_EXTS,
                "PDF to Word", "Word to PDF",
            )
        if mismatch is None:
            messagebox.showwarning(
                "Word <-> PDF", f"\"{os.path.basename(path)}\" isn't a file this tool can use here."
            )
            return
        if messagebox.askyesno("Word <-> PDF", mismatch.message):
            self.mode_var.set(mismatch.switch_to_mode)
            self._on_mode_change()
            self.file_panel._add_paths([path])

    def run(self):
        if not self.file_panel.paths:
            messagebox.showwarning("Word <-> PDF", "Add a file to convert.")
            return
        input_path = self.file_panel.paths[0]
        mode = self.mode_var.get()

        if mode == WORD_TO_PDF:
            default_name = os.path.splitext(os.path.basename(input_path))[0] + ".pdf"
            out_path = filedialog.asksaveasfilename(
                defaultextension=".pdf", initialfile=default_name,
                filetypes=[("PDF files", "*.pdf")],
            )
        else:
            default_name = os.path.splitext(os.path.basename(input_path))[0] + ".docx"
            out_path = filedialog.asksaveasfilename(
                defaultextension=".docx", initialfile=default_name,
                filetypes=[("Word documents", "*.docx")],
            )
        if not out_path:
            return

        path_problem = check_output_path(out_path)
        if path_problem:
            messagebox.showerror("Word <-> PDF", path_problem)
            return

        self.progress.set_running(True)

        def work():
            converter = WordToPdfConverter if mode == WORD_TO_PDF else PdfToWordConverter
            return converter.convert(
                input_path, out_path, progress=threadsafe_progress(self.root, self.progress)
            )

        def done(result):
            self.progress.set_running(False)
            self.progress.reset()
            kind = "PDF" if mode == WORD_TO_PDF else "Word document"
            messagebox.showinfo("Word <-> PDF", f"Saved {kind} to:\n{result}")

        def error(e):
            self.progress.set_running(False)
            self.progress.reset()
            messagebox.showerror("Word <-> PDF", f"Something went wrong:\n{e}")

        run_in_background(self.root, work, done, error)
import tkinter as tk
from tkinter import filedialog, messagebox

from Script.base_tool import BaseTool
from Script.pdf_operations import PDFMerger
from Script.threading_utils import run_in_background, threadsafe_progress
from Script.widgets import FileListPanel, ProgressRow


class MergeTool(BaseTool):
    title = "Merge PDF"
    description = "Combine multiple PDF files into one, in the order shown below."

    def __init__(self, master, root):
        super().__init__(master, root)
        self.file_panel = FileListPanel(self.body, multiple=True)
        self.file_panel.pack(fill=tk.BOTH, expand=True)
        self.progress = ProgressRow(self.body, "Merge PDFs", self.run)
        self.progress.pack(fill=tk.X, pady=(12, 0))

    def run(self):
        paths = self.file_panel.paths
        if len(paths) < 2:
            messagebox.showwarning("Merge PDF", "Add at least two PDF files to merge.")
            return
        out_path = filedialog.asksaveasfilename(
            defaultextension=".pdf", initialfile="merged.pdf",
            filetypes=[("PDF files", "*.pdf")],
        )
        if not out_path:
            return

        self.progress.set_running(True)

        def work():
            return PDFMerger.merge(
                paths, out_path, progress=threadsafe_progress(self.root, self.progress)
            )

        def done(result):
            self.progress.set_running(False)
            self.progress.reset()
            messagebox.showinfo("Merge PDF", f"Saved merged PDF to:\n{result}")

        def error(e):
            self.progress.set_running(False)
            self.progress.reset()
            messagebox.showerror("Merge PDF", f"Something went wrong:\n{e}")

        run_in_background(self.root, work, done, error)

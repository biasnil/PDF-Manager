import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from Config import config
from Script.base_tool import BaseTool
from Script.pdf_operations import PDFCompressor
from Script.threading_utils import run_in_background, threadsafe_progress
from Script.widgets import FileListPanel, ProgressRow


class CompressTool(BaseTool):
    title = "Compress PDF"
    description = (
        "Shrink a PDF by downsampling and re-encoding its embedded images. "
        "Text-only PDFs won't shrink much — that's expected."
    )

    def __init__(self, master, root):
        super().__init__(master, root)
        self.file_panel = FileListPanel(self.body, multiple=False)
        self.file_panel.pack(fill=tk.X)

        quality_row = tk.Frame(self.body, bg=config.PANEL_BG)
        quality_row.pack(fill=tk.X, pady=(12, 4))
        tk.Label(
            quality_row, text="Compression level:", bg=config.PANEL_BG, fg=config.TEXT_MAIN,
            font=config.FONT_BODY,
        ).pack(side=tk.LEFT)
        self.quality_var = tk.StringVar(value=config.DEFAULT_COMPRESS_QUALITY)
        ttk.Combobox(
            quality_row, textvariable=self.quality_var,
            values=list(config.COMPRESS_QUALITY_PRESETS.keys()),
            state="readonly", width=14,
        ).pack(side=tk.LEFT, padx=(8, 0))

        self.progress = ProgressRow(self.body, "Compress PDF", self.run)
        self.progress.pack(fill=tk.X, pady=(12, 0))

    def run(self):
        if not self.file_panel.paths:
            messagebox.showwarning("Compress PDF", "Add a PDF file to compress.")
            return
        input_path = self.file_panel.paths[0]
        out_path = filedialog.asksaveasfilename(
            defaultextension=".pdf", initialfile="compressed.pdf",
            filetypes=[("PDF files", "*.pdf")],
        )
        if not out_path:
            return

        quality = self.quality_var.get()
        self.progress.set_running(True)

        def work():
            return PDFCompressor.compress(
                input_path, out_path, quality=quality,
                progress=threadsafe_progress(self.root, self.progress),
            )

        def done(result):
            self.progress.set_running(False)
            self.progress.reset()
            self.progress.set_output_path(result.output_path)
            messagebox.showinfo(
                "Compress PDF",
                f"Saved to:\n{result.output_path}\n\n"
                f"{result.original_bytes / 1024:.0f} KB -> "
                f"{result.compressed_bytes / 1024:.0f} KB "
                f"({result.percent_saved:.0f}% smaller)",
            )

        def error(e):
            self.progress.set_running(False)
            self.progress.reset()
            messagebox.showerror("Compress PDF", f"Something went wrong:\n{e}")

        run_in_background(self.root, work, done, error)
"""
main.py — PDF Toolbox entry point.

Run this file to launch the app:
    python main.py

All appearance/behavior constants live in Config/config.py.
All GUI and PDF logic lives in Script/, one class per file:
    Script/pdf_operations.py  -> PDFMerger, PDFSplitter, PDFCompressor
    Script/merge_tool.py      -> MergeTool
    Script/split_tool.py      -> SplitTool
    Script/compress_tool.py   -> CompressTool
    Script/coming_soon_tool.py-> ComingSoonTool
    Script/main_window.py     -> PDFToolboxApp (sidebar + tab registry)
    Script/widgets.py         -> shared widgets (buttons, thumbnails, progress bar...)
    Script/threading_utils.py -> background-thread helpers
    Script/dnd_support.py     -> drag-and-drop file support (all tabs)
    Script/base_tool.py       -> BaseTool (parent class for every tab)

Warning/mode_mismatch.py and Error/dependency_checks.py hold the checks
used across tools: Warning is for "this file doesn't match the mode
you've got selected" (e.g. a .pdf dropped in while on "Word to PDF");
Error is for "this can't proceed at all" (a required package or
LibreOffice isn't installed, or a save folder no longer exists).

To add a new tool: write a new Script/<name>_tool.py subclassing BaseTool,
then add one line for it to PDFToolboxApp.TOOLS in Script/main_window.py.
"""

import tkinter as tk
from tkinter import messagebox, ttk

from Config import config
from Script.main_window import PDFToolboxApp
from Error.dependency_checks import missing_required_packages

try:
    from tkinterdnd2 import TkinterDnD
    _DND_AVAILABLE = True
except ImportError:
    _DND_AVAILABLE = False


def main():
    # TkinterDnD.Tk() is a drop-in replacement for tk.Tk() that adds OS-level
    # drag-and-drop support (see Script/dnd_support.py). If tkinterdnd2 isn't
    # installed, fall back to a plain Tk() — every tool still works, dragging
    # files in just won't.
    root = TkinterDnD.Tk() if _DND_AVAILABLE else tk.Tk()
    root.title(config.APP_TITLE)
    root.geometry(config.APP_GEOMETRY)
    root.configure(bg=config.APP_BG)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(
        "TProgressbar", background=config.ACCENT, troughcolor=config.INPUT_BG,
        bordercolor=config.INPUT_BG, lightcolor=config.ACCENT, darkcolor=config.ACCENT,
    )

    PDFToolboxApp(root)

    # Tell the person up front if a required package is missing, rather than
    # letting the affected tool fail with a confusing error the first time
    # they try to use it. The rest of the app still opens and works fine.
    missing = missing_required_packages()
    if missing:
        root.after(300, lambda: messagebox.showerror(
            "Missing requirements",
            "Some tools won't work until these are installed:\n\n"
            + "\n".join(f"- {m}" for m in missing),
        ))

    root.mainloop()


if __name__ == "__main__":
    main()
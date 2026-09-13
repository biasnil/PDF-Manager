"""
Script/main_window.py

PDFToolboxApp is the main window: a sidebar listing every tool, and a
content area that swaps between them. TOOLS is the single registry of
what's in the sidebar — add a new tool by adding one line here.
"""

import tkinter as tk

from Config import config
from Script.compress_tool import CompressTool
from Script.merge_tool import MergeTool
from Script.image_pdf_tool import ImagePdfTool
from Script.organize_pdf_tool import OrganizePdfTool
from Script.pdf_edit_tool import PdfEditTool
from Script.ppt_pdf_tool import PptPdfTool
from Script.split_tool import SplitTool
from Script.word_pdf_tool import WordPdfTool


class PDFToolboxApp(tk.Frame):
    # (sidebar label, tool class, extra kwargs for that class's __init__)
    TOOLS = [
        ("Merge PDF", MergeTool, {}),
        ("Split PDF", SplitTool, {}),
        ("Organize PDF", OrganizePdfTool, {}),
        ("Compress PDF", CompressTool, {}),
        ("Word <-> PDF", WordPdfTool, {}),
        ("PDF <-> PowerPoint", PptPdfTool, {}),
        ("Image <-> PDF", ImagePdfTool, {}),
        ("Edit PDF", PdfEditTool, {}),
    ]

    def __init__(self, root: tk.Tk):
        """root: the Tk() instance — passed down to every tool so their
        background threads can safely marshal updates via root.after."""
        super().__init__(root, bg=config.APP_BG)
        self.root = root
        self.pack(fill=tk.BOTH, expand=True)
        self.tool_frames = {}

        sidebar = tk.Frame(self, bg=config.APP_BG, width=200)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)

        tk.Label(
            sidebar, text=config.APP_TITLE, bg=config.APP_BG, fg=config.TEXT_MAIN,
            font=config.FONT_SIDEBAR_TITLE, anchor="w",
        ).pack(fill=tk.X, padx=18, pady=(20, 16))

        self.nav_buttons = {}
        for name, cls, kwargs in self.TOOLS:
            btn = tk.Button(
                sidebar, text=name, anchor="w",
                bg=config.APP_BG, fg=config.TEXT_MUTED, activebackground=config.PANEL_BG,
                activeforeground=config.TEXT_MAIN, relief=tk.FLAT, bd=0,
                font=config.FONT_BODY, padx=18, pady=10, cursor="hand2",
                command=lambda n=name: self.show_tool(n),
            )
            btn.pack(fill=tk.X)
            self.nav_buttons[name] = btn

        content = tk.Frame(self, bg=config.PANEL_BG)
        content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.content = content

        for name, cls, kwargs in self.TOOLS:
            frame = cls(content, root, **kwargs)
            frame.place(x=0, y=0, relwidth=1, relheight=1)
            self.tool_frames[name] = frame

        self.show_tool("Merge PDF")

    def show_tool(self, name):
        for n, btn in self.nav_buttons.items():
            btn.config(bg=config.PANEL_BG if n == name else config.APP_BG,
                       fg=config.TEXT_MAIN if n == name else config.TEXT_MUTED)
        self.tool_frames[name].tkraise()
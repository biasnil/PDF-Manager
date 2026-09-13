import tkinter as tk

from Config import config
from Script.base_tool import BaseTool


class ComingSoonTool(BaseTool):
    def __init__(self, master, root, title, description):
        self.title = title
        self.description = description
        super().__init__(master, root)
        tk.Label(
            self.body, text="Coming soon", bg=config.PANEL_BG, fg=config.TEXT_MUTED,
            font=config.FONT_SMALL_ITALIC,
        ).pack(anchor="w", pady=20)

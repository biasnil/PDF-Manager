import tkinter as tk

from Config import config


class BaseTool(tk.Frame):
    title = "Tool"
    description = ""

    def __init__(self, master, root):
        """
        master: the tkinter parent widget (the main window's content area)
        root:   the Tk() root window — kept so this tool's background
                threads can safely marshal updates back via root.after
        """
        super().__init__(master, bg=config.PANEL_BG)
        self.root = root

        tk.Label(
            self, text=self.title, bg=config.PANEL_BG, fg=config.TEXT_MAIN,
            font=config.FONT_TITLE, anchor="w",
        ).pack(fill=tk.X, padx=24, pady=(24, 2))
        tk.Label(
            self, text=self.description, bg=config.PANEL_BG, fg=config.TEXT_MUTED,
            font=config.FONT_BODY, anchor="w", justify=tk.LEFT, wraplength=560,
        ).pack(fill=tk.X, padx=24, pady=(0, 16))

        self.body = tk.Frame(self, bg=config.PANEL_BG)
        self.body.pack(fill=tk.BOTH, expand=True, padx=24, pady=(0, 24))

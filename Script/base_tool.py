import tkinter as tk

from Config import config


class _InfoTooltip:
    """Shows `text` in a small borderless popup near `widget` on hover,
    and hides it on leave. Used for a tool's full description when it
    opts into the short-description + (i) icon pattern — see
    BaseTool.description_short."""

    def __init__(self, widget: tk.Widget, text: str):
        self.widget = widget
        self.text = text
        self._tip: "tk.Toplevel | None" = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _event=None):
        if self._tip is not None:
            return
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self._tip, text=self.text, bg=config.BUTTON_BG, fg=config.TEXT_MAIN,
            font=config.FONT_SMALL, justify=tk.LEFT, wraplength=420,
            padx=10, pady=8, relief=tk.SOLID, bd=1,
        ).pack()

    def _hide(self, _event=None):
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


class BaseTool(tk.Frame):
    title = "Tool"
    description = ""
    # Optional. If set, this short line is shown instead of `description`,
    # followed by a small (i) icon — hovering it reveals the full
    # `description` text in a tooltip instead of wrapping several lines
    # under the title. Tools that leave this unset keep the old plain
    # "just show the full description" behavior unchanged.
    description_short = ""

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

        desc_row = tk.Frame(self, bg=config.PANEL_BG)
        desc_row.pack(fill=tk.X, padx=24, pady=(0, 16))
        if self.description_short:
            tk.Label(
                desc_row, text=self.description_short, bg=config.PANEL_BG, fg=config.TEXT_MUTED,
                font=config.FONT_BODY, anchor="w", justify=tk.LEFT, wraplength=520,
            ).pack(side=tk.LEFT)
            info_icon = tk.Label(
                desc_row, text=" \u24d8", bg=config.PANEL_BG, fg=config.TEXT_MUTED,
                font=config.FONT_BODY,
            )
            info_icon.pack(side=tk.LEFT)
            _InfoTooltip(info_icon, self.description)
        else:
            tk.Label(
                desc_row, text=self.description, bg=config.PANEL_BG, fg=config.TEXT_MUTED,
                font=config.FONT_BODY, anchor="w", justify=tk.LEFT, wraplength=560,
            ).pack(fill=tk.X)

        self.body = tk.Frame(self, bg=config.PANEL_BG)
        self.body.pack(fill=tk.BOTH, expand=True, padx=24, pady=(0, 24))
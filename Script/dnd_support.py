"""
Script/dnd_support.py

Thin wrapper around tkinterdnd2 so every tool can accept dragged-in files
without repeating the setup. If tkinterdnd2 isn't installed, everything
here quietly no-ops — the app still works, it just falls back to the
"+ Add file(s)" / "Choose PDF" buttons only.
"""

import os

try:
    from tkinterdnd2 import DND_FILES
    DND_AVAILABLE = True
except ImportError:
    DND_FILES = None
    DND_AVAILABLE = False


def extensions_from_filetypes(filetypes) -> set[str]:
    """Turn a tkinter filedialog `filetypes` list (e.g.
    [("PDF files", "*.pdf")] or [("Image files", "*.jpg *.jpeg *.png")])
    into a lowercase set of extensions like {".pdf"} or {".jpg", ".jpeg", ".png"}."""
    exts = set()
    for _label, patterns in filetypes:
        for pat in patterns.split():
            if pat.startswith("*."):
                exts.add(pat[1:].lower())
    return exts


def register_drop_target(widget, on_drop, extensions: "set[str] | None" = None) -> bool:
    """
    Make `widget` accept files dragged in from the OS file explorer.
    on_drop(paths: list[str]) is called with the dropped paths that match
    `extensions` (all paths are kept if extensions is None/empty).
    Returns True if drag-and-drop was actually enabled for this widget.
    """
    if not DND_AVAILABLE:
        return False
    try:
        widget.drop_target_register(DND_FILES)
    except Exception:
        return False

    def _handler(event):
        try:
            paths = list(widget.tk.splitlist(event.data))
        except Exception:
            return
        if extensions:
            paths = [p for p in paths if os.path.splitext(p)[1].lower() in extensions]
        if paths:
            on_drop(paths)

    widget.dnd_bind("<<Drop>>", _handler)
    return True
"""
Script/edit_pdf/ — the "Edit PDF" tab, split into a package because it's
by far the biggest tool in the app:

    constants.py -> tool ids, tool groupings, color/font lookup helpers
    dialogs.py    -> the two popup dialogs (signature pad, free-text editor)
    tool.py       -> PdfEditTool itself

Only PdfEditTool is meant to be imported from outside this package.
"""

from .tool import PdfEditTool

__all__ = ["PdfEditTool"]
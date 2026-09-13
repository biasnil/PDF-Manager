"""
Error/dependency_checks.py

These are *errors*, not warnings: something the app needs isn't there —
a required Python package, the LibreOffice binary, or the folder someone
picked to save into — and the operation genuinely cannot proceed. Every
check here returns a plain-English message (never raises), so a tool can
do:

    problem = check_libreoffice()
    if problem:
        messagebox.showerror("...", problem)
        return

and the person gets a clear, actionable message instead of a raw
traceback or a cryptic subprocess failure.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from typing import Optional

# (import name, pip package name, used by...)
REQUIRED_PACKAGES = [
    ("pymupdf", "pymupdf", "every PDF tool"),
    ("pptx", "python-pptx", "PDF <-> PowerPoint"),
    ("docx2pdf", "docx2pdf", "Word <-> PDF"),
    ("pdf2docx", "pdf2docx", "Word <-> PDF (PDF to Word)"),
    ("PIL", "Pillow", "Image <-> PDF, Edit PDF"),
]

# Not required for the app to run at all — only for drag-and-drop, so
# checked separately and never blocks startup.
OPTIONAL_PACKAGES = [
    ("tkinterdnd2", "tkinterdnd2", "drag-and-drop file support"),
]


def missing_required_packages() -> list[str]:
    """Returns a list of friendly messages, one per required package that
    isn't importable — empty list if everything needed is present."""
    missing = []
    for import_name, pip_name, used_by in REQUIRED_PACKAGES:
        if importlib.util.find_spec(import_name) is None:
            missing.append(
                f"{pip_name} isn't installed (needed for {used_by}). "
                f"Run: pip install -r requirements.txt"
            )
    return missing


def check_libreoffice() -> Optional[str]:
    """Returns an error message if LibreOffice's command-line binary can't
    be found — used before a conversion that has no other fallback
    (PowerPoint to PDF; Word to PDF also tries MS Word first)."""
    if shutil.which("soffice") or shutil.which("libreoffice"):
        return None
    return (
        "This conversion needs LibreOffice installed on this computer "
        "(it's free): https://www.libreoffice.org/download/"
    )


def check_output_path(output_path: str) -> Optional[str]:
    """Returns an error message if the folder someone chose to save into
    no longer exists or isn't writable — e.g. it was deleted or moved
    after they picked it in the save dialog."""
    folder = os.path.dirname(output_path) or "."
    if not os.path.isdir(folder):
        return f"The folder to save into no longer exists:\n{folder}"
    if not os.access(folder, os.W_OK):
        return f"This folder isn't writable:\n{folder}"
    return None


def check_output_dir(dir_path: str) -> Optional[str]:
    """Same check as check_output_path, but for tools where the person
    picks an output FOLDER directly (askdirectory) rather than a file
    path (asksaveasfilename)."""
    if not os.path.isdir(dir_path):
        return f"The folder to save into no longer exists:\n{dir_path}"
    if not os.access(dir_path, os.W_OK):
        return f"This folder isn't writable:\n{dir_path}"
    return None
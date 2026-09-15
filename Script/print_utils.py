"""
Script/print_utils.py

Backs the "Print..." button next to every tab's Save/Run button (see
widgets.ProgressRow / widgets.PrinterPickerDialog) — lets the person pick
one of the system's installed printers and send a finished file straight
to it, no separate PDF viewer needed.

Windows-only for now: this app currently only ships a Windows build (see
PDFToolbox.spec / build.bat), and both printer enumeration and printing
go through pywin32, which is already an installed dependency of docx2pdf
on Windows — this doesn't add a new third-party requirement in practice,
but it's listed explicitly in requirements.txt now anyway so a build never
depends on it only being pulled in transitively.

On any other OS, list_printers() just returns [] and print_file() raises
PrintingUnavailable, so the dialog can show a clear message instead of
crashing — nothing here assumes Windows at import time.
"""

import platform
from typing import Optional


class PrintingUnavailable(Exception):
    """Raised by print_file() when printing isn't supported on this OS."""


def is_supported() -> bool:
    return platform.system() == "Windows"


def list_printers() -> list[str]:
    """Names of every installed printer (local + network-connected), or
    [] if none are found or printing isn't supported here."""
    if not is_supported():
        return []
    try:
        import win32print
    except ImportError:
        return []

    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    try:
        return sorted(p[2] for p in win32print.EnumPrinters(flags))
    except Exception:
        return []


def get_default_printer() -> Optional[str]:
    """The system's current default printer, or None if there isn't one /
    it can't be determined."""
    if not is_supported():
        return None
    try:
        import win32print
        return win32print.GetDefaultPrinter()
    except Exception:
        return None


def print_file(path: str, printer_name: str) -> None:
    """Sends `path` to `printer_name`, via whatever app/handler Windows
    has associated with the file's type (normally Edge or Acrobat for a
    PDF) — using the "printto" shell verb specifically so a particular
    printer can be targeted without changing the user's actual Windows
    default printer."""
    if not is_supported():
        raise PrintingUnavailable("Printing is only supported on Windows in this build.")
    try:
        import win32api
    except ImportError as exc:
        raise PrintingUnavailable("pywin32 isn't installed — can't print from here.") from exc

    win32api.ShellExecute(0, "printto", path, f'"{printer_name}"', ".", 0)
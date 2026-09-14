# PDFToolbox.spec
#
# Build with:
#   pyinstaller PDFToolbox.spec
#
# Run from the project root (same folder as main.py), inside the venv
# that has requirements.txt + pyinstaller installed. Output lands in
# dist/PDFToolbox/PDFToolbox.exe.
#
# Rebuilding after code changes: just re-run the command above — no
# need to delete build/ or dist/ first, PyInstaller overwrites them.
# If you add a NEW third-party package to requirements.txt later and
# the build silently doesn't include it, that's usually a missing
# hiddenimport — add it to the list below.

import os
import tkinterdnd2

block_cipher = None

# tkinterdnd2 ships its actual drag-and-drop engine (Tcl scripts + a
# platform DLL) as data files inside its own package folder — these are
# loaded at runtime via tk.eval(), not a Python import, so PyInstaller's
# static analysis never sees them on its own. Without this, the built
# exe launches fine but drag-and-drop silently does nothing.
tkdnd_path = os.path.join(os.path.dirname(tkinterdnd2.__file__), "tkdnd")

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[(tkdnd_path, 'tkinterdnd2/tkdnd')],
    hiddenimports=[
        # pymupdf's importable name is "fitz" — PyInstaller usually
        # catches this via its own bundled hook, but listing both
        # explicitly costs nothing and saves a confusing first-run
        # ModuleNotFoundError if that hook is ever missing/outdated.
        'fitz',
        'pymupdf',
        # docx2pdf drives real MS Word through pywin32's COM bridge on
        # Windows — these two are what usually go missing first if a
        # built exe's Word conversion fails while `python main.py`
        # worked fine.
        'win32com.client',
        'win32com.gen_py',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='PDFToolbox',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,           # no console window behind the GUI
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,                # set to r'path\to\icon.ico' once you have one
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='PDFToolbox',
)

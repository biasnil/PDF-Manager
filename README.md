# PDF Toolbox

A desktop PDF utility app in the style of iLovePDF, built with Python + tkinter.

## Setup

```bash
cd pdftoolbox
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
python main.py
```

## What's working now

- **Merge PDF** — combine any number of PDFs; drag an item in the list to reorder it, save as one file.
- **Split PDF** — pick a PDF, see a thumbnail of every page with a tick box on
  each (click to toggle). "Select all" / "Deselect all", and a "Merge selected
  pages into one PDF" checkbox — off gives one file per page, on gives a single
  file with just the pages you kept.
- **Compress PDF** — downsamples and re-encodes embedded images at a chosen
  quality level (`low` / `recommended` / `high`). Text/vector content is
  untouched, so text-only PDFs won't shrink much — that's expected.

All three run on a background thread with a progress bar, so the window never
freezes on large files. Progress updates are marshaled back to the main thread
via `root.after(...)` (see `Script/threading_utils.py`) — never touch a tkinter
widget directly from a worker thread, it's unsafe and can crash the app.

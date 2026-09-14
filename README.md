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

## Project structure

```
PDF-Manager-main/
├── Config/
│   └── config.py              # all colors, fonts, sizes — nothing hardcoded elsewhere
├── Error/
│   └── dependency_checks.py   # "can't proceed at all" checks (missing package, missing LibreOffice...)
├── Warning/
│   └── mode_mismatch.py       # "this file doesn't match the mode you've got selected" checks
├── Script/
│   ├── main_window.py         # PDFToolboxApp — sidebar + tab registry (TOOLS list)
│   ├── base_tool.py           # BaseTool — parent class every tab subclasses
│   ├── widgets.py             # shared widgets: buttons, thumbnails, progress bar...
│   ├── threading_utils.py     # background-thread + thread-safe progress helpers
│   ├── dnd_support.py         # drag-and-drop file support (all tabs)
│   │
│   ├── merge_tool.py          # Merge PDF
│   ├── split_tool.py          # Split PDF
│   ├── organize_pdf_tool.py   # Organize PDF (reorder/remove/insert blank pages)
│   ├── compress_tool.py       # Compress PDF
│   ├── word_pdf_tool.py       # Word <-> PDF
│   ├── ppt_pdf_tool.py        # PDF <-> PowerPoint
│   ├── image_pdf_tool.py      # Image <-> PDF
│   ├── pdf_operations.py      # engine behind Merge/Split/Organize/Compress
│   ├── office_operations.py   # engine behind Word/PowerPoint conversion
│   ├── image_operations.py    # engine behind Image <-> PDF
│   │
│   ├── edit_pdf/              # Edit PDF (its own package — by far the biggest tool)
│   │   ├── __init__.py        #   re-exports PdfEditTool
│   │   ├── constants.py       #   tool ids, tool groupings, color/font lookup helpers
│   │   ├── dialogs.py         #   popup dialogs (signature pad, free-text editor)
│   │   └── tool.py            #   PdfEditTool itself
│   └── pdf_edit_operations.py # engine behind Edit PDF's save step
│
├── main.py                    # entry point
├── requirements.txt
└── README.md
```

## What's working now

- **Merge PDF** — combine any number of PDFs; drag an item in the list to reorder it, save as one file.
- **Split PDF** — pick a PDF, see a thumbnail of every page with a tick box on
  each (click to toggle). "Select all" / "Deselect all", and a "Merge selected
  pages into one PDF" checkbox — off gives one file per page, on gives a single
  file with just the pages you kept.
- **Organize PDF** — drag a thumbnail to reorder it, click its × to remove a
  page, or hover the gap between two pages for a moment to insert a blank
  page right there.
- **Compress PDF** — downsamples and re-encodes embedded images at a chosen
  quality level (`low` / `recommended` / `high`). Text/vector content is
  untouched, so text-only PDFs won't shrink much — that's expected.
- **Word ↔ PDF** and **PDF ↔ PowerPoint** — convert between formats.
- **Image ↔ PDF** — build a PDF from images (page size, orientation, and
  margin options) or pull the images back out.
- **Edit PDF** — two modes, toggled at the top:
  - *Annotate*: Underline, Strikeout, Squiggly, Free Hand, Free Hand
    Highlight, Free Text, Insert Text, Replace Text, Undo, Eraser, Shapes
    (Rectangle/Circle/Line/Arrow), and Insert (Stamp, Signature, File
    Attachment).
  - *Edit*: Add Paragraph, New Image — burned directly into the page
    content, not removable annotations.
  - The left-hand page list works the same way as Organize's grid: hover
    the gap above a page for a moment to insert a blank page there.

Everything that touches a whole file runs on a background thread with a
progress bar, so the window never freezes on large files. Progress updates
are marshaled back to the main thread via `root.after(...)` (see
`Script/threading_utils.py`) — never touch a tkinter widget directly from a
worker thread, it's unsafe and can crash the app.
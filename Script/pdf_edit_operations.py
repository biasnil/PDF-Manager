"""
Script/pdf_edit_operations.py

Core logic behind the "Edit PDF" tab. No GUI code — the tab collects a list
of plain-dict "pending edits" while the person works, and this module turns
that list into real PDF annotations/content and saves the result.

Each pending edit is a dict with at least {"type": ..., "page": <index>}.
Supported types and their extra keys:

    underline / strikeout / squiggly   {"quads": [pymupdf.Quad, ...], "color": (r,g,b)}
    freehand / highlight_freehand      {"points": [(x,y), ...], "color": (r,g,b), "width": float}
    freetext                            {"rect": (x0,y0,x1,y1), "text": str, "color": (r,g,b), "fontsize": int}
    insert_text_note                    {"point": (x,y), "text": str}
    replace_text                        {"quads": [...], "new_text": str, "color": (r,g,b), "fontsize": int}
    shape                               {"shape": "Rectangle"/"Circle"/"Line"/"Arrow", "rect" or "points", "color": (r,g,b), "width": float}
    stamp                               {"rect": (x0,y0,x1,y1), "name": str}
    signature                           {"rect": (x0,y0,x1,y1), "png_bytes": bytes}
    file_attachment                     {"point": (x,y), "filepath": str}
    paragraph                           {"rect": (x0,y0,x1,y1), "text": str, "fontsize": int, "color": (r,g,b)}
    image                               {"rect": (x0,y0,x1,y1), "filepath": str}
"""

from __future__ import annotations

import os
from collections import defaultdict
from typing import Callable, Optional

import pymupdf

from Config import config

ProgressCallback = Optional[Callable[[int, int, str], None]]


def get_word_quads_in_rect(page: "pymupdf.Page", rect) -> list["pymupdf.Quad"]:
    """Return the quad of every word whose box intersects the given rect —
    this is what lets a click-drag selection on the canvas turn into real
    text-anchored annotations (underline, strikeout, squiggly, replace)."""
    rect = pymupdf.Rect(rect)
    quads = []
    for w in page.get_text("words"):
        word_rect = pymupdf.Rect(w[:4])
        if word_rect.intersects(rect):
            quads.append(word_rect.quad)
    return quads


def apply_edits_and_save(input_path: str, output_path: str, edits: list[dict],
                          progress: ProgressCallback = None) -> str:
    """Apply every pending edit (grouped by page) to a copy of input_path
    and save it to output_path."""
    doc = pymupdf.open(input_path)

    by_page = defaultdict(list)
    for e in edits:
        by_page[e["page"]].append(e)

    total = len(edits)
    done_count = 0

    for page_index, page_edits in by_page.items():
        page = doc[page_index]

        # Replace-text edits redact the original words first, in one batch,
        # before anything else touches this page.
        redact_edits = [e for e in page_edits if e["type"] == "replace_text"]
        for e in redact_edits:
            for q in e["quads"]:
                page.add_redact_annot(pymupdf.Rect(q.rect), fill=(1, 1, 1))
        if redact_edits:
            page.apply_redactions()

        for e in page_edits:
            done_count += 1
            if progress:
                progress(done_count, total, f"Applying edit {done_count}/{total}")
            _apply_one_edit(doc, page, e)

    doc.save(output_path)
    doc.close()
    return output_path


def _apply_one_edit(doc: "pymupdf.Document", page: "pymupdf.Page", e: dict) -> None:
    t = e["type"]

    if t == "underline":
        a = page.add_underline_annot(e["quads"])
        a.set_colors(stroke=e["color"])
        a.update()

    elif t == "strikeout":
        a = page.add_strikeout_annot(e["quads"])
        a.set_colors(stroke=e["color"])
        a.update()

    elif t == "squiggly":
        a = page.add_squiggly_annot(e["quads"])
        a.set_colors(stroke=e["color"])
        a.update()

    elif t in ("freehand", "highlight_freehand"):
        a = page.add_ink_annot([e["points"]])
        a.set_colors(stroke=e["color"])
        a.set_border(width=e["width"])
        if t == "highlight_freehand":
            a.set_opacity(config.EDIT_HIGHLIGHT_OPACITY)
        a.update()

    elif t == "freetext":
        page.add_freetext_annot(
            pymupdf.Rect(e["rect"]), e["text"],
            fontsize=e.get("fontsize", config.EDIT_DEFAULT_FONT_SIZE),
            fontname=e.get("fontname", "helv"),
            text_color=e["color"],
        )

    elif t == "insert_text_note":
        page.add_text_annot(e["point"], e["text"])

    elif t == "replace_text":
        # redaction for this edit was already applied above; place the
        # replacement text starting at the first selected word's baseline
        first_rect = pymupdf.Rect(e["quads"][0].rect)
        page.insert_text(
            (first_rect.x0, first_rect.y1 - 2), e["new_text"],
            fontsize=e.get("fontsize", config.EDIT_DEFAULT_FONT_SIZE),
            fontname=e.get("fontname", "helv"),
            color=e.get("color", (0, 0, 0)),
        )

    elif t == "shape":
        shape = e["shape"]
        color = e["color"]
        width = e["width"]
        if shape == "Rectangle":
            a = page.add_rect_annot(pymupdf.Rect(e["rect"]))
        elif shape == "Circle":
            a = page.add_circle_annot(pymupdf.Rect(e["rect"]))
        elif shape in ("Line", "Arrow"):
            p1, p2 = e["points"]
            a = page.add_line_annot(p1, p2)
            if shape == "Arrow":
                a.set_line_ends(pymupdf.PDF_ANNOT_LE_NONE, pymupdf.PDF_ANNOT_LE_OPEN_ARROW)
        else:
            return
        a.set_colors(stroke=color)
        a.set_border(width=width)
        a.update()

    elif t == "stamp":
        stamp_id = getattr(pymupdf, f"STAMP_{e['name']}", 0)
        page.add_stamp_annot(pymupdf.Rect(e["rect"]), stamp=stamp_id)

    elif t == "signature":
        page.insert_image(pymupdf.Rect(e["rect"]), stream=e["png_bytes"])

    elif t == "file_attachment":
        with open(e["filepath"], "rb") as f:
            data = f.read()
        page.add_file_annot(e["point"], data, os.path.basename(e["filepath"]))

    elif t == "paragraph":
        page.insert_textbox(
            pymupdf.Rect(e["rect"]), e["text"],
            fontsize=e.get("fontsize", config.EDIT_DEFAULT_FONT_SIZE),
            fontname=e.get("fontname", "helv"),
            color=e.get("color", (0, 0, 0)),
        )

    elif t == "image":
        page.insert_image(pymupdf.Rect(e["rect"]), filename=e["filepath"])
"""
Script/edit_pdf/image_edit.py

The engine behind detecting EXISTING embedded images in Edit mode: every
image actually placed on the page gets a persistent solid outline
(get_image_blocks), and clicking inside one promotes it into a real
"existing_image" pending edit sharing that image's own bytes and current
rect. From that moment on it's just an ordinary movable/resizable/
rotatable pending edit — tool.py's generic interaction code (the same
machinery the New Image annotation tool already uses) takes over
completely. This module only owns detection, outline drawing, and the
click-to-promote hand-off.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional


def get_image_blocks(page) -> list[dict]:
    """One entry per image actually placed on this page: its bounding
    rect (pdf points), xref, and the raw encoded bytes + extension —
    ready to drop straight into an "existing_image" pending edit with
    no re-decoding needed. Images PyMuPDF can't resolve a placement
    rect for (rare — e.g. used only inside a Form XObject/tiling
    pattern) are skipped rather than guessed at. If the same xref is
    physically placed more than once on the page, only its first
    placement is offered here — same simplification get_image_bbox
    itself makes."""
    doc = page.parent
    blocks = []
    for img in page.get_images(full=True):
        xref = img[0]
        try:
            rect = page.get_image_bbox(img)
        except ValueError:
            continue
        if rect.is_empty or rect.is_infinite:
            continue
        try:
            extracted = doc.extract_image(xref)
        except Exception:
            continue
        blocks.append({
            "rect": (rect.x0, rect.y0, rect.x1, rect.y1),
            "xref": xref,
            "image_bytes": extracted["image"],
            "ext": extracted["ext"],
        })
    return blocks


class ImageEditOverlay:
    """Owns the Edit-mode canvas state for existing images only: the
    solid outlines drawn around every not-yet-promoted image on the
    page, and the hit-testing that finds which one a click landed on.
    Knows nothing about pending_edits or tool selection — PdfEditTool
    decides what a hit means (it builds and adds the actual pending
    edit); this class just answers "what's here" and draws boxes."""

    OUTLINE_COLOR = "#2ecc71"  # same green the New Image tool uses

    def __init__(self, canvas: tk.Canvas, get_page: Callable[[], Optional[object]],
                 pdf_rect_to_canvas: Callable[[tuple], tuple[float, float, float, float]]):
        self.canvas = canvas
        self.get_page = get_page
        self.pdf_rect_to_canvas = pdf_rect_to_canvas
        self._blocks: list[dict] = []
        self._outline_ids: dict[int, int] = {}  # xref -> canvas item id

    def refresh(self, promoted_xrefs: set):
        """Recompute every image on the page and redraw outlines for
        the ones NOT already in promoted_xrefs (those are already live
        pending edits, drawn elsewhere — outlining them again here
        would double-draw them and let a stray click re-promote a
        duplicate). Call this at controlled points only (page render,
        mode change, undo/redo/erase) — it decodes every image on the
        page, so it's not meant to run on every drag frame."""
        self._clear_outlines()
        page = self.get_page()
        if page is None:
            self._blocks = []
            return
        self._blocks = [b for b in get_image_blocks(page) if b["xref"] not in promoted_xrefs]
        for block in self._blocks:
            r = self.pdf_rect_to_canvas(block["rect"])
            iid = self.canvas.create_rectangle(
                *r, outline=self.OUTLINE_COLOR, width=2, tags="image_edit_outline",
            )
            self._outline_ids[block["xref"]] = iid

    def remove(self, xref):
        """Cheap single-item removal, used right after a promotion —
        avoids re-decoding every image on the page just to drop one
        outline."""
        self._blocks = [b for b in self._blocks if b["xref"] != xref]
        iid = self._outline_ids.pop(xref, None)
        if iid is not None:
            self.canvas.delete(iid)

    def clear(self):
        self._clear_outlines()
        self._blocks = []

    def _clear_outlines(self):
        for iid in self._outline_ids.values():
            self.canvas.delete(iid)
        self._outline_ids = {}

    def hit_test(self, px: float, py: float) -> Optional[dict]:
        """px, py in PDF space. Returns the block dict under that
        point, or None."""
        for block in self._blocks:
            x0, y0, x1, y1 = block["rect"]
            if x0 <= px <= x1 and y0 <= py <= y1:
                return block
        return None
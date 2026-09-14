"""
Script/pdf_edit_operations.py

Core logic behind the "Edit PDF" tab. No GUI code — the tab collects a list
of plain-dict "pending edits" while the person works, and this module turns
that list into real PDF annotations/content and saves the result.

Each pending edit is a dict with at least {"type": ..., "page": <index>}.
Supported types and their extra keys:

    underline / strikeout / squiggly   {"quads": [pymupdf.Quad, ...], "color": (r,g,b)}
    freehand / highlight_freehand      {"points": [(x,y), ...], "color": (r,g,b), "width": float}
    freetext                            {"rect": (x0,y0,x1,y1), "text": str, "color": (r,g,b), "fontsize": int, "tk_font": str, "rotation": degrees (optional, default 0)}
    insert_text_note                    {"point": (x,y), "text": str}
    replace_text                        {"quads": [...], "new_text": str, "color": (r,g,b), "fontsize": int, "tk_font": str, "bold": bool, "italic": bool}
    shape                               {"shape": "Rectangle"/"Circle"/"Line"/"Arrow", "rect" or "points", "color": (r,g,b), "width": float, "rotation": degrees (optional, default 0)}
    stamp                               {"rect": (x0,y0,x1,y1), "name": str}
    signature                           {"rect": (x0,y0,x1,y1), "png_bytes": bytes}
    file_attachment                     {"point": (x,y), "filepath": str}
    paragraph                           {"rect": (x0,y0,x1,y1), "text": str, "fontsize": int, "color": (r,g,b), "tk_font": str, "bold": bool, "italic": bool}
    image                               {"rect": (x0,y0,x1,y1), "filepath": str, "rotation": degrees (optional, default 0)}
    existing_image                      {"rect": current (x0,y0,x1,y1), "original_rect": (x0,y0,x1,y1) it was found at, "image_bytes": bytes, "ext": str, "rotation": degrees (optional, default 0)}
"""

from __future__ import annotations

import io
import math
import os
from collections import defaultdict
from typing import Callable, Optional

import pymupdf

from Config import config

ProgressCallback = Optional[Callable[[int, int, str], None]]


def draw_arrowhead(draw, p0: tuple, p1: tuple, color: tuple, stroke_width: float):
    """Draws a simple open arrowhead at p1, pointing from p0 -> p1, onto a
    PIL ImageDraw. Shared by the shape rasterizer below and the Edit PDF
    tab's live canvas preview (tool.py), so a rotated Arrow looks the
    same rasterized for save as it did on screen."""
    x0, y0 = p0
    x1, y1 = p1
    angle = math.atan2(y1 - y0, x1 - x0)
    length = max(10, stroke_width * 3)
    spread = math.radians(25)
    for sign in (1, -1):
        ax = x1 - length * math.cos(angle - sign * spread)
        ay = y1 - length * math.sin(angle - sign * spread)
        draw.line([(x1, y1), (ax, ay)], fill=color, width=max(1, round(stroke_width)))


def rasterize_shape(shape: str, w: int, h: int, color_rgb: tuple, stroke_width_px: float,
                     line_points_local: "tuple | None" = None):
    """Draws a Rectangle/Circle/Line/Arrow onto a transparent RGBA PIL
    Image of size (w, h). For Line/Arrow, line_points_local are the two
    endpoints already in this image's local coordinate space. Shared by
    the save path (this file, at print resolution) and the live preview
    (tool.py, at canvas resolution) so a rotated shape rasterizes
    identically in both places."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (max(1, round(w)), max(1, round(h))), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = tuple(round(c * 255) for c in color_rgb) + (255,)
    sw = max(1, round(stroke_width_px))

    if shape == "Rectangle":
        inset = sw / 2
        draw.rectangle([inset, inset, w - inset, h - inset], outline=color, width=sw)
    elif shape == "Circle":
        inset = sw / 2
        draw.ellipse([inset, inset, w - inset, h - inset], outline=color, width=sw)
    elif shape in ("Line", "Arrow") and line_points_local:
        p0, p1 = line_points_local
        draw.line([p0, p1], fill=color, width=sw)
        if shape == "Arrow":
            draw_arrowhead(draw, p0, p1, color, sw)
    return img

# ---------------------------------------------------------------------
# Font resolution for Free Text / Add Paragraph / Replace Text.
#
# insert_text() and insert_textbox() (used by Add Paragraph and Replace
# Text) can embed an arbitrary TTF via fontfile=, so those two get the
# REAL font — Arial/Calibri/Georgia — when it's actually installed on
# this machine (standard pre-installed Windows fonts, found via the
# paths below).
#
# add_freetext_annot() (used by Free Text) has NO fontfile support at
# all in PyMuPDF — it only accepts the base-14 built-in names. So Free
# Text always uses the closest base-14 stand-in (_BASE14_FALLBACK),
# regardless of whether the real font is installed. This is a real
# PyMuPDF/PDF-spec limitation of that annotation type, not a bug.
# ---------------------------------------------------------------------

_FONT_FILE_CANDIDATES = {
    "Arial": {
        (False, False): ["arial.ttf"], (True, False): ["arialbd.ttf"],
        (False, True): ["ariali.ttf"], (True, True): ["arialbi.ttf"],
    },
    "Calibri": {
        (False, False): ["calibri.ttf"], (True, False): ["calibrib.ttf"],
        (False, True): ["calibrii.ttf"], (True, True): ["calibriz.ttf"],
    },
    "Georgia": {
        (False, False): ["georgia.ttf"], (True, False): ["georgiab.ttf"],
        (False, True): ["georgiai.ttf"], (True, True): ["georgiaz.ttf"],
    },
}

_FONT_SEARCH_DIRS = [
    r"C:\Windows\Fonts",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\Fonts"),
    "/usr/share/fonts/truetype/msttcorefonts",  # sometimes present via ttf-mscorefonts on Linux
    "/Library/Fonts",
    os.path.expanduser("~/Library/Fonts"),
    os.path.expanduser("~/.fonts"),
]

_BASE14_FALLBACK = {
    "Arial": {(False, False): "helv", (True, False): "hebo", (False, True): "heit", (True, True): "hebi"},
    "Calibri": {(False, False): "helv", (True, False): "hebo", (False, True): "heit", (True, True): "hebi"},
    "Georgia": {(False, False): "tiro", (True, False): "tibo", (False, True): "tiit", (True, True): "tibi"},
}


def _find_font_file(display_name: str, bold: bool, italic: bool) -> Optional[str]:
    for filename in _FONT_FILE_CANDIDATES.get(display_name, {}).get((bold, italic), []):
        for d in _FONT_SEARCH_DIRS:
            if d and os.path.isfile(os.path.join(d, filename)):
                return os.path.join(d, filename)
    return None


def _base14_fallback_code(display_name: str, bold: bool = False, italic: bool = False) -> str:
    variants = _BASE14_FALLBACK.get(display_name)
    return variants[(bold, italic)] if variants else "helv"


def resolve_embeddable_font(display_name: str, bold: bool = False, italic: bool = False) -> dict:
    """For insert_text/insert_textbox (Add Paragraph, Replace Text): real
    font file if found on this machine, else the closest base-14 stand-
    in. Returns kwargs to splat directly into either call."""
    path = _find_font_file(display_name, bold, italic)
    if path:
        alias = f"{display_name}{'-Bold' if bold else ''}{'-Italic' if italic else ''}"
        return {"fontname": alias, "fontfile": path}
    return {"fontname": _base14_fallback_code(display_name, bold, italic)}


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
                          blank_pages: "list[int] | None" = None,
                          progress: ProgressCallback = None) -> str:
    """Apply every pending edit (grouped by page) to a copy of input_path
    and save it to output_path.

    blank_pages: page indices (in the FINAL, post-insertion numbering —
    i.e. the same numbering `edits[i]["page"]` already uses) that should
    be blank pages not present in input_path. Must be given in ascending
    order (the Edit PDF tab's blank_page_indices, sorted, satisfies this).
    Inserting them ascending into a fresh copy of input_path reconstructs
    exactly the layout the person was looking at live, so every edit's
    "page" index still lands on the right page.
    """
    doc = pymupdf.open(input_path)

    for pno in (blank_pages or []):
        doc.new_page(pno=pno)

    by_page = defaultdict(list)
    for e in edits:
        by_page[e["page"]].append(e)

    total = len(edits)
    done_count = 0

    for page_index, page_edits in by_page.items():
        page = doc[page_index]

        # Replace-text and existing-image edits both need the ORIGINAL
        # content wiped before anything reinserts new content, in one
        # redaction batch, before anything else touches this page.
        redact_edits = [e for e in page_edits if e["type"] in ("replace_text", "existing_image")]
        for e in redact_edits:
            if e["type"] == "replace_text":
                for q in e["quads"]:
                    page.add_redact_annot(pymupdf.Rect(q.rect), fill=(1, 1, 1))
            else:  # existing_image — wipe where it USED to sit, not where it's going
                page.add_redact_annot(pymupdf.Rect(e["original_rect"]), fill=(1, 1, 1))
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
            a.set_opacity(e.get("opacity", config.EDIT_HIGHLIGHT_OPACITY))
        a.update()

    elif t == "freetext":
        rotation = e.get("rotation", 0)
        display_name = e.get("tk_font", config.DEFAULT_FONT_NAME)
        bold, italic = e.get("bold", False), e.get("italic", False)
        font_path = _find_font_file(display_name, bold, italic) if rotation else None

        if not rotation or not font_path:
            # Unrotated (or rotated but no real font file to rasterize
            # with — better to show it unrotated than render with an
            # ugly fallback bitmap font) uses the normal annotation path.
            page.add_freetext_annot(
                pymupdf.Rect(e["rect"]), e["text"],
                fontsize=e.get("fontsize", config.EDIT_DEFAULT_FONT_SIZE),
                fontname=_base14_fallback_code(display_name, bold, italic),
                text_color=e["color"],
            )
        else:
            from PIL import Image, ImageDraw, ImageFont

            rect = pymupdf.Rect(e["rect"])
            scale = 4
            fontsize_px = max(1, round(e.get("fontsize", config.EDIT_DEFAULT_FONT_SIZE) * scale))
            pil_font = ImageFont.truetype(font_path, fontsize_px)
            color = tuple(round(c * 255) for c in e["color"]) + (255,)

            probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
            bbox = probe.textbbox((0, 0), e["text"], font=pil_font)
            text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            pad = 4
            img = Image.new("RGBA", (text_w + pad * 2, text_h + pad * 2), (0, 0, 0, 0))
            ImageDraw.Draw(img).text((pad - bbox[0], pad - bbox[1]), e["text"], font=pil_font, fill=color)

            rotated = img.rotate(-rotation, expand=True)
            buf = io.BytesIO()
            rotated.save(buf, format="PNG")

            cx, cy = (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2
            new_w_pdf, new_h_pdf = rotated.width / scale, rotated.height / scale
            new_rect = pymupdf.Rect(
                cx - new_w_pdf / 2, cy - new_h_pdf / 2,
                cx + new_w_pdf / 2, cy + new_h_pdf / 2,
            )
            page.insert_image(new_rect, stream=buf.getvalue())

    elif t == "existing_image":
        rotation = e.get("rotation", 0)
        rect = pymupdf.Rect(e["rect"])
        if not rotation:
            page.insert_image(rect, stream=e["image_bytes"])
        else:
            from PIL import Image

            scale = 4
            box_w_px = max(1, round(rect.width * scale))
            box_h_px = max(1, round(rect.height * scale))

            img = Image.open(io.BytesIO(e["image_bytes"])).convert("RGBA")
            img.thumbnail((box_w_px, box_h_px))
            rotated = img.rotate(-rotation, expand=True)

            buf = io.BytesIO()
            rotated.save(buf, format="PNG")

            new_w_pdf, new_h_pdf = rotated.width / scale, rotated.height / scale
            cx, cy = (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2
            new_rect = pymupdf.Rect(
                cx - new_w_pdf / 2, cy - new_h_pdf / 2,
                cx + new_w_pdf / 2, cy + new_h_pdf / 2,
            )
            page.insert_image(new_rect, stream=buf.getvalue())

    elif t == "insert_text_note":
        page.add_text_annot(e["point"], e["text"])

    elif t == "replace_text":
        # redaction for this edit was already applied above; place the
        # replacement text starting at the first selected word's baseline
        first_rect = pymupdf.Rect(e["quads"][0].rect)
        font_kwargs = resolve_embeddable_font(
            e.get("tk_font", config.DEFAULT_FONT_NAME), e.get("bold", False), e.get("italic", False),
        )
        page.insert_text(
            (first_rect.x0, first_rect.y1 - 2), e["new_text"],
            fontsize=e.get("fontsize", config.EDIT_DEFAULT_FONT_SIZE),
            color=e.get("color", (0, 0, 0)),
            **font_kwargs,
        )

    elif t == "shape":
        shape = e["shape"]
        color = e["color"]
        width = e["width"]
        rotation = e.get("rotation", 0)

        if not rotation:
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
        else:
            # Arbitrary angle: PDF annotation rotation is 90-degree-
            # stepped at best, so rasterize (same reasoning as Image's
            # rotation) and insert as a plain image instead of an
            # annotation. Loses the "still an editable annotation"
            # property, same trade-off rotating an Image accepts.
            scale = 4
            if shape in ("Rectangle", "Circle"):
                rect = pymupdf.Rect(e["rect"])
                cx, cy = (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2
                w_px = max(1, round(rect.width * scale))
                h_px = max(1, round(rect.height * scale))
                img = rasterize_shape(shape, w_px, h_px, color, width * scale)
            else:  # Line / Arrow
                p1, p2 = e["points"]
                pad = width / 2 + 2
                bx0 = min(p1[0], p2[0]) - pad
                by0 = min(p1[1], p2[1]) - pad
                bx1 = max(p1[0], p2[0]) + pad
                by1 = max(p1[1], p2[1]) + pad
                cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
                w_px = max(1, round((bx1 - bx0) * scale))
                h_px = max(1, round((by1 - by0) * scale))
                local_p1 = ((p1[0] - bx0) * scale, (p1[1] - by0) * scale)
                local_p2 = ((p2[0] - bx0) * scale, (p2[1] - by0) * scale)
                img = rasterize_shape(shape, w_px, h_px, color, width * scale, (local_p1, local_p2))

            rotated = img.rotate(-rotation, expand=True)
            buf = io.BytesIO()
            rotated.save(buf, format="PNG")
            new_w_pdf, new_h_pdf = rotated.width / scale, rotated.height / scale
            new_rect = pymupdf.Rect(
                cx - new_w_pdf / 2, cy - new_h_pdf / 2,
                cx + new_w_pdf / 2, cy + new_h_pdf / 2,
            )
            page.insert_image(new_rect, stream=buf.getvalue())

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
        font_kwargs = resolve_embeddable_font(
            e.get("tk_font", config.DEFAULT_FONT_NAME), e.get("bold", False), e.get("italic", False),
        )
        page.insert_textbox(
            pymupdf.Rect(e["rect"]), e["text"],
            fontsize=e.get("fontsize", config.EDIT_DEFAULT_FONT_SIZE),
            color=e.get("color", (0, 0, 0)),
            **font_kwargs,
        )

    elif t == "image":
        rotation = e.get("rotation", 0)
        if not rotation:
            page.insert_image(pymupdf.Rect(e["rect"]), filename=e["filepath"])
        else:
            # PyMuPDF's own insert_image rotation only supports 0/90/180/270.
            # For an arbitrary angle, rasterize the rotated image ourselves
            # with Pillow and insert that instead — same idea as the Edit
            # PDF tab's live preview, so save matches what was shown there.
            from PIL import Image

            rect = pymupdf.Rect(e["rect"])
            scale = 4  # render at ~4x the box's point size for print quality
            box_w_px = max(1, round(rect.width * scale))
            box_h_px = max(1, round(rect.height * scale))

            img = Image.open(e["filepath"]).convert("RGBA")
            img.thumbnail((box_w_px, box_h_px))
            rotated = img.rotate(-rotation, expand=True)

            buf = io.BytesIO()
            rotated.save(buf, format="PNG")

            new_w_pdf, new_h_pdf = rotated.width / scale, rotated.height / scale
            cx, cy = (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2
            new_rect = pymupdf.Rect(
                cx - new_w_pdf / 2, cy - new_h_pdf / 2,
                cx + new_w_pdf / 2, cy + new_h_pdf / 2,
            )
            page.insert_image(new_rect, stream=buf.getvalue())
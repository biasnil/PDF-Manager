"""
Script/edit_pdf/constants.py

Mode ids, tool ids, tool groupings, and the small lookup helpers that
translate between the style widgets (color name, font display name) and
the values PyMuPDF/pdf_edit_operations actually want.
"""

from Config import config

ANNOTATE = "annotate"
EDIT = "edit"

# tool ids
T_UNDERLINE = "underline"
T_STRIKEOUT = "strikeout"
T_SQUIGGLY = "squiggly"
T_FREEHAND = "freehand"
T_HIGHLIGHT = "highlight_freehand"
T_FREETEXT = "freetext"
T_INSERT_TEXT = "insert_text_note"
T_REPLACE_TEXT = "replace_text"
T_ERASER = "eraser"
T_SHAPE = "shape"
T_STAMP = "stamp"
T_SIGNATURE = "signature"
T_FILE_ATTACHMENT = "file_attachment"
T_PARAGRAPH = "paragraph"
T_IMAGE = "image"
T_EXISTING_IMAGE = "existing_image"   # an embedded image detected in Edit mode
T_SELECT = "select"

TEXT_SELECT_TOOLS = (T_UNDERLINE, T_STRIKEOUT, T_SQUIGGLY, T_REPLACE_TEXT)
DRAG_RECT_TOOLS = (T_FREETEXT, T_SIGNATURE, T_PARAGRAPH, T_IMAGE, T_SHAPE)
DRAG_PATH_TOOLS = (T_FREEHAND, T_HIGHLIGHT)
CLICK_POINT_TOOLS = (T_INSERT_TEXT, T_STAMP, T_FILE_ATTACHMENT)

# Every placed annotation/edit can be dragged to reposition it EXCEPT
# Replace Text (redaction-anchored, moving it wouldn't mean anything).
# Only Free Text and Add Paragraph also get a resize handle — see
# PdfEditTool.RESIZABLE_TYPES.
MOVABLE_TYPES = (
    T_UNDERLINE, T_STRIKEOUT, T_SQUIGGLY, T_FREEHAND, T_HIGHLIGHT,
    T_FREETEXT, T_PARAGRAPH, T_INSERT_TEXT, T_SHAPE, T_STAMP,
    T_SIGNATURE, T_FILE_ATTACHMENT, T_IMAGE, T_EXISTING_IMAGE,
)

# The quick-pick swatch row (toolbar Style row + the Free Text/Paragraph
# dialog) shows only these 5 presets, plus a 6th "custom color" swatch
# that opens the system color picker — see PdfEditTool._pick_custom_color
# and dialogs.TextBoxDialog._pick_custom_color.
KEPT_SWATCH_NAMES = ("Black", "Red", "Yellow", "Green", "Blue")


def _hex_for(color_name: str) -> str:
    for name, hexval, _rgb in config.ANNOT_COLOR_PALETTE:
        if name == color_name:
            return hexval
    return "#000000"


def _rgb_for(color_name: str) -> tuple:
    for name, _hexval, rgb in config.ANNOT_COLOR_PALETTE:
        if name == color_name:
            return rgb
    return (0, 0, 0)


# Base-14 shorthand codes for each bold/italic combination — the fallback
# used by the Edit-mode style panel's Bold/Italic toggles (and Free Text/
# Add Paragraph) if the real font file can't be found on the machine at
# save time; see Script/pdf_edit_operations.py's font-file resolution.
_BASE14_VARIANTS = {
    "Arial": {(False, False): "helv", (True, False): "hebo",
              (False, True): "heit", (True, True): "hebi"},
    "Calibri": {(False, False): "helv", (True, False): "hebo",
                (False, True): "heit", (True, True): "hebi"},
    "Georgia": {(False, False): "tiro", (True, False): "tibo",
                (False, True): "tiit", (True, True): "tibi"},
}


def _font_code_for(display_name: str, bold: bool = False, italic: bool = False) -> str:
    variants = _BASE14_VARIANTS.get(display_name)
    if variants:
        return variants[(bold, italic)]
    for name, code in config.FONT_CHOICES:
        if name == display_name:
            return code
    return "helv"


def _color_name_for_rgb(rgb):
    """Reverse-lookup: which preset name (if any) matches this rgb. None
    means it doesn't match any preset — i.e. it's a custom color, and the
    caller should prefill the "Custom" swatch with this rgb instead of
    silently mislabeling it as one of the presets."""
    for name, _hexval, c in config.ANNOT_COLOR_PALETTE:
        if all(abs(a - b) < 0.01 for a, b in zip(c, rgb)):
            return name
    return None
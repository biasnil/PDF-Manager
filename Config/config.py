# --------------------------------------------------------------------------
# Window
# --------------------------------------------------------------------------
APP_TITLE = "PDF Toolbox"
APP_GEOMETRY = "1150x800"

# --------------------------------------------------------------------------
# Colors
# --------------------------------------------------------------------------
APP_BG = "#1e1f26"
PANEL_BG = "#262832"
ACCENT = "#e5533c"       
ACCENT_HOVER = "#c94631"
TEXT_MAIN = "#f2f2f2"
TEXT_MUTED = "#9a9ba5"

BUTTON_BG = "#33343e"
INPUT_BG = "#1a1b21"
DESELECTED_BORDER = "#3a3b45"

# --------------------------------------------------------------------------
# Fonts
# --------------------------------------------------------------------------
FONT_FAMILY = "Segoe UI"
FONT_TITLE = (FONT_FAMILY, 18, "bold")
FONT_SIDEBAR_TITLE = (FONT_FAMILY, 15, "bold")
FONT_BODY = (FONT_FAMILY, 10)
FONT_BODY_BOLD = (FONT_FAMILY, 10, "bold")
FONT_SMALL = (FONT_FAMILY, 9)
FONT_SMALL_ITALIC = (FONT_FAMILY, 12, "italic")

# --------------------------------------------------------------------------
# Split tool — thumbnail grid
# --------------------------------------------------------------------------
THUMB_MAX_DIM = 130      # pixel size (longest side) of each page preview
THUMB_GRID_COLS = 5
THUMB_GRID_HEIGHT = 320  # visible height of the scrollable preview area

# --------------------------------------------------------------------------
# Compress tool — quality presets
# --------------------------------------------------------------------------
# Target DPI per preset: lower = smaller file, more visible quality loss.
COMPRESS_QUALITY_PRESETS = {
    "low": 72,
    "recommended": 120,
    "high": 200,
}
COMPRESS_JPEG_QUALITY = 70          # re-encode quality for downsampled images
COMPRESS_ASSUMED_SOURCE_DPI = 150   # heuristic baseline used to compute scale
DEFAULT_COMPRESS_QUALITY = "recommended"

# --------------------------------------------------------------------------
# PDF to PowerPoint — page render scale
# --------------------------------------------------------------------------
# Each PDF page is rendered to an image and placed as a full-slide picture.
# 2.0 = roughly 144 DPI equivalent (72 DPI base * 2), a good balance of
# crispness vs. file size/render time.
PPTX_RENDER_SCALE = 2.0

# --------------------------------------------------------------------------
# Image <-> PDF
# --------------------------------------------------------------------------
# Page sizes in millimeters, given as (width, height) in PORTRAIT orientation.
# "Fit to image" is special-cased: the page is sized to match each image's
# own aspect ratio instead of a fixed physical size.
PAGE_SIZES_MM = {
    "A1": (594, 841),
    "A2": (420, 594),
    "A3": (297, 420),
    "A4": (210, 297),
    "US Letter": (215.9, 279.4),
    "Fit to image": None,
}
DEFAULT_PAGE_SIZE = "A4"

# Assumed DPI used only for "Fit to image", to turn an image's pixel
# dimensions into a physical page size.
IMAGE_ASSUMED_DPI = 96

MARGIN_PRESETS_MM = {
    "No margin": 0,
    "Small": 10,
    "Big": 25,
}
DEFAULT_MARGIN = "No margin"
DEFAULT_CUSTOM_MARGIN_MM = 15

# Live preview box size (pixels) for the Image <-> PDF page-layout preview
IMAGE_PDF_PREVIEW_WIDTH = 150
IMAGE_PDF_PREVIEW_HEIGHT = 190

# PDF to Images
PDF_TO_IMAGE_FORMATS = ["PNG", "JPEG"]
PDF_TO_IMAGE_DPI_PRESETS = {
    "Low (72 DPI)": 72,
    "Medium (150 DPI)": 150,
    "High (300 DPI)": 300,
}
DEFAULT_PDF_TO_IMAGE_DPI = "Medium (150 DPI)"

# --------------------------------------------------------------------------
# Edit PDF (Annotate / Edit modes)
# --------------------------------------------------------------------------
ANNOT_COLOR_PALETTE = [
    ("Black", "#000000", (0.0, 0.0, 0.0)),
    ("Red", "#e5533c", (0.90, 0.32, 0.24)),
    ("Orange", "#f5a623", (0.96, 0.65, 0.14)),
    ("Yellow", "#f7d716", (0.97, 0.84, 0.09)),
    ("Green", "#2ecc71", (0.18, 0.80, 0.44)),
    ("Blue", "#3b82f6", (0.23, 0.51, 0.96)),
    ("Purple", "#a855f7", (0.66, 0.33, 0.97)),
]
DEFAULT_ANNOT_COLOR_NAME = "Red"

STROKE_WIDTH_OPTIONS = [1, 2, 3, 5, 8]
DEFAULT_STROKE_WIDTH = 2

SHAPE_TYPES = ["Rectangle", "Circle", "Line", "Arrow"]
DEFAULT_SHAPE_TYPE = "Rectangle"

STAMP_NAMES = [
    "Approved", "AsIs", "Confidential", "Departmental", "Draft",
    "Experimental", "Expired", "Final", "ForComment", "ForPublicRelease",
    "NotApproved", "NotForPublicRelease", "Sold", "TopSecret",
]
DEFAULT_STAMP_NAME = "Approved"

EDIT_PAGE_RENDER_SCALE = 1.5     # resolution of the main page canvas
EDIT_THUMB_MAX_DIM = 90          # left-hand page-nav thumbnail size
EDIT_DEFAULT_FONT_SIZE = 12
EDIT_HIGHLIGHT_OPACITY = 0.35
EDIT_HIGHLIGHT_WIDTH = 14        # stroke width used for "Free Hand Highlight"

# (display name, PDF font code) — PyMuPDF's base-14 shorthand aliases
FONT_CHOICES = [
    ("Helvetica", "helv"),
    ("Times Roman", "tiro"),
    ("Courier", "cour"),
]
DEFAULT_FONT_NAME = "Helvetica"
FONT_SIZE_OPTIONS = [8, 10, 12, 14, 16, 18, 24, 32, 48]

# a plain click (no drag) with a box-placing tool active uses this default
# size instead of requiring a drag to define the box
EDIT_DEFAULT_BOX_WIDTH = 180   # canvas pixels
EDIT_DEFAULT_BOX_HEIGHT = 40   # canvas pixels
EDIT_CLICK_VS_DRAG_THRESHOLD = 5  # canvas pixels of movement to count as a "drag"

# --------------------------------------------------------------------------
# Organize PDF
# --------------------------------------------------------------------------
ORGANIZE_THUMB_MAX_DIM = 130
ORGANIZE_GRID_COLS = 5
ORGANIZE_GRID_HEIGHT = 420
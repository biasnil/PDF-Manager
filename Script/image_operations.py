from __future__ import annotations

import io
import os
from dataclasses import dataclass
from typing import Callable, Optional

import pymupdf
from PIL import Image

from Config import config

ProgressCallback = Optional[Callable[[int, int, str], None]]
# callback(current_step, total_steps, message) -> None

MM_TO_PT = 72.0 / 25.4


def mm_to_pt(mm: float) -> float:
    return mm * MM_TO_PT


@dataclass
class PageLayout:
    """The computed geometry for placing one image on one page."""
    page_width_pt: float
    page_height_pt: float
    image_rect: tuple[float, float, float, float]  # (x0, y0, x1, y1)


def compute_page_layout(page_size: str, orientation: str,
                         img_width_px: int, img_height_px: int,
                         margin_pt: float) -> PageLayout:
    """
    Work out the page dimensions and where the image should sit on it,
    for the given page size name ("A4", "Fit to image", ...), orientation
    ("portrait"/"landscape"), image pixel size, and margin (in points).
    The image is always "contain"-fit (scaled to fit within the margin,
    aspect ratio preserved) and centered.
    """
    img_aspect = img_width_px / img_height_px if img_height_px else 1.0

    if page_size == "Fit to image":
        img_w_pt = img_width_px / config.IMAGE_ASSUMED_DPI * 72.0
        img_h_pt = img_height_px / config.IMAGE_ASSUMED_DPI * 72.0
        page_w = img_w_pt + 2 * margin_pt
        page_h = img_h_pt + 2 * margin_pt
        rect_w, rect_h = img_w_pt, img_h_pt
    else:
        w_mm, h_mm = config.PAGE_SIZES_MM[page_size]
        short, long_ = min(w_mm, h_mm), max(w_mm, h_mm)
        if orientation == "landscape":
            w_mm, h_mm = long_, short
        else:
            w_mm, h_mm = short, long_
        page_w = mm_to_pt(w_mm)
        page_h = mm_to_pt(h_mm)

        avail_w = max(page_w - 2 * margin_pt, 1.0)
        avail_h = max(page_h - 2 * margin_pt, 1.0)
        avail_aspect = avail_w / avail_h

        if img_aspect > avail_aspect:
            rect_w = avail_w
            rect_h = avail_w / img_aspect
        else:
            rect_h = avail_h
            rect_w = avail_h * img_aspect

    x0 = (page_w - rect_w) / 2
    y0 = (page_h - rect_h) / 2
    return PageLayout(page_w, page_h, (x0, y0, x0 + rect_w, y0 + rect_h))


class ImageToPdfConverter:
    """Places one or more images onto PDF pages, laid out per the chosen
    page size / orientation / margin."""

    @staticmethod
    def convert(image_paths: list[str], output_path_or_dir: str,
                page_size: str = config.DEFAULT_PAGE_SIZE,
                orientation: str = "portrait",
                margin_mm: float = 0,
                merge: bool = True,
                progress: ProgressCallback = None) -> list[str]:
        """
        merge=True:  output_path_or_dir is a file path; every image becomes
                     one page in a single output PDF. Returns [that path].
        merge=False: output_path_or_dir is a folder; each image becomes its
                     own single-page PDF. Returns all created paths.
        """
        if not image_paths:
            raise ValueError("No images provided.")

        margin_pt = mm_to_pt(margin_mm)
        total = len(image_paths)

        if merge:
            doc = pymupdf.open()
            for i, path in enumerate(image_paths, start=1):
                if progress:
                    progress(i, total, f"Adding {os.path.basename(path)}")
                ImageToPdfConverter._add_page(doc, path, page_size, orientation, margin_pt)
            doc.save(output_path_or_dir)
            doc.close()
            return [output_path_or_dir]

        else:
            os.makedirs(output_path_or_dir, exist_ok=True)
            created = []
            for i, path in enumerate(image_paths, start=1):
                if progress:
                    progress(i, total, f"Converting {os.path.basename(path)}")
                doc = pymupdf.open()
                ImageToPdfConverter._add_page(doc, path, page_size, orientation, margin_pt)
                base_name = os.path.splitext(os.path.basename(path))[0]
                out_path = os.path.join(output_path_or_dir, f"{base_name}.pdf")
                doc.save(out_path)
                doc.close()
                created.append(out_path)
            return created

    @staticmethod
    def _add_page(doc: "pymupdf.Document", image_path: str, page_size: str,
                  orientation: str, margin_pt: float) -> None:
        png_bytes, w_px, h_px = ImageToPdfConverter._load_as_png(image_path)
        layout = compute_page_layout(page_size, orientation, w_px, h_px, margin_pt)
        page = doc.new_page(width=layout.page_width_pt, height=layout.page_height_pt)
        page.insert_image(pymupdf.Rect(*layout.image_rect), stream=png_bytes)

    @staticmethod
    def _load_as_png(image_path: str) -> tuple[bytes, int, int]:
        """Open any Pillow-supported image, normalize to RGB, and return
        PNG bytes + pixel size — guarantees PyMuPDF can embed it regardless
        of the original format (webp, gif, bmp, etc.)."""
        with Image.open(image_path) as img:
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue(), img.width, img.height


class PdfToImageConverter:
    """Renders each page of a PDF to its own image file."""

    @staticmethod
    def convert(input_path: str, output_dir: str,
                image_format: str = "PNG", dpi: int = 150,
                progress: ProgressCallback = None) -> list[str]:
        os.makedirs(output_dir, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(input_path))[0]
        ext = "jpg" if image_format.upper() == "JPEG" else "png"
        created = []

        with pymupdf.open(input_path) as doc:
            total = doc.page_count
            pad = len(str(total))
            scale = dpi / 72.0
            for i in range(total):
                if progress:
                    progress(i + 1, total, f"Rendering page {i + 1}/{total}")
                page = doc[i]
                pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale))
                out_path = os.path.join(
                    output_dir, f"{base_name}_page_{str(i + 1).zfill(pad)}.{ext}"
                )
                if ext == "jpg":
                    # get_pixmap has no alpha here, but be explicit for JPEG
                    pix.save(out_path, output="jpeg")
                else:
                    pix.save(out_path)
                created.append(out_path)

        return created
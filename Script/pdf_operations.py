from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Optional

import pymupdf  # PyMuPDF

from Config import config

ProgressCallback = Optional[Callable[[int, int, str], None]]
# callback(current_step, total_steps, message) -> None


class PDFMerger:
    """Combines multiple PDFs into one, in the given order."""

    @staticmethod
    def merge(input_paths: list[str], output_path: str,
              progress: ProgressCallback = None) -> str:
        if not input_paths:
            raise ValueError("No input files provided.")

        merged = pymupdf.open()
        total = len(input_paths)

        for i, path in enumerate(input_paths, start=1):
            if progress:
                progress(i, total, f"Adding {os.path.basename(path)}")
            with pymupdf.open(path) as src:
                merged.insert_pdf(src)

        merged.save(output_path)
        merged.close()
        return output_path


class PDFSplitter:
    """Everything to do with previewing and splitting a PDF's pages."""

    @staticmethod
    def get_page_count(input_path: str) -> int:
        with pymupdf.open(input_path) as doc:
            return doc.page_count

    @staticmethod
    def generate_thumbnails(input_path: str, max_dim: int = config.THUMB_MAX_DIM,
                             progress: ProgressCallback = None) -> list[bytes]:
        """
        Render a low-res PNG thumbnail of every page, for use as a visual
        preview (tk.PhotoImage(data=...) loads PNG bytes directly — no
        Pillow required). Returns one PNG per page, in page order.
        """
        thumbnails = []
        with pymupdf.open(input_path) as doc:
            total = doc.page_count
            for i in range(total):
                if progress:
                    progress(i + 1, total, f"Rendering preview {i + 1}/{total}")
                page = doc[i]
                rect = page.rect
                longest_side = max(rect.width, rect.height) or 1
                scale = max_dim / longest_side
                pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale))
                thumbnails.append(pix.tobytes("png"))
        return thumbnails

    @staticmethod
    def split_every_page(input_path: str, output_dir: str,
                          progress: ProgressCallback = None) -> list[str]:
        """Split a PDF into one file per page. Returns the created paths."""
        os.makedirs(output_dir, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(input_path))[0]
        created = []

        with pymupdf.open(input_path) as src:
            total = src.page_count
            pad = len(str(total))
            for i in range(total):
                if progress:
                    progress(i + 1, total, f"Extracting page {i + 1}")
                out = pymupdf.open()
                out.insert_pdf(src, from_page=i, to_page=i)
                out_path = os.path.join(
                    output_dir, f"{base_name}_page_{str(i + 1).zfill(pad)}.pdf"
                )
                out.save(out_path)
                out.close()
                created.append(out_path)

        return created

    @staticmethod
    def split_by_ranges(input_path: str, output_dir: str,
                         ranges: list[tuple[int, int]],
                         progress: ProgressCallback = None) -> list[str]:
        """
        Split by custom page ranges.
        ranges: list of (start_page, end_page), 1-indexed, inclusive.
        """
        os.makedirs(output_dir, exist_ok=True)
        base_name = os.path.splitext(os.path.basename(input_path))[0]
        created = []

        with pymupdf.open(input_path) as src:
            page_count = src.page_count
            total = len(ranges)
            for i, (start, end) in enumerate(ranges, start=1):
                if start < 1 or end > page_count or start > end:
                    raise ValueError(
                        f"Invalid range {start}-{end} for a {page_count}-page PDF."
                    )
                if progress:
                    progress(i, total, f"Extracting pages {start}-{end}")
                out = pymupdf.open()
                out.insert_pdf(src, from_page=start - 1, to_page=end - 1)
                out_path = os.path.join(output_dir, f"{base_name}_{start}-{end}.pdf")
                out.save(out_path)
                out.close()
                created.append(out_path)

        return created

    @staticmethod
    def split_selected_pages(input_path: str, output_dir_or_file: str,
                              selected_pages: list[int], mode: str = "separate",
                              progress: ProgressCallback = None) -> list[str]:
        """
        Extract only the given pages (1-indexed, any order — sorted first).

        mode="separate": output_dir_or_file is a folder; one PDF per page.
        mode="single":   output_dir_or_file is a file path; one PDF with
                          all selected pages, in ascending order.
        """
        if not selected_pages:
            raise ValueError("No pages selected.")
        pages = sorted(set(selected_pages))

        with pymupdf.open(input_path) as src:
            page_count = src.page_count
            for p in pages:
                if p < 1 or p > page_count:
                    raise ValueError(
                        f"Page {p} is out of range (PDF has {page_count} pages)."
                    )

            if mode == "single":
                if progress:
                    progress(0, 1, f"Building PDF with {len(pages)} page(s)")
                out = pymupdf.open()
                for p in pages:
                    out.insert_pdf(src, from_page=p - 1, to_page=p - 1)
                out.save(output_dir_or_file)
                out.close()
                if progress:
                    progress(1, 1, "Done")
                return [output_dir_or_file]

            elif mode == "separate":
                os.makedirs(output_dir_or_file, exist_ok=True)
                base_name = os.path.splitext(os.path.basename(input_path))[0]
                pad = len(str(page_count))
                created = []
                total = len(pages)
                for i, p in enumerate(pages, start=1):
                    if progress:
                        progress(i, total, f"Extracting page {p}")
                    out = pymupdf.open()
                    out.insert_pdf(src, from_page=p - 1, to_page=p - 1)
                    out_path = os.path.join(
                        output_dir_or_file, f"{base_name}_page_{str(p).zfill(pad)}.pdf"
                    )
                    out.save(out_path)
                    out.close()
                    created.append(out_path)
                return created

            else:
                raise ValueError('mode must be "separate" or "single"')


@dataclass
class CompressResult:
    output_path: str
    original_bytes: int
    compressed_bytes: int

    @property
    def percent_saved(self) -> float:
        if self.original_bytes == 0:
            return 0.0
        return (1 - self.compressed_bytes / self.original_bytes) * 100


class PDFOrganizer:
    """Reorders and/or removes pages in a PDF — the engine behind the
    Organize PDF tool's thumbnail grid."""

    @staticmethod
    def reorder_and_save(input_path: str, output_path: str,
                          page_order: list[int],
                          progress: ProgressCallback = None) -> str:
        """
        page_order: the ORIGINAL (0-indexed) page numbers, in the new
        order you want them saved in. Omit a page number to drop that
        page entirely.
        """
        if not page_order:
            raise ValueError("No pages to save — the document would be empty.")

        if progress:
            progress(0, 1, "Reordering pages...")

        doc = pymupdf.open(input_path)
        doc.select(page_order)
        doc.save(output_path)
        doc.close()

        if progress:
            progress(1, 1, "Done")
        return output_path


class PDFCompressor:
    """Shrinks a PDF by downsampling/re-encoding its embedded images."""

    @staticmethod
    def compress(input_path: str, output_path: str,
                 quality: str = config.DEFAULT_COMPRESS_QUALITY,
                 progress: ProgressCallback = None) -> CompressResult:
        """
        Text and vector content are left untouched — only raster images are
        recompressed, so text-heavy PDFs may not shrink much. That's expected.
        """
        presets = config.COMPRESS_QUALITY_PRESETS
        if quality not in presets:
            raise ValueError(f"quality must be one of {list(presets)}")

        target_dpi = presets[quality]
        original_bytes = os.path.getsize(input_path)

        doc = pymupdf.open(input_path)
        total = doc.page_count

        for page_index in range(total):
            if progress:
                progress(page_index + 1, total, f"Compressing page {page_index + 1}")
            page = doc[page_index]
            for img in page.get_images(full=True):
                xref = img[0]
                PDFCompressor._recompress_image(doc, xref, target_dpi)

        doc.save(output_path, deflate=True, garbage=4, clean=True)
        doc.close()

        compressed_bytes = os.path.getsize(output_path)
        return CompressResult(output_path, original_bytes, compressed_bytes)

    @staticmethod
    def _recompress_image(doc: "pymupdf.Document", xref: int, target_dpi: int) -> None:
        """Downsample and re-encode one embedded image as JPEG in place, if
        it's currently larger than the target size. Silently skips images
        it can't safely touch (transparency, non-RGB/gray colorspaces)."""
        try:
            base = doc.extract_image(xref)
        except Exception:
            return

        if base.get("smask") or base.get("colorspace_n", 3) not in (1, 3):
            return  # skip images with transparency or non-RGB/gray colorspaces

        width, height = base["width"], base["height"]
        # Heuristic: assume a typical embedded-image DPI, since the true
        # print DPI isn't stored per-image.
        scale = min(1.0, target_dpi / config.COMPRESS_ASSUMED_SOURCE_DPI)
        new_w, new_h = max(1, int(width * scale)), max(1, int(height * scale))

        if new_w >= width and new_h >= height:
            return  # nothing to gain by "upscaling"

        try:
            pix = pymupdf.Pixmap(base["image"])
            if pix.n - pix.alpha >= 4:  # CMYK -> RGB
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            pix = pymupdf.Pixmap(pix, new_w, new_h)
            new_bytes = pix.tobytes("jpg", jpg_quality=config.COMPRESS_JPEG_QUALITY)
            doc.update_stream(xref, new_bytes)
        except Exception:
            return  # leave the original image untouched on any failure
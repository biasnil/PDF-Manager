import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from Config import config
from Script.base_tool import BaseTool
from Script.image_operations import ImageToPdfConverter, PdfToImageConverter, compute_page_layout, mm_to_pt
from Script.threading_utils import run_in_background, threadsafe_progress
from Script.widgets import FileListPanel, PagePreview, ProgressRow

IMAGES_TO_PDF = "images_to_pdf"
PDF_TO_IMAGES = "pdf_to_images"

IMAGE_FILETYPES = [("Image files", "*.jpg *.jpeg *.png *.webp *.bmp *.gif *.tiff")]
PDF_FILETYPES = [("PDF files", "*.pdf")]

MARGIN_CHOICES = ["No margin", "Small", "Big", "Custom"]


class ImagePdfTool(BaseTool):
    title = "Image <-> PDF"
    description = (
        "Convert images to a PDF — choose page size, orientation, and margin, "
        "with a live preview — or split a PDF back into one image per page."
    )

    def __init__(self, master, root):
        super().__init__(master, root)

        mode_row = tk.Frame(self.body, bg=config.PANEL_BG)
        mode_row.pack(fill=tk.X, pady=(0, 10))
        self.mode_var = tk.StringVar(value=IMAGES_TO_PDF)
        tk.Radiobutton(
            mode_row, text="Images to PDF", variable=self.mode_var, value=IMAGES_TO_PDF,
            bg=config.PANEL_BG, fg=config.TEXT_MAIN, selectcolor=config.INPUT_BG,
            activebackground=config.PANEL_BG, activeforeground=config.TEXT_MAIN,
            font=config.FONT_BODY, command=self._on_mode_change,
        ).pack(side=tk.LEFT)
        tk.Radiobutton(
            mode_row, text="PDF to Images", variable=self.mode_var, value=PDF_TO_IMAGES,
            bg=config.PANEL_BG, fg=config.TEXT_MAIN, selectcolor=config.INPUT_BG,
            activebackground=config.PANEL_BG, activeforeground=config.TEXT_MAIN,
            font=config.FONT_BODY, command=self._on_mode_change,
        ).pack(side=tk.LEFT, padx=(16, 0))

        self.images_to_pdf_frame = tk.Frame(self.body, bg=config.PANEL_BG)
        self.pdf_to_images_frame = tk.Frame(self.body, bg=config.PANEL_BG)
        self._build_images_to_pdf(self.images_to_pdf_frame)
        self._build_pdf_to_images(self.pdf_to_images_frame)

        self._show_mode(IMAGES_TO_PDF)
        self._refresh_preview()

    # ==================================================================
    # Images -> PDF
    # ==================================================================

    def _build_images_to_pdf(self, parent):
        self.file_panel_img = FileListPanel(parent, multiple=True, filetypes=IMAGE_FILETYPES)
        self.file_panel_img.on_change = lambda paths: self._refresh_preview()
        self.file_panel_img.pack(fill=tk.BOTH, expand=True)

        options_row = tk.Frame(parent, bg=config.PANEL_BG)
        options_row.pack(fill=tk.X, pady=(12, 0))

        left_col = tk.Frame(options_row, bg=config.PANEL_BG)
        left_col.pack(side=tk.LEFT, fill=tk.Y, anchor="n")

        right_col = tk.Frame(options_row, bg=config.PANEL_BG)
        right_col.pack(side=tk.RIGHT, anchor="n", padx=(16, 0))

        # -- page orientation --
        tk.Label(
            left_col, text="Page orientation", bg=config.PANEL_BG, fg=config.TEXT_MAIN,
            font=config.FONT_BODY_BOLD, anchor="w",
        ).pack(fill=tk.X)
        orient_row = tk.Frame(left_col, bg=config.PANEL_BG)
        orient_row.pack(fill=tk.X, pady=(4, 10))
        self.orientation_var = tk.StringVar(value="portrait")
        self.portrait_btn = self._make_choice_button(
            orient_row, "Portrait", lambda: self._set_orientation("portrait")
        )
        self.portrait_btn.pack(side=tk.LEFT)
        self.landscape_btn = self._make_choice_button(
            orient_row, "Landscape", lambda: self._set_orientation("landscape")
        )
        self.landscape_btn.pack(side=tk.LEFT, padx=(6, 0))

        # -- page size --
        tk.Label(
            left_col, text="Page size", bg=config.PANEL_BG, fg=config.TEXT_MAIN,
            font=config.FONT_BODY_BOLD, anchor="w",
        ).pack(fill=tk.X)
        self.page_size_var = tk.StringVar(value=config.DEFAULT_PAGE_SIZE)
        size_combo = ttk.Combobox(
            left_col, textvariable=self.page_size_var, state="readonly", width=16,
            values=list(config.PAGE_SIZES_MM.keys()),
        )
        size_combo.pack(anchor="w", pady=(4, 10))
        size_combo.bind("<<ComboboxSelected>>", lambda e: self._on_page_size_change())

        # -- margin --
        tk.Label(
            left_col, text="Margin", bg=config.PANEL_BG, fg=config.TEXT_MAIN,
            font=config.FONT_BODY_BOLD, anchor="w",
        ).pack(fill=tk.X)
        margin_row = tk.Frame(left_col, bg=config.PANEL_BG)
        margin_row.pack(fill=tk.X, pady=(4, 4))
        self.margin_var = tk.StringVar(value=config.DEFAULT_MARGIN)
        self.margin_buttons = {}
        for choice in MARGIN_CHOICES:
            btn = self._make_choice_button(margin_row, choice, lambda c=choice: self._set_margin(c))
            btn.pack(side=tk.LEFT, padx=(0 if choice == MARGIN_CHOICES[0] else 6, 0))
            self.margin_buttons[choice] = btn

        self.custom_margin_var = tk.StringVar(value=str(config.DEFAULT_CUSTOM_MARGIN_MM))
        self.custom_margin_row = tk.Frame(left_col, bg=config.PANEL_BG)
        tk.Label(
            self.custom_margin_row, text="Custom margin (mm):", bg=config.PANEL_BG,
            fg=config.TEXT_MUTED, font=config.FONT_SMALL,
        ).pack(side=tk.LEFT)
        custom_entry = tk.Entry(
            self.custom_margin_row, textvariable=self.custom_margin_var, width=6,
            bg=config.INPUT_BG, fg=config.TEXT_MAIN, insertbackground=config.TEXT_MAIN,
            relief=tk.FLAT, font=config.FONT_SMALL,
        )
        custom_entry.pack(side=tk.LEFT, padx=(6, 0))
        custom_entry.bind("<KeyRelease>", lambda e: self._refresh_preview())

        # -- merge checkbox --
        self.merge_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            left_col, text="Merge all images into one PDF file", variable=self.merge_var,
            bg=config.PANEL_BG, fg=config.TEXT_MAIN, selectcolor=config.INPUT_BG,
            activebackground=config.PANEL_BG, activeforeground=config.TEXT_MAIN,
            font=config.FONT_SMALL,
        ).pack(anchor="w", pady=(10, 0))

        # -- preview --
        tk.Label(
            right_col, text="Preview", bg=config.PANEL_BG, fg=config.TEXT_MUTED,
            font=config.FONT_SMALL,
        ).pack()
        self.preview = PagePreview(right_col)
        self.preview.pack(pady=(4, 0))

        self.progress_images = ProgressRow(parent, "Convert to PDF", self.run_images_to_pdf)
        self.progress_images.pack(fill=tk.X, pady=(14, 0))

        self._update_choice_buttons(
            {self.portrait_btn: True, self.landscape_btn: False}
        )
        self._update_choice_buttons(
            {btn: (name == config.DEFAULT_MARGIN) for name, btn in self.margin_buttons.items()}
        )

    def _make_choice_button(self, parent, text, command):
        return tk.Button(
            parent, text=text, command=command,
            bg=config.BUTTON_BG, fg=config.TEXT_MAIN, relief=tk.FLAT, bd=0,
            padx=10, pady=5, font=config.FONT_SMALL, cursor="hand2",
        )

    def _update_choice_buttons(self, button_selected_map):
        for btn, selected in button_selected_map.items():
            btn.config(bg=config.ACCENT if selected else config.BUTTON_BG)

    def _set_orientation(self, value):
        self.orientation_var.set(value)
        self._update_choice_buttons({
            self.portrait_btn: value == "portrait",
            self.landscape_btn: value == "landscape",
        })
        self._refresh_preview()

    def _set_margin(self, value):
        self.margin_var.set(value)
        self._update_choice_buttons(
            {btn: (name == value) for name, btn in self.margin_buttons.items()}
        )
        if value == "Custom":
            self.custom_margin_row.pack(fill=tk.X, pady=(4, 0))
        else:
            self.custom_margin_row.pack_forget()
        self._refresh_preview()

    def _on_page_size_change(self):
        is_fit = self.page_size_var.get() == "Fit to image"
        state = tk.DISABLED if is_fit else tk.NORMAL
        cursor = "arrow" if is_fit else "hand2"
        self.portrait_btn.config(state=state, cursor=cursor)
        self.landscape_btn.config(state=state, cursor=cursor)
        if is_fit:
            # orientation has no effect while "Fit to image" is active —
            # gray both out instead of leaving one looking "selected"
            self.portrait_btn.config(bg=config.DESELECTED_BORDER)
            self.landscape_btn.config(bg=config.DESELECTED_BORDER)
        else:
            value = self.orientation_var.get()
            self._update_choice_buttons({
                self.portrait_btn: value == "portrait",
                self.landscape_btn: value == "landscape",
            })
        self._refresh_preview()

    def _current_margin_mm(self) -> float:
        choice = self.margin_var.get()
        if choice == "Custom":
            try:
                return max(0.0, float(self.custom_margin_var.get()))
            except ValueError:
                return 0.0
        return config.MARGIN_PRESETS_MM[choice]

    def _current_image_pixel_size(self) -> tuple[int, int]:
        if self.file_panel_img.paths:
            try:
                from PIL import Image
                with Image.open(self.file_panel_img.paths[0]) as img:
                    return img.width, img.height
            except Exception:
                pass
        return (3, 4)  # placeholder portrait-ish aspect when nothing's loaded yet

    def _refresh_preview(self):
        w_px, h_px = self._current_image_pixel_size()
        margin_pt = mm_to_pt(self._current_margin_mm())
        layout = compute_page_layout(
            self.page_size_var.get(), self.orientation_var.get(), w_px, h_px, margin_pt
        )
        self.preview.update_preview(layout.page_width_pt, layout.page_height_pt, layout.image_rect)

    def run_images_to_pdf(self):
        paths = self.file_panel_img.paths
        if not paths:
            messagebox.showwarning("Image <-> PDF", "Add at least one image.")
            return

        merge = self.merge_var.get()
        if merge:
            target = filedialog.asksaveasfilename(
                defaultextension=".pdf", initialfile="images.pdf",
                filetypes=[("PDF files", "*.pdf")],
            )
        else:
            target = filedialog.askdirectory(title="Choose a folder for the converted PDFs")
        if not target:
            return

        page_size = self.page_size_var.get()
        orientation = self.orientation_var.get()
        margin_mm = self._current_margin_mm()

        self.progress_images.set_running(True)

        def work():
            return ImageToPdfConverter.convert(
                paths, target, page_size=page_size, orientation=orientation,
                margin_mm=margin_mm, merge=merge,
                progress=threadsafe_progress(self.root, self.progress_images),
            )

        def done(result):
            self.progress_images.set_running(False)
            self.progress_images.reset()
            if merge:
                # One resulting PDF — Print can target it directly.
                self.progress_images.set_output_path(result[0])
                messagebox.showinfo("Image <-> PDF", f"Saved PDF to:\n{result[0]}")
            else:
                # Several separate PDFs — nothing single for Print to
                # target, so leave it disabled.
                self.progress_images.set_output_path(None)
                messagebox.showinfo(
                    "Image <-> PDF", f"Created {len(result)} PDF(s) in:\n{target}"
                )

        def error(e):
            self.progress_images.set_running(False)
            self.progress_images.reset()
            messagebox.showerror("Image <-> PDF", f"Something went wrong:\n{e}")

        run_in_background(self.root, work, done, error)

    # ==================================================================
    # PDF -> Images
    # ==================================================================

    def _build_pdf_to_images(self, parent):
        self.file_panel_pdf = FileListPanel(parent, multiple=False, filetypes=PDF_FILETYPES)
        self.file_panel_pdf.listbox.config(height=4)
        self.file_panel_pdf.pack(fill=tk.X)

        opts_row = tk.Frame(parent, bg=config.PANEL_BG)
        opts_row.pack(fill=tk.X, pady=(12, 0))

        tk.Label(
            opts_row, text="Image format:", bg=config.PANEL_BG, fg=config.TEXT_MAIN,
            font=config.FONT_BODY,
        ).pack(side=tk.LEFT)
        self.format_var = tk.StringVar(value=config.PDF_TO_IMAGE_FORMATS[0])
        ttk.Combobox(
            opts_row, textvariable=self.format_var, state="readonly", width=8,
            values=config.PDF_TO_IMAGE_FORMATS,
        ).pack(side=tk.LEFT, padx=(6, 20))

        tk.Label(
            opts_row, text="Resolution:", bg=config.PANEL_BG, fg=config.TEXT_MAIN,
            font=config.FONT_BODY,
        ).pack(side=tk.LEFT)
        self.dpi_var = tk.StringVar(value=config.DEFAULT_PDF_TO_IMAGE_DPI)
        ttk.Combobox(
            opts_row, textvariable=self.dpi_var, state="readonly", width=16,
            values=list(config.PDF_TO_IMAGE_DPI_PRESETS.keys()),
        ).pack(side=tk.LEFT, padx=(6, 0))

        self.progress_pdf = ProgressRow(parent, "Convert to Images", self.run_pdf_to_images)
        self.progress_pdf.pack(fill=tk.X, pady=(14, 0))

    def run_pdf_to_images(self):
        if not self.file_panel_pdf.paths:
            messagebox.showwarning("Image <-> PDF", "Add a PDF file to convert.")
            return
        input_path = self.file_panel_pdf.paths[0]
        out_dir = filedialog.askdirectory(title="Choose a folder for the images")
        if not out_dir:
            return

        image_format = self.format_var.get()
        dpi = config.PDF_TO_IMAGE_DPI_PRESETS[self.dpi_var.get()]

        self.progress_pdf.set_running(True)

        def work():
            return PdfToImageConverter.convert(
                input_path, out_dir, image_format=image_format, dpi=dpi,
                progress=threadsafe_progress(self.root, self.progress_pdf),
            )

        def done(result):
            self.progress_pdf.set_running(False)
            self.progress_pdf.reset()
            messagebox.showinfo(
                "Image <-> PDF", f"Created {len(result)} image(s) in:\n{out_dir}"
            )

        def error(e):
            self.progress_pdf.set_running(False)
            self.progress_pdf.reset()
            messagebox.showerror("Image <-> PDF", f"Something went wrong:\n{e}")

        run_in_background(self.root, work, done, error)

    # ==================================================================
    # mode switching
    # ==================================================================

    def _on_mode_change(self):
        self._show_mode(self.mode_var.get())

    def _show_mode(self, mode):
        if mode == IMAGES_TO_PDF:
            self.pdf_to_images_frame.pack_forget()
            self.images_to_pdf_frame.pack(fill=tk.BOTH, expand=True)
        else:
            self.images_to_pdf_frame.pack_forget()
            self.pdf_to_images_frame.pack(fill=tk.BOTH, expand=True)
"""
pdf_preview.py
--------------
Rasterises a generated PDF so the app can show a true live preview and export
the same artwork as PNG / JPG.

The PDF stays the master format - images are rendered *from* the PDF, so the
preview is exactly what gets printed (no second layout engine to drift).

Rendering uses ``pypdfium2`` (PDFium, BSD/Apache licensed - compatible with
this project's MIT licence). It is an optional dependency: when it is missing
every function degrades gracefully and :data:`AVAILABLE` is ``False`` so the UI
can offer to open the PDF externally instead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

try:  # pragma: no cover - environment dependent
    import pypdfium2 as pdfium
    AVAILABLE = True
except Exception:  # pragma: no cover
    pdfium = None  # type: ignore
    AVAILABLE = False

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None  # type: ignore

# A4 landscape at 72 dpi, used to reason about scale factors.
BASE_DPI = 72
# Print-quality raster export (A4 landscape at 300 dpi is 3508 x 2481).
EXPORT_DPI = 300
# Exports are never smaller than "2.5K" on the long edge, whatever dpi is
# requested, so a certificate is always usable at large sizes.
MIN_EXPORT_LONG_EDGE = 2560
# Ceiling for on-screen preview rasters, to keep memory sane.
MAX_PREVIEW_PIXELS = 4000

UNAVAILABLE_MESSAGE = (
    "Image rendering needs the 'pypdfium2' package.\n"
    "Install it with:  pip install pypdfium2")


def render_page(pdf_path: Path, page_index: int = 0,
                scale: float = 2.0) -> Optional["Image.Image"]:
    """Render one PDF page to a Pillow image, or ``None`` when unavailable.

    ``scale`` is relative to 72 dpi, so 2.0 renders at 144 dpi.
    """
    if not AVAILABLE or Image is None:
        return None
    pdf = None
    try:
        pdf = pdfium.PdfDocument(str(pdf_path))
        page = pdf[page_index]
        bitmap = page.render(scale=scale)
        image = bitmap.to_pil().convert("RGB")
        return image
    except Exception:
        return None
    finally:
        try:
            if pdf is not None:
                pdf.close()
        except Exception:
            pass


def render_page_fit(pdf_path: Path, max_width: int, max_height: int,
                    page_index: int = 0, supersample: float = 2.0
                    ) -> Optional["Image.Image"]:
    """Render a page to fit ``max_width`` x ``max_height`` *device* pixels.

    ``max_width``/``max_height`` are real screen pixels, and the page is
    rendered ``supersample``x larger than that so the downscale to display size
    produces sharp text instead of a blurry upscale.
    """
    if not AVAILABLE or Image is None:
        return None
    if max_width <= 0 or max_height <= 0:
        return None
    size = page_size(pdf_path, page_index)
    if size is None:
        return None
    width_pt, height_pt = size
    scale = min(max_width / width_pt, max_height / height_pt)
    scale = max(scale, 0.05) * max(supersample, 1.0)
    # Keep the raster within a sane ceiling on very large displays.
    longest_pt = max(width_pt, height_pt)
    scale = min(scale, MAX_PREVIEW_PIXELS / longest_pt)
    return render_page(pdf_path, page_index, scale=scale)


def fit_size(image_size, box_width: int, box_height: int):
    """Largest (w, h) with ``image_size``'s aspect that fits the given box."""
    iw, ih = image_size
    if iw <= 0 or ih <= 0 or box_width <= 0 or box_height <= 0:
        return (max(box_width, 1), max(box_height, 1))
    aspect = iw / ih
    if box_width / box_height > aspect:
        height = box_height
        width = aspect * height
    else:
        width = box_width
        height = width / aspect
    return (max(int(round(width)), 1), max(int(round(height)), 1))


def page_size(pdf_path: Path, page_index: int = 0) -> Optional[Tuple[float, float]]:
    """Return the page size in points, or ``None`` when unavailable."""
    if not AVAILABLE:
        return None
    pdf = None
    try:
        pdf = pdfium.PdfDocument(str(pdf_path))
        page = pdf[page_index]
        return float(page.get_width()), float(page.get_height())
    except Exception:
        return None
    finally:
        try:
            if pdf is not None:
                pdf.close()
        except Exception:
            pass


def export_image(pdf_path: Path, out_path: Path, fmt: str = "PNG",
                 dpi: int = EXPORT_DPI, page_index: int = 0,
                 min_long_edge: int = MIN_EXPORT_LONG_EDGE) -> Path:
    """Export a PDF page as a high-resolution PNG or JPG.

    The raster is at least ``min_long_edge`` pixels on its long side even if a
    low ``dpi`` is requested, so exported certificates are never soft.

    Raises ``RuntimeError`` when no rasteriser is installed so callers can show
    an actionable message.
    """
    if not AVAILABLE or Image is None:
        raise RuntimeError(UNAVAILABLE_MESSAGE)
    scale = dpi / BASE_DPI
    size = page_size(pdf_path, page_index)
    if size and min_long_edge:
        longest_pt = max(size)
        if longest_pt > 0:
            scale = max(scale, min_long_edge / longest_pt)
    image = render_page(pdf_path, page_index, scale=scale)
    if image is None:
        raise RuntimeError(f"Could not render {pdf_path.name} to an image.")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = fmt.upper()
    if fmt in ("JPG", "JPEG"):
        image.convert("RGB").save(out_path, "JPEG", quality=95, dpi=(dpi, dpi),
                                  optimize=True, subsampling=0)
    else:
        image.save(out_path, "PNG", dpi=(dpi, dpi), optimize=True)
    return out_path

"""The adversarial PDFs — documents that exist only to prove an edge case is handled.

* **hybrid (E12)** — a digital, text-layer cover letter followed by a scanned annex, in one
  file. This is the document that makes "flavour is per page, not per document" concrete: the
  parser must report `rendering=MIXED` with page 1 `DIGITAL` and pages 2+ `SCANNED`.
* **encrypted (E7)** — password-protected. Must be detected at open time, recorded as
  `PARSE_FAILED` with a reason, and the *message* still classified from its body rather than
  vanishing from the queue.
* **image-only attachment (E6)** — a bare JPEG of a damaged blister pack, which gets the same
  cheap vision description as an embedded image.
* **corrupt / zero-byte (E7)** — truncated bytes that are not a readable PDF at all.

Everything here is built with PyMuPDF so no extra dependency is introduced.
"""

from __future__ import annotations

import io
import random
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw, ImageFilter

from .pdf_scanned import DPI, ScanStyle, page_images


def build_hybrid_pdf(
    path: Path,
    digital_source: Path,
    scanned_pages_text: list[str],
    *,
    seed: int,
    style: ScanStyle | None = None,
) -> Path:
    """Digital cover page(s) from `digital_source`, then image-only annex pages (E12)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    style = style or ScanStyle.handwritten()

    out = pymupdf.open()

    # 1. the digital part, text layer intact
    with pymupdf.open(str(digital_source)) as src:
        out.insert_pdf(src, from_page=0, to_page=0)

    # 2. the scanned annex, as images with no text layer
    for image in page_images(scanned_pages_text, style=style, seed=seed,
                             heading="ANNEX — completed by hand at the clinic"):
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=style.jpeg_quality)
        buffer.seek(0)
        width_pt = image.width * 72.0 / DPI
        height_pt = image.height * 72.0 / DPI
        page = out.new_page(width=width_pt, height=height_pt)
        page.insert_image(pymupdf.Rect(0, 0, width_pt, height_pt), stream=buffer.getvalue())

    out.set_metadata({
        "title": "Adverse event report with handwritten annex",
        "subject": "SYNTHETIC TEST DOCUMENT",
    })
    out.save(str(path), garbage=3, deflate=True)
    out.close()
    return path


def build_encrypted_pdf(path: Path, source: Path, password: str = "clinevo2026") -> Path:
    """Re-save `source` with a user password, so it cannot be opened without one (E7)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(str(source)) as doc:
        doc.save(
            str(path),
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            owner_pw=password + "-owner",
            user_pw=password,
            permissions=pymupdf.PDF_PERM_ACCESSIBILITY,
        )
    return path


def build_corrupt_pdf(path: Path, source: Path) -> Path:
    """A file that starts with %PDF- but is not a readable document (E7).

    Magic-byte sniffing will correctly call it a PDF; only opening it reveals the damage, which
    is exactly the distinction the parse-failure path has to make.

    Simple truncation is not enough: PyMuPDF rebuilds a missing cross-reference table and
    happily returns a blank page, which would test nothing. So the header is kept, the object
    bodies are taken from the middle of a real file (no `obj` boundaries intact), and both the
    xref table and the trailer are omitted entirely. The result is unrecoverable by design.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = source.read_bytes()
    middle = data[len(data) // 3: len(data) // 3 + 2048]
    path.write_bytes(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + middle)
    return path


def build_defect_photo(path: Path, seed: int = 7) -> Path:
    """A photo-like JPEG of a damaged blister pack, for the PQC attachment path (E6, E19).

    Drawn rather than photographed: there is no real product here, and a synthetic image keeps
    the "no real data" guarantee total. It has enough colour variance to survive the
    meaningful-image filter, which a flat logo would not.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)

    w, h = 900, 640
    image = Image.new("RGB", (w, h), (208, 206, 200))
    draw = ImageDraw.Draw(image)

    # backing card
    draw.rounded_rectangle([60, 60, w - 60, h - 60], radius=18,
                           fill=(232, 228, 216), outline=(150, 146, 138), width=3)

    # blister pockets, one cracked and one empty
    cols, rows = 5, 3
    for r in range(rows):
        for c in range(cols):
            x0 = 110 + c * 140
            y0 = 120 + r * 150
            x1, y1 = x0 + 100, y0 + 100
            broken = (r == 1 and c == 2)
            empty = (r == 2 and c == 4)
            if empty:
                draw.ellipse([x0, y0, x1, y1], fill=(190, 186, 176), outline=(140, 136, 130), width=3)
                continue
            tint = (
                rng.randint(150, 175) if broken else rng.randint(215, 235),
                rng.randint(120, 140) if broken else rng.randint(210, 228),
                rng.randint(110, 130) if broken else rng.randint(205, 222),
            )
            draw.ellipse([x0, y0, x1, y1], fill=tint, outline=(120, 118, 112), width=2)
            if broken:
                # a crack across the tablet
                draw.line([(x0 + 12, y0 + 55), (x1 - 14, y0 + 42)], fill=(90, 60, 50), width=4)
                draw.line([(x0 + 40, y0 + 18), (x0 + 52, y1 - 16)], fill=(90, 60, 50), width=3)

    # torn foil along one edge
    tear = [(w - 62, 60)]
    y = 60
    while y < h - 60:
        y += rng.randint(18, 34)
        tear.append((w - 62 - rng.randint(0, 26), min(y, h - 60)))
    draw.line(tear, fill=(120, 116, 108), width=5)

    image = image.filter(ImageFilter.GaussianBlur(0.6))
    image.save(str(path), "JPEG", quality=84)
    return path


def build_company_logo(path: Path) -> Path:
    """A flat two-colour letterhead logo, repeated on every page of some documents.

    This is the negative example for E19: low colour variance, small area, and the same xref on
    every page. If the meaningful-image filter is working, this never reaches the vision model
    and never appears in the reviewer's image list.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (240, 60), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, 52, 59], fill=(31, 59, 84))
    draw.rectangle([12, 16, 40, 24], fill=(255, 255, 255))
    draw.rectangle([22, 8, 30, 44], fill=(255, 255, 255))
    draw.text((66, 22), "CORELINE PHARMA", fill=(31, 59, 84))
    image.save(str(path), "PNG")
    return path

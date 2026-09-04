"""Scanned and handwritten PDFs — pages with no text layer at all.

Produced by rendering text with Pillow into a bitmap and wrapping the bitmap in a PDF. There is
deliberately **no** text layer, so `page.get_text()` returns nothing and the flavour detector
must fall to the E13 rules (few extractable characters, or one image covering most of the page)
and route the page to Claude vision instead.

Realism matters here, because a clean bitmap is not a scan. Each page gets a slight rotation, a
paper tint, gaussian noise, reduced contrast and JPEG artefacts. One document is generated at
`difficulty="hard"` — heavier noise, a worse font, more skew — specifically so the
transcription's `legibility` score has something to be low about, and so the E34 confidence
propagation (`field_confidence = min(model_confidence, page_legibility)`) is exercised by real
data rather than by a unit test alone.

Fonts are the Windows handwriting faces confirmed present on this machine; the loader degrades
to whatever it can find rather than failing the build.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from .cases import CaseSpec
from .fixtures import SYNTHETIC_NOTICE

# A4 at 150 dpi — a plausible flatbed scan resolution, and small enough to keep the corpus light.
DPI = 150
PAGE_PX = (int(8.27 * DPI), int(11.69 * DPI))

WINDOWS_FONTS = Path("C:/Windows/Fonts")

HANDWRITING_FONTS = (
    "segoescb.ttf",   # Segoe Script (bold)
    "Inkfree.ttf",    # Ink Free
    "BRADHITC.TTF",   # Bradley Hand ITC
    "FREESCPT.TTF",   # Free Script
)

TYPED_FONTS = ("cour.ttf", "times.ttf", "arial.ttf")


@dataclass
class ScanStyle:
    """How rough this particular "scan" is."""

    font_files: tuple[str, ...]
    font_size: int
    rotation_deg: float
    noise_sigma: float
    contrast: float
    blur_radius: float
    jpeg_quality: int
    ink: tuple[int, int, int]
    paper: tuple[int, int, int]

    @staticmethod
    def clean_scan() -> "ScanStyle":
        return ScanStyle(TYPED_FONTS, 26, 0.4, 5.0, 0.94, 0.3, 82, (32, 34, 40), (252, 251, 246))

    @staticmethod
    def handwritten() -> "ScanStyle":
        return ScanStyle(HANDWRITING_FONTS, 30, 1.1, 9.0, 0.88, 0.5, 74, (28, 38, 92), (250, 248, 240))

    @staticmethod
    def hard() -> "ScanStyle":
        """Deliberately difficult: heavy skew, low contrast, strong noise, aggressive JPEG."""
        return ScanStyle(HANDWRITING_FONTS, 29, 2.3, 20.0, 0.68, 1.0, 42, (60, 62, 74), (238, 233, 220))


def _load_font(style: ScanStyle, size: int | None = None) -> ImageFont.FreeTypeFont:
    size = size or style.font_size
    for name in style.font_files:
        candidate = WINDOWS_FONTS / name
        if candidate.exists():
            try:
                return ImageFont.truetype(str(candidate), size)
            except OSError:
                continue
    # Last resort: whatever Pillow bundles. The document is still image-only, which is the
    # property the parser is being tested on.
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default(size=size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    for raw_line in text.split("\n"):
        if not raw_line.strip():
            lines.append("")
            continue
        words = raw_line.split()
        current = ""
        for word in words:
            trial = f"{current} {word}".strip()
            if draw.textlength(trial, font=font) <= max_width:
                current = trial
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines


def _degrade(image: Image.Image, style: ScanStyle, rng: random.Random) -> Image.Image:
    """Turn a crisp render into something that looks like it came off a tired office scanner."""
    # Skew, as a sheet fed slightly crooked.
    angle = rng.uniform(-style.rotation_deg, style.rotation_deg)
    image = image.rotate(angle, resample=Image.BICUBIC, expand=False,
                         fillcolor=style.paper)

    if style.blur_radius > 0:
        image = image.filter(ImageFilter.GaussianBlur(style.blur_radius))

    image = ImageEnhance.Contrast(image).enhance(style.contrast)

    # Sensor noise.
    if style.noise_sigma > 0:
        pixels = image.load()
        w, h = image.size
        sigma = style.noise_sigma
        for y in range(0, h, 2):          # every other row: visually equivalent, much faster
            for x in range(0, w, 2):
                r, g, b = pixels[x, y]
                n = int(rng.gauss(0, sigma))
                pixels[x, y] = (
                    min(255, max(0, r + n)),
                    min(255, max(0, g + n)),
                    min(255, max(0, b + n)),
                )
    return image


def render_page_image(
    lines_text: str,
    style: ScanStyle,
    rng: random.Random,
    *,
    heading: str | None = None,
) -> Image.Image:
    """Render one page of text as a degraded bitmap."""
    image = Image.new("RGB", PAGE_PX, style.paper)
    draw = ImageDraw.Draw(image)

    margin = int(0.9 * DPI)
    max_width = PAGE_PX[0] - 2 * margin
    y = margin

    if heading:
        head_font = _load_font(style, int(style.font_size * 1.35))
        draw.text((margin, y), heading, font=head_font, fill=style.ink)
        y += int(style.font_size * 2.1)
        draw.line([(margin, y), (PAGE_PX[0] - margin, y)], fill=style.ink, width=2)
        y += int(style.font_size * 0.9)

    font = _load_font(style)
    line_height = int(style.font_size * 1.55)
    for line in _wrap(draw, lines_text, font, max_width):
        if y > PAGE_PX[1] - margin - line_height:
            break
        # Hand-written lines do not sit perfectly on the baseline.
        jitter_x = rng.randint(-3, 3) if style.font_files is HANDWRITING_FONTS else 0
        jitter_y = rng.randint(-2, 2) if style.font_files is HANDWRITING_FONTS else 0
        draw.text((margin + jitter_x, y + jitter_y), line, font=font, fill=style.ink)
        y += line_height

    small = _load_font(ScanStyle.clean_scan(), 15)
    draw.text((margin, PAGE_PX[1] - margin + 10), SYNTHETIC_NOTICE,
              font=small, fill=(150, 150, 150))

    return _degrade(image, style, rng)


def handwritten_case_text(case: CaseSpec, *, reference: str, report_date: str) -> str:
    """The text a clinician would actually scribble on a paper reporting form."""
    p = case.patient
    drug = case.drugs[0] if case.drugs else None
    event = case.reactions[0] if case.reactions else None
    reporter = case.reporter

    lines = [
        f"Ref: {reference}        Date: {report_date}",
        "",
        "PATIENT",
        f"  Initials: {p.initials if p else '—'}     Age: {p.age_raw if p else '—'}"
        f"     Sex: {p.sex.title() if p and p.sex else '—'}",
        f"  Weight: {p.weight if p and p.weight else '—'}",
        f"  History: {p.medical_history if p else '—'}",
        "",
        "SUSPECT DRUG",
    ]
    if drug:
        lines += [
            f"  {drug.product.name} {drug.dose_amount or ''} {drug.dose_unit or ''} "
            f"{drug.frequency or ''}",
            f"  Route: {(drug.route or drug.product.route).lower()}"
            f"     Batch: {drug.batch or '—'}",
            f"  Started: {drug.start_date_raw or '—'}",
        ]
    lines += ["", "REACTION"]
    if event:
        lines += [
            f"  {event.reaction.term}",
            f"  Onset: {event.onset_raw or '—'}"
            f"     Outcome: {(event.outcome or 'unknown').replace('_', ' ').lower()}",
            f"  Serious: {'YES - ' + ', '.join(event.serious_criteria) if event.serious_criteria else 'no'}",
        ]
    lines += ["", "NARRATIVE", f"  {case.narrative}"] if case.narrative else []
    lines += [
        "",
        "REPORTED BY",
        f"  {reporter.name if reporter else '—'}"
        f"  ({reporter.qualification if reporter and reporter.qualification else reporter.role.title() if reporter else '—'})",
        f"  {reporter.organisation if reporter else ''}",
        f"  {reporter.country if reporter else ''}",
        "",
        "  Signature: ______________________",
    ]
    return "\n".join(lines)


def build_scanned_pdf(
    path: Path,
    pages_text: list[str],
    *,
    style: ScanStyle,
    seed: int,
    heading: str | None = "ADVERSE EVENT REPORT (paper form)",
) -> Path:
    """Wrap rendered bitmaps into an image-only PDF — no text layer anywhere."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    images = [
        render_page_image(text, style, rng, heading=heading if i == 0 else None)
        for i, text in enumerate(pages_text)
    ]
    first, rest = images[0], images[1:]
    first.save(
        str(path), "PDF", resolution=float(DPI), save_all=True, append_images=rest,
        title="Scanned adverse event report", subject=SYNTHETIC_NOTICE,
    )
    return path


def page_images(pages_text: list[str], *, style: ScanStyle, seed: int,
                heading: str | None = None) -> list[Image.Image]:
    """The rendered bitmaps on their own — used to build the hybrid document (E12)."""
    rng = random.Random(seed)
    return [
        render_page_image(text, style, rng, heading=heading if i == 0 else None)
        for i, text in enumerate(pages_text)
    ]

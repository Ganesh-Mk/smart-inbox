"""Vision descriptions for the images that survived the filter (E19).

Two things worth noting about the design.

**Only survivors get here.** `pdf/images.py` has already discarded logos, spacers, rules and
full-page scans. That filter is what makes this affordable: describing every image in a corpus
of letterheaded documents would cost a vision call per page and fill the reviewer's screen with
"a blue rectangle containing the word CORELINE".

**`needs_review` is always Y.** The brief asks for images to be flagged for human review, and
that is not a judgement the model gets to make about its own output. A confident description of
a photograph is still an interpretation of a picture, and in a safety context a human confirms
it. So the flag is set unconditionally rather than, say, only when confidence is low.
"""

from __future__ import annotations

import base64
import logging

import pymupdf

from app.llm.client import LlmCall, LlmClient, image_part, text_part
from app.llm.prompts import load, system_prompt
from app.llm.schemas import ImageDescription
from app.pdf.images import CandidateImage
from app.pdf.render import render_region
from app.settings import Settings, get_settings

log = logging.getLogger("smartinbox.ai.pipeline.describe")

PROMPT_ID = "P7_describe_image"


def describe_image(
    client: LlmClient,
    png_base64: str,
) -> tuple[ImageDescription, LlmCall]:
    """Describe one already-rendered image."""
    prompt = load(PROMPT_ID)
    call = client.complete_json(
        purpose=PROMPT_ID,
        system_prompt=system_prompt().text,
        user_content=[text_part(prompt.text), image_part(png_base64)],
        schema_model=ImageDescription,
        prompt_version=prompt.label,
        max_tokens=2000,
    )
    return ImageDescription.model_validate(call.parsed), call


def describe_embedded(
    client: LlmClient,
    doc: pymupdf.Document,
    candidate: CandidateImage,
    content_sha256: str,
    settings: Settings | None = None,
) -> tuple[ImageDescription, LlmCall, str]:
    """Crop an embedded image out of its page, render it, and describe it.

    The region is cropped from the page rather than the image xref extracted directly, because
    the xref may be a mask, a tile, or in a colour space that does not round-trip. What the
    reviewer sees is the region as it appears on the page, which is also what the model reads.
    """
    settings = settings or get_settings()
    page = doc[candidate.page_no - 1]

    rendered = render_region(
        page, content_sha256, candidate.page_no, candidate.bbox,
        label=f"img{candidate.image_index}", settings=settings)

    description, call = describe_image(client, rendered.as_base64())

    log.info("Described image on page %d: %s (%.2f)",
             candidate.page_no, description.category.value, description.confidence)

    return description, call, str(rendered.path)


def describe_attachment_image(
    client: LlmClient,
    data: bytes,
) -> tuple[ImageDescription, LlmCall]:
    """Describe a bare image attachment — a photo of a damaged blister pack (E6).

    The brief permits logging non-PDF attachments and going no further, but a photograph sent
    with a quality complaint is the evidence for that complaint. One extra branch, and it is the
    difference between "one image attachment, unprocessed" and a described, reviewable artefact.
    """
    return describe_image(client, base64.b64encode(data).decode("ascii"))

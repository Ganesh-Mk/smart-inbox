"""MIME construction for the corpus.

Every structural shape the ingestion layer has to survive is built here rather than described:
`multipart/alternative` bodies, nested `multipart/mixed`, a `message/rfc822` forward with the
real case one level down (E5), an attachment that lies about its content type (E4), the same
PDF attached twice under different filenames (E9), and a reply chain whose quoted history
repeats an earlier case (E10).

The generated `.eml` files are committed, so the corpus is inspectable in a text editor and
`scripts/seed_mailbox.py` only has to post bytes it did not have to invent.
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path
from typing import Any

from .fixtures import SAFETY_MAILBOX


@dataclass
class AttachmentSpec:
    """One attachment. `declared_type` may deliberately disagree with the real bytes (E4)."""

    filename: str
    path: Path
    declared_type: str | None = None
    inline: bool = False

    def resolved_type(self) -> tuple[str, str]:
        if self.declared_type:
            main, _, sub = self.declared_type.partition("/")
            return main, sub or "octet-stream"
        guessed, _ = mimetypes.guess_type(self.filename)
        if guessed:
            main, _, sub = guessed.partition("/")
            return main, sub
        return "application", "octet-stream"


@dataclass
class MessageSpec:
    """One email, plus the ground truth about what a correct triage produces for it."""

    key: str
    subject: str
    sender_name: str
    sender_email: str
    body_text: str
    sent_at: datetime
    body_html: str | None = None
    attachments: list[AttachmentSpec] = field(default_factory=list)
    # E5: this whole message is carried as a message/rfc822 attachment of the outer one.
    forwarded: "MessageSpec | None" = None
    # E10: quoted reply history appended after the new text, repeating an earlier case.
    quoted_history: str | None = None
    # Ground truth (PROJECT_PLAN §14.2)
    golden_categories: list[str] = field(default_factory=list)
    golden_cases: list[dict[str, Any]] = field(default_factory=list)
    golden_notes: str = ""
    edge_cases: list[str] = field(default_factory=list)
    expect_documents: int = 1

    def full_body_text(self) -> str:
        if not self.quoted_history:
            return self.body_text
        return f"{self.body_text}\n\n{self.quoted_history}"


def _attach(message: EmailMessage, spec: AttachmentSpec) -> None:
    main, sub = spec.resolved_type()
    data = spec.path.read_bytes()
    message.add_attachment(
        data,
        maintype=main,
        subtype=sub,
        filename=spec.filename,
        disposition="inline" if spec.inline else "attachment",
    )


def build_eml(spec: MessageSpec, *, domain: str = "smart-inbox.test") -> EmailMessage:
    """Render one MessageSpec into a real MIME message."""
    message = EmailMessage()
    # Deterministic, derived from the corpus key. `make_msgid` embeds a timestamp and a random
    # number, so every rebuild of the corpus produced different Message-IDs — which broke the
    # reproducibility the fixed seed is supposed to give, made every regeneration a large git
    # diff, and, worst of all, defeated E2: re-seeding after a rebuild created a second copy of
    # every case in the database instead of being recognised as the same message.
    message["Message-ID"] = f"<{spec.key}@{domain}>"
    message["From"] = f"{spec.sender_name} <{spec.sender_email}>"
    message["To"] = f"Drug Safety <{SAFETY_MAILBOX}>"
    message["Subject"] = spec.subject
    message["Date"] = format_datetime(spec.sent_at)
    message["X-Smart-Inbox-Corpus-Key"] = spec.key

    body = spec.full_body_text()
    message.set_content(body, subtype="plain", cte="quoted-printable")

    if spec.body_html:
        # multipart/alternative: the walker must prefer text/plain and keep both (E3).
        message.add_alternative(spec.body_html, subtype="html")

    for attachment in spec.attachments:
        _attach(message, attachment)

    if spec.forwarded is not None:
        # E5: the real case sits one level down, inside a message/rfc822 part.
        inner = build_eml(spec.forwarded, domain=domain)
        message.add_attachment(inner, filename=f"{spec.forwarded.key}.eml")

    _freeze_boundaries(message, spec.key)
    return message


def _freeze_boundaries(message: EmailMessage, key: str) -> None:
    """Replace the random MIME boundaries with deterministic ones.

    `EmailMessage` invents a fresh boundary string for every multipart part on every build.
    That alone makes the corpus different bytes each time it is generated, which defeats the
    point of a fixed seed and turns every regeneration into a large, meaningless diff.
    """
    counter = 0
    for part in message.walk():
        if part.is_multipart():
            part.set_boundary(f"=-=-smartinbox-{key}-{counter}-=-=")
            counter += 1


HTML_TEMPLATE = """\
<html>
  <head><meta charset="utf-8"></head>
  <body style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#1f2933">
    <p>{intro}</p>
    <div style="border-left:3px solid #c9d4de;padding-left:12px;margin:12px 0">
      {body}
    </div>
    <p style="color:#5a6b7a;font-size:12px">
      This message was sent to the drug safety mailbox. Synthetic test data.
    </p>
  </body>
</html>
"""


def html_version(intro: str, body_text: str) -> str:
    """An HTML alternative whose visible text matches the plain part (E3)."""
    paragraphs = "".join(
        f"<p>{line.strip()}</p>" for line in body_text.split("\n\n") if line.strip())
    return HTML_TEMPLATE.format(intro=intro, body=paragraphs)


def quoted_reply(previous_from: str, previous_date: datetime, previous_body: str) -> str:
    """A quoted history block in the usual Outlook/Gmail shape (E10).

    The boundary marker and the `>` prefixes are the two signals `QuotedTextDetector` looks
    for. The quoted case is a *repeat*, not a new report, and must never be counted twice.
    """
    quoted = "\n".join(f"> {line}" if line.strip() else ">"
                       for line in previous_body.strip().split("\n"))
    stamp = previous_date.strftime("%a, %d %b %Y at %H:%M")
    return f"On {stamp}, {previous_from} wrote:\n{quoted}"


def original_message_reply(previous_from: str, previous_subject: str,
                           previous_date: datetime, previous_body: str) -> str:
    """The other common quoting style — an `-----Original Message-----` separator (E10)."""
    stamp = previous_date.strftime("%d %B %Y %H:%M")
    return (
        "-----Original Message-----\n"
        f"From: {previous_from}\n"
        f"Sent: {stamp}\n"
        f"To: Drug Safety <{SAFETY_MAILBOX}>\n"
        f"Subject: {previous_subject}\n\n"
        f"{previous_body.strip()}"
    )

#!/usr/bin/env python
"""Post the synthetic corpus into the test mailbox over real SMTP.

    python scripts/seed_mailbox.py                 # send everything
    python scripts/seed_mailbox.py --only adv-      # send a subset, by key prefix
    python scripts/seed_mailbox.py --check          # just count what is in the mailbox

The whole demo rebuilds from nothing with:

    docker compose down -v && docker compose up -d
    python -m testdata.generator.build
    python scripts/seed_mailbox.py

Messages are sent verbatim from the committed `.eml` files — including their `Message-ID`
headers, which is what makes the E2 dedupe test meaningful: run this twice and the poller must
still create exactly one `INBOX_MESSAGE` row per message.
"""

from __future__ import annotations

import argparse
import os
import smtplib
import sys
from email import policy
from email.parser import BytesParser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EMAIL_DIR = REPO_ROOT / "testdata" / "corpus" / "emails"


def load_env() -> dict[str, str]:
    """Read the repo-root .env without adding a dependency just for this script."""
    env: dict[str, str] = dict(os.environ)
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env.setdefault(key.strip(), value.strip())
    return env


def send_all(host: str, port: int, recipient: str, keys: list[str] | None, verbose: bool) -> int:
    files = sorted(EMAIL_DIR.glob("*.eml"))
    if keys:
        files = [f for f in files if any(f.stem.startswith(k) for k in keys)]
    if not files:
        print(f"No .eml files matched under {EMAIL_DIR}.")
        print("Run:  python -m testdata.generator.build")
        return 1

    parser = BytesParser(policy=policy.default)
    sent = 0
    with smtplib.SMTP(host, port, timeout=30) as smtp:
        for path in files:
            raw = path.read_bytes()
            message = parser.parsebytes(raw)
            sender = message.get("From", "corpus@smart-inbox.test")
            # send_message would re-encode; sendmail posts the exact committed bytes, so the
            # MIME structure the ingestion layer sees is the one in git.
            smtp.sendmail(sender, [recipient], raw)
            sent += 1
            if verbose:
                print(f"  sent {path.stem:38s} {len(raw):>7,} bytes  {message.get('Subject', '')[:52]}")

    print(f"\nSeeded {sent} message(s) to {recipient} via {host}:{port}")
    return 0


def check(host: str, imap_port: int, user: str, password: str) -> int:
    """Count what is actually sitting in the mailbox, over IMAP."""
    import imaplib

    with imaplib.IMAP4(host, imap_port) as imap:
        imap.login(user, password)
        status, data = imap.select("INBOX")
        if status != "OK":
            print(f"Could not select INBOX: {data}")
            return 1
        total = int(data[0])
        status, unseen = imap.search(None, "UNSEEN")
        unseen_count = len(unseen[0].split()) if status == "OK" and unseen[0] else 0
        print(f"INBOX at {host}:{imap_port} — {total} message(s), {unseen_count} unseen")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the GreenMail test mailbox")
    parser.add_argument("--only", action="append", default=None,
                        help="only send keys starting with this prefix (repeatable)")
    parser.add_argument("--check", action="store_true", help="report mailbox contents and exit")
    parser.add_argument("--quiet", action="store_true", help="do not list each message")
    args = parser.parse_args()

    env = load_env()
    host = env.get("MAIL_HOST", "localhost")
    smtp_port = int(env.get("SMTP_PORT", "3025"))
    imap_port = int(env.get("MAIL_PORT", "3143"))
    user = env.get("MAIL_USER", "safety@smart-inbox.test")
    password = env.get("MAIL_PASSWORD", "")

    if args.check:
        return check(host, imap_port, user, password)

    return send_all(host, smtp_port, user, args.only, verbose=not args.quiet)


if __name__ == "__main__":
    raise SystemExit(main())

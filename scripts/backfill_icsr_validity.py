#!/usr/bin/env python
"""Repair ICSR_VALIDITY rows written against the wrong unit.

`ClassifyMessageHandler.storeIcsrValidity` used to read `units[0].icsr_elements` unconditionally.
A message is classified unit by unit and the message-level label is the strongest of them (E25),
so on a covering email with a completed form attached the label came from the attachment while
unit 0 was the email body. The stored checklist therefore described a different document from the
label sitting above it: the Classification card read "All four ICSR minimum criteria are present"
directly above a checklist reading 1/4, quoting email text for criteria decided on a PDF.

The handler now uses the label's `source_unit`. This script repairs rows written before that.

**No model calls.** Every per-unit classification response is already in AI_CALL_LOG. Units are
built from `document ORDER BY id` and called in that order, so the Nth `P1_classify` call for a
message is the Nth unit; the label's own reason ends with "(from <unit name>)", which names the
one to use.

    python scripts/backfill_icsr_validity.py [--apply]

Prints what it would change; `--apply` writes.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
ELEMENT_KEYS = [
    ("patient", "has_identifiable_patient"),
    ("reporter", "has_identifiable_reporter"),
    ("product", "has_suspect_product"),
    ("event", "has_adverse_event"),
]


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    path = REPO_ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    return env


def read_clob(value) -> str | None:
    if value is None:
        return None
    return value.read() if hasattr(value, "read") else str(value)


def unit_names(cursor, message_id: int) -> list[str]:
    """The unit names in the order `buildUnits` produces them."""
    cursor.execute(
        "SELECT source_kind, filename FROM document WHERE message_id = :1 ORDER BY id",
        [message_id])
    return ["email body" if kind == "EMAIL_BODY" else filename
            for kind, filename in cursor.fetchall()]


def unit_elements(cursor, message_id: int) -> list[dict | None]:
    """`icsr_elements` from each P1_classify call for the message, in unit order."""
    cursor.execute(
        "SELECT a.response_json FROM ai_call_log a JOIN job j ON j.id = a.job_id"
        " WHERE a.purpose = 'P1_classify' AND j.subject_type = 'MESSAGE'"
        "   AND j.subject_id = :1 ORDER BY a.id", [message_id])
    out: list[dict | None] = []
    for (raw,) in cursor.fetchall():
        try:
            envelope = json.loads(read_clob(raw))
            content = envelope["choices"][0]["message"]["content"]
            out.append(json.loads(content).get("icsr_elements"))
        except Exception:                                  # noqa: BLE001 - a bad row is skipped
            out.append(None)
    return out


def source_unit_of(reason: str) -> str | None:
    """The `(from <unit>)` suffix roll_up_message appends to every message-level label."""
    if not reason or not reason.rstrip().endswith(")"):
        return None
    marker = reason.rfind("(from ")
    return reason[marker + len("(from "):].rstrip().removesuffix(")") if marker >= 0 else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write the corrections")
    args = parser.parse_args()

    import oracledb

    env = load_env()
    connection = oracledb.connect(
        user=env["ORACLE_APP_USER"], password=env["ORACLE_APP_PASSWORD"],
        dsn=f'{env.get("ORACLE_HOST", "localhost")}:{env.get("ORACLE_PORT", "1521")}'
            f'/{env.get("ORACLE_SERVICE", "FREEPDB1")}')
    cursor = connection.cursor()
    writer = connection.cursor()

    cursor.execute(
        "SELECT c.id, c.subject_id, c.category, c.reason, v.elements_present"
        "  FROM classification c JOIN icsr_validity v ON v.classification_id = c.id"
        " WHERE c.subject_type = 'MESSAGE' AND c.decided_by = 'RULE'"
        " ORDER BY c.subject_id")
    rows = cursor.fetchall()

    checked = corrected = skipped = 0
    for classification_id, message_id, category, reason, stored_present in rows:
        checked += 1
        reason_text = read_clob(reason) or ""
        unit = source_unit_of(reason_text)
        names = unit_names(cursor, message_id)
        if unit is None or unit not in names:
            print(f"  ? {message_id}: cannot resolve source unit from reason; left alone")
            skipped += 1
            continue

        elements_per_unit = unit_elements(cursor, message_id)
        index = names.index(unit)
        if index >= len(elements_per_unit) or elements_per_unit[index] is None:
            print(f"  ? {message_id}: no stored response for unit {unit!r}; left alone")
            skipped += 1
            continue

        elements = elements_per_unit[index]
        present, missing, values = 0, [], {}
        for short, key in ELEMENT_KEYS:
            check = elements.get(key) or {}
            is_present = bool(check.get("present"))
            present += is_present
            if not is_present:
                missing.append(short)
            values[short] = ("Y" if is_present else "N",
                             float(check.get("confidence") or 0),
                             (check.get("quote") or "")[:2000])

        if present == stored_present:
            continue

        print(f"  ✓ {message_id} {category}: {stored_present}/4 -> {present}/4  (unit {unit!r})")
        corrected += 1
        if args.apply:
            writer.execute(
                "UPDATE icsr_validity SET"
                "   has_patient = :1, patient_confidence = :2, patient_evidence = :3,"
                "   has_reporter = :4, reporter_confidence = :5, reporter_evidence = :6,"
                "   has_product = :7, product_confidence = :8, product_evidence = :9,"
                "   has_event = :10, event_confidence = :11, event_evidence = :12,"
                "   elements_present = :13, missing_elements_json = :14"
                " WHERE classification_id = :15",
                [*values["patient"], *values["reporter"], *values["product"], *values["event"],
                 present, json.dumps(missing), classification_id])

    if args.apply:
        connection.commit()
    connection.close()

    print(f"\nchecked {checked} · {'corrected' if args.apply else 'would correct'} {corrected}"
          f" · skipped {skipped}")
    if corrected and not args.apply:
        print("re-run with --apply to write these")
    return 0


if __name__ == "__main__":
    sys.exit(main())

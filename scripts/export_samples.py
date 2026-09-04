#!/usr/bin/env python
"""Export the extracted JSON for each processed message to `docs/sample-outputs/`.

Deliverable 5 of the brief. One file per message, showing exactly what the system produced —
including the fields it correctly declined to answer and the citations it could not verify,
because a sample that shows only the successes is not a sample of the system's behaviour.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "docs" / "sample-outputs"


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    return env


def clob(value) -> str | None:
    if value is None:
        return None
    return value.read() if hasattr(value, "read") else str(value)


def main() -> int:
    import oracledb

    env = load_env()
    connection = oracledb.connect(
        user=env["ORACLE_APP_USER"], password=env["ORACLE_APP_PASSWORD"],
        dsn=f'{env.get("ORACLE_HOST","localhost")}:{env.get("ORACLE_PORT","1521")}'
            f'/{env.get("ORACLE_SERVICE","FREEPDB1")}')
    cursor = connection.cursor()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for existing in OUT_DIR.glob("*.json"):
        existing.unlink()

    cursor.execute("""SELECT id, sender_email, subject, status, needs_attention,
                             attention_reason FROM inbox_message ORDER BY id""")
    messages = cursor.fetchall()

    written = 0
    for message_id, sender, subject, status, attention, reason in messages:
        payload: dict = {
            "message_id": message_id,
            "sender": sender,
            "subject": subject,
            "status": status,
            "needs_attention": attention == "Y",
            "attention_reason": reason,
            "classifications": [],
            "documents": [],
            "cases": [],
        }

        cursor.execute("""SELECT category, confidence, reason, decided_by FROM classification
                          WHERE subject_type='MESSAGE' AND subject_id=:1
                            AND superseded_by IS NULL ORDER BY confidence DESC""", [message_id])
        payload["classifications"] = [
            {"category": c, "confidence": round(float(conf), 4),
             "reason": r, "decided_by": d}
            for c, conf, r, d in cursor.fetchall()]

        cursor.execute("""SELECT id, source_kind, filename, page_count, doc_rendering, doc_genre,
                                 primary_language, parse_status, parse_error
                          FROM document WHERE message_id=:1 ORDER BY id""", [message_id])
        for row in cursor.fetchall():
            doc_id, kind, filename, pages, rendering, genre, language, pstatus, perror = row
            payload["documents"].append({
                "source_kind": kind, "filename": filename, "page_count": pages,
                "rendering": rendering, "genre": genre, "language": language,
                "parse_status": pstatus, "parse_error": perror,
            })

        cursor.execute("""SELECT id, case_type, is_serious, seriousness_json, confidence,
                                 narrative FROM case_record WHERE message_id=:1
                          ORDER BY case_index""", [message_id])
        for case_id, case_type, serious, seriousness, confidence, narrative in cursor.fetchall():
            case: dict = {
                "case_type": case_type,
                "is_serious": serious == "Y",
                "seriousness": json.loads(seriousness) if seriousness else None,
                "confidence": round(float(confidence), 4),
                "narrative": clob(narrative),
                "fields": [],
            }
            cursor.execute("""SELECT id, field_group, field_path, value_text, value_json, unit,
                                     raw_text, status, confidence, confidence_pre_adjust,
                                     adjust_reason
                              FROM extracted_field WHERE case_id=:1 AND superseded_by IS NULL
                              ORDER BY field_group, field_index, field_path""", [case_id])
            for frow in cursor.fetchall():
                (fid, group, path, value, vjson, unit, raw, fstatus,
                 fconf, fpre, adjust) = frow
                field: dict = {
                    "field_group": group, "field_path": path, "value": value,
                    "typed_value": json.loads(clob(vjson)) if vjson else None,
                    "unit": unit, "raw": raw, "status": fstatus,
                    "confidence": round(float(fconf), 4),
                    "confidence_before_adjustment": round(float(fpre or 0), 4),
                    "adjustment_reason": adjust or None,
                    "evidence": [],
                }
                cursor.execute("""SELECT source_type, page_no, quote, char_start, char_end,
                                         bbox, verified, verify_method, match_score
                                  FROM field_evidence WHERE field_id=:1""", [fid])
                for e in cursor.fetchall():
                    stype, page, quote, cstart, cend, bbox, verified, method, score = e
                    field["evidence"].append({
                        "source_type": stype, "page_no": page, "quote": quote,
                        "char_start": cstart, "char_end": cend, "bbox": bbox,
                        "verified": verified == "Y", "verify_method": method,
                        "match_score": round(float(score or 0), 2),
                    })
                case["fields"].append(field)
            payload["cases"].append(case)

        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (subject or ""))[:70]
        (OUT_DIR / f"{message_id:03d}_{safe}.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        written += 1

    connection.close()
    print(f"wrote {written} sample output(s) to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

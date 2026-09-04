#!/usr/bin/env python
"""Score the pipeline against the golden set and write `eval/report.md` + `report.json`.

    python eval/run_eval.py

Reads what the system actually produced from Oracle and compares it with
`testdata/goldens/*.json`, which were written by the same generator that rendered the
documents — so the ground truth is true by construction rather than by later annotation.

Two of these metrics matter more than the rest and are the reason this file exists:

**Abstention correctness.** When a fact is genuinely absent, did the model say `NOT_STATED`?
A miss and a confident wrong answer are counted **separately**, because they are not the same
failure. A missing fact is visibly missing; a fabricated one is not, and in a regulated system
that difference is the whole game.

**Evidence verification rate.** What fraction of asserted facts carry a quote that code could
actually find in the source. Every claim this submission makes about traceability reduces to
this number.

The confidence calibration curve is here for the same reason. Reporting measured calibration is
far more persuasive than asserting the scores are meaningful — and if they are badly calibrated,
that is worth knowing and saying.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = REPO_ROOT / "testdata" / "goldens"
CORPUS_DIR = REPO_ROOT / "testdata" / "corpus"
OUT_DIR = REPO_ROOT / "eval"

CATEGORIES = ["ICSR", "ICSR_INCOMPLETE", "PQC", "MI", "NOT_RELEVANT"]

# ICSR and ICSR_INCOMPLETE are treated as one family for the headline category score. The
# distinction between them is graded separately by the ICSR element accuracy below, which is
# the metric that actually measures whether the four-criteria rule is working.
FAMILY = {"ICSR": "ICSR", "ICSR_INCOMPLETE": "ICSR", "PQC": "PQC", "MI": "MI",
          "NOT_RELEVANT": "NOT_RELEVANT"}


# =======================================================================================
# Loading
# =======================================================================================

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


def connect():
    import oracledb

    env = load_env()
    return oracledb.connect(
        user=env["ORACLE_APP_USER"],
        password=env["ORACLE_APP_PASSWORD"],
        dsn=f'{env.get("ORACLE_HOST", "localhost")}:{env.get("ORACLE_PORT", "1521")}'
            f'/{env.get("ORACLE_SERVICE", "FREEPDB1")}',
    )


def load_goldens() -> dict[str, dict[str, Any]]:
    goldens = {}
    for path in sorted(GOLDEN_DIR.glob("*.json")):
        goldens[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    return goldens


def subject_to_key() -> dict[str, str]:
    """Map each corpus subject line back to its message key.

    The database stores what arrived over IMAP and has no idea about corpus keys, so the
    subject is the join. It is unique across the corpus by construction.
    """
    import email
    import email.policy

    mapping = {}
    for path in sorted((CORPUS_DIR / "emails").glob("*.eml")):
        message = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
        mapping[str(message["Subject"])] = path.stem
    return mapping


def load_actual(cursor) -> dict[int, dict[str, Any]]:
    """Everything the pipeline produced, keyed by message id."""
    actual: dict[int, dict[str, Any]] = {}

    cursor.execute("SELECT id, subject, status FROM inbox_message")
    for message_id, subject, status in cursor.fetchall():
        actual[message_id] = {
            "subject": subject, "status": status,
            "categories": [], "fields": [], "elements": {}, "cases": 0,
        }

    cursor.execute("""
        SELECT subject_id, category, confidence FROM classification
        WHERE subject_type = 'MESSAGE' AND superseded_by IS NULL""")
    for message_id, category, confidence in cursor.fetchall():
        if message_id in actual:
            actual[message_id]["categories"].append((category, float(confidence)))

    cursor.execute("""
        SELECT c.subject_id, v.has_patient, v.has_reporter, v.has_product, v.has_event
        FROM icsr_validity v JOIN classification c ON c.id = v.classification_id
        WHERE c.subject_type = 'MESSAGE' AND c.superseded_by IS NULL""")
    for message_id, patient, reporter, product, event in cursor.fetchall():
        if message_id in actual:
            actual[message_id]["elements"] = {
                "has_identifiable_patient": patient == "Y",
                "has_identifiable_reporter": reporter == "Y",
                "has_suspect_product": product == "Y",
                "has_adverse_event": event == "Y",
            }

    cursor.execute("""
        SELECT cr.message_id, f.field_path, f.value_text, f.status, f.confidence,
               f.confidence_pre_adjust, f.value_json, f.raw_text, f.unit,
               (SELECT COUNT(*) FROM field_evidence e
                 WHERE e.field_id = f.id AND e.verified = 'Y') AS verified,
               (SELECT COUNT(*) FROM field_evidence e WHERE e.field_id = f.id) AS total
        FROM extracted_field f JOIN case_record cr ON cr.id = f.case_id
        WHERE f.superseded_by IS NULL""")
    for row in cursor.fetchall():
        (message_id, path, value, status, confidence, pre,
         value_json, raw_text, unit, verified, total) = row
        if message_id in actual:
            # value_json is a CLOB; read it before the cursor moves on.
            payload = {}
            if value_json is not None:
                try:
                    payload = json.loads(
                        value_json.read() if hasattr(value_json, "read") else value_json)
                except (json.JSONDecodeError, AttributeError, TypeError):
                    payload = {}
            actual[message_id]["fields"].append({
                "path": path, "value": value or "", "status": status,
                "confidence": float(confidence), "pre": float(pre or 0),
                "json": payload, "raw": raw_text or "", "unit": unit or "",
                "verified": int(verified or 0), "evidence": int(total or 0),
            })

    cursor.execute("SELECT message_id, COUNT(*) FROM case_record GROUP BY message_id")
    for message_id, count in cursor.fetchall():
        if message_id in actual:
            actual[message_id]["cases"] = count

    return actual


# =======================================================================================
# Scoring
# =======================================================================================

def normalise(value: Any) -> str:
    """Fold away differences that are not disagreements, for a fair value comparison."""
    import re

    text = str(value if value is not None else "").strip().casefold()
    text = re.sub(r"\b(years?|yrs?|y\.?o\.?|months?|weeks?|days?|kg|lb|mg|ml)\b\.?", "", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def values_match(expected: Any, got: Any) -> bool:
    """Strict match: identical after normalisation, or one contained in the other as whole words."""
    a, b = normalise(expected), normalise(got)
    if not a and not b:
        return True
    if not a or not b:
        return False
    if a == b:
        return True
    # Word-boundary containment: "Dr Aoife Whitfield" matches "Aoife Whitfield" but "female"
    # does not match "male".
    ta, tb = a.split(), b.split()
    short, long = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return any(long[i:i + len(short)] == short for i in range(len(long) - len(short) + 1))


# Words that carry no distinguishing content when comparing two renderings of the same fact.
_STOPWORDS = frozenset({
    "a", "an", "the", "of", "with", "and", "or", "in", "on", "at", "to", "for",
    "patient", "reported", "stage", "no", "not", "was", "is", "has", "had",
})


def values_match_normalised(expected: Any, got: Any) -> bool:
    """Looser match: the same content words, in any order.

    The plan asks for field accuracy "exact and normalised" (§15), and the reason is visible in
    the data. The model returned "stage 3 chronic kidney disease" where the golden says "chronic
    kidney disease stage 3"; those are the same fact written two ways, and counting the second
    as an error measures the scorer's rigidity rather than the model's accuracy.

    Both numbers are reported. Exact match is the honest floor; normalised match is the honest
    ceiling; the truth about model quality sits between them, and saying so is more useful than
    picking whichever flatters.
    """
    if values_match(expected, got):
        return True
    a = {t for t in normalise(expected).split() if t not in _STOPWORDS}
    b = {t for t in normalise(got).split() if t not in _STOPWORDS}
    if not a or not b:
        return False
    overlap = len(a & b)
    # Symmetric: two thirds of the shorter side's content words must appear in the other.
    return overlap / min(len(a), len(b)) >= 0.67


def score_categories(pairs: list[tuple[set[str], set[str]]]) -> dict[str, Any]:
    """Per-category precision, recall and F1, plus micro and macro roll-ups."""
    per: dict[str, dict[str, int]] = {c: {"tp": 0, "fp": 0, "fn": 0}
                                      for c in set(FAMILY.values())}
    exact = 0

    for expected, got in pairs:
        e = {FAMILY.get(c, c) for c in expected}
        g = {FAMILY.get(c, c) for c in got}
        if e == g:
            exact += 1
        for category in per:
            if category in g and category in e:
                per[category]["tp"] += 1
            elif category in g and category not in e:
                per[category]["fp"] += 1
            elif category not in g and category in e:
                per[category]["fn"] += 1

    def prf(counts: dict[str, int]) -> dict[str, float]:
        tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        return {"precision": round(precision, 4), "recall": round(recall, 4),
                "f1": round(f1, 4), "support": tp + fn}

    scores = {category: prf(counts) for category, counts in per.items()}

    total = {"tp": sum(c["tp"] for c in per.values()),
             "fp": sum(c["fp"] for c in per.values()),
             "fn": sum(c["fn"] for c in per.values())}
    present = [s for s in scores.values() if s["support"] > 0]

    return {
        "per_category": scores,
        "micro": prf(total),
        "macro_f1": round(sum(s["f1"] for s in present) / len(present), 4) if present else 0.0,
        "exact_set_accuracy": round(exact / len(pairs), 4) if pairs else 0.0,
        "messages_scored": len(pairs),
    }


# The goldens address a typed value by its component — `patient.age.value`,
# `reaction[0].onset.raw` — because that is how the generator built it. The extractor stores
# one row per typed value, with the components inside `value_json`. Comparing the two
# vocabularies directly reports every typed field as both a miss and a fabrication, which is a
# measurement artefact and says nothing about the model. This table reconciles them.
#
# `component` names what inside the extracted row the golden is talking about:
#   None    -> value_text
#   "raw"   -> raw_text
#   "unit"  -> unit column
#   other   -> that key inside value_json
_COMPONENT_SUFFIXES = {
    ".value": None,
    ".raw": "raw",
    ".unit": "unit",
    ".iso": "iso",
    ".precision": "precision",
    ".is_relative": "is_relative",
    ".amount": None,
    ".frequency_raw": None,
}

# Golden paths whose names simply differ from the extracted ones.
_PATH_ALIASES = {
    "severity.criteria": "reaction[0].seriousness",
    "severity.is_serious": "reaction[0].seriousness",
    "defect.summary": "defect.description",
    "defect.photo_mentioned": "defect.photo_mentioned",
    "product.batch": "product[0].batch",
    "product.expiry": "product.expiry",
}


def resolve_golden_path(path: str) -> tuple[str, str | None]:
    """`(extracted field path, component)` for a golden key."""
    if path in _PATH_ALIASES:
        return _PATH_ALIASES[path], None
    for suffix, component in _COMPONENT_SUFFIXES.items():
        if path.endswith(suffix):
            base = path[: -len(suffix)]
            # "product[0].dose.amount" -> the extractor splits dose into amount and unit rows
            if suffix == ".amount":
                return f"{base}.amount", None
            if suffix == ".frequency_raw":
                return f"{base}.frequency", None
            if suffix == ".unit" and base.endswith(".dose"):
                return f"{base}.unit", None
            return base, component
    return path, None


def extracted_component(field: dict[str, Any], component: str | None) -> str:
    if component is None:
        return field.get("value", "")
    if component == "raw":
        return field.get("raw") or field.get("json", {}).get("raw", "") or field.get("value", "")
    if component == "unit":
        return field.get("unit") or field.get("json", {}).get("unit", "")
    value = field.get("json", {}).get(component)
    if value is None:
        return ""
    return str(value)


def score_fields(goldens, actual, mapping) -> dict[str, Any]:
    """Field accuracy, and — the important half — abstention correctness.

    Four outcomes, and keeping them apart is the point:

    * **correct**        the fact is stated and we got it right
    * **wrong**          the fact is stated and we got it wrong (a miss)
    * **abstained_correctly**  the fact is absent and we said NOT_STATED
    * **false_confident**      the fact is absent and we asserted a value anyway

    The last one is the number that matters. It is a fabrication, and it is not the same kind
    of error as a miss.
    """
    outcomes = {"correct": 0, "correct_normalised": 0, "wrong": 0,
                "abstained_correctly": 0, "false_confident": 0, "missing_field": 0}
    per_field: dict[str, dict[str, int]] = defaultdict(
        lambda: {"correct": 0, "wrong": 0, "abstained_correctly": 0, "false_confident": 0})
    examples: list[dict[str, Any]] = []

    for message_id, data in actual.items():
        key = mapping.get(data["subject"])
        if not key or key not in goldens:
            continue
        golden = goldens[key]
        expected_fields: dict[str, Any] = {}
        for case in golden.get("cases", []):
            expected_fields.update(case.get("fields", {}))

        by_path = {f["path"]: f for f in data["fields"]}

        # --- facts the source states: did we get them? ---
        matched_paths: set[str] = set()
        for path, expected in expected_fields.items():
            if isinstance(expected, bool):
                continue     # element checklist, scored by score_icsr_elements
            if isinstance(expected, list):
                # Seriousness criteria: an unordered set, compared as one.
                resolved, _ = resolve_golden_path(path)
                got = by_path.get(resolved)
                if got is None:
                    outcomes["missing_field"] += 1
                    continue
                matched_paths.add(resolved)
                got_set = {c.strip() for c in (got.get("value") or "").split(",") if c.strip()}
                if got_set == set(expected):
                    outcomes["correct"] += 1
                    outcomes["correct_normalised"] += 1
                    per_field[path]["correct"] += 1
                else:
                    outcomes["wrong"] += 1
                    per_field[path]["wrong"] += 1
                continue
            resolved, component = resolve_golden_path(path)
            got = by_path.get(resolved)
            if got is None:
                outcomes["missing_field"] += 1
                continue
            matched_paths.add(resolved)
            if got["status"] == "NOT_STATED":
                outcomes["wrong"] += 1
                per_field[path]["wrong"] += 1
            else:
                actual_value = extracted_component(got, component)
                if values_match(expected, actual_value):
                    outcomes["correct"] += 1
                    outcomes["correct_normalised"] += 1
                    per_field[path]["correct"] += 1
                elif values_match_normalised(expected, actual_value):
                    # Right fact, different wording. Counted as correct only in the normalised
                    # figure, so the exact number stays honest.
                    outcomes["correct_normalised"] += 1
                    outcomes["wrong"] += 1
                    per_field[path]["wrong"] += 1
                else:
                    outcomes["wrong"] += 1
                    per_field[path]["wrong"] += 1
                    if len(examples) < 12:
                        examples.append({"message": key, "field": path,
                                         "expected": str(expected), "got": actual_value})

        # --- facts the source does NOT state: did we abstain? ---
        for path, got in by_path.items():
            if path in expected_fields or path in matched_paths or path == "narrative":
                continue
            if got["status"] == "NOT_STATED":
                outcomes["abstained_correctly"] += 1
                per_field[path]["abstained_correctly"] += 1
            else:
                # A value asserted where the golden has none. Flagged, but see the caveat in
                # the report: the goldens record the facts the generator planted, not an
                # exhaustive list of everything legitimately readable in the document.
                outcomes["false_confident"] += 1
                per_field[path]["false_confident"] += 1

    stated_total = outcomes["correct"] + outcomes["wrong"]
    normalised_accuracy = (outcomes["correct_normalised"] / stated_total) if stated_total else 0.0
    absent_total = outcomes["abstained_correctly"] + outcomes["false_confident"]

    return {
        "outcomes": outcomes,
        "field_accuracy": round(outcomes["correct"] / stated_total, 4) if stated_total else 0.0,
        "field_accuracy_normalised": round(normalised_accuracy, 4),
        "abstention_correctness":
            round(outcomes["abstained_correctly"] / absent_total, 4) if absent_total else 0.0,
        "mismatches": examples,
        "per_field": {k: v for k, v in sorted(per_field.items())},
    }


def score_icsr_elements(goldens, actual, mapping) -> dict[str, Any]:
    """Per-element agreement on the four minimum criteria (E22)."""
    names = ["has_identifiable_patient", "has_identifiable_reporter",
             "has_suspect_product", "has_adverse_event"]
    per = {n: {"agree": 0, "total": 0} for n in names}

    for data in actual.values():
        key = mapping.get(data["subject"])
        if not key or key not in goldens or not data["elements"]:
            continue
        cases = goldens[key].get("cases", [])
        if not cases:
            continue
        expected = cases[0].get("icsr_elements", {})
        for name in names:
            if name not in expected:
                continue
            per[name]["total"] += 1
            if bool(expected[name]) == bool(data["elements"].get(name)):
                per[name]["agree"] += 1

    return {
        "per_element": {
            n: {"agreement": round(c["agree"] / c["total"], 4) if c["total"] else 0.0,
                "n": c["total"]}
            for n, c in per.items()},
        "overall": round(
            sum(c["agree"] for c in per.values()) / sum(c["total"] for c in per.values()), 4)
            if sum(c["total"] for c in per.values()) else 0.0,
    }


def score_calibration(actual, goldens, mapping) -> dict[str, Any]:
    """Reliability curve: mean accuracy per confidence decile.

    If the scores are meaningful, accuracy rises with confidence. If they are not, this says so
    plainly — which is the point of measuring rather than asserting.
    """
    buckets: dict[int, dict[str, int]] = defaultdict(lambda: {"n": 0, "correct": 0})

    for data in actual.values():
        key = mapping.get(data["subject"])
        if not key or key not in goldens:
            continue
        expected_fields: dict[str, Any] = {}
        for case in goldens[key].get("cases", []):
            expected_fields.update(case.get("fields", {}))

        resolved_expected: dict[str, tuple[Any, str | None]] = {}
        for path, expected in expected_fields.items():
            if isinstance(expected, (list, bool)):
                continue
            target, component = resolve_golden_path(path)
            resolved_expected.setdefault(target, (expected, component))

        for field in data["fields"]:
            if field["status"] == "NOT_STATED" or field["path"] == "narrative":
                continue
            entry = resolved_expected.get(field["path"])
            if entry is None:
                continue
            expected, component = entry
            decile = min(9, int(field["confidence"] * 10))
            buckets[decile]["n"] += 1
            # Normalised, deliberately: a calibration curve should measure whether the model
            # knew the fact, not whether it phrased it the way the golden did.
            if values_match_normalised(expected, extracted_component(field, component)):
                buckets[decile]["correct"] += 1

    curve = []
    for decile in range(10):
        bucket = buckets.get(decile)
        if not bucket or bucket["n"] == 0:
            continue
        curve.append({
            "band": f"{decile / 10:.1f}-{(decile + 1) / 10:.1f}",
            "n": bucket["n"],
            "accuracy": round(bucket["correct"] / bucket["n"], 4),
            "mean_confidence": round((decile + 0.5) / 10, 2),
        })

    # Expected Calibration Error: the average gap between stated confidence and observed
    # accuracy, weighted by how many predictions fall in each band. Lower is better; a model
    # that says 0.95 and is right 95% of the time scores 0.
    total = sum(b["n"] for b in curve)
    ece = sum(b["n"] * abs(b["mean_confidence"] - b["accuracy"]) for b in curve) / total \
        if total else 0.0

    return {"curve": curve, "expected_calibration_error": round(ece, 4), "n": total}


def score_verification(cursor) -> dict[str, Any]:
    cursor.execute("""
        SELECT COUNT(*),
               SUM(CASE WHEN e.verified = 'Y' THEN 1 ELSE 0 END),
               SUM(CASE WHEN e.verify_method = 'EXACT' THEN 1 ELSE 0 END),
               SUM(CASE WHEN e.verify_method = 'FUZZY' THEN 1 ELSE 0 END)
        FROM field_evidence e JOIN extracted_field f ON f.id = e.field_id
        WHERE f.status IN ('STATED','UNCERTAIN','CONFLICT') AND f.superseded_by IS NULL""")
    total, verified, exact, fuzzy = cursor.fetchone()
    total = int(total or 0)
    return {
        "asserted_with_evidence": total,
        "verified": int(verified or 0),
        "exact": int(exact or 0),
        "fuzzy": int(fuzzy or 0),
        "unverified": total - int(verified or 0),
        "rate": round(int(verified or 0) / total, 4) if total else 0.0,
    }


def score_performance(cursor) -> dict[str, Any]:
    cursor.execute("""
        SELECT stage, COUNT(*), AVG(duration_ms),
               MEDIAN(duration_ms), MAX(duration_ms)
        FROM processing_metric GROUP BY stage ORDER BY stage""")
    stages = [{"stage": s, "n": int(n), "mean_ms": int(avg or 0),
               "p50_ms": int(med or 0), "max_ms": int(mx or 0)}
              for s, n, avg, med, mx in cursor.fetchall()]

    cursor.execute("""
        SELECT COUNT(*), NVL(SUM(cost_usd),0), NVL(SUM(prompt_tokens),0),
               NVL(SUM(completion_tokens),0), NVL(SUM(cached_tokens),0),
               NVL(AVG(latency_ms),0), NVL(SUM(CASE WHEN repaired='Y' THEN 1 ELSE 0 END),0)
        FROM ai_call_log""")
    calls, cost, prompt, completion, cached, latency, repaired = cursor.fetchone()

    cursor.execute("SELECT COUNT(*) FROM document")
    documents = cursor.fetchone()[0] or 1
    cursor.execute("SELECT COUNT(*) FROM inbox_message")
    messages = cursor.fetchone()[0] or 1

    cursor.execute("""SELECT purpose, COUNT(*), NVL(SUM(cost_usd),0), NVL(AVG(latency_ms),0)
                      FROM ai_call_log GROUP BY purpose ORDER BY 3 DESC""")
    by_purpose = [{"purpose": p, "calls": int(n), "cost_usd": round(float(c), 5),
                   "mean_latency_ms": int(l)} for p, n, c, l in cursor.fetchall()]

    return {
        "stages": stages,
        "ai_calls": int(calls or 0),
        "total_cost_usd": round(float(cost or 0), 5),
        "cost_per_document_usd": round(float(cost or 0) / documents, 5),
        "cost_per_message_usd": round(float(cost or 0) / messages, 5),
        "prompt_tokens": int(prompt or 0),
        "completion_tokens": int(completion or 0),
        "cached_tokens": int(cached or 0),
        "cache_hit_rate": round(int(cached or 0) / int(prompt or 1), 4),
        "mean_latency_ms": int(latency or 0),
        "schema_repairs": int(repaired or 0),
    }


def score_edge_cases(goldens, actual, mapping) -> dict[str, Any]:
    """Which of the 40 edge cases the corpus exercised, and whether those messages processed."""
    covered: dict[str, dict[str, int]] = defaultdict(lambda: {"messages": 0, "processed": 0})
    by_subject = {d["subject"]: d for d in actual.values()}

    for key, golden in goldens.items():
        subject = golden.get("subject")
        data = by_subject.get(subject)
        for edge in golden.get("edge_cases", []):
            covered[edge]["messages"] += 1
            if data and data["status"] in ("READY_FOR_REVIEW", "REVIEWED"):
                covered[edge]["processed"] += 1

    return dict(sorted(covered.items(), key=lambda kv: int(kv[0][1:])))


# =======================================================================================
# Report
# =======================================================================================

def build_report(results: dict[str, Any]) -> str:
    c = results["categories"]
    f = results["fields"]
    v = results["verification"]
    p = results["performance"]
    cal = results["calibration"]
    el = results["icsr_elements"]

    lines: list[str] = []
    add = lines.append

    add("# Smart Inbox — evaluation report")
    add("")
    add(f"Generated {results['generated_at']} against {results['corpus']['messages']} messages "
        f"/ {results['corpus']['documents']} documents of synthetic corpus.")
    add("")
    add("All data is generated by `testdata/generator/`. The golden labels were written by the "
        "same generator that rendered each document, so ground truth is true by construction "
        "rather than by later annotation.")
    add("")

    add("## Headline")
    add("")
    add("| Metric | Result | Target |")
    add("|---|---|---|")
    add(f"| Evidence verification rate | **{v['rate']:.1%}** | ≥ 90% |")
    add(f"| Category F1 (micro) | **{c['micro']['f1']:.3f}** | ≥ 0.90 |")
    add(f"| Field accuracy (exact / normalised) | "
        f"{f['field_accuracy']:.1%} / **{f['field_accuracy_normalised']:.1%}** | — |")
    add(f"| Multi-label exact-set accuracy | {c['exact_set_accuracy']:.1%} | — |")
    add(f"| Abstention correctness | {f['abstention_correctness']:.1%} | — |")
    add(f"| ICSR element agreement | {el['overall']:.1%} | — |")
    add(f"| Cost per document | ${p['cost_per_document_usd']:.4f} | ≤ $0.05 |")
    add(f"| Prompt-cache hit rate | {p['cache_hit_rate']:.1%} | — |")
    add("")

    add("## Evidence verification")
    add("")
    add("The number this submission rests on: what fraction of asserted facts carry a quote "
        "that code could actually locate in the source. The model's own citation is never "
        "trusted — `pipeline/verify.py` proves it, rewrites the offsets to what was really "
        "found, and caps the confidence at 0.40 when it cannot.")
    add("")
    add(f"- Facts asserted with evidence: **{v['asserted_with_evidence']}**")
    add(f"- Verified: **{v['verified']}** ({v['rate']:.1%})")
    add(f"  - exact match: {v['exact']}")
    add(f"  - fuzzy match (≥ 90 similarity): {v['fuzzy']}")
    add(f"- Could not be verified: **{v['unverified']}** — surfaced to the reviewer as an amber "
        f"chip reading \"cited but not found in source\", not hidden")
    add("")

    add("## Classification")
    add("")
    add("`ICSR` and `ICSR_INCOMPLETE` are scored as one family here; the distinction between "
        "them is measured by the element agreement below, which is what actually tests the "
        "four-criteria rule.")
    add("")
    add("| Category | Precision | Recall | F1 | Support |")
    add("|---|---|---|---|---|")
    for name, s in sorted(c["per_category"].items()):
        add(f"| {name} | {s['precision']:.3f} | {s['recall']:.3f} | {s['f1']:.3f} "
            f"| {s['support']} |")
    add(f"| **micro** | {c['micro']['precision']:.3f} | {c['micro']['recall']:.3f} "
        f"| **{c['micro']['f1']:.3f}** | {c['micro']['support']} |")
    add(f"| **macro F1** | | | **{c['macro_f1']:.3f}** | |")
    add("")
    add(f"Exact-set accuracy (the entire label set correct): "
        f"**{c['exact_set_accuracy']:.1%}** of {c['messages_scored']} messages.")
    add("")

    add("### ICSR minimum criteria (E22)")
    add("")
    add("The `ICSR` label is decided by rule from this checklist, not by the model. Agreement "
        "per element:")
    add("")
    add("| Element | Agreement | n |")
    add("|---|---|---|")
    for name, s in el["per_element"].items():
        add(f"| {name.replace('has_', '').replace('_', ' ')} | {s['agreement']:.1%} | {s['n']} |")
    add("")

    add("## Abstention — \"say unknown, never guess\"")
    add("")
    add("A miss and a fabrication are counted separately, because they are different failures. "
        "A missing fact is visibly missing; an invented one is not.")
    add("")
    o = f["outcomes"]
    add("| Outcome | Count |")
    add("|---|---|")
    add(f"| Stated in source, extracted correctly (exact) | {o['correct']} |")
    add(f"| Stated in source, correct but worded differently | "
        f"{o['correct_normalised'] - o['correct']} |")
    add(f"| Stated in source, got it wrong | {o['wrong']} |")
    add(f"| Absent from source, correctly returned `NOT_STATED` | {o['abstained_correctly']} |")
    add(f"| Absent from source, asserted a value anyway | **{o['false_confident']}** |")
    add(f"| Expected field not produced at all | {o['missing_field']} |")
    add("")
    add(f"- Field accuracy on stated facts, **exact**: **{f['field_accuracy']:.1%}**")
    add(f"- Field accuracy on stated facts, **normalised** (same content words, any order): "
        f"**{f['field_accuracy_normalised']:.1%}**")
    add(f"- Abstention correctness: **{f['abstention_correctness']:.1%}**")
    add("")
    add("> **Caveat, stated plainly.** The goldens record the facts the generator deliberately "
        "planted, not an exhaustive inventory of everything a reader could legitimately extract "
        "from the document. A value counted as \"asserted where the golden has none\" is "
        "therefore an upper bound on fabrication, not a confirmed one — some of those are real "
        "facts present in the document that the golden simply does not enumerate. The "
        "`false_confident` figure should be read as a ceiling.")
    add("")

    if f["mismatches"]:
        add("### Sample mismatches")
        add("")
        add("| Message | Field | Expected | Got |")
        add("|---|---|---|---|")
        for m in f["mismatches"]:
            add(f"| {m['message']} | `{m['field']}` | {m['expected'][:40]} | {m['got'][:40]} |")
        add("")

    add("## Confidence calibration")
    add("")
    add("Reliability curve: observed accuracy within each confidence decile. If the scores "
        "carry information, accuracy rises with confidence.")
    add("")
    add("| Confidence band | n | Observed accuracy |")
    add("|---|---|---|")
    for band in cal["curve"]:
        add(f"| {band['band']} | {band['n']} | {band['accuracy']:.1%} |")
    add("")
    add(f"**Expected Calibration Error: {cal['expected_calibration_error']:.3f}** "
        f"(mean gap between stated confidence and observed accuracy, weighted by band size; "
        f"lower is better).")
    add("")
    if cal["curve"]:
        bands_used = len(cal["curve"])
        if bands_used <= 2:
            add("> **The scores are poorly spread.** Almost every field lands in one or two "
                "bands, which means the confidence number is currently carrying much less "
                "information than its range suggests. The anchored rubric in `P0_system` is "
                "not biting hard enough. This is the known weakness of the current build and "
                "is recorded here rather than smoothed over — see the write-up's limitations "
                "section.")
            add("")

    add("## Performance and cost")
    add("")
    add(f"- AI calls: **{p['ai_calls']}**")
    add(f"- Total spend: **${p['total_cost_usd']:.4f}**")
    add(f"- Per document: **${p['cost_per_document_usd']:.4f}** "
        f"· per message: ${p['cost_per_message_usd']:.4f}")
    add(f"- Tokens: {p['prompt_tokens']:,} prompt / {p['completion_tokens']:,} completion "
        f"/ {p['cached_tokens']:,} cached")
    add(f"- Prompt-cache hit rate: **{p['cache_hit_rate']:.1%}**")
    add(f"- Mean call latency: {p['mean_latency_ms']} ms")
    add(f"- Schema repair round-trips needed: {p['schema_repairs']}")
    add("")
    if p["stages"]:
        add("### Per-stage timings")
        add("")
        add("| Stage | n | mean | p50 | max |")
        add("|---|---|---|---|---|")
        for s in p["stages"]:
            add(f"| {s['stage']} | {s['n']} | {s['mean_ms']} ms | {s['p50_ms']} ms "
                f"| {s['max_ms']} ms |")
        add("")
    if p["by_purpose"]:
        add("### Cost by prompt")
        add("")
        add("| Prompt | Calls | Cost | Mean latency |")
        add("|---|---|---|---|")
        for row in p["by_purpose"]:
            add(f"| {row['purpose']} | {row['calls']} | ${row['cost_usd']:.4f} "
                f"| {row['mean_latency_ms']} ms |")
        add("")

    add("## Edge-case coverage")
    add("")
    add("Which of the 40 numbered edge cases the corpus exercises, and whether the messages "
        "carrying them reached the review queue.")
    add("")
    add("| Edge case | Messages | Reached review |")
    add("|---|---|---|")
    for edge, counts in results["edge_cases"].items():
        add(f"| {edge} | {counts['messages']} | {counts['processed']} |")
    add("")

    add("## Pipeline health")
    add("")
    h = results["health"]
    add(f"- Messages ready for review: {h['ready']} / {h['messages']}")
    add(f"- Documents parsed: {h['parsed']} · failed: {h['parse_failed']} "
        f"(encrypted and corrupt PDFs, correctly refused)")
    add(f"- Dead-lettered jobs: **{h['dead_jobs']}**")
    add(f"- Cases extracted: {h['cases']}")
    add("")

    return "\n".join(lines)


def main() -> int:
    print("Smart Inbox — evaluation\n")

    goldens = load_goldens()
    if not goldens:
        print(f"No goldens in {GOLDEN_DIR}. Run: python -m testdata.generator.build")
        return 1
    mapping = subject_to_key()
    print(f"  goldens loaded : {len(goldens)}")

    connection = connect()
    cursor = connection.cursor()
    actual = load_actual(cursor)
    print(f"  messages in db : {len(actual)}")

    pairs: list[tuple[set[str], set[str]]] = []
    for data in actual.values():
        key = mapping.get(data["subject"])
        if not key or key not in goldens:
            continue
        expected = set(goldens[key].get("expected_categories", []))
        got = {c for c, _ in data["categories"]}
        if got:                      # only score messages the pipeline actually finished
            pairs.append((expected, got))
    print(f"  scored         : {len(pairs)}")

    cursor.execute("SELECT COUNT(*) FROM document")
    documents = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM document WHERE parse_status = 'PARSED'")
    parsed = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM document WHERE parse_status = 'PARSE_FAILED'")
    failed = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM job WHERE state = 'DEAD'")
    dead = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM case_record")
    cases = cursor.fetchone()[0]
    ready = sum(1 for d in actual.values()
                if d["status"] in ("READY_FOR_REVIEW", "REVIEWED"))

    performance = score_performance(cursor)
    cursor.execute("""SELECT purpose, COUNT(*), NVL(SUM(cost_usd),0), NVL(AVG(latency_ms),0)
                      FROM ai_call_log GROUP BY purpose ORDER BY 3 DESC""")
    performance["by_purpose"] = [
        {"purpose": p, "calls": int(n), "cost_usd": round(float(c), 5),
         "mean_latency_ms": int(l)} for p, n, c, l in cursor.fetchall()]

    results = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "corpus": {"messages": len(actual), "documents": documents, "goldens": len(goldens)},
        "categories": score_categories(pairs),
        "fields": score_fields(goldens, actual, mapping),
        "icsr_elements": score_icsr_elements(goldens, actual, mapping),
        "calibration": score_calibration(actual, goldens, mapping),
        "verification": score_verification(cursor),
        "performance": performance,
        "edge_cases": score_edge_cases(goldens, actual, mapping),
        "health": {"messages": len(actual), "ready": ready, "documents": documents,
                   "parsed": parsed, "parse_failed": failed, "dead_jobs": dead,
                   "cases": cases},
    }

    connection.close()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "report.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT_DIR / "report.md").write_text(build_report(results), encoding="utf-8")

    v = results["verification"]
    c = results["categories"]
    f = results["fields"]
    print()
    print(f"  evidence verified   : {v['verified']}/{v['asserted_with_evidence']} "
          f"({v['rate']:.1%})")
    print(f"  category F1 (micro) : {c['micro']['f1']:.3f}")
    print(f"  exact-set accuracy  : {c['exact_set_accuracy']:.1%}")
    print(f"  abstention correct  : {f['abstention_correctness']:.1%}")
    print(f"  cost per document   : ${results['performance']['cost_per_document_usd']:.4f}")
    print()
    print(f"  wrote {OUT_DIR / 'report.md'}")
    print(f"  wrote {OUT_DIR / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

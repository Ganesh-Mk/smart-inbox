# Smart Inbox Assistant for a Healthcare Company
## Project Plan & Implementation Specification

**Project:** Clinevo Technologies — Live Assignment (Forward Deployment / GenAI Integration Engineer)
**Version:** 1.0 — 4 September 2026
**Target submission:** 11 September 2026 (7 calendar days)
**Status:** Plan complete — implementation not yet started

---

## 0. How to read this document

| Section | Purpose |
|---|---|
| 1–2 | What is actually being asked, and how the scoring rubric maps to work items |
| 3 | **Critical analysis** — the ambiguities and edge cases the brief does not spell out, and the decision taken for each |
| 4 | Domain primer — the pharmacovigilance rules we encode |
| 5–7 | Architecture, tech stack, non-functional targets |
| 8–13 | Component specifications (mail, DB, AI service, prompts, APIs, UI) — this is the build spec |
| 14–16 | Synthetic test data, evaluation harness, security & data handling |
| 17–21 | Repo layout, 7-day schedule, risks, assumptions, environment setup |

Sections 3 and 4 are what the 15–20 minute live walkthrough will be judged on. Sections 8–13 are what we code from.

---

## 1. The assignment in one paragraph

A shared pharma safety mailbox receives emails with PDF attachments from doctors, patients and other
sources. Today a human reads every one, decides what kind of message it is, and types out the key facts by
hand. We build a working prototype that does this first pass automatically: pull mail over IMAP, understand
the attached PDFs (digital, scanned/handwritten, published articles, non-English), sort each message into
one or more of four categories, extract the structured facts for each category with a confidence score and
a pointer back to the exact source, and present all of it on an Angular review screen where a human accepts
or overrides. Every AI decision and every reviewer action is written to an audit trail. Optional bonus: the
same engine applied to a batch of literature articles uploaded outside the mailbox.

**The core insight.** This is a document-understanding and classification problem with an unusually strict
*provenance* requirement — the brief says traceability is "required, not optional". The obvious build treats
`source` as a string the model fills in. We instead treat provenance as a first-class, **machine-verified**
data type: the model states where a fact came from, and code proves it. That is the main differentiator of
this submission, and it directly addresses the 10% traceability weight while also being the honest engineering answer.

---

## 2. Requirements → rubric → work items

| # | Requirement (brief §) | Rubric area | Weight | Where implemented |
|---|---|---|---|---|
| R1 | Connect to a real mailbox; parse sender/subject/date/body | Core functionality | 30% | §8 `mail` module (real IMAP: GreenMail or Gmail) |
| R2 | Grab every PDF attachment; log other file types | Core | 30% | §8.3 `MimeWalker`, `AttachmentSniffer` |
| R3 | Persist everything queryable in Oracle | Core / Traceability | 30 / 10% | §9 schema + PL/SQL packages |
| R4 | Detect and handle the 4 PDF flavours | Core | 30% | §10.2 flavour detector (per **page**) |
| R5 | Tables → structured rows/columns | Core | 30% | §10.5 |
| R6 | Meaningful images → description + review flag | Core | 30% | §10.6 |
| R7 | 10–15 sentence AI summary per PDF + relevance verdict | Core / AI quality | 30 / 25% | §11 prompt `P6_summarise` |
| R8 | Multi-label classification + confidence + one-line reason | Core / Domain | 30 / 10% | §11 prompt `P1_classify` + §4 rules |
| R9 | Angular review screen with accept/override | Core / Code | 30 / 20% | §13 |
| R10 | ICSR field extraction (6 field groups) | Core / AI quality | 30 / 25% | §11 prompt `P2_extract_icsr` |
| R11 | **Every fact links to email or PDF page** | Traceability | 10% | §12 evidence model + verifier |
| R12 | PQC fields (product/batch/lot, defect, photo mentioned) | Core | 30% | §11 prompt `P3_extract_pqc` |
| R13 | MI fields (questions asked, product/topic) | Core | 30% | §11 prompt `P4_extract_mi` |
| R14 | Say "unknown", never guess; confidence per field | AI quality | 25% | §11.4 rubric + schema `status` enum |
| R15 | Log every AI decision; timestamp every reviewer action | Traceability | 10% | §9.3 `AI_CALL_LOG`, `AUDIT_EVENT` (autonomous txn) |
| R16 | No real patient data; note the cloud-AI trade-off | Traceability | 10% | §14 generator, §16 data-handling note |
| R17 | Batch of 10–15 docs; report per-document timing | Core | 30% | §15 eval harness + `PROCESSING_METRIC` |
| R18 | A queue, not synchronous calls | Code & architecture | 20% | §9.4 Oracle `SKIP LOCKED` queue |
| R19 | Architecture diagram + 2–5 page write-up | Documentation | 5% | §5 + `docs/WRITEUP.md` |
| R20 | **Bonus:** literature screening, multi-case splitting | Bonus | +30% | §10.8 + prompt `P9_screen_article` |

Every rubric line has an owner. Nothing is left to "we'll see how far we get".

---

## 3. Critical analysis — ambiguities, edge cases, and the decision taken

This section is the point of the exercise. The brief is deliberately under-specified in places; each row is
a real decision with its reasoning. All of them are carried into `docs/WRITEUP.md` as declared assumptions,
per the brief's own instruction to "make a reasonable assumption, note it in your write-up, and keep going."

### 3.1 Mailbox and MIME

| # | Edge case | Decision |
|---|---|---|
| E1 | **What is a "real test mailbox"?** Gmail needs 2FA plus an app password, can be revoked, and breaks a demo without internet. | Build **one** IMAP code path (`jakarta.mail`) behind a `MailboxAdapter` interface. The default profile points at **GreenMail** in Docker — a genuine IMAP server on :3143 and SMTP on :3025 — seeded by our corpus generator. Reproducible, credential-free, works offline. Three env vars repoint the identical code at Gmail/Outlook. We demo GreenMail and show the Gmail config in the README. |
| E2 | **Re-processing the same mail on every poll.** | Dedupe key = the `Message-ID` header. It can be missing or duplicated in the wild → fallback key = SHA-256 of `(from, subject, sent_at, normalised body)`. Unique constraint in Oracle makes the poller idempotent by construction, not by convention. |
| E3 | **Multipart bodies:** `multipart/alternative` (text + HTML), `multipart/related` (inline images), arbitrarily nested `multipart/mixed`. | Depth-first walk. Prefer `text/plain`; if absent, convert `text/html` → text (jsoup) and store both. Preserve the declared charset; let jakarta.mail decode quoted-printable / base64. |
| E4 | **The attachment lies about its type** — `application/octet-stream`, or a `.dat` that is really a PDF. | Never trust `Content-Type` or the extension. Sniff magic bytes (`%PDF-`) on the first 1 KB. Store the declared type *and* the sniffed type; they differing is itself audit-interesting. |
| E5 | **A forwarded email arrives as a `message/rfc822` attachment** — very common in real safety mailboxes; the actual case sits one level down. | Recurse **one** level into `message/rfc822`, hoist its attachments onto the parent message, record `nesting_level`. Deeper nesting is logged and skipped with a reason. |
| E6 | **ZIP / DOCX / image attachments.** | Recorded in `MESSAGE_ATTACHMENT` with `processed='N'`, `skip_reason='UNSUPPORTED_TYPE'` — the brief explicitly permits logging non-PDFs. Exception: a bare image attachment (`.jpg` of a damaged blister pack) gets the same cheap vision description as an embedded image. One extra branch, visible payoff on PQC cases. |
| E7 | **Password-protected, corrupt, or zero-byte PDF.** | Detected at open time. Document status `PARSE_FAILED` with a reason; the message is still classified from its body; the item reaches the reviewer flagged `NEEDS_ATTENTION` rather than vanishing. |
| E8 | **Oversized attachment** (a 60 MB colour scan). | Configurable caps (default 25 MB / 60 pages). Over cap → first N pages processed and `truncated=true` shown in the UI. The service must never OOM on an 8 GB machine. |
| E9 | **The same PDF attached to several emails.** | The blob store is content-addressed by SHA-256 and parse results are cached per content hash. The second occurrence costs zero LLM calls. Real money saved on the batch run and an easy talking point. |
| E10 | **Reply / forward chains** where the quoted history repeats the case, risking double extraction. | Detect the quote boundary (`On <date> ... wrote:`, `>` prefixes, `-----Original Message-----`) and segment the body. New text is the primary source; quoted text is retained but de-prioritised in the prompt and never counted as an independent report. |
| E11 | **Email body only, no attachment** — a perfectly valid ICSR. | The email body is itself a `DOCUMENT` row with `source_kind='EMAIL_BODY'`. Everything downstream is uniform: one code path for bodies and PDFs, one evidence model for both. |

### 3.2 PDF understanding

| # | Edge case | Decision |
|---|---|---|
| E12 | **The four "flavours" are not mutually exclusive, and the brief implies a single per-document label.** A real submission is a digital cover letter plus a scanned annex; a non-English document is *also* digital or scanned. Forcing one label is wrong. | **Flavour is detected per page, not per document.** Two orthogonal axes — *rendering* (`DIGITAL` \| `SCANNED`) and *genre* (`FORM` \| `ARTICLE` \| `LETTER`) — plus *language* as an attribute. The document-level flavour is a roll-up and `MIXED` is a legal value. This is the single most important structural decision in the PDF layer, and it is what lets one document exercise several of the brief's four handling paths at once. |
| E13 | **Detecting "scanned".** The naive rule (no extractable text) fails on scanners that embed garbage OCR text. | Composite per-page score: extractable characters < 100, **or** a single image covers > 80% of the page area, **or** the extracted text fails a printable-character-ratio / dictionary sanity check. Any hit → treat as scanned and use vision. It errs toward vision, which is safe: vision on a digital page still works. |
| E14 | **Two-column articles destroy naive reading order** — `get_text()` interleaves the columns into nonsense, and every downstream extraction inherits the damage. | Column-aware reading order: cluster text blocks by x-midpoint (1-D k-means over k ∈ {1,2,3} with a separation check), then sort by y within each column. Article genre is then detected from column count ≥ 2 plus markers (`Abstract`, `DOI`, `References`, `Keywords`). A concrete, demonstrable correctness win we can show side-by-side in the walkthrough. |
| E15 | **"Ignore references and general discussion"** in articles. | Section segmentation by heading regex. `References` / `Bibliography` / `Acknowledg*` / `Conflict of interest` / `Funding` sections are extracted but marked `excluded_from_case=true` and withheld from the extraction prompt (they still feed the summary prompt). This stops the model manufacturing a "patient" out of a citation — a failure mode that is otherwise very common. |
| E16 | **Non-English: translate, or extract natively?** The brief lets us choose but demands "a link back to the original text". | **Both, deliberately.** Per page we keep `text_original` (source language, canonical, with character offsets) and `text_english` (translation). **All evidence quotes and offsets always point at the original text**, because the original is the record of truth for audit. Extracted values carry `value` plus `value_en`. A translation never becomes the evidentiary record. |
| E17 | **Mixed-language pages** — English form labels with German free text; and language ID is unreliable on short strings. | Language detection at block level with **lingua-py** (materially better than `langdetect` on short text and it returns a confidence), rolled up to a page `primary_language` plus a `languages[]` list. Below the confidence threshold we defer to the model's own reported language rather than asserting one. |
| E18 | **Tables:** borderless, spanning cells, split across a page break, rotated pages. | Primary: PyMuPDF `page.find_tables()` → `{headers[], rows[][], bbox, page_no}`. Fallback when it finds nothing but the block layout looks tabular: crop the region and send the image to the vision model with a table-to-JSON prompt. Cross-page continuation is merged when the next page's first table has an identical header row. Page `/Rotate` is normalised before anything else runs. |
| E19 | **"Meaningful images"** — logos, header banners and 3×3 px spacers are not meaningful, and describing them wastes money and clutters review. | Filter: area ≥ 3% of the page, colour standard deviation above a floor (kills solid blocks), and **not repeated across pages by xref** (kills letterheads and logos). A full-page image *is* the scanned page and is handled by the page path, not as an embedded image. Survivors get a one-paragraph vision description, a category (`PRODUCT_DEFECT` \| `CLINICAL_PHOTO` \| `FORM_CHECKBOX` \| `CHART` \| `OTHER`) and `needs_review='Y'` — exactly what the brief asks for. |
| E20 | **A 200-page PDF** blows the context window and the budget. | Page cap plus map-reduce: per-page digests → grouped into ≤ 30k-token chunks → group summaries → one final 10–15 sentence summary. Field extraction runs only over the pages triage marked case-relevant, not the whole document. |

### 3.3 Classification

| # | Edge case | Decision |
|---|---|---|
| E21 | **It is multi-label, and one label is an "else" bucket.** An LLM will cheerfully return `NOT_RELEVANT` alongside `ICSR`. | `NOT_RELEVANT` is enforced **in code**, not by prompt: it is assigned if and only if the other three sets are empty. A post-processing rule with a unit test, not a hope. |
| E22 | **What actually makes something an ICSR?** The brief says "a specific patient, a specific person reporting it, a specific drug, and a bad outcome — all four present, even loosely." That is a plain-English restatement of the regulatory **four minimum criteria**. | The model returns an explicit **element checklist** — `has_identifiable_patient`, `has_identifiable_reporter`, `has_suspect_product`, `has_adverse_event` — each with its own evidence and confidence. The `ICSR` label is then decided **by rule**: all four present → valid ICSR with confidence = min of the four; two or three present → `ICSR_INCOMPLETE`, surfaced to the reviewer with the missing elements named. This turns a fuzzy judgement into an auditable, defensible decision and is the biggest single win available on the "Getting the domain right" 10%. |
| E23 | **ICSR + PQC together** — a defective product that caused a reaction. The brief calls this out explicitly. | Labels are independent booleans, never a softmax. Both extraction pipelines run; the UI shows two chips with two confidences. |
| E24 | **MI + ICSR** — "my rash got worse, how should I taper the dose?" | Same mechanism. The rules are independent: presence of a genuine question → MI; presence of the four elements → ICSR. They do not compete for probability mass. |
| E25 | **Message-level vs document-level classification.** A bland covering email with an ICSR form attached must not come out "Not Relevant". | Classify each source unit (body, and each document) **and** roll up to the message as the union, with every label naming the unit that triggered it. Both levels are stored and both are shown in the UI. |
| E26 | **Confidence inflation** — LLMs emit 0.95 for everything, which makes the score worthless. | Three defences. (a) An anchored rubric in the cached system prompt with a worked example per band. (b) Forced abstention — the schema's `status` enum makes `NOT_STATED` a first-class, zero-cost answer rather than something the model must argue for. (c) **Deterministic downgrades applied in code after the call**: unverified evidence, low page legibility, and cross-source conflicts each cap the final confidence. We then report actual calibration on the golden set in the write-up, which is far more persuasive than claiming the scores are meaningful. |

### 3.4 Extraction and traceability

| # | Edge case | Decision |
|---|---|---|
| E27 | **"Every extracted fact must link back to exactly where it came from."** A self-reported page number is not proof — models hallucinate citations, and a fabricated citation in a regulated system is worse than no citation. | Every field carries `evidence[] = {source_type, document_id, page_no, quote, char_start, char_end, bbox?}`. A **deterministic verifier** then searches for `quote` inside that page's `text_original` — normalised whitespace and unicode first, then `rapidfuzz` ≥ 90 if the exact match fails. On success we overwrite `char_start`/`char_end` with the **real** offsets and resolve a bounding box via PyMuPDF; on failure the field is marked `evidence_verified='N'` and its confidence is capped at 0.4. **We never trust the model's citation — we prove it.** This is what makes the click-to-highlight UI trustworthy, and it is the direct answer to the 10% traceability weight. |
| E28 | **Dates.** "last March", "two weeks ago", "in 2023", "03/04/2024" (US or EU reading?). | A `PartialDate` type: `{raw, iso, precision: DAY\|MONTH\|YEAR\|UNKNOWN, is_relative}`. Relative dates are **never silently resolved against today** — stored raw with `is_relative=true` and flagged for the reviewer. Ambiguous numeric dates keep both readings with `precision=UNKNOWN` unless the document's language or locale disambiguates. |
| E29 | **Units and dosing.** "154 lb", "500 mg BID", "2 puffs PRN". | Structured as `{value, unit, raw}` plus `dose: {amount, unit, frequency_raw, route}`. `raw` is always retained. We normalise but never destroy the source string. |
| E30 | **Age.** "elderly", "3 y.o.", a date of birth instead of an age, "6-week-old". | `{value, unit: YEAR\|MONTH\|WEEK\|DAY, raw, derived_from_dob}`. Free-text descriptors live in `raw` with `status=UNCERTAIN` rather than being coerced to a number. |
| E31 | **Multiple products and multiple reactions in one case.** | Arrays throughout, with `role: SUSPECT \| CONCOMITANT` on products and a per-reaction outcome plus seriousness. |
| E32 | **Multiple patients in one document** — a case series article. This is exactly the bonus requirement. | The data model carries `CASE_RECORD.case_index` from day one, so one document can hold N cases. The bonus then requires **no schema change** — only a splitting prompt and an upload endpoint. Designing for this on day one is why the +30% is cheap to reach. |
| E33 | **The email body and the attachment disagree** — body says "58-year-old female", the form says age 85. | Extraction runs per source unit; a merge step detects field-level conflicts and stores **both** values with `status=CONFLICT`, each with its own evidence. The UI shows them side by side and asks the reviewer to choose. Silently picking one would be the wrong behaviour in a regulated context, and saying so is a good walkthrough moment. |
| E34 | **Handwriting uncertainty has to flow downstream**, not stop at the OCR step. | The vision transcription returns a per-page `legibility` score and per-segment `uncertain` markers. Propagation rule: `final_field_confidence = min(model_field_confidence, page_legibility)`. Deterministic, explainable, and it satisfies "show a confidence score since handwriting is uncertain" at the field level rather than only at the page level. |
| E35 | **"Serious" is a regulatory term of art, not an adjective.** | Seriousness is a fixed enum of the six regulatory criteria (§4.2), each an independent boolean with its own evidence — not a free-text field and not a severity slider. |

### 3.5 System behaviour

| # | Edge case | Decision |
|---|---|---|
| E36 | **AI calls fail, rate-limit, or return invalid JSON.** | Typed retry policy: `429`/`5xx`/timeout → exponential backoff with jitter, 3 attempts. Schema-invalid JSON → exactly one repair round-trip with the validation error appended to the conversation; still invalid → job `FAILED` and the item is surfaced as `NEEDS_ATTENTION`. Never a silent partial write. |
| E37 | **A worker crashes mid-job**, leaving a row locked forever. | Jobs carry `locked_by` and `locked_at`; `PKG_JOB_QUEUE.reap_stale_locks` returns anything locked past its lease to `PENDING`. Every job handler is idempotent (delete-then-insert keyed on `(subject, stage)`), so re-running is always safe. |
| E38 | **Poison messages** retry forever and burn the budget. | `attempts >= max_attempts` → state `DEAD`, visible in the UI, never retried automatically. |
| E39 | **A message is partly done** — three of four PDFs parsed. | Classification is gated on a **completion barrier**: a PL/SQL check that every document for the message is terminal (`PARSED` or `PARSE_FAILED`). A failed document does not block the message; it is declared as missing in the prompt so the model can honestly lower its confidence. |
| E40 | **The reviewer edits a record the AI wrote.** | Records are append-only. The AI value stays; the override is a new row with `decided_by='REVIEWER'`; `AUDIT_EVENT` records before/after JSON with actor and timestamp. Nothing is overwritten in place. This is what a 21 CFR Part 11-style regulated system requires, and it is a strong point to raise unprompted in the walkthrough. |

---

## 4. Domain primer — the rules we encode

Written out explicitly because "Getting the domain right" is 10% of the score and because these rules drive §11's prompts.

### 4.1 The four categories

| Category | Decision rule we implement | Positive signals | Negative signals |
|---|---|---|---|
| **ICSR** (Safety Report) | All four minimum elements present (§4.3) | An identifiable patient, a reporter with a role, a suspect drug, an adverse outcome | No patient at all; hypothetical "what if" phrasing; aggregate study data with no individual case |
| **PQC** (Quality Complaint) | A physical defect in the product itself | broken seal, wrong colour or odour, contamination, particulates, damaged packaging, counterfeit, wrong tablet count, leaking, cracked tablet | Dissatisfaction with efficacy (not a defect); an adverse reaction with no defect described |
| **MI** (Info Request) | A genuine question about a product, with no reaction and no defect | dosing, administration, storage, interactions, pregnancy/lactation, "can I…", "what is the…" | A question *plus* a reaction → both MI and ICSR, not MI alone |
| **Not Relevant** | Assigned **only** when the other three are empty | marketing, newsletters, out-of-office replies, internal admin, invoices, spam | — |

### 4.2 Seriousness criteria (fixed enum `SeriousnessCriterion`)

`DEATH`, `LIFE_THREATENING`, `HOSPITALISATION_OR_PROLONGATION`, `DISABILITY_OR_INCAPACITY`,
`CONGENITAL_ANOMALY`, `OTHER_MEDICALLY_IMPORTANT`.

A case is *serious* if any criterion is true. Each is an independent boolean with its own evidence, so
"hospitalised" and "life-threatening" are never conflated into one vague severity score.

### 4.3 The four ICSR minimum elements (`IcsrValidity`)

1. **Identifiable patient** — age, sex, initials, patient ID, or any descriptor that pins down one person.
2. **Identifiable reporter** — a person with a role (physician, pharmacist, patient, nurse, consumer, lawyer), ideally with a country.
3. **Suspect product** — at least one named medicinal product suspected of causing the event.
4. **Adverse event / outcome** — an undesirable medical occurrence.

Each is reported with evidence; the label decision is then **rule-based rather than model-based** (see E22).

### 4.4 Supporting enums

- **Outcome:** `RECOVERED`, `RECOVERING`, `NOT_RECOVERED`, `RECOVERED_WITH_SEQUELAE`, `FATAL`, `UNKNOWN`
- **Reporter role:** `PHYSICIAN`, `PHARMACIST`, `NURSE`, `OTHER_HCP`, `PATIENT`, `CONSUMER`, `LAWYER`, `UNKNOWN`
- **Route:** `ORAL`, `IV`, `IM`, `SUBCUTANEOUS`, `TOPICAL`, `INHALATION`, `OPHTHALMIC`, `RECTAL`, `OTHER`, `UNKNOWN`
- **Field status:** `STATED`, `NOT_STATED`, `UNCERTAIN`, `CONFLICT`
---

## 5. Architecture

```
                        ┌───────────────────────────────────────────────┐
                        │   Angular 22 reviewer UI      localhost:4200  │
                        │   queue · page viewer · editable fields ·     │
                        │   evidence highlight · audit timeline         │
                        └────────────────────┬──────────────────────────┘
                                             │ REST/JSON + SSE
                                             ▼
  ┌───────────────┐    IMAP :3143   ┌───────────────────────────────────────────┐
  │  GreenMail    │◄───────────────►│  Spring Boot 3.5 / Java 21      :8080     │
  │  test mailbox │    SMTP :3025   │  ───────────────────────────────────────  │
  │  (Docker)     │                 │  MailPoller · MimeWalker · IngestService  │
  └───────────────┘                 │  JobWorkerPool (4) · ReviewService        │
   (or Gmail IMAP —                 │  AuditService · REST controllers          │
    same code path)                 └──────┬──────────────────────┬─────────────┘
                                           │ REST/JSON            │ JDBC
                                           ▼                      ▼
                      ┌────────────────────────────────┐  ┌───────────────────────────┐
                      │ Python FastAPI AI service :8000│  │ Oracle Database 23ai Free │
                      │ ────────────────────────────── │  │ (Docker)           :1521  │
                      │ PyMuPDF  flavour · blocks ·    │  │ ───────────────────────── │
                      │   reading order · tables ·     │  │ 16 tables + 1 view        │
                      │   images · render · language   │  │ PKG_JOB_QUEUE             │
                      │ LLM  triage · classify ·       │  │ PKG_AUDIT (autonomous)    │
                      │   extract · summarise          │  │ JOB queue, SKIP LOCKED    │
                      │ Verifier  evidence proof       │  └───────────────────────────┘
                      └───────────────┬────────────────┘
                                      │ HTTPS                  ┌────────────────────┐
                                      ▼                        │ Blob store         │
                      ┌────────────────────────────────┐       │ ./data/blobs       │
                      │ OpenRouter                     │       │ SHA-256 content-   │
                      │ anthropic/claude-haiku-4.5     │       │ addressed          │
                      │ (text + vision, 200K ctx)      │       └────────────────────┘
                      └────────────────────────────────┘
```

Flow: **Angular → Spring Boot → Python AI service → Oracle**, exactly as the brief prescribes, with a
durable queue between ingestion and AI work so nothing on the critical path is synchronous.

### 5.1 Why responsibilities split this way

- **Spring Boot owns state, orchestration and security.** It never calls an LLM. It reads mail, writes
  Oracle, drives the queue, serves the API and records the audit trail.
- **The Python service is a stateless pure function:** `(bytes, task, params) → JSON`. No database handle,
  no queue, no session state. That makes it independently testable, independently deployable and trivially
  scalable horizontally — and it is the cleanest available answer to "clean separation across the stack" (20%).
- **Oracle is not merely a data sink.** It holds the work queue and the audit logic in PL/SQL, which is what
  the stated stack ("Oracle (PL/SQL)") actually implies.

### 5.2 Processing state machine

```
INBOX_MESSAGE.status

  RECEIVED ─▶ PARSING ─▶ PARSED ─▶ CLASSIFYING ─▶ CLASSIFIED ─▶ EXTRACTING ─┐
                                                       │                     │
                                     (only NOT_RELEVANT)│                     │
                                                       └────────▶ READY_FOR_REVIEW ─▶ REVIEWED
  any stage ─▶ NEEDS_ATTENTION   (recoverable; visible in UI)
  any stage ─▶ FAILED            (dead-lettered after max attempts)
```

Job types and their sequencing:

```
PARSE_DOCUMENT (fan-out: 1 per document)
      └── completion barrier (all documents terminal)
            └── CLASSIFY_MESSAGE
                  └── EXTRACT_CASE  (fan-out: 1 per matched category)
                        └── FINALISE_MESSAGE  (merge, conflict detect, verify, set READY_FOR_REVIEW)

SCREEN_ARTICLE  (bonus path — entered directly from the upload endpoint)
```

---

## 6. Tech stack — decisions and rejected alternatives

| Layer | Chosen | Version | Why, and what was rejected |
|---|---|---|---|
| Frontend | **Angular** + Angular Material | 22.x | Stated stack. Material buys a credible review UI fast. Rejected: hand-rolled CSS (slower), React (off-spec). |
| Backend | **Spring Boot** (Java) | **3.5.16**, `--release 21` | Stated stack. Chose the 3.5 line over the current 4.1 deliberately: 3.5 is the production-proven branch and an assignment is not the place to debug a brand-new major version. Compiles on the installed JDK 24 targeting 21. |
| Build | Maven Wrapper (`mvnw`) | — | Maven is not installed locally; the wrapper self-bootstraps. A reviewer needs no machine setup. |
| AI service | **Python** + FastAPI + Uvicorn | 3.12 / 0.115 | Stated stack. FastAPI gives OpenAPI docs for free, which is genuinely useful during the live walkthrough. |
| PDF engine | **PyMuPDF (fitz)** | latest | Text with per-span bounding boxes (needed for evidence highlighting), `find_tables()`, image xrefs, and page rendering — all from one pure-wheel dependency. **Rejected: Tesseract + poppler** — native installs, and unnecessary once a vision model does the OCR. Zero non-pip system dependencies is a real win on "clear setup instructions". |
| Language ID | **lingua-py** | latest | Materially better than `langdetect` on short strings and returns a usable confidence. |
| Fuzzy match | **rapidfuzz** | latest | Evidence-quote verification (§12). |
| Database | **Oracle Database 23ai Free** | `gvenzl/oracle-free:23-slim-faststart` | Stated stack. Slim/faststart keeps the image to ~4.6 GB and boots in seconds; SGA capped for an 8 GB machine. We write real PL/SQL packages, not just tables. |
| Migrations | Flyway | bundled with Boot | Versioned, repeatable schema so a reviewer can rebuild from zero. |
| Mail server | **GreenMail standalone** (Docker) | latest | A genuine IMAP/SMTP server: the code path is real IMAP, but there are no external credentials and the demo works offline. One profile switch → Gmail. |
| Queue | Oracle table + `FOR UPDATE SKIP LOCKED` + Spring poller | — | The brief permits an in-process queue; a DB queue is barely more work and is **durable, restart-safe and inspectable in SQL**. Rejected: RabbitMQ/Kafka — infrastructure weight on an 8 GB machine for no marginal credit. |
| Blob store | Content-addressed filesystem `./data/blobs/<sha256>` | — | Free attachment de-duplication (E9); keeps multi-MB BLOBs out of Oracle. Paths recorded in the DB. |
| **AI model** | **`anthropic/claude-haiku-4.5` via OpenRouter** | — | Constrained choice; see §6.1. |
| LLM client | `openai` Python SDK against `https://openrouter.ai/api/v1` | 1.58 | OpenRouter is OpenAI-wire-compatible, so the standard SDK keeps the client thin and the provider swappable. |

### 6.1 AI model decision

Verified live against the OpenRouter model catalogue on 4 September 2026:

| Property | Value |
|---|---|
| Model id | `anthropic/claude-haiku-4.5` |
| Context / max output | 200,000 / 64,000 tokens |
| Input modalities | `text`, `image`, `file` — **vision confirmed** |
| Supported parameters | `response_format`, **`structured_outputs`**, `tools`, `tool_choice`, `reasoning`, `temperature`, `top_p`, `top_k`, `stop` |
| Pricing | **$1.00 / Mtok input, $5.00 / Mtok output**; cache read **$0.10 / Mtok**, cache write $1.25 / Mtok |
| Half-price batch variant | `anthropic/claude-haiku-4.5:batch` |

**Why this is a defensible choice on the merits**, not merely a constraint we accepted: the workload is
high-volume, schema-constrained document extraction with a human in the loop — exactly where a fast, cheap
model with reliable structured output and vision beats a frontier model. The cost/speed/accuracy trade-off
the brief asks us to document: a full 20-document batch costs roughly **$0.60** (≈300k input tokens
including rendered page images, ≈60k output), so we can afford several full re-runs per day while tuning
prompts, and the confidence-plus-human-review design is precisely what absorbs the residual accuracy gap.
Where a larger model would genuinely help — subtle multi-case splitting in articles — we compensate with
better task decomposition rather than a bigger model, and we say so honestly in the write-up.

**Consequences of routing through OpenRouter, and how each is handled:**

| Consequence | Handling |
|---|---|
| Anthropic's **native citations API is not exposed** through OpenRouter's OpenAI-compatible surface. | Already irrelevant to our design: we use self-reported evidence plus a **deterministic verifier** (E27), which is stronger than trusting any citation API and is portable across providers. |
| OpenRouter's PDF `file-parser` plugin **defaults to `mistral-ocr`** — a different vendor's model. | Forbidden by the single-model constraint. We do **not** use the file plugin at all. PDFs are parsed locally by PyMuPDF and scanned pages are sent as **rendered PNG images** to Claude's own vision. 100% of inference is Claude Haiku 4.5. |
| Two processors sit in the data path (OpenRouter, then Anthropic). | Stated plainly in the data-handling note (§16) — the brief explicitly asks for this trade-off to be noted. |
| Prompt caching is still available via `cache_control: {"type":"ephemeral"}` on content parts; usage returns as `usage.prompt_tokens_details.cached_tokens`. | We cache the large static system preamble (taxonomy, rubric, schema guidance) — a 10× price reduction on that segment and a latency win. |

---

## 7. Non-functional targets

| Target | Value |
|---|---|
| End-to-end latency, typical 2-page digital PDF email | ≤ 15 s |
| End-to-end latency, 6-page scanned PDF | ≤ 60 s |
| Batch of 15 documents at 4 workers | ≤ 6 min wall clock |
| Cost per document | ≤ $0.05 |
| Evidence verification rate on the golden set | ≥ 90% of `STATED` fields |
| Category F1 on the golden set | ≥ 0.90 |
| Cold `docker compose up` → usable UI | ≤ 5 min |

---

## 8. Spring Boot service specification

### 8.1 Package layout

```
com.clinevo.inbox
├── config/          AppProperties, AsyncConfig, WebConfig(CORS), OpenApiConfig
├── mail/            MailboxAdapter, ImapMailboxAdapter, MailPoller,
│                    MimeWalker, AttachmentSniffer, QuotedTextDetector
├── ingest/          IngestService, DedupeService, BlobStore
├── queue/           JobQueueRepository, JobWorkerPool, JobHandler (SPI),
│                    handlers/{ParseDocument,ClassifyMessage,ExtractCase,
│                              FinaliseMessage,ScreenArticle}Handler
├── ai/              AiServiceClient (WebClient), dto/ (request+response records)
├── domain/          Entities + enums
├── repo/            Spring Data JPA repositories + JdbcTemplate for the queue
├── merge/           CaseMergeService (cross-source conflict detection, E33)
├── review/          ReviewService, OverrideService
├── audit/           AuditService (writes AUDIT_EVENT via PKG_AUDIT)
├── metrics/         StageTimer, ProcessingMetricService
├── literature/      LiteratureUploadService (bonus)
└── api/             Controllers + view DTOs + SSE emitter
```

### 8.2 Mail ingestion

`MailPoller` runs on a fixed schedule (default 10 s, configurable) against `MailboxAdapter`:

1. Open the folder read-write, `SEARCH UNSEEN` (or since a stored high-water UID).
2. For each message compute the dedupe key (E2); skip if it already exists.
3. Persist `INBOX_MESSAGE` (sender, sender name, subject, sent/received dates, body text, body HTML, charset).
4. Walk the MIME tree (`MimeWalker`, E3/E5): collect parts, hoist one level of `message/rfc822`.
5. For each attachment: sniff type (E4), stream to `BlobStore` computing SHA-256, insert `MESSAGE_ATTACHMENT`.
6. Create `DOCUMENT` rows: one `EMAIL_BODY` document plus one `PDF_ATTACHMENT` per PDF.
7. Enqueue one `PARSE_DOCUMENT` job per document. Mark the message `PARSING`. Flag `\Seen`.

`ImapMailboxAdapter` is configured by `inbox.mail.{host,port,user,password,ssl,folder}` — GreenMail by
default, Gmail by changing four values. The Gmail recipe (app password, `imap.gmail.com:993`, SSL) is
documented in the README.

### 8.3 Queue and workers

`JobWorkerPool` starts N daemon threads (default 4). Each loop:

```
jobs = PKG_JOB_QUEUE.dequeue(worker_id, batch_size)   -- FOR UPDATE SKIP LOCKED
for job in jobs:
    handler = registry[job.type]
    timer.start()
    try:    handler.handle(job);  PKG_JOB_QUEUE.complete(job.id)
    except: PKG_JOB_QUEUE.fail(job.id, error, backoff)   -- DEAD past max_attempts
    finally: PROCESSING_METRIC.record(subject, stage, elapsed_ms)
```

A separate scheduled task calls `PKG_JOB_QUEUE.reap_stale_locks()` every 60 s (E37).

### 8.4 REST API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/messages` | Paged review queue. Filters: `status`, `category`, `minConfidence`, `flagged`, `q`. Sorted by (needs-attention, lowest confidence, oldest). |
| `GET` | `/api/messages/{id}` | Full detail: message, documents, pages, tables, images, classifications, cases, fields with evidence, conflicts. |
| `GET` | `/api/documents/{id}/pages/{n}/image` | Rendered page PNG (served from the parse-time render cache). |
| `GET` | `/api/documents/{id}/summary` | AI summary + relevance verdict + reason. |
| `POST` | `/api/messages/{id}/review` | `{decision, categories[], notes}` — accept or override the classification. |
| `PATCH` | `/api/cases/{caseId}/fields/{fieldId}` | Reviewer override of one extracted field (`{value, status, note}`). |
| `POST` | `/api/messages/{id}/reprocess` | Re-enqueue from a chosen stage (demo + recovery). |
| `GET` | `/api/messages/{id}/audit` | Full audit timeline for the message. |
| `GET` | `/api/ai-calls/{id}` | The raw prompt/response for one AI decision — the traceability money shot. |
| `POST` | `/api/literature/batches` | **Bonus.** Multipart upload of N article PDFs → returns batch id. |
| `GET` | `/api/literature/batches/{id}` | **Bonus.** Screening results, one row per detected case. |
| `GET` | `/api/stats/batch-report` | Per-document timing + token + cost report (deliverable R17). |
| `GET` | `/api/events` | SSE stream of status changes so the queue updates live. |

Security: Spring Security with HTTP Basic and two in-memory roles (`REVIEWER`, `ADMIN`) — enough to make
the reviewer identity real for the audit trail without burning a day on auth. Called out as a deliberate
prototype simplification in the write-up.

---

## 9. Oracle schema and PL/SQL

Flyway migrations under `backend/src/main/resources/db/migration/`:
`V1__tables.sql`, `V2__indexes.sql`, `V3__packages.sql`, `V4__triggers_views.sql`.

### 9.1 Core tables

| Table | Key columns |
|---|---|
| `INBOX_MESSAGE` | `id`, `dedupe_key` (UQ), `message_id_hdr`, `folder`, `imap_uid`, `sender_email`, `sender_name`, `subject`, `sent_at`, `received_at`, `body_text` CLOB, `body_html` CLOB, `body_charset`, `quoted_offset`, `status`, `needs_attention`, `created_at` |
| `MESSAGE_ATTACHMENT` | `id`, `message_id` FK, `filename`, `declared_type`, `sniffed_type`, `size_bytes`, `sha256`, `blob_path`, `nesting_level`, `processed`, `skip_reason` |
| `DOCUMENT` | `id`, `message_id` FK (nullable for uploads), `attachment_id` FK (nullable), `source_kind` (`EMAIL_BODY`\|`PDF_ATTACHMENT`\|`UPLOADED_ARTICLE`), `content_sha256`, `page_count`, `doc_rendering` (`DIGITAL`\|`SCANNED`\|`MIXED`), `doc_genre` (`FORM`\|`ARTICLE`\|`LETTER`\|`MIXED`), `primary_language`, `is_encrypted`, `truncated`, `parse_status`, `parse_error`, `parse_ms` |
| `DOCUMENT_PAGE` | `id`, `document_id` FK, `page_no`, `rendering`, `genre`, `language`, `lang_confidence`, `char_count`, `has_text_layer`, `column_count`, `text_original` CLOB, `text_english` CLOB, `legibility`, `width`, `height`, `rotation`, `render_path` |
| `DOCUMENT_SECTION` | `id`, `document_id`, `page_no`, `heading`, `section_kind`, `char_start`, `char_end`, `excluded_from_case` — supports E15 |
| `DOCUMENT_TABLE` | `id`, `document_id`, `page_no`, `table_index`, `n_rows`, `n_cols`, `caption`, `headers_json`, `rows_json` CLOB, `bbox`, `extraction_method` |
| `DOCUMENT_IMAGE` | `id`, `document_id`, `page_no`, `image_index`, `xref`, `bbox`, `width`, `height`, `category`, `description`, `needs_review`, `blob_path` |
| `DOCUMENT_SUMMARY` | `id`, `document_id`, `summary_text` CLOB, `sentence_count`, `relevance` (`RELEVANT`\|`POSSIBLY`\|`NOT_RELEVANT`), `relevance_reason`, `model`, `prompt_version`, `ai_call_id` |

### 9.2 Classification, cases, evidence

| Table | Key columns |
|---|---|
| `CLASSIFICATION` | `id`, `subject_type` (`MESSAGE`\|`DOCUMENT`), `subject_id`, `category` (`ICSR`\|`ICSR_INCOMPLETE`\|`PQC`\|`MI`\|`NOT_RELEVANT`), `confidence`, `reason`, `decided_by` (`AI`\|`REVIEWER`), `superseded_by`, `model`, `prompt_version`, `ai_call_id`, `created_at` |
| `ICSR_VALIDITY` | `id`, `classification_id` FK, `has_patient`, `has_reporter`, `has_product`, `has_event` (each Y/N + confidence + evidence FK), `missing_elements_json` |
| `CASE_RECORD` | `id`, `message_id`, `document_id`, `case_index`, `case_type` (`ICSR`\|`PQC`\|`MI`), `narrative` CLOB, `is_serious`, `seriousness_json`, `confidence`, `ai_call_id` |
| `EXTRACTED_FIELD` | `id`, `case_id` FK, `field_group`, `field_path`, `value_text`, `value_json`, `value_en`, `unit`, `raw_text`, `status` (`STATED`\|`NOT_STATED`\|`UNCERTAIN`\|`CONFLICT`), `confidence`, `confidence_pre_adjust`, `adjust_reason`, `decided_by`, `superseded_by` |
| `FIELD_EVIDENCE` | `id`, `field_id` FK, `source_type` (`EMAIL_BODY`\|`PDF_PAGE`\|`TABLE`\|`IMAGE`), `document_id`, `page_no`, `quote`, `char_start`, `char_end`, `bbox`, `verified` (Y/N), `verify_method` (`EXACT`\|`FUZZY`\|`FAILED`), `match_score` |

### 9.3 Operations, audit, metrics

| Table | Key columns |
|---|---|
| `JOB` | `id`, `job_type`, `subject_type`, `subject_id`, `state` (`PENDING`\|`RUNNING`\|`DONE`\|`FAILED`\|`DEAD`), `priority`, `attempts`, `max_attempts`, `available_at`, `locked_by`, `locked_at`, `last_error`, `created_at`, `updated_at` |
| `AI_CALL_LOG` | `id`, `job_id`, `purpose`, `model`, `prompt_version`, `request_json` CLOB, `response_json` CLOB, `prompt_tokens`, `completion_tokens`, `cached_tokens`, `cost_usd`, `latency_ms`, `http_status`, `retries`, `repaired`, `created_at` |
| `AUDIT_EVENT` | `id`, `correlation_id`, `actor`, `actor_type` (`SYSTEM`\|`REVIEWER`), `action`, `entity_type`, `entity_id`, `before_json` CLOB, `after_json` CLOB, `occurred_at` |
| `REVIEW_DECISION` | `id`, `message_id`, `reviewer`, `decision` (`ACCEPT`\|`OVERRIDE`\|`REJECT`), `final_categories_json`, `notes`, `decided_at` |
| `PROCESSING_METRIC` | `id`, `subject_type`, `subject_id`, `stage`, `duration_ms`, `created_at` |

`V_REVIEW_QUEUE` — a view joining message, roll-up classification, minimum field confidence, unverified
evidence count and attention flags, ordered for the reviewer. The UI queue reads this view, not five joins.

### 9.4 PL/SQL packages

**`PKG_JOB_QUEUE`**
- `enqueue(p_type, p_subject_type, p_subject_id, p_priority DEFAULT 5, p_delay_s DEFAULT 0)`
- `dequeue(p_worker VARCHAR2, p_limit NUMBER) RETURN SYS_REFCURSOR` — `SELECT ... FOR UPDATE SKIP LOCKED`, sets `RUNNING`, `locked_by`, `locked_at`
- `complete(p_job_id)` / `fail(p_job_id, p_error)` — exponential backoff `available_at = SYSTIMESTAMP + 2^attempts sec`, `DEAD` past `max_attempts`
- `reap_stale_locks(p_lease_seconds DEFAULT 300)` — returns abandoned jobs to `PENDING`
- `all_documents_terminal(p_message_id) RETURN NUMBER` — the completion barrier of E39

**`PKG_AUDIT`**
- `log(p_actor, p_actor_type, p_action, p_entity_type, p_entity_id, p_before CLOB, p_after CLOB, p_corr VARCHAR2)`
  declared `PRAGMA AUTONOMOUS_TRANSACTION` so the audit record survives a rolled-back business transaction.
  This is the point of writing it in PL/SQL rather than Java, and it is worth saying out loud in the walkthrough.

**`PKG_REVIEW`**
- `apply_override(p_message_id, p_reviewer, p_categories CLOB, p_notes)` — inserts the superseding
  classification rows, writes `REVIEW_DECISION`, and calls `PKG_AUDIT.log` in one transaction.

**Triggers** — `TRG_FIELD_AUDIT` and `TRG_CLASSIFICATION_AUDIT` fire on update/insert and call `PKG_AUDIT.log`
with before/after JSON, so nothing can change without an audit row even if a future code path forgets.
---

## 10. Python AI service specification

### 10.1 Module layout

```
ai-service/app/
├── main.py               FastAPI app, routers, exception handlers
├── settings.py           pydantic-settings; all limits and model config
├── routes/
│   ├── parse.py          POST /v1/parse
│   ├── classify.py       POST /v1/classify
│   ├── extract.py        POST /v1/extract
│   ├── summarise.py      POST /v1/summarise
│   ├── literature.py     POST /v1/literature/screen        (bonus)
│   └── meta.py           GET /health, GET /v1/prompts
├── pdf/
│   ├── loader.py         open, decrypt check, rotation normalise, page cap
│   ├── flavour.py        per-page rendering + genre detection      (E12, E13)
│   ├── layout.py         blocks, column clustering, reading order  (E14)
│   ├── sections.py       heading segmentation, exclusions          (E15)
│   ├── tables.py         find_tables + vision fallback + merge     (E18)
│   ├── images.py         meaningful-image filter                   (E19)
│   └── render.py         page → PNG at target DPI
├── lang/detect.py        lingua-py block + page language           (E17)
├── llm/
│   ├── client.py         OpenRouter client, caching, retries, usage accounting
│   ├── schemas.py        pydantic models == JSON Schemas
│   ├── repair.py         one-shot schema repair round-trip         (E36)
│   └── prompts/          versioned prompt templates (see §11)
├── pipeline/
│   ├── triage.py         page-relevance pre-pass                   (E20)
│   ├── classify.py       P1 + rule post-processing                 (E21, E22)
│   ├── extract.py        P2/P3/P4 dispatch
│   ├── summarise.py      P6, map-reduce for long docs
│   ├── transcribe.py     P5 vision transcription of scanned pages  (E34)
│   ├── translate.py      P8 block translation                      (E16)
│   ├── describe.py       P7 image description                      (E19)
│   ├── screen.py         P9 literature screening, multi-case split (E32, bonus)
│   └── verify.py         evidence verification                     (E27)
└── telemetry.py          per-stage timings returned in every response
```

### 10.2 Flavour detection algorithm (per page)

```python
def detect_page(page) -> PageFlavour:
    normalise_rotation(page)
    text   = page.get_text()
    imgs   = page.get_images(full=True)
    area   = max_image_area_ratio(page, imgs)

    scanned = (len(text.strip()) < 100
               or area > 0.80
               or printable_ratio(text) < 0.75)          # E13

    blocks  = page.get_text("blocks")
    ncols   = cluster_columns(blocks)                     # E14
    genre   = ("ARTICLE" if ncols >= 2 or has_article_markers(text)
               else "FORM" if form_field_density(blocks) > T
               else "LETTER")

    lang, conf = detect_language(text) if not scanned else (None, 0.0)
    return PageFlavour(rendering="SCANNED" if scanned else "DIGITAL",
                       genre=genre, language=lang, lang_confidence=conf,
                       column_count=ncols)
```

Document roll-up: if all pages agree → that value; otherwise `MIXED`. `primary_language` is the modal
page language weighted by character count.

### 10.3 Text extraction and reading order

For digital pages: `page.get_text("dict")` → spans with bounding boxes. Blocks are clustered into columns by
x-midpoint, ordered column-by-column then top-to-bottom. **We build `text_original` by concatenating spans
and simultaneously record a `char_offset → (span, bbox)` index.** That index is what makes evidence
highlighting possible later, and it is why we do not simply call `get_text()`.

For scanned pages: render at 200 DPI → PNG → prompt `P5_transcribe` returns
`{text, legibility, segments:[{text, uncertain}]}`. Character offsets are computed over the returned text;
the bounding box for evidence falls back to the whole page (honest — we cannot localise within an image
without an OCR box model, and we say so rather than fabricating coordinates).

### 10.4 Translation

Non-English pages: `text_original` stays canonical; `P8_translate` produces `text_english` block-by-block so
block boundaries (and therefore approximate offsets) survive. Extraction runs on the original text so that
evidence offsets are real; the model is instructed to quote in the source language. `value_en` is produced
alongside each free-text value.

### 10.5 Tables

`page.find_tables()` → for each table, `{headers, rows, bbox, page_no, method:"pymupdf"}`. If it returns
nothing but block geometry suggests a grid (≥ 3 rows with ≥ 2 consistent x-clusters), crop the region, render
it, and call `P10_table_to_json` with `method:"vision"`. Cross-page merge when the following page's first
table repeats the header row (E18). Stored as `headers_json` + `rows_json`, never flattened to a string.

### 10.6 Images

Filter → describe → flag, per E19. `P7_describe_image` returns
`{description, category, mentions_defect, mentions_injury, confidence}`; the row is always written with
`needs_review='Y'` because the brief requires human review of image interpretation.

### 10.7 Response contract (`POST /v1/parse`)

```jsonc
{
  "document": { "page_count": 3, "rendering": "MIXED", "genre": "FORM",
                "primary_language": "de", "truncated": false, "parse_ms": 4210 },
  "pages": [ { "page_no": 1, "rendering": "DIGITAL", "genre": "FORM",
               "language": "de", "lang_confidence": 0.97, "column_count": 1,
               "char_count": 1834, "legibility": 1.0,
               "text_original": "...", "text_english": "...",
               "width": 595, "height": 842, "rotation": 0,
               "render_path": "renders/<sha>/p1.png" } ],
  "sections": [ ... ], "tables": [ ... ], "images": [ ... ],
  "timings": { "load_ms": 30, "layout_ms": 210, "vision_ms": 3100, "llm_calls": 2 },
  "usage": { "prompt_tokens": 8210, "completion_tokens": 940,
             "cached_tokens": 6100, "cost_usd": 0.0119 }
}
```

`/v1/classify`, `/v1/extract`, `/v1/summarise` and `/v1/literature/screen` follow the same envelope shape,
always returning `timings` and `usage` so Java can persist `AI_CALL_LOG` and `PROCESSING_METRIC` without
guessing.

### 10.8 Literature screening (bonus, §4 of the brief)

`POST /v1/literature/screen` takes one article PDF and returns:

```jsonc
{ "is_case_report": true, "confidence": 0.88,
  "relevance_reason": "Describes a single identifiable 62-year-old male patient ...",
  "cases": [ { "case_index": 0, "patient_descriptor": "62-year-old male",
               "summary": "...", "icsr_validity": {...},
               "evidence": [ {"page_no": 2, "quote": "..."} ] } ],
  "excluded_sections": ["References", "Discussion"] }
```

Multi-case splitting (E32) uses the section structure: candidate case boundaries come from headings
(`Case 1`, `Case 2`, `Patient A`) and from patient-descriptor changes, then the model confirms and assigns
page ranges. Because `CASE_RECORD.case_index` already exists, persistence and the entire review UI are reused
unchanged — which is exactly what the brief asks for ("reusing your Section 3 UI and logic where it makes sense").

---

## 11. LLM and prompting strategy

### 11.1 Prompt catalogue (all versioned; `prompt_version` recorded on every AI call)

| ID | Purpose | Input | Output schema |
|---|---|---|---|
| `P0_system` | Shared cached preamble: role, taxonomy (§4), confidence rubric, abstention rule, evidence rules | — | — |
| `P1_classify` | Multi-label classification + ICSR element checklist | email body + per-document digests | `ClassificationResult` |
| `P2_extract_icsr` | Patient / reporter / product / reaction / severity / narrative | relevant page text | `IcsrCase` |
| `P3_extract_pqc` | Product, batch/lot, defect, photo mentioned | relevant page text | `PqcCase` |
| `P4_extract_mi` | Questions asked + product/topic | relevant page text | `MiCase` |
| `P5_transcribe` | Vision transcription of a scanned/handwritten page + legibility | page PNG | `PageTranscription` |
| `P6_summarise` | 10–15 sentence summary + relevance verdict + reason | document digest | `DocumentSummary` |
| `P7_describe_image` | Short image description + category | image PNG | `ImageDescription` |
| `P8_translate` | Block-wise translation to English | source blocks | `TranslationResult` |
| `P9_screen_article` | Literature screening + multi-case split (bonus) | article sections | `ScreeningResult` |
| `P10_table_to_json` | Vision fallback for tables | cropped table PNG | `TableResult` |

Prompts live as files under `ai-service/app/llm/prompts/<id>/v<N>.md` — versioned in git, loaded at startup,
hashed into `AI_CALL_LOG`. Changing a prompt is a visible, reviewable diff, which matters when someone asks
"how do you know which prompt produced this record?"

### 11.2 Structured output

Every call uses OpenRouter's `response_format` with a strict JSON Schema generated from the pydantic model:

```python
resp = client.chat.completions.create(
    model="anthropic/claude-haiku-4.5",
    messages=[system_with_cache_control, user_parts],
    response_format={"type": "json_schema",
                     "json_schema": {"name": "IcsrCase", "strict": True,
                                     "schema": IcsrCase.model_json_schema()}},
    temperature=0, max_tokens=8000,
)
```

`temperature=0` throughout — this is extraction, not generation, and reproducibility matters for audit.
Schema-invalid output triggers exactly one repair round-trip carrying the validation error (E36).

### 11.3 Prompt caching

`P0_system` is a large static block (taxonomy, rubric, field definitions, worked examples). It is sent as a
system content part with `cache_control: {"type": "ephemeral"}`, so across a 20-document batch it is written
once and read ~60 times at $0.10/Mtok instead of $1.00/Mtok. We assert the saving from
`usage.prompt_tokens_details.cached_tokens` in the batch report — measured, not claimed.

### 11.4 The confidence rubric (verbatim in `P0_system`)

| Band | Meaning | Example |
|---|---|---|
| 0.90–1.00 | Explicitly stated in the source, unambiguous | "Patient is a 58-year-old female" → age 58 |
| 0.70–0.89 | Stated but needs light normalisation or one inference step | "born 1966" with a 2024 report date → age ≈ 58 |
| 0.40–0.69 | Strongly implied but not stated | "she" throughout → sex = female |
| 0.10–0.39 | Weakly implied; a reviewer should check | "elderly patient" → age band only |
| — | Absent | Return `status: NOT_STATED`. **Never guess.** |

The prompt states plainly that `NOT_STATED` is always an acceptable answer and is never penalised. This is the
mechanism behind the brief's "say unknown instead of guessing" rule — a schema affordance, not a plea.

### 11.5 Deterministic confidence adjustment (applied in code, after the call)

```
c = model_confidence
if evidence_verified == False:            c = min(c, 0.40)     # E27
if page.rendering == SCANNED:             c = min(c, page.legibility)   # E34
if field in conflict across sources:      c = min(c, 0.50); status = CONFLICT   # E33
if source_section.excluded_from_case:     drop the field entirely          # E15
```

Both `confidence_pre_adjust` and the final value are stored, with `adjust_reason` — so the write-up can show
exactly how much of the final score is model self-report versus system verification.

### 11.6 Cost model (measured target)

| Stage | Calls per doc | Est. input | Est. output | Est. cost |
|---|---|---|---|---|
| Transcribe (scanned pages only) | 0–6 | 1.6k tok/page image | 800 | $0.006/page |
| Translate (non-English only) | 0–3 | 1.5k | 1.5k | $0.009 |
| Summarise | 1 | 6k (mostly cached) | 600 | $0.004 |
| Classify (per message) | 1 | 8k (mostly cached) | 500 | $0.005 |
| Extract | 1–3 | 6k | 2k | $0.016 |
| Describe images | 0–4 | 1.6k | 200 | $0.003 |
| **Typical document** | | | | **≈ $0.03** |
| **20-document batch** | | | | **≈ $0.60** |

---

## 12. Traceability design

This is the differentiator, so it is specified precisely.

```
EXTRACTED_FIELD ──1:N──▶ FIELD_EVIDENCE
                          ├── source_type   EMAIL_BODY | PDF_PAGE | TABLE | IMAGE
                          ├── document_id, page_no
                          ├── quote         (verbatim, source language)
                          ├── char_start/char_end   ← rewritten by the verifier
                          ├── bbox          ← resolved from the span index
                          ├── verified      Y | N
                          ├── verify_method EXACT | FUZZY | FAILED
                          └── match_score
```

**Verification algorithm** (`pipeline/verify.py`):

1. Normalise both the quote and `page.text_original` — NFKC, collapse whitespace, unify quote characters and dashes.
2. Exact substring search → `verify_method=EXACT`, `match_score=100`.
3. Otherwise `rapidfuzz.fuzz.partial_ratio_alignment` over a sliding window; accept at ≥ 90 → `FUZZY` with the real offsets.
4. Otherwise `verified='N'`, `verify_method='FAILED'`, and the field's confidence is capped at 0.40.
5. On success, map `char_start/char_end` back through the span index to a union bounding box for the UI overlay.

Every AI decision is additionally traceable to its `AI_CALL_LOG` row, which holds the exact request JSON,
the exact response, the prompt version, token counts and cost. `GET /api/ai-calls/{id}` renders it in the UI —
so "why did the system say this?" is answerable in two clicks, not by grepping logs.

---

## 13. Angular reviewer UI

### 13.1 Screens

1. **Queue** (`/queue`) — table from `V_REVIEW_QUEUE`: sender, subject, received, category chips with
   confidence, min field confidence, flags (`NEEDS_ATTENTION`, `UNVERIFIED_EVIDENCE`, `CONFLICT`,
   `TRUNCATED`), status. Filters and a live SSE-driven status column. Sorted worst-confidence-first, because
   that is what a reviewer's day actually looks like.
2. **Message detail** (`/messages/:id`) — three panes:
   - *Left:* source viewer. Email body (with the quoted region visually dimmed) and one tab per document;
     document tabs show **rendered page PNGs** with absolutely-positioned highlight overlays.
   - *Centre:* classification card (chips, confidence bars, one-line reasons, ICSR element checklist with
     ticks/crosses and the missing elements named), then the AI summary with its relevance verdict, then the
     extracted-fields accordion grouped as Patient / Reporter / Product / Reaction / Severity / Narrative.
     Each field row: value, `status` badge, confidence bar, an **evidence chip** and an edit control.
     Conflicts render as two stacked values with a "choose" control.
   - *Right:* tables (rendered as real HTML tables), images (thumbnail + description + review flag), and the
     audit timeline.
   - Footer: **Accept all** / **Override** / **Reject**, plus reviewer notes.
3. **Literature** (`/literature`) — **bonus.** Drag-and-drop batch upload, progress per file, results table
   (article → N cases, include/exclude verdict, reason), and each case opens the *same* detail component.
4. **Batch report** (`/report`) — per-document timings, token counts, cost, cache-hit rate; this is
   deliverable R17 rendered as a page rather than a spreadsheet.

### 13.2 The one interaction that sells the demo

Clicking a field's evidence chip scrolls the left pane to the right document and page and draws a highlight
box over the exact quoted text. If evidence failed verification, the chip is amber and says
"cited but not found in source" — showing that the system catches its own hallucinations rather than hiding them.

**Implementation note:** we deliberately do **not** embed PDF.js. Pages are pre-rendered to PNG at parse time,
and the highlight overlay uses the same PyMuPDF coordinate space we already stored. Fewer moving parts, and
the highlight coordinates are guaranteed to align with the extraction because they come from the same source.

### 13.3 Structure

Standalone components, Angular Material, a typed `ApiService` per resource, `MessageStore` (signals) for the
detail view, `EventService` wrapping `EventSource` for SSE. `ng build` output is copied into the Spring Boot
static resources for the single-command demo, while `ng serve` with a proxy stays available for development —
which also keeps memory use sane on an 8 GB machine.

---

## 14. Synthetic test data

**No real patient data at any point.** Everything is generated by `testdata/generator/` — a Python program,
committed and re-runnable, using a fixed random seed so the corpus is reproducible.

### 14.1 Required corpus (brief §6) and how each item is produced

| Brief requirement | Count | How generated |
|---|---|---|
| Sample emails with varying detail about a reaction | **12** | Templated bodies at four detail levels (complete / missing reporter / missing product / vague), fake senders and drug names |
| Normal digital PDF attachments (filled-in report forms) | **6** | ReportLab: a CIOMS-style form with labelled fields, plus a lab-values **table** |
| Scanned / handwritten-style PDFs | **3** | Pillow renders the form text in Segoe Script / Ink Free / Bradley Hand, then applies rotation ±1.5°, gaussian noise, contrast loss and JPEG artefacts → image-only PDF. One is deliberately hard to read, to exercise the legibility path |
| Made-up "article" PDFs describing a fictional patient case | **6** | ReportLab **two-column** layout with Abstract / Introduction / Case Report / Discussion / References. **Two are case series with 2–3 patients each** (drives E32 and the bonus) |
| Non-English PDFs with case-relevant content | **3** | German and French (Latin fonts) and one Japanese (MS Gothic / Yu Gothic, present on this machine). One is **mixed-language**: English labels, German free text (E17) |
| Quality-complaint-only examples | **3** | Broken seal, particulate contamination, damaged packaging — one with an embedded photo-like image |
| Info-request-only examples | **3** | Dosing in renal impairment, storage temperature, drug interaction |
| Clearly irrelevant examples | **2** | A conference marketing blast and an out-of-office auto-reply |

Plus **adversarial extras** that exist purely to prove the edge-case handling is real, not theoretical:

- One email carrying the **same PDF twice** under different filenames (E9)
- One **forwarded** email where the case is inside a `message/rfc822` attachment (E5)
- One **password-protected** PDF (E7)
- One `.docx` and one `.zip` attachment (E6)
- One PDF whose attachment is declared `application/octet-stream` (E4)
- One **hybrid** PDF: digital cover page + scanned annex (E12)
- One email whose **body contradicts the attachment** on the patient's age (E33)
- One reply chain that repeats an earlier case in the quoted section (E10)

Total ≈ **38 documents across ~30 emails** — comfortably over the "10–15 sample documents" bar, and every
number in the brief's §6 is met or exceeded.

### 14.2 Golden labels

`testdata/goldens/<message_id>.json` holds the true categories and the true value for each key field
(written by hand as the generator emits each document, so the labels are ground truth by construction, not
by later annotation). These drive §15.

### 14.3 Seeding

`scripts/seed_mailbox.py` sends the generated emails to GreenMail over SMTP :3025 with correct MIME structure.
Re-runnable: `docker compose down -v && docker compose up && python scripts/seed_mailbox.py` reproduces the
entire demo from nothing.

---

## 15. Evaluation harness

`eval/run_eval.py` runs the full corpus end-to-end and writes `eval/report.md` + `eval/report.json`:

| Metric | Definition |
|---|---|
| Category precision / recall / F1 | Per category, micro and macro, over golden labels |
| Multi-label exact-set accuracy | Fraction of messages whose full label set matched |
| ICSR element accuracy | Per-element agreement on the four minimum criteria |
| Field accuracy | Exact and normalised match per field path |
| **Abstention correctness** | When the golden label is absent, did the model return `NOT_STATED`? (This is the number that proves "unknown, not guessing" — a false confident value is counted separately from a miss) |
| **Evidence verification rate** | % of `STATED` fields with `verified='Y'` |
| Confidence calibration | Reliability curve: mean accuracy per confidence decile |
| Latency | Per document and per stage, p50 / p95 |
| Cost | Per document; total; cache-hit rate |

The report is a submission deliverable in its own right (brief §7 items 5 and the §3.E "report how long each
one takes" requirement), and the calibration curve is the evidence behind every claim we make about confidence.

---

## 16. Security, audit and data handling

- **No real patient data, ever.** All data is generated by `testdata/generator/` with obviously fictional
  names, drugs and addresses. The README says so in the first section, and the generator is the proof.
- **Audit trail.** `AUDIT_EVENT` records actor, action, entity, before/after JSON and timestamp for every
  reviewer action and every system state change. Written through `PKG_AUDIT` with
  `PRAGMA AUTONOMOUS_TRANSACTION` so audit records survive a rolled-back business transaction. Records are
  append-only; overrides supersede rather than overwrite (E40).
- **AI decision log.** `AI_CALL_LOG` stores the exact request and response for every model call with prompt
  version, tokens, cost and latency — so any output can be reproduced and explained.
- **Cloud-AI trade-off (the brief asks for this explicitly).** Document content is sent to OpenRouter, which
  forwards it to Anthropic — **two processors**, not one. For a prototype on synthetic data this is fine and
  it buys vision, structured output and speed at negligible cost. For production with real safety data it is
  not: we would move to a direct enterprise agreement with a zero-data-retention configuration, or run a
  self-hosted vision model inside the pharma VPC. Additional production requirements we would design for and
  are not building here: 21 CFR Part 11 electronic signatures on reviewer acceptance, record retention
  policies, per-region data residency, and a PII redaction pass before any external call. All of this goes in
  `docs/WRITEUP.md` under "what I'd change for production".
- **Secrets.** `OPENROUTER_API_KEY` and DB credentials come from environment variables only. `.env.example`
  ships with placeholders; `.env` is gitignored; no key appears in any committed file, log or `AI_CALL_LOG` row.
- **Prototype simplifications, declared rather than hidden:** HTTP Basic instead of SSO; blobs on local disk
  rather than encrypted object storage; no TLS between the internal services.

---

## 17. Repository layout

```
clinevo-smart-inbox/
├── README.md                      setup, run, env vars (placeholders only)
├── docker-compose.yml             oracle + greenmail (+ optional app profiles)
├── .env.example
├── docs/
│   ├── PROJECT_PLAN.md            this document
│   ├── WRITEUP.md                 deliverable 4 (2–5 pages)
│   ├── architecture.svg
│   ├── screenshots/
│   └── sample-outputs/            deliverable 5: extracted JSON per test document
├── infra/oracle/init/             container bootstrap (user, tablespace)
├── backend/                       Spring Boot; mvnw included
│   └── src/main/resources/db/migration/  V1..V4 Flyway (tables, indexes, packages, triggers)
├── ai-service/                    FastAPI; requirements.txt; prompts/ versioned
├── frontend/                      Angular 22
├── testdata/
│   ├── generator/                 synthetic corpus generator (seeded)
│   ├── corpus/                    generated .eml + .pdf (committed)
│   └── goldens/                   ground-truth labels
├── eval/                          run_eval.py + report.md/json
└── scripts/                       seed_mailbox.py, run_batch.py, smoke_llm.py
```

---

## 18. Seven-day schedule

Each day ends with something runnable. The bonus is last so it can be dropped without breaking anything.

| Day | Deliverable at end of day | Detail |
|---|---|---|
| **0** (today, ~3 h) | Environment green | Move Docker Desktop's disk image to `D:` (§21); pull `gvenzl/oracle-free:23-slim-faststart` and `greenmail/standalone`; scaffold the four projects; `scripts/smoke_llm.py` proves an OpenRouter call to `anthropic/claude-haiku-4.5` returns schema-valid JSON and reports token usage |
| **1** | DB + skeleton up | `docker-compose up` gives Oracle + GreenMail; Flyway V1–V4 apply cleanly (tables, indexes, `PKG_JOB_QUEUE`, `PKG_AUDIT`, triggers, `V_REVIEW_QUEUE`); Spring Boot boots and connects; queue enqueue/dequeue/backoff/reap covered by tests; generator v1 emits emails + digital form PDFs |
| **2** | Mail flows into Oracle | IMAP poller, `MimeWalker`, sniffing, `rfc822` recursion, quote detection, blob store, dedupe; `JobWorkerPool` running with a no-op parse handler; generator completes all 8 corpus categories **plus** the adversarial extras; `seed_mailbox.py` works; end of day: 30 emails visible in `INBOX_MESSAGE` with correct attachment rows |
| **3** | PDFs genuinely understood | Python service: loader, per-page flavour, column-aware reading order, sections, tables, image filter, language detection, page rendering, span-offset index; `POST /v1/parse` complete; `ParseDocumentHandler` persists pages/tables/images; visually verify the two-column reading order against a generated article |
| **4** | AI pipeline complete | OpenRouter client with caching, retries, repair and usage accounting; `P0`–`P8`; classify with the ICSR element checklist and the rule-based label decision; ICSR/PQC/MI extraction; **evidence verifier**; conflict merge; summaries; handlers wired; full corpus processes end-to-end and lands in `READY_FOR_REVIEW` |
| **5** | Reviewer UI | Angular queue + detail with page-image viewer, evidence highlighting, editable fields with confidence, ICSR checklist, conflicts, accept/override writing audit rows, SSE live status, AI-call inspector |
| **6** | Bonus + evidence of quality | Literature upload, `P9` multi-case splitting, results reusing the detail component; `eval/run_eval.py` with all metrics; full batch run; `/report` page; tune the prompts against the calibration curve |
| **7** | Submission | `README.md`, `docs/WRITEUP.md` (architecture diagram, tech choices, prompting approach, limitations, production changes), `docs/sample-outputs/*.json`, screenshots + short screen recording, final clean-clone test (`git clone` → `docker compose up` → seed → works), send to the four addresses |

**Checkpoint rule:** if Day 4 slips, Day 6's bonus is cut first, then Day 6's evaluation harness is reduced to
category F1 + timings only. The core rubric (85% of the score) stays protected.

---

## 19. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Oracle image exhausts `C:` (8.4 GB free) | High if not addressed | Blocks everything | **Day 0 action:** relocate Docker Desktop's disk image to `D:` (71 GB free). Fallback: `gvenzl/oracle-xe:21-slim-faststart` (smaller) |
| 8 GB RAM with Oracle + JVM + Python + Angular + browser | Medium | Thrashing, flaky demo | Cap Oracle SGA (~1.2 GB) and the JVM (`-Xmx512m`); serve the built Angular bundle from Spring Boot for the demo instead of running `ng serve` |
| Haiku 4.5 under-performs on subtle multi-case article splitting | Medium | Bonus quality | Decompose harder: section-boundary candidates from code, model only confirms. Report honestly in the write-up rather than papering over it |
| Structured-output schema rejections on deep nested schemas | Medium | Failed extractions | Keep schemas shallow and flat; one repair round-trip; a `tool_choice`-forced-function fallback path if `response_format` proves unreliable for a given schema |
| OpenRouter rate limits or an outage on demo day | Low | Demo failure | Parse results and AI responses are cached by content hash — a full re-run of an already-processed corpus needs **zero** live calls. The demo is therefore replayable offline |
| Angular 22 + Material learning-curve friction | Low | Day 5 slips | Keep the UI to three screens; Material components only; no custom design system |
| PL/SQL `SKIP LOCKED` behaviour surprises | Low | Queue stalls | Covered by an integration test on day 1 with concurrent workers |
| Scope creep into a "real" PV system | Medium | Missed deadline | This document is the scope. Anything not in §2 is out |

---

## 20. Assumptions to declare in the write-up

1. GreenMail is the demo mailbox; the identical IMAP code path runs against Gmail with four config values.
2. Flavour is a **per-page** property; `MIXED` is a legal document-level value (E12).
3. Non-English documents are extracted from the **original** text with translation stored alongside; evidence always points at the original (E16).
4. `NOT_RELEVANT` is mutually exclusive with the other three labels, enforced in code (E21).
5. ICSR validity is decided by the four-minimum-criteria **rule** over the model's element checklist, not by the model's label (E22).
6. Relative dates are never resolved automatically; they are flagged for the reviewer (E28).
7. Evidence bounding boxes for scanned pages are page-level, not word-level — we do not fabricate coordinates we cannot compute (§10.3).
8. Only PDFs are content-processed; other attachment types are logged, except bare images which get a description (E6).
9. Auth is HTTP Basic with in-memory roles — a deliberate prototype simplification (§8.4).
10. All data is synthetic; the cloud-AI data path involves two processors and would change for production (§16).

---

## 21. Environment setup (Day 0 actions)

Verified state of this machine on 4 September 2026:

| Component | Status |
|---|---|
| Java | 24.0.2 — fine, we compile with `--release 21` |
| Node | 22.18.0, npm 11.6.2 — meets Angular 22's requirement |
| Python | 3.12.7, with `openai`, `fastapi`, `uvicorn`, `pypdf`, `pillow` already installed |
| Docker | Client 28.3.3 installed, **daemon not running** |
| Maven / Angular CLI | Not installed — handled by `mvnw` and `npx` |
| Tesseract / poppler | Not installed — **not needed** by design (§6) |
| Disk | `C:` 8.4 GB free, `D:` 71.3 GB free |
| RAM / CPU | 8 GB / 8 logical cores |
| Fonts for the generator | Segoe Script, Ink Free, Bradley Hand, Free Script (handwriting); MS Gothic, Yu Gothic, SimSun (CJK); Nirmala (Devanagari) — all present |

**Actions before coding starts:**

1. Start Docker Desktop.
2. **Settings → Resources → Advanced → Disk image location → move to `D:\DockerData`.** This is the one
   manual step; without it the Oracle image will not fit.
3. Settings → Resources → limit Docker memory to ~4 GB so the host keeps headroom.
4. `docker pull gvenzl/oracle-free:23-slim-faststart` and `docker pull greenmail/standalone`.
5. Create an OpenRouter key and put it in `.env` as `OPENROUTER_API_KEY=` (never committed).
6. `pip install pymupdf lingua-language-detector rapidfuzz reportlab python-multipart pydantic-settings httpx`.

---

## 22. Definition of done

- [ ] `git clone` → set `.env` → `docker compose up` → `python scripts/seed_mailbox.py` → UI shows a populated queue, on a clean machine
- [ ] All four PDF flavours demonstrably handled, including one hybrid document
- [ ] Tables render as tables; meaningful images described and flagged; logos correctly ignored
- [ ] Every message multi-label classified with confidence and a one-line reason per label
- [ ] ICSR / PQC / MI fields extracted, with `NOT_STATED` where absent
- [ ] Every `STATED` field has verified evidence; clicking it highlights the exact source text
- [ ] Reviewer can accept and override; every action lands in `AUDIT_EVENT` with a timestamp
- [ ] Batch report: ≥ 15 documents with per-document timings, tokens and cost
- [ ] `eval/report.md` with F1, abstention correctness, evidence verification rate and a calibration curve
- [ ] Bonus: literature batch upload splits a multi-case article into separate cases
- [ ] README + 2–5 page write-up + architecture diagram + sample JSON outputs + screenshots/recording
- [ ] No API key, no real data, and no `TODO` left anywhere in the submitted tree

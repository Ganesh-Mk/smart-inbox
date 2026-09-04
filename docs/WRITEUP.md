# Smart Inbox — design write-up

**Clinevo Technologies live assignment · September 2026**

An AI first-pass triage system for a pharmacovigilance shared mailbox. This document covers the
architecture, the decisions I would defend, what the measured results actually say, and what I
would change before this went anywhere near a real safety mailbox.

---

## 1. The one decision that shapes everything else

The brief says traceability is *"required, not optional"*. There is an easy reading of that: add
a `source` field, let the model fill it in with "page 2", show it in the UI. That would tick the
box and be close to worthless.

Models hallucinate citations as readily as they hallucinate facts. **A fabricated citation is
worse than no citation**, because it manufactures confidence a reviewer will act on — it looks
like corroboration, and a reviewer working a queue of forty messages will not open the PDF to
check every one.

So provenance here is a machine-verified data type, not a string:

1. The model states a `quote`, in the source language, with the page it came from.
2. `pipeline/verify.py` searches for that quote in that page's stored text — NFKC normalisation,
   unified quotes and dashes, collapsed whitespace, then an exact substring search, then
   `rapidfuzz` at ≥ 90 similarity.
3. On success the character offsets are **overwritten with the ones we actually found** and a
   bounding box is resolved from the span index built at parse time. The model's numbers are a
   hint about where to look, never the record.
4. On failure the evidence is stored as `verified='N'` and the field's confidence is capped at
   0.40.

The reviewer sees the consequence directly. Clicking an evidence chip highlights the exact
source text on the rendered page. An unprovable citation shows amber and says *"cited but not
found in source"* — the system reporting its own hallucination rather than hiding it.

**Measured: 98.7% of asserted facts verified (545 of 552), all but 7 as exact matches.**

Everything else in this submission is ordinary engineering. This is the part I would defend
hardest.

---

## 2. Architecture

```
Angular 22  →  Spring Boot 3.5 (Java 21)  →  Python 3.12 FastAPI  →  Oracle 23ai Free
                       │                              │
              Oracle JOB queue                OpenRouter → claude-haiku-4.5
              (FOR UPDATE SKIP LOCKED)
```

See [`architecture.svg`](architecture.svg) for the full picture.

**Spring Boot owns state, orchestration and the audit trail. It never calls an LLM.** The Python
service is a stateless pure function — `(bytes, task, params) → JSON` — with no database handle
and no session state. That boundary is what makes the AI side independently testable and keeps
the Java side free of model-specific code.

**Oracle is not a data sink.** The work queue and the audit logic are PL/SQL, which is what the
stated stack ("Oracle (PL/SQL)") actually implies:

- `PKG_JOB_QUEUE` — `FOR UPDATE SKIP LOCKED` dequeue, 2ⁿ backoff, dead-lettering, a stale-lock
  reaper, and the completion barrier.
- `PKG_AUDIT` — declared `PRAGMA AUTONOMOUS_TRANSACTION`, so an audit row survives the rollback
  of the business transaction that wrote it. *"We attempted X and it failed"* is precisely the
  event a regulated system must not lose. There is a test for it.
- `PKG_REVIEW` — an override inserts a new row and points the AI's original at it. Nothing is
  ever updated in place.

**A durable queue, not an in-process one.** The brief permits in-process; a database queue is
barely more work and is restart-safe and inspectable in SQL. Eight threads released from a
barrier onto twenty jobs claim each exactly once — measured, not asserted.

---

## 3. The judgement calls

The full set is in [`PROJECT_PLAN.md`](PROJECT_PLAN.md) §3 (40 numbered edge cases) and
[`DECISIONS.md`](DECISIONS.md) (18 decisions made while building, with evidence). The ones that
mattered most:

### Flavour is a property of a *page*, not a document

The brief lists four PDF flavours and implies one label per document. That is wrong for real
submissions: a genuine attachment is a typed cover letter *plus* a scanned annex, and a
non-English document is *also* digital or scanned. Forcing one label discards information the
very next step needs.

So flavour is two orthogonal axes — `rendering` (DIGITAL | SCANNED) and `genre` (FORM | ARTICLE
| LETTER) — decided per page, with language as an attribute. `MIXED` is a legal document-level
answer. `adv-06-hybrid-pdf` in the corpus proves it: page 1 digital form, page 2 a handwritten
scan read by vision, document `MIXED`.

### The ICSR label is decided by rule, not by the model

The four regulatory minimum criteria (identifiable patient, identifiable reporter, suspect
product, adverse event) are reported by the model as an **explicit checklist**, each with its
own evidence and confidence. Code then decides: four present → `ICSR` with confidence equal to
the *weakest* element; an adverse event plus at least one other → `ICSR_INCOMPLETE` with the
missing elements named.

This converts a fuzzy regulatory judgement into an auditable one. "ICSR_INCOMPLETE: no
identifiable reporter" is a claim a reviewer can check line by line, and the UI shows the
checklist with ticks, crosses and the quote behind each.

Taking the *minimum* rather than the average is deliberate: a case resting on a barely
identifiable patient is a weak case however clear the other three are, and averaging would hide
exactly what the reviewer needs to see.

### `NOT_RELEVANT` exclusivity is enforced in code

On the very first live call — before any pipeline existed — the model returned `NOT_RELEVANT`
at 0.05 *alongside* `ICSR` at 0.95 on a textbook ICSR. It was treating four independent booleans
as a probability distribution. That is exactly the failure the plan predicted, observed on call
one, and it is why the rule is code with a unit test rather than an instruction in a prompt.

### Conflicts are surfaced, not resolved

When the email body says the patient is 58 and the attached form says 71, the tempting
behaviours are all defensible and all wrong: prefer the structured form, prefer higher
confidence, prefer the most recent. Each silently discards a real disagreement about a patient's
age in a safety report.

Both values are kept, both keep their own evidence, both are marked `CONFLICT` and capped at
0.50, and the UI stacks them with a choose control. The system's job is to notice and escalate.

### Abstention is a schema affordance, not a plea

Every fact carries a `status` enum containing `NOT_STATED`. The model selects "not stated" the
same way it selects any other answer — it is never asked to omit a key or invent a null
convention. The prompt states plainly that `NOT_STATED` is always acceptable and never
penalised. That is the mechanism behind "say unknown instead of guessing"; a prompt instruction
alone would not survive contact with a model that wants to be helpful.

---

## 4. Prompting

Thirteen versioned prompts live as markdown under `ai-service/app/llm/prompts/<id>/v<N>.md`,
loaded at startup, SHA-hashed, and recorded on every `AI_CALL_LOG` row. Changing a prompt is a
reviewable diff, and "which prompt produced this record?" is answerable from the database.

**One large cached preamble.** `P0_system` (~3,100 tokens) carries the taxonomy, the six
seriousness criteria, an anchored confidence rubric with a worked example per band, the
abstention rule, the evidence rule, typed-value conventions and the field conventions. Its size
is functional: Anthropic will not cache a prefix below a minimum, and a smaller preamble is
*silently* not cached at all.

**`temperature=0` everywhere.** This is extraction, not generation, and reproducibility matters
for audit.

**Decomposition over a bigger model.** ICSR extraction is three calls — parties, products,
reactions — assembled in code. That was forced by a provider limit (§5) but improves quality
independently: each call gets a shorter, more focused instruction.

**Rules after the model, never inside it.** The model reports observations; code makes the
decisions that must be defensible. That split is what makes the classification testable without
a network call.

---

## 5. Three things I learned by running it

Every one of these was invisible in testing and only appeared when the system ran against real
infrastructure. They are in `DECISIONS.md` with the evidence.

### OpenRouter silently discards a `response_format` schema above ~4 KB

The first live ICSR extraction returned free-form JSON in a markdown fence, in a shape nothing
like the schema, with an entirely normal `finish_reason: stop`. It looked like a model quality
problem.

`prompt_tokens` gave it away: for the 8,807-byte schema it was **2,648 — byte for byte identical
to sending no schema at all.** Bracketed with synthetic schemas, the cliff sits between 3,527 B
(sent) and 4,683 B (dropped).

The important fix was not splitting the schema. It was **making the failure loud**:
`strict_schema` now measures and raises `SchemaTooLarge` above 4,000 B, with a test asserting
every registered schema stays under it. The defect was never that a schema was too big; it was
that nothing said so.

### Two IMAP bugs that a file fixture cannot catch

Reading `.eml` files from disk, every test passed. Against a real server:

- The forwarded case **silently vanished**. jakarta.mail fetches `IMAPMessage` parts lazily, so
  a nested `message/rfc822` part read after the stream moved gave a **zero-byte** attachment
  with no filename — sniffed as `application/x-empty`, recorded `skip_reason='EMPTY'`, and the
  case simply missing from the queue with nothing in the log.
- Worse, and partly caused by fixing the first: reading a body over IMAP makes the server set
  `\Seen` itself (RFC 3501 §6.4.5), so a message whose ingestion *threw* was skipped forever.
  Fixed with `mail.imap.peek=true`.

`ImapForwardedMessageTest` now drives a real in-process IMAP server. Its third assertion — that
a handler which throws leaves the message unread — is what caught the second bug.

### `TO_CHAR` on a CLOB caps at 4,000 bytes

Classification dead-lettered with `ORA-22835` on the first page of text over 4 KB, which is most
of them. **Oracle throwing was lucky**: had it truncated silently, the model would have been
handed the first 4,000 characters of every page and the missing text would have surfaced as
unexplained extraction misses months later.

---

## 6. Results

Full report: [`eval/report.md`](../eval/report.md). Measured over all 38 messages / 59 documents.

| Metric | Result | Target |
|---|---|---|
| **Evidence verification rate** | **98.7%** (545/552) | ≥ 90% |
| Category F1 (micro) | **0.952** | ≥ 0.90 |
| Category F1 (macro) | 0.983 | — |
| Multi-label exact-set accuracy | 89.5% | — |
| Field accuracy (exact / normalised) | 70.2% / 70.7% | — |
| Abstention correctness | 47.5% | — |
| ICSR element agreement | 71.9% | — |
| Cost per document | **$0.025** | ≤ $0.05 |
| Prompt-cache hit rate | 75.8% | — |
| Dead-lettered jobs | **0** | — |
| Schema repair round-trips needed | **0** | — |

Per category: MI, PQC and NOT_RELEVANT all scored 1.000 F1; ICSR scored 0.933 (precision 0.875,
recall 1.000) — it never missed a real case, and over-applied on four.

**Cost.** 414 calls, $1.47 for the whole corpus. Prompt caching is worth a measured **3.5×** on
an identical call ($0.00911 cold → $0.00259 warm). It is keyed per *schema*, because structured
output becomes a tool definition ahead of the system block — so batch work must be grouped by
prompt type rather than round-robined per document, which is the difference between $0.0026 and
$0.0091 across several hundred calls.

### Where the numbers are weaker, and why

**Field accuracy at 70% is the honest headline, and it is lower than the verification rate
because they measure different things.** Verification asks "is this quote really in the source?"
Field accuracy asks "is this the value the golden expected?" Inspecting the mismatches, a
substantial share are not model errors:

- *Source attribution.* The golden expects `reaction[0].onset.raw = "12 March 2026"` (from the
  attached form); the model returned `"nine days after starting Velmoradine"` — which is what
  the **email body** says. Both are correct readings of different sources.
- *Genuinely debatable enums.* `reporter.role` expected `OTHER_HCP` for a Clinical
  Pharmacologist; the model said `PHYSICIAN`. My fixture is one defensible answer, not the only
  one.

I report exact and normalised match rather than picking whichever flatters. The truth about
model quality sits between them, and the gap is small (70.2% vs 70.7%), which tells you the
errors are substantive rather than cosmetic.

**Abstention correctness at 47.5% is an underestimate and I would not quote it without this
caveat.** The goldens record the facts the generator deliberately planted, not an exhaustive
inventory of everything legitimately readable in a document. A value counted as "asserted where
the golden has none" is therefore an **upper bound on fabrication**, not a confirmed one. Fixing
this properly means enumerating every extractable fact per document, which the generator could
do and currently does not.

**Confidence calibration: ECE 0.101.** The reliability curve is genuinely informative — the
0.5–0.6 band scores 47.6% accuracy, the 0.9–1.0 band 85.9%. So the scores carry real signal and
the model is overconfident by roughly nine points at the top. But 426 of 516 scored fields land
in the top decile, so the *spread* is poor: the rubric is separating confident from uncertain,
but not finely. Tightening that is the first thing I would do with more time.

---

## 7. Cost, speed and accuracy — the trade-off

`anthropic/claude-haiku-4.5` was a constrained choice, but it is defensible on the merits. The
workload is high-volume, schema-constrained document extraction **with a human in the loop** —
precisely where a fast, cheap model with reliable structured output and vision beats a frontier
model. At $0.025 per document I can re-run the entire corpus for $1.47 while tuning prompts,
which matters more for quality than a better model I can afford to run once.

Where a larger model would genuinely help is subtle multi-case splitting in articles. I
compensated with decomposition — section headings give candidate case boundaries from code and
the model only confirms them — and it worked: the three-patient case series split into exactly
three correct cases, and the methodology review returned zero, inventing no patients from its
reference list.

Measured latency: p50 1.1 s for parsing a document, 6.0 s to classify a message, 14.3 s to
extract a case. A typical message completes in well under a minute on four workers.

---

## 8. Known limitations

Stated plainly, because a system that hides these is harder to trust.

1. **Confidence spread is narrow.** 83% of scored fields land in the top decile. Calibration is
   informative but coarse. The rubric needs sharper anchors and probably a few negative
   worked examples.
2. **Abstention is measured against an incomplete ground truth**, as above. The number is a
   floor, not an estimate.
3. **Evidence bounding boxes for scanned pages are page-level, not word-level.** Vision
   transcription gives no coordinates, and I would rather highlight the whole page honestly
   than fabricate a box. The UI shows the quote text instead.
4. **One case record per message.** The schema supports N (`CASE_RECORD.case_index` exists and
   the literature path uses it), but the mailbox pipeline currently writes one. A single email
   describing two unrelated patients would be merged.
5. **`ICSR` precision 0.875** — it over-applied on four messages, mostly vague ones where the
   model read an identifiable patient into thin description.
6. **HTTP Basic with two in-memory users.** Deliberate: its job is to make the reviewer's
   identity real in the audit trail, not to be a security boundary.
7. **The corpus is synthetic and my own.** It is deliberately adversarial, but a corpus written
   by the same person who wrote the extractor will always flatter it somewhat.

---

## 9. What I would change for production

**Data handling first.** Document content currently passes through **two processors** —
OpenRouter, then Anthropic. For synthetic data that is fine and it buys vision, structured
output and speed at negligible cost. For real safety data it is not. I would move to a direct
enterprise agreement with zero-data-retention, or a self-hosted vision model inside the pharma
VPC, and add a PII redaction pass before any external call.

**Then, in rough order:**

- **21 CFR Part 11 electronic signatures** on reviewer acceptance, with record retention
  policies and per-region data residency. The append-only audit model is already the right
  shape; it needs signing.
- **SSO** replacing HTTP Basic, with roles that mean something.
- **Encrypted object storage** for blobs rather than local disk, and TLS between the internal
  services.
- **A human-labelled evaluation set.** The synthetic corpus is good for edge cases and bad for
  measuring real-world accuracy. I would want a few hundred genuinely labelled historical
  messages before trusting any accuracy claim operationally.
- **Prompt A/B infrastructure.** Prompts are versioned and every call records which produced it,
  so comparing two versions over the same corpus is already possible — it just needs wiring.
- **Duplicate case detection across messages**, not just across polls. The same case reported by
  a doctor and a patient is currently two cases.
- **A model-drift monitor.** Verification rate and calibration are exactly the signals that would
  degrade quietly after a provider-side model update, and both are already computed.

---

## 10. Declared assumptions

Per the brief's instruction to make a reasonable assumption, note it, and keep going.

1. GreenMail is the demo mailbox; the identical IMAP code path runs against Gmail with four
   config values.
2. Flavour is a per-page property; `MIXED` is a legal document-level value.
3. Non-English documents are extracted from the **original** text with translation stored
   alongside. Evidence always points at the original — a translation never becomes the
   evidentiary record.
4. `NOT_RELEVANT` is mutually exclusive with the other three labels, enforced in code.
5. ICSR validity is decided by the four-minimum-criteria rule over the model's checklist, not by
   the model's own label. An adverse event is treated as *necessary*: without one there is no
   safety case to be incomplete about.
6. Relative dates are never resolved automatically; they are stored raw and flagged.
7. Evidence bounding boxes for scanned pages are page-level — I do not fabricate coordinates I
   cannot compute.
8. Only PDFs are content-processed. Other attachment types are logged with a reason, except
   bare images, which get a vision description.
9. Auth is HTTP Basic with in-memory roles — a deliberate prototype simplification.
10. All data is synthetic; the cloud-AI data path involves two processors and would change for
    production.

---

## 11. Deliverables

| Brief item | Where |
|---|---|
| Working prototype | This repository — `docker compose up`, seed, open :8080 |
| Source code | `backend/` · `ai-service/` · `frontend/` |
| Sample data | `testdata/corpus/` — 38 emails, 59 documents, generator committed |
| This write-up | `docs/WRITEUP.md` |
| Architecture diagram | `docs/architecture.svg` |
| Sample JSON outputs | `docs/sample-outputs/` — 38 files, one per message |
| Evaluation | `eval/report.md` + `report.json` |
| Bonus: literature screening | `POST /v1/literature/screen` — splits a 3-patient case series into 3 cases |

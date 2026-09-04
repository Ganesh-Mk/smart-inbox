# Smart Inbox — Phase Tracker

**Live progress document. Update after every work session.**
Spec: `docs/PROJECT_PLAN.md` · Decisions: `docs/DECISIONS.md` · Context: `CLAUDE.md`

Legend: `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked · `[-]` cut from scope

**Deadline: 11 Sep 2026.** Started 4 Sep 2026.

---

## Progress summary

| Phase | Title | Status | Target day |
|---|---|---|---|
| P0 | Environment & foundations | `[x]` | Day 0 |
| P1 | Oracle schema, PL/SQL, queue, Spring skeleton | `[~]` | Day 1 |
| P2 | Mail ingestion + synthetic corpus | `[~]` | Day 2 |
| P3 | PDF understanding (Python) | `[ ]` | Day 3 |
| P4 | LLM pipeline + evidence verification | `[ ]` | Day 4 |
| P5 | Angular reviewer UI | `[ ]` | Day 5 |
| P6 | Literature screening (bonus) + evaluation | `[ ]` | Day 6 |
| P7 | Documentation & submission | `[ ]` | Day 7 |

**Cut order if time runs short:** P6 bonus → P6 eval (reduce to F1 + timings only) → P5 polish.
The core rubric (85%) is P0–P5 and must never be sacrificed.

---

## P0 — Environment & foundations · Day 0

- [x] Read and decompose the assignment PDF (4 pages, 8 sections)
- [x] Verify local toolchain (Java 24.0.2, Node 22.18.0, Python 3.12.7, Docker 28.3.3)
- [x] Confirm Maven / Angular CLI / Tesseract / poppler absent — plan around them
- [x] Write `docs/PROJECT_PLAN.md` (22 sections, E1–E40 edge cases)
- [x] Verify `anthropic/claude-haiku-4.5` on OpenRouter (200K ctx, text+image+file, structured_outputs, caching)
- [x] Smoke-test the OpenRouter key — structured JSON output confirmed, cost reported per call
- [x] Move Docker data C: → `D:\DockerData\wsl` (freed 22.5 GB; C: 8.5 → 31.1 GB)
- [x] Set Docker `MemoryMiB=4096`, `Cpus=6`, `SwapMiB=1024`
- [x] Create repo root `smart-inbox/`, `CLAUDE.md`, `docs/PHASES.md`, `docs/DECISIONS.md`
- [x] Docker daemon healthy (28.3.3, 6 CPU / 4 GB cap). Relocation partially reverted — see DECISIONS D-006. Old images orphaned but preserved on D:
- [x] `git init`, `.gitignore` (`.env` confirmed ignored), `.env` + `.env.example`
- [x] Pull `gvenzl/oracle-free:23-slim-faststart` (6.46 GB) and `greenmail/standalone` (435 MB)
- [x] Scaffold the four projects (backend, ai-service, frontend, testdata)
- [x] `scripts/smoke_llm.py` committed — proves schema-valid call + usage accounting, abstention
      and measured cost; prompt caching separately verified at 11.8x saving (DECISIONS D-007)

**Exit criteria:** Docker healthy, both images pulled, all four project skeletons present, smoke test green.

---

## P1 — Oracle schema, PL/SQL, queue, Spring skeleton · Day 1

- [x] `docker-compose.yml`: Oracle 23ai Free (2 GB container cap) + GreenMail, healthchecks, named volumes
- [x] `infra/oracle/init/` bootstrap: app user, tablespace, grants
- [x] Flyway `V1__tables.sql` — 20 tables (18 core + 2 for the literature bonus; plan §9.1–9.3)
- [x] Flyway `V2__indexes.sql` — 33 indexes: queue hot path, dedupe UQ, FKs, review-queue covering
- [x] Flyway `V3__packages.sql` — `PKG_JOB_QUEUE`, `PKG_AUDIT`, `PKG_REVIEW` (all VALID, 0 errors)
- [x] Flyway `V4__triggers_views.sql` — audit triggers (scope: DECISIONS D-008), `V_REVIEW_QUEUE`
- [x] Spring Boot 3.5.16 — Initializr no longer offers 3.5.x, so the zip was taken at 4.0.8 for the
      wrapper and the pom rewritten to 3.5.16 / `--release 21`. Builds clean on JDK 24.
- [x] Datasource + Flyway wired; app boots against Oracle, V1–V4 applied clean
- [x] `JobQueueRepository` + `JobWorkerPool` (4 threads, idempotent handler SPI, stale-lock reaper)
- [x] **Test:** 8 threads / 20 jobs from a `CyclicBarrier` — each claimed exactly once (D-009)
- [x] **Test:** 2^n backoff measured, `DEAD` after 3 attempts and never re-served, reaper recovers
- [x] **Test:** `PKG_AUDIT` autonomous transaction survives a rolled-back business txn

**Exit criteria:** `docker compose up` → Flyway applies clean → Spring boots → queue tests green.

---

## P2 — Mail ingestion + synthetic corpus · Day 2

### Ingestion
- [x] `MailboxAdapter` + `ImapMailboxAdapter` (jakarta.mail), GreenMail default, Gmail by 4 values
- [x] `MailPoller` scheduled, non-overlapping, marks `\Seen` **only after** the handler commits
      (needed `mail.imap.peek=true` — see DECISIONS D-011)
- [x] `MimeWalker` — depth-first, text/plain preferred, HTML→text via jsoup **(E3)**
- [x] `AttachmentSniffer` — magic bytes for PDF/PNG/JPEG/GIF/RTF/OLE/OOXML **(E4)**
- [x] One-level `message/rfc822` recursion with `nesting_level` **(E5)** — and the two live-IMAP
      bugs this exposed, fixed and regression-tested (D-011)
- [x] `QuotedTextDetector` — both quoting styles; quoted text de-prioritised, never deleted **(E10)**
- [x] `BlobStore` — SHA-256 content-addressed, atomic writes, verified dedupe **(E9)**
- [x] `DedupeService` — Message-ID + fallback content hash, UQ constraint enforces it **(E2)**
- [x] `IngestService` — creates `DOCUMENT` rows incl. `EMAIL_BODY` **(E11)**, enqueues `PARSE_DOCUMENT`
- [x] Size caps enforced **(E8)**; non-PDF logged with `skip_reason` **(E6)**

### Corpus generator (`testdata/generator/`, seeded RNG)
- [x] 12 emails, varying detail levels (complete / missing reporter / missing product / vague)
- [x] 6 digital form PDFs + 1 multi-page with a lab table continuing across a page break (E18)
- [x] 3 scanned/handwritten PDFs — verified 0 extractable chars, so genuinely image-only; one
      built at `ScanStyle.hard()` to drive the legibility path (E34)
- [x] 6 article PDFs, two-column — 2 case series (2 and 3 patients, E32), plus a review and a
      trial write-up as negative examples. Naive `sort=True` reading order verified to
      interleave ("Introduction    Conclusion" on one line) — the E14 demo is real
- [x] 3 non-English (German, French, Japanese — CJK glyphs render correctly) + 1 mixed-language
- [x] 3 PQC-only, 3 MI-only, 2 irrelevant, plus 2 deliberate combinations (E23, E24)
- [x] Adversarial set, all 10 verified by inspecting the generated files: duplicate PDF **(E9)**,
      forwarded rfc822 with the case one level down **(E5)**, password-protected **(E7)**,
      corrupt-but-valid-magic-bytes **(E7)**, .docx + .zip **(E6)**, mislabelled octet-stream
      **(E4)**, hybrid digital+scanned **(E12)** — confirmed page 1 DIGITAL / page 2 SCANNED,
      body-vs-form age conflict 58 vs 71 **(E33)**, two quoted reply styles **(E10)**
- [x] `testdata/goldens/*.json` — 38 files, derived from the same `CaseSpec` that renders the
      document, so ground truth is true by construction rather than by later annotation
- [x] `scripts/seed_mailbox.py` — 38 messages posted over real SMTP, all 38 read back over IMAP

**Exit criteria:** ~30 emails / ~38 documents land in `INBOX_MESSAGE` with correct attachment rows and no duplicates.

**MET, verified live against GreenMail on 4 Sep 2026:** 38 messages / 59 documents / 23 attachment
rows, 0 duplicates, and **0 mismatches against `testdata/corpus/manifest.json`** — every message
produced exactly the documents the corpus says it should. 45/45 backend tests green.

---

## P3 — PDF understanding (Python) · Day 3

- [ ] FastAPI skeleton, settings, telemetry envelope
- [ ] `pdf/loader.py` — open, encryption check **(E7)**, rotation normalise, page cap **(E8)**
- [ ] `pdf/flavour.py` — **per-page** rendering × genre detection **(E12, E13)**
- [ ] `pdf/layout.py` — blocks, column clustering, reading order, **char-offset → bbox span index (E14)**
- [ ] `pdf/sections.py` — heading segmentation, `excluded_from_case` **(E15)**
- [ ] `pdf/tables.py` — `find_tables()` + vision fallback + cross-page merge **(E18)**
- [ ] `pdf/images.py` — meaningful-image filter (area, stddev, repeated-xref) **(E19)**
- [ ] `pdf/render.py` — page → PNG at 200 DPI, cached by content hash
- [ ] `lang/detect.py` — lingua-py, block → page roll-up **(E17)**
- [ ] `POST /v1/parse` returning the full plan §10.7 envelope
- [ ] `ParseDocumentHandler` in Java persists pages/sections/tables/images
- [ ] **Verify visually:** two-column article reading order is correct, not interleaved
- [ ] **Verify:** hybrid PDF reports `rendering=MIXED` with correct per-page values

**Exit criteria:** every corpus PDF parses; per-page flavours match expectations; spans carry bboxes.

---

## P4 — LLM pipeline + evidence verification · Day 4

- [ ] `llm/client.py` — OpenRouter, retries **(E36)**, `cache_control`, usage + cost accounting
- [ ] `llm/schemas.py` — pydantic models → JSON Schema (single definition)
- [ ] `llm/repair.py` — one-shot schema repair round-trip **(E36)**
- [ ] `P0_system` preamble — taxonomy, confidence rubric, abstention rule. **Must exceed ~2048 tokens or caching will not engage** (see DECISIONS D-004)
- [ ] `P5_transcribe` — vision OCR + legibility **(E34)**
- [ ] `P8_translate` — block-wise, original stays canonical **(E16)**
- [ ] `P6_summarise` — 10–15 sentences + relevance + reason, map-reduce for long docs **(E20)**
- [ ] `P7_describe_image` **(E19)** · `P10_table_to_json` **(E18)**
- [ ] `P1_classify` + ICSR element checklist **(E22)**
- [ ] Rule post-processing: `NOT_RELEVANT` exclusivity **(E21)**, ICSR validity rule **(E22)**
- [ ] `P2/P3/P4` extraction — ICSR / PQC / MI
- [ ] Typed value handling: `PartialDate` **(E28)**, units/dose **(E29)**, age **(E30)**
- [ ] **`pipeline/verify.py`** — exact → fuzzy ≥90, offset rewrite, bbox resolve, cap at 0.40 **(E27)**
- [ ] Deterministic confidence adjustment chain **(E26, E34)**
- [ ] `merge/CaseMergeService` — cross-source conflict detection **(E33)**
- [ ] Completion barrier before classification **(E39)**
- [ ] All handlers wired; full corpus runs end-to-end to `READY_FOR_REVIEW`

**Exit criteria:** whole corpus processed; every `STATED` field has evidence; verification rate measured.

---

## P5 — Angular reviewer UI · Day 5

- [ ] Angular 22 scaffold, Material, proxy to :8080
- [ ] Queue screen from `V_REVIEW_QUEUE` — chips, confidence, flags, filters, sort worst-first
- [ ] SSE live status via `/api/events`
- [ ] Detail: left pane page-image viewer with **absolute-positioned highlight overlays**
- [ ] Detail: classification card, confidence bars, reasons, **ICSR element checklist**
- [ ] Detail: summary + relevance verdict
- [ ] Detail: field accordion by group, status badges, confidence bars, evidence chips, edit controls
- [ ] Conflict rows rendered as two stacked values with a choose control **(E33)**
- [ ] Tables as real HTML tables; images with description + review flag
- [ ] Audit timeline; AI-call inspector (`/api/ai-calls/{id}`)
- [ ] Accept / Override / Reject writing `REVIEW_DECISION` + `AUDIT_EVENT` **(E40)**
- [ ] **The demo moment:** click evidence chip → correct page + highlight; amber chip when unverified

**Exit criteria:** a reviewer can work a message end-to-end and every action is audited.

---

## P6 — Literature screening (bonus) + evaluation · Day 6

### Bonus
- [ ] `POST /api/literature/batches` multipart upload
- [ ] `P9_screen_article` — case-report verdict + reason + **multi-case split** **(E32)**
- [ ] Section-derived case-boundary candidates, model confirms
- [ ] Results reuse the P5 detail component unchanged
- [ ] Literature screen with per-file progress

### Evaluation
- [ ] `eval/run_eval.py` over the golden set
- [ ] Category P/R/F1 (micro + macro), multi-label exact-set accuracy
- [ ] ICSR element accuracy
- [ ] Field accuracy (exact + normalised)
- [ ] **Abstention correctness** — false-confident-value counted separately from a miss
- [ ] **Evidence verification rate**
- [ ] Confidence calibration curve by decile
- [ ] Latency p50/p95 per document and per stage
- [ ] Cost per document, total, cache-hit rate
- [ ] `/report` page in the UI
- [ ] Prompt tuning pass against the calibration curve

**Exit criteria:** `eval/report.md` exists with real numbers; bonus demonstrably splits a case series.

---

## P7 — Documentation & submission · Day 7

- [ ] `README.md` — setup, run, env vars (**placeholders only**), Gmail recipe
- [ ] `docs/WRITEUP.md` (2–5 pages) — architecture diagram, tech choices, **prompting approach**,
      known limitations, what I'd change for production, the 10 declared assumptions
- [ ] `docs/architecture.svg`
- [ ] `docs/sample-outputs/*.json` — extracted JSON per test document
- [ ] Screenshots + short screen recording of the review screen
- [ ] **Clean-clone test:** fresh clone → `.env` → `docker compose up` → seed → works
- [ ] Final sweep: no API key, no real data, no `TODO` in the tree
- [ ] Send to pavithra.r@ / vivek.w@ / ashish.b@ / rehan.n@ clinevotech.com

**Exit criteria:** all six brief §7 deliverables present and verified on a clean clone.

---

## Open questions / blockers

_(none currently)_

## Session log

| Date | Session | What happened |
|---|---|---|
| 2026-09-04 | 2 | P0 closed and P1 built: both Docker images confirmed; `docker compose up` brings up Oracle 23ai Free + GreenMail healthy; Flyway V1–V4 apply clean (20 tables, 33 indexes, 3 PL/SQL packages, 4 triggers, `V_REVIEW_QUEUE`, zero compile errors); `JobQueueRepository` + `JobWorkerPool`; **10/10 backend tests green** incl. 8-way SKIP LOCKED concurrency and autonomous-transaction audit survival; LLM client + smoke test proving schema-valid output, abstention, measured cost and an 11.8× prompt-cache saving. Decisions D-007..D-010 recorded. |
| 2026-09-04 | 1 | Assignment analysed; `PROJECT_PLAN.md` written; OpenRouter model verified and key smoke-tested; Docker data relocated to D: (freed 22.5 GB); repo + tracking docs created |

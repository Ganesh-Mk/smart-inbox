# Smart Inbox — Decision Log

Decisions made **during** implementation, with the evidence behind them. The plan (`docs/PROJECT_PLAN.md`)
holds design decisions made up front; this file records what we learn as we build, including anything that
contradicts the plan. Newest last.

Each entry: what was decided, why, and what it affects.

---

### D-001 · Project name: **Smart Inbox** · 4 Sep 2026

The user chose **Smart Inbox**, matching the assignment's own title ("Smart Inbox Assistant for a
Healthcare Company") and the GitHub repository `Ganesh-Mk/smart-inbox`. Naming the deliverable after the
thing the brief asked for is the least surprising choice for the reviewers.

Briefly considered and dropped: *FirstPass* (a pun on "first-pass metabolism" — clever, but it obscures
what the repo is when a reviewer scans a list of submissions) and **Argus** — rejected outright because
Oracle Argus Safety is *the* incumbent pharmacovigilance case-management product, so the name would read
as derivative in exactly the room we are presenting to.

**Affects:** repo directory `smart-inbox/`, DB user `SMARTINBOX`, Java package `com.clinevo.smartinbox`,
Angular app name, README title.

---

### D-002 · Docker data relocated to `D:\DockerData\wsl` · 4 Sep 2026

`docker_data.vhdx` was **22.51 GB, non-sparse, on C:**, which had only 8.4 GB free — not enough for the
Oracle image (~4.6 GB) plus container layers plus Windows headroom.

Moved with `robocopy /MOVE /J` (70 s, 22.6 GB verified), then set `DataFolder` in
`%APPDATA%\Docker\settings-store.json`. Original settings backed up to `settings-store.json.bak-smartinbox`.

Result: **C: 8.5 → 31.1 GB free**, D: 48.6 GB free. Also set `MemoryMiB=4096`, `Cpus=6`, `SwapMiB=1024` so
Docker cannot starve the 8 GB host while the JVM, Python and Angular are also running.

Note for future reference: `robocopy` exit code **1 means success** ("one or more files copied"). Only
codes ≥ 8 are failures. A shell wrapper will report exit 1 as a failure — it is not.

**Affects:** `CLAUDE.md` environment section; nothing in the application.

---

### D-003 · OpenRouter key verified; multi-label failure mode confirmed live · 4 Sep 2026

Smoke call to `anthropic/claude-haiku-4.5` with a `json_schema` `response_format` returned valid structured
JSON in 5.9 s and reported exact cost (`usage.cost`), so we do not have to estimate spend — we can record
the real figure in `AI_CALL_LOG.cost_usd`.

**The model returned `NOT_RELEVANT` at 0.05 alongside `ICSR` at 0.95** on a textbook ICSR input — i.e. it
treated the labels as a probability distribution rather than independent booleans. This is exactly the
failure mode edge case **E21** predicts, observed on the very first call. It confirms the plan's decision to
enforce `NOT_RELEVANT` exclusivity **in code** rather than by prompt instruction.

**Affects:** P4 rule post-processing is mandatory, not optional. Add a unit test asserting that a label set
containing any of ICSR/PQC/MI drops `NOT_RELEVANT`.

---

### D-004 · Prompt caching needs a large enough static prefix · 4 Sep 2026

The smoke call sent `cache_control: {"type":"ephemeral"}` on the system block and got
`cached_tokens: 0, cache_write_tokens: 0`. Cause: Anthropic has a **minimum cacheable prefix** and the test
system prompt was only ~40 tokens — well under it, so the breakpoint was silently ignored (no error).

Consequence for the design: the cost model in plan §11.3/§11.6 assumes the `P0_system` preamble is cached
across the batch. That only materialises if `P0_system` is genuinely large — the full taxonomy, the
confidence rubric with worked examples per band, field definitions and evidence rules. **Target ≥ 2,048
tokens for `P0_system`.** This is not padding for its own sake: those are the instructions that carry the
domain rules, and they belong in the prompt regardless.

**Verification:** the batch report must assert `cached_tokens > 0` on the second and subsequent calls. If
it is still zero, caching is not working and the cost claim in the write-up must be corrected rather than repeated.

**Affects:** P4 `P0_system` authoring; `eval/run_eval.py` cache-hit-rate metric; write-up cost section.

---

### D-005 · Repo root is `smart-inbox/`; the assignment PDF is **not** committed · 4 Sep 2026

Repo lives at `D:\Prep\Clinevo_Technologies\smart-inbox\`. `Clinevo_Assignment.pdf` stays in the parent
directory and is never added to git — it is marked *"Confidential. Provided solely for candidate
evaluation"* and *"do not share externally"*, and the submitted repo may be forwarded internally.

**Affects:** `.gitignore`; nothing else.

---

### D-006 · Docker data relocation: partially reverted, objective still met · 4 Sep 2026

Follow-up to D-002. The relocation went wrong and then recovered. Sequence:

1. `DataFolder` was written to `settings-store.json` by PowerShell with `-Encoding utf8`, which emits a
   **UTF-8 BOM**. Docker Desktop's Go JSON parser rejected it:
   `formatting settings-store.json: invalid character 'ï' looking for beginning of value`.
2. Unable to read its settings, Docker fell back to defaults, **unregistered the `docker-desktop` WSL
   distro** and began provisioning a fresh disk on C:.
3. Fixed by rewriting the file as UTF-8 **without** BOM (`Path.write_bytes(json.dumps(...).encode())`),
   then `wsl --import-in-place docker-desktop D:\DockerData\wsl\main\ext4.vhdx` to re-register the distro.
4. Docker now starts cleanly, but it recreated its working disks under `C:\Users\manoj\AppData\Local\Docker\wsl`
   despite `DataFolder` still being set to D:. The setting is retained in the file but not honoured by
   this build for the data disk.

**Decision: stop here and accept it.** The actual objective — enough free space on C: for the Oracle
image — is met with a large margin: **C: went from 8.4 GB to 29.5 GB free.** Continuing to chase the
relocation risks breaking a working daemon against a 7-day deadline, for no benefit.

**Loose end for the user to decide later:** the original 22.51 GB `docker_data.vhdx` containing their
pre-existing images is intact and orphaned at `D:\DockerData\wsl\disk\docker_data.vhdx`. It is not in
use. It can be restored, or deleted to reclaim 22.5 GB of D:. Not Smart Inbox's call — leave it until asked.

**Lesson worth keeping:** never write a config file consumed by a Go/Rust/C program with PowerShell's
`-Encoding utf8` on Windows PowerShell 5.1 — it adds a BOM. Use `utf8NoBOM` (PS 7+) or write bytes from Python.

**Affects:** `CLAUDE.md` environment section corrected; no application impact.

---

### D-007 · Prompt caching verified live: threshold, field names and the real saving · 4 Sep 2026

Follow-up to D-004, which predicted caching would not engage below Anthropic's minimum cacheable
prefix but did not establish the threshold or the field names. Both are now measured.

A ~700-token system prompt (`scripts/smoke_llm.py`) still reports `cached_tokens: 0,
cache_write_tokens: 0` — silently not cached, no error. A **4,376-token** preamble caches on the
first call and reads back on the second:

| Call | `prompt_tokens` | `cached_tokens` | `cache_write_tokens` | `cost` |
|---|---|---|---|---|
| 1 (cold) | 4,385 | 0 | 4,376 | $0.005504 |
| 2 (warm) | 4,386 | 4,376 | 0 | $0.000468 |

**An 11.8× cost reduction on the cached segment, measured rather than claimed.** This is the number
the write-up quotes.

Two concrete corrections to the implementation:

1. **Field location.** OpenRouter reports *both* counters inside `usage.prompt_tokens_details`
   (`cached_tokens`, `cache_write_tokens`). It does **not** surface Anthropic's native
   `cache_creation_input_tokens` at the top level. `LlmClient._usage_of` originally read the
   top-level name and would have recorded every cache write as zero — silently under-reporting
   spend and making the cache look ineffective. Fixed.
2. **Cached tokens are counted inside `prompt_tokens`**, not in addition to it (4,386 total of
   which 4,376 cached). `estimate_cost` therefore prices the *fresh* remainder only. The fallback
   estimator is a backstop anyway: `usage.cost` is present on every call and is what we store.

**Affects:** `ai-service/app/llm/client.py`; the `P0_system` authoring target of ≥ 2,048 tokens in
D-004 is confirmed as necessary (and comfortably exceeded by the real preamble); the cache-hit-rate
metric in `eval/run_eval.py` reads `prompt_tokens_details.cache_write_tokens`.

---

### D-008 · Audit triggers fire on every UPDATE, but only on REVIEWER inserts · 4 Sep 2026

Plan §9.4 specifies `TRG_FIELD_AUDIT` and `TRG_CLASSIFICATION_AUDIT` firing "on update/insert" as a
safety net so nothing can change without an audit row. Implemented with one deliberate narrowing.

**Every UPDATE is audited, unconditionally.** The schema is append-only by design (E40), so an
in-place change to a classification or an extracted field is exactly the anomaly the net exists to
catch — including the one legitimate update, setting `superseded_by`.

**INSERTs are audited only when `decided_by = 'REVIEWER'`.** Auditing the AI's own inserts would add
roughly 40 rows per case — on the ~38-document corpus, well over a thousand `AUDIT_EVENT` rows that
say nothing a reviewer needs. It would also make the message audit timeline in the UI unreadable,
which defeats the purpose of having one. The AI's inserts lose nothing: `AI_CALL_LOG` already holds
the exact request, response, prompt version, tokens and cost behind every one of them, and the
handler writes a single case-level audit event.

The rule in one line: **a human action is always an audit event; a machine action is always an
`AI_CALL_LOG` entry; an in-place mutation is always both.**

**Affects:** `V4__triggers_views.sql`; the audit timeline in the P5 detail view stays legible.

---

### D-009 · `FOR UPDATE SKIP LOCKED` verified under 8-way concurrency · 4 Sep 2026

Oracle locks rows for a `FOR UPDATE` cursor at OPEN time, which raised a real doubt about the
dequeue design: if opening the cursor locked the whole PENDING backlog, the first worker to arrive
would claim everything and the other three would idle — correct, but not a queue.

`PKG_JOB_QUEUE.dequeue` therefore uses `OPEN` → `FETCH BULK COLLECT ... LIMIT n` → `CLOSE`, and
`JobQueueConcurrencyTest.skipLockedGivesNoDoubleDequeue` measures what actually happens: **8 threads
released simultaneously from a `CyclicBarrier`, 20 jobs, batch size 3.** Result: all 20 claimed,
every one exactly once, no thread starved, no PENDING row left behind. Combined with
`SKIP LOCKED`, the fetch limit does bound what gets locked.

Two supporting choices fell out of this:

1. **`dequeue` runs in its own short transaction** (`Propagation.REQUIRES_NEW`). The row locks are
   released as soon as the claim commits; the claim is then carried by `state = 'RUNNING'`. Holding
   database row locks for the minute a handler spends waiting on the LLM would be the wrong trade —
   the lease plus `reap_stale_locks` is what protects an abandoned job (E37), not a long-lived lock.
2. **The result cursor binds `SYS.ODCINUMBERLIST`**, not a `locked_by = :me` filter. A worker that
   claimed jobs on a previous call would otherwise see them again in this call's result set.

**Affects:** `V3__packages.sql`; `JobQueueRepository.dequeue`; the walkthrough can cite a measured
concurrency result rather than a claim about Oracle semantics.

---

### D-010 · Angular 22's CLI needs Node ≥ 22.22.3; a portable Node 24 LTS sits in `.tools/` · 4 Sep 2026

`npx @angular/cli@22 new` refuses to run on this machine's Node 22.18.0:
*"The Angular CLI requires a minimum Node.js version of v22.22.3 or v24.15.0 or v26.0.0."*

Three options were available: downgrade to Angular 20/21 (off the plan's stated stack, and Angular
is the one framework the brief names), upgrade the system Node (an installer, and it changes a
machine the user shares with other work), or vendor a portable runtime.

**Chosen: a portable Node 24.20.0 LTS extracted to `.tools/node-v24.20.0-win-x64/`,** used only for
frontend commands. No installer, no admin prompt, nothing about the user's system Node touched.
`.tools/` is gitignored — it is ~100 MB of downloaded runtime, not source.

The README states Node ≥ 22.22.3 (or 24 LTS) as a prerequisite, which is Angular 22's real
requirement and not something we invented. A reviewer on a current Node needs nothing extra.

**Affects:** `.gitignore`; frontend build commands take a `PATH` prefix; README prerequisites.

---

### D-011 · Two IMAP bugs that only a real server exposes · 4 Sep 2026

Both were found by running the application against the GreenMail container and comparing the
resulting Oracle rows against `testdata/corpus/manifest.json` — not by a test. Every unit and
integration test was green while both were live, because all of them parsed `.eml` files from
disk, and a file-backed `MimeMessage` behaves differently from an `IMAPMessage`.

**Bug 1 — the forwarded case silently vanished.** `adv-02-forwarded-rfc822` produced one
document instead of two. The inner PDF arrived as a **zero-byte** part named `part-1.pdf`
(rather than `AER-2026-00188.pdf`), sniffed as `application/x-empty`, and was recorded with
`skip_reason='EMPTY'`. Cause: jakarta.mail fetches `IMAPMessage` parts lazily from the folder's
connection, and a nested `message/rfc822` part cannot be read reliably once the stream position
has moved on. Fix: materialise each message with `writeTo` into a plain `MimeMessage` before
handing it to the handler. The failure mode is the dangerous kind — no exception, no warning,
just a case quietly missing from the review queue.

**Bug 2 — a failed message would never be retried.** Worse, and partly created by the fix for
bug 1. Reading a message body over IMAP causes the server to set `\Seen` itself (RFC 3501
§6.4.5). So although the poller only calls `setFlag(SEEN)` *after* the handler commits, the
flag was already set by the act of reading — and a message whose ingestion threw was skipped
forever by the next poll. Fix: `mail.imap.peek=true`, which issues `BODY.PEEK[]` instead of
`BODY[]`. The `\Seen` flag is meant to be *our* high-water mark and now genuinely is.

**What this changes about testing.** File-based fixtures cannot catch either bug. Added
`ImapForwardedMessageTest`, which drives an in-process GreenMail IMAP server: it asserts the
forwarded PDF arrives with real bytes and its real filename, that `\Seen` prevents
re-ingestion, and that a handler which throws leaves the message unread. The third assertion is
the one that caught bug 2.

**Affects:** `ImapMailboxAdapter`; a new integration test; the write-up's "what I'd change for
production" gains a concrete point about testing mail code against a real protocol rather than
against saved messages.

---

### D-012 · Four layout and language corrections, all found by measuring the corpus · 4 Sep 2026

Running the P3 modules over all 23 corpus PDFs and reading the output — rather than assuming
the plan's algorithms were right — turned up four defects. Each is recorded with the number
that exposed it, because in every case the plan's specified approach was plausible and wrong.

**1. Columns cluster on the left edge, not the x-midpoint (E14).** Plan §10.2 specifies
clustering block x-midpoints. Measured on page 1 of `article_A01.pdf`, the left edges are
cleanly bimodal at **51 and 308**, while the midpoints scatter across **72, 82, 169, 199, 328,
336, 357 and 426** — because a heading like "Discussion" is 56pt wide and the paragraph under
it is 237pt. k-means on midpoints fits **three** clusters on a two-column page, splitting the
left column into headings and body; reading order then emits every left heading, then every
left paragraph, then the right column. That is worse than not clustering at all. Blocks in a
column share a left edge exactly, because they are laid out in the same frame.

**2. A form's field grid is not a set of columns.** A filled-in report form clusters into two
x-groups exactly like a two-column article, but must be read *across* each row: "Report
reference | Date of report", not every left cell followed by every right cell. Added a
row-alignment test — in a grid, blocks outside column 0 almost always have a vertically
aligned partner in column 0; in a genuine two-column article they do not. All nine corpus
forms now report one column and read row-major; the four two-column articles are unaffected.

**3. Language detection needed three guards, not one confidence threshold (E17).**

| Observed | Cause | Guard |
|---|---|---|
| `HOSPITALISATION_OR_PROLONGATION` → French, 0.82 | a 31-character single token clears any length threshold | require ≥ 3 whitespace-separated words (underscores deliberately not split) |
| a page of lab values → German, 1.00 | no block was individually judgeable, so the fallback joined them all and judged the concatenation | join only blocks that are individually prose |
| a lab cell → passes a "mostly letters" test | "Alanine aminotransferase640U/L10 – 40H" is 76% letters because the analyte name dominates | a **digit ratio** test: prose is 1–2% digits, that cell is 18% |
| `form_ja.pdf` → English | 163 Japanese characters lost to 223 characters of Latin furniture (product name, MAH address, footer) | weight CJK characters ×2.5, since they carry ~2–3× the content per code point |

**4. The corpus's "non-English" PDFs were English documents wearing translated labels.**
`form_de` and `form_fr` had translated field labels but an English narrative, so the
content-weighted roll-up correctly reported them as English — and the corpus's claim to contain
three non-English documents was not honest. The generator now writes German, French and
Japanese *narratives*. `form_mixed` keeps English labels around a German narrative, which is
the actual E17 case. All four now detect correctly: de, fr, ja, and de.

**Affects:** `ai-service/app/pdf/layout.py`, `ai-service/app/lang/detect.py`,
`testdata/generator/corpus_messages.py`; 31 Python tests, each written from an observed
failure rather than from imagination.

---

### D-013 · OpenRouter silently discards a `response_format` schema above ~4 KB · 4 Sep 2026

The first live test of `P2_extract_icsr` came back as free-form JSON in a markdown fence, in a
shape nothing like the schema. `finish_reason` was a perfectly ordinary `stop`. No error, no
warning, no indication anything had gone wrong — it looked exactly like a model quality problem.

It was a transport problem. `prompt_tokens` gave it away:

| Request | schema size | `prompt_tokens` | outcome |
|---|---|---|---|
| system prompt, no schema at all | — | 2,648 | baseline |
| `P1_classify` | 3,281 B | 4,011 | schema sent, output conformed |
| `P2_extract_icsr` | 8,807 B | **2,648** | **schema never sent** |

`prompt_tokens` for the large schema is *identical to sending no schema*. OpenRouter drops it
and forwards the request unconstrained. Bracketed with synthetic schemas, the cliff sits
between **3,527 B (sent)** and **4,683 B (dropped)** — and it is size, not `$defs` count:
a fully inlined 3,527 B schema with zero `$defs` is transmitted, while an inlined 4,683 B one
is not.

Three responses, in order of importance:

1. **Make the failure loud.** `strict_schema` now measures every schema and raises
   `SchemaTooLarge` above 4,000 B. A schema that would be silently ignored is a build-time
   bug, not something to diagnose later from oddly-shaped output. This is the part that
   matters: the defect was not that a schema was too big, it was that nothing said so.
2. **Split the extraction.** `IcsrCase` (9,388 B) is no longer sent at all. Three calls —
   `IcsrParties` (3,205 B), `IcsrProducts` (2,533 B), `IcsrReactions` (2,795 B) — are assembled
   in code by `IcsrCase.assemble`, taking the case confidence as the minimum of the three. This
   is the "decompose the task rather than reach for a bigger model" answer the plan anticipated
   in §19, and each call gets a shorter, more focused instruction as a bonus.
3. **Strip descriptions from the transmitted schema.** They were ~35% of every schema's bytes.
   They are also paid for in full on every call, because `response_format` is *not* covered by
   the prompt cache — unlike the system prompt, which is. So the field conventions moved into
   `P0_system` §9, where they are cached, and the pydantic `description=` text stays in the
   source as documentation for humans. Largest transmitted schema is now 3,205 B.

Verified live afterwards: all three extraction calls conform with no repair round-trip, and
correctly return age 71 YEAR from "71-year-old", batch `FNQ-2210A`, start date `2026-04-17` at
`DAY` precision, and `HOSPITALISATION_OR_PROLONGATION` as the single seriousness criterion.

**Affects:** `app/llm/schema_tools.py`, `app/llm/schemas.py`, `P0_system` §9; the write-up gains
a concrete, measured provider limitation worth describing.

---

### D-014 · The prompt cache is keyed per schema, so batch by prompt type · 4 Sep 2026

With `P0_system` at ~3,100 tokens, caching finally engages (D-004 and D-007 established that it
does not below the minimum prefix). But the three split extraction calls all reported
`cache_write_tokens ≈ 4,700` and `cached_tokens = 0` — every call writing, none ever reading.

Measured directly:

| Call | schema | `cached_tokens` | cost |
|---|---|---|---|
| 1 | IcsrParties (cold) | 0 → writes | $0.009107 |
| 2 | IcsrParties | 4,938 | $0.002589 |
| 3 | IcsrParties | 4,938 | $0.002587 |
| 4 | **PqcCase** | **0** | $0.005457 |
| 5 | IcsrParties again | 4,938 | $0.002588 |

The cached prefix **includes the response schema**, not just the system prompt — structured
output is translated into a tool definition that sits ahead of the system block, so changing
the schema changes the prefix and misses the cache entirely. Call 5 shows the IcsrParties entry
survives the interruption, so the entries are independent, not evicted.

**Measured saving: $0.009107 → $0.002589, a 3.5× reduction** on an identical call.

**Consequence for the batch runner:** work must be grouped **by prompt type**, not
round-robined per document. Classify every message, then extract every set of parties, then
every set of products. Processing document-by-document changes the schema on every call and
throws the cache away completely — the difference between $0.0026 and $0.0091 per call, across
several hundred calls in a full corpus run.

**Affects:** `pipeline/` call ordering; `scripts/run_batch.py`; the cost section of the
write-up, which can now quote a measured figure and the reason behind it.

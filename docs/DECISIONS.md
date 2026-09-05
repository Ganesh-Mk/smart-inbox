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

---

### D-015 · The corpus was not reproducible, and it was quietly duplicating cases · 4 Sep 2026

Noticed while bringing the stack up for a demo: the database held **76 messages and 118
documents** where the corpus defines 38 and 59. Nothing had failed; the counts were simply
wrong.

Cause: `build_eml` used `email.utils.make_msgid()`, which embeds a clock reading and a random
number — `<178852229627.1696.15523711796242495092.icsr-01-complete-body@smart-inbox.test>`.
Every regeneration of the corpus therefore produced *new* `Message-ID` headers, so re-seeding
after a rebuild looked like 38 genuinely new messages rather than the same ones. E2 dedupe was
working perfectly and being fed different identities.

This is a bad failure in a pharmacovigilance context specifically: a duplicated individual case
safety report is a reportable data quality problem, not a cosmetic one. And it contradicted the
generator's own claim to be reproducible from a fixed seed.

Fixed by deriving the header from the corpus key: `<icsr-01-complete-body@smart-inbox.test>`.
Deterministic, unique, and obviously synthetic.

Chasing that exposed three further sources of build-to-build drift, all now pinned:

* **ReportLab** stamps `/CreationDate`, `/ModDate` and a random `/ID` into every PDF —
  `rl_config.invariant = 1`.
* **`EmailMessage`** invents a random MIME boundary per multipart part — boundaries are now
  derived from the corpus key.
* **ZIP archives** record each entry's mtime — entries now carry a pinned timestamp.

**57 of 66 corpus files are now byte-identical across rebuilds.** The nine that are not are
correct to differ, and the docstring says so rather than overclaiming: `encrypted_report.pdf`
uses a random AES salt and IV (identical output would mean the encryption was broken), and the
PyMuPDF- and Pillow-written PDFs each stamp their own creation date. Their *content* and their
parse results are identical; only the container bytes move. The property that actually matters
— re-seeding never creates a duplicate case — is keyed on `Message-ID` and now holds.

**Affects:** `testdata/generator/messages.py`, `build.py`, `corpus_messages.py`. Verified by
clearing the database and mailbox and re-seeding: 38 messages, 59 documents, 23 attachments,
0 duplicates.

---

### D-016 · Spring's mail health check probed our IMAP port as SMTP · 4 Sep 2026

`/actuator/health` reported the whole application **DOWN**:

    "mail": { "status": "DOWN", "location": "localhost:3143",
              "error": "Got bad greeting from SMTP host: localhost, port: 3143,
                        response: * OK IMAP4rev1 Server GreenMail v2.1.13 ready" }

`spring-boot-starter-mail` auto-configures a `JavaMailSender` whenever `spring.mail.host` is
set, and with it a health indicator that opens an **SMTP** connection. Our port 3143 is IMAP,
so it got an IMAP greeting and declared failure — of a connection this service never makes.
`ImapMailboxAdapter` builds its own `jakarta.mail` Session from `inbox.mail.*`; the
`spring.mail` block was vestigial.

Removed it, which removes the indicator, and added `MailboxHealthIndicator` that checks the
dependency we actually have, through the same adapter the poller uses. Health now reports
something worth knowing:

    "mailbox": { "status": "UP", "mailbox": "imap://safety@smart-inbox.test@localhost:3143/INBOX",
                 "messages": 38, "unseen": 0 }

Worth keeping in mind generally: a health check that tests a different protocol on the right
port fails loudly but tells you nothing true, and it trains people to ignore the endpoint.

**Affects:** `application.yml`, new `MailboxHealthIndicator`.

---

### D-017 · `TO_CHAR` on a CLOB caps at 4,000 bytes and dead-lettered the pipeline · 4 Sep 2026

The first full corpus run stalled at classification:

    ORA-22835: Buffer too small for CLOB to CHAR conversion (actual: 4340, maximum: 4000)

Reading a CLOB into a `Map<String,Object>` via `queryForList` needs a conversion, and
`TO_CHAR(column)` is the obvious one. It converts to `VARCHAR2`, which caps at 4,000 bytes.
Page text passes that on the first page of any real document.

**This failing loudly was lucky.** Oracle threw rather than truncating; had it truncated
silently, the model would have been handed the first 4,000 characters of every page and the
missing text would have surfaced as unexplained extraction misses, months later, with nothing
to point at. The same applies to the `LISTAGG(TO_CHAR(...))` used to assemble a document for
summarisation.

Fixed by not converting at all: `ClobSupport.clob` selects the column as itself and reads it
with `ResultSet.getString`, which Oracle's driver materialises in full. Every affected read —
classification, extraction, summarisation, the message detail API, the AI-call inspector — now
goes through it, and a grep for `TO_CHAR` over CLOB columns returns nothing.

**Affects:** `ClobSupport` (new), all four job handlers, `ReviewController`.

---

### D-018 · Two corrections the first full corpus run exposed · 4 Sep 2026

Both were visible only once real data was on the screen.

**1. Every quality complaint was also labelled `ICSR_INCOMPLETE`.** The E22 rule assigned it at
two or more of the four minimum criteria. A PQC always names a reporter and a product, so it
always scores exactly 2 of 4 — and all three PQC-only messages in the corpus came back
`ICSR_INCOMPLETE, PQC`.

The rule was wrong, not the model. **An adverse event is necessary, not one of four
interchangeable boxes**: without one there is no safety case to be incomplete about. The rule is
now `four present → ICSR`, `an adverse event plus at least one other → ICSR_INCOMPLETE`,
otherwise no ICSR label. This matters more than it sounds: a flag that fires on almost every
complaint teaches reviewers to ignore it, and then it fails on the case that mattered.

**2. `min_field_confidence` was reporting 0.00 for well-extracted messages.** The queue sorts
worst-first on this column, and seven messages showed 0.00 while every extracted fact in them
sat between 0.85 and 0.95 — so the *best*-evidenced cases were being sorted to the top of the
"needs attention" list.

Cause: the view took the minimum over every asserting field, including `NARRATIVE`. The
narrative is a paragraph of prose carrying the case-level confidence, not a discrete fact with
its own quote. `V5__queue_view_confidence.sql` excludes it, so the column now means what its
name says — the least confident *fact* awaiting sign-off.

**Affects:** `pipeline/classify.py` and its tests; new Flyway migration `V5`.

---

### D-019 · The full stack does not fit in 8 GB; a read-only mode is the fix · 4 Sep 2026

The backend was killed **three times** by the OS for low memory while serving the demo, the
third time in the reduced configuration described below — so the lean mode helps but is not on
its own a fix.

My first diagnosis blamed the application stack and was wrong. Measured properly afterwards:

| | |
|---|---|
| Total RAM | 7.7 GB |
| Available | **0.5 GB** |
| Sum of all working sets | 5.7 GB across **314 processes** |
| Committed | 18.1 GB against a 24.7 GB limit — heavy paging |

The whole Docker VM (`vmmemWSL`, holding Oracle and GreenMail) was **778 MB**, and the JVM
~320 MB. The bulk was elsewhere: three VS Code instances (~700 MB), other editors and agents
(~670 MB), Defender, browsers. **The machine is oversubscribed by its normal working set, and
this project is a minority contributor.**

That matters for what the fix is. Shrinking the JVM further buys almost nothing; the honest
statement is that running the full stack alongside a working IDE session needs more headroom
than this machine has, and a reviewer on a 16 GB machine will not notice any of it.

**Viewing processed results needs neither GreenMail nor the AI service.** The mail is already
ingested and the corpus already processed; both live in Oracle. So there is a legitimate
read-only configuration that serves the entire reviewer UI — queue, detail, evidence
highlighting, audit trail, AI-call inspector — on roughly a third of the footprint:

```bash
docker compose stop greenmail          # mail already ingested
# stop the AI service                  # corpus already processed
java -Xmx320m -XX:MaxMetaspaceSize=160m -jar target/smart-inbox-backend-1.0.0.jar \
     --inbox.mail.poll-enabled=false --inbox.queue.worker-threads=0
```

Verified: `/` and `/queue` serve, all 38 messages present, verification rate unchanged at 98.7%.

The full pipeline (ingesting new mail, running new AI calls) needs everything running and about
2 GB of headroom.

**Worth saying in the write-up rather than treating as a laptop annoyance:** Oracle Database
Free wants 2 GB on its own, and a four-process stack on one developer machine is a real
deployment constraint. In production these are separate hosts and the question does not arise —
but it is the kind of thing that only surfaces when everything actually runs at once, and a
reviewer cloning this repo onto a 8 GB laptop will meet it.

**Affects:** README should mention the read-only mode; `CLAUDE.md` environment facts.

---

### D-020 · An `<img src>` bypasses the auth interceptor and the browser prompts for credentials · 4 Sep 2026

Opening a message with a scanned page put a native **"Sign in to http://localhost:8080"** dialog
over the review screen — on the one interaction the whole traceability design exists for.

Nothing was wrong with the credentials. The cause is that there are two different fetch paths:

- Every `HttpClient` call is cloned by the interceptor in `app.config.ts` and carries
  `Authorization: Basic …`.
- The page PNG was `<img [src]="pageImageUrl()">`. That is a **browser subresource fetch**. It
  never enters `HttpClient`, so no interceptor runs, so no header is attached.

`/api/documents/**` is `authenticated()`, so it answered `401` with `WWW-Authenticate: Basic`,
and the browser did what that header tells it to do — asked the user itself. Reproduced exactly:

```
curl -o /dev/null -w '%{http_code}'    localhost:8080/api/documents/696/pages/1/image   -> 401
curl -o /dev/null -w '%{http_code}' -u reviewer:reviewer  …/image                       -> 200
```

**Fix:** fetch the page as a blob through `HttpClient` (`ApiService.pageImageBlob`) and bind an
object URL, in an `effect` keyed on the selected document and page. Object URLs are revoked on
replacement and on destroy, so the 4 MB page renders do not accumulate.

**The option not taken** was `permitAll()` on the image endpoint. It would have been one line,
but page renders *are* the document content — the scanned patient form itself. Making the
protected data public to silence a dialog about protecting it is the wrong trade, even with a
synthetic corpus and even in a prototype whose auth is admittedly not a security boundary.

A second-order point worth stating: the template now branches on `hasPageImage()` (known
synchronously) rather than on the image URL, so the plain-text fallback does not flash while the
blob is in flight.

**Also found:** `mvn package` without `clean` leaves stale hashed bundles in `target/classes`,
so the jar shipped both `main-BN4H6VEW.js` and `main-UW5R5ECW.js`. `ng build` had correctly
removed the old file from the working tree; only Maven's output directory was stale. Pruning
`target/classes/static` before packaging is enough.

**Affects:** `frontend/src/app/api.service.ts`, `detail.component.{ts,html}`.

---

### D-021 · The reviewer UI rebuilt on a token-based design system · 4 Sep 2026

The first UI was written to be dense and was never designed. It worked, but it read as a
prototype: one flat colour list, hard-coded hexes scattered through two components, and a detail
screen that stacked classification, summaries, every extracted field, tables, images, AI calls
and the audit trail into one column. Reaching the decision buttons meant scrolling past three
screens of tables.

**What changed**

*A design system, not a stylesheet.* `styles.scss` now defines tokens only — surfaces, lines,
text, semantic tones, elevation, radii, type. Nothing below it names a colour. Dark mode is a
second token block and cost no component work, which is the test of whether the layer is real.

*Eight reusable primitives* in `src/app/ui/`: `ui-tabs`, `ui-card`, `ui-badge`, `ui-meter`,
`ui-stat`, `ui-empty`, `ui-modal`, `ui-icon`, plus `domain.ts` — the shared mapping from a domain
value to a UI meaning. Both screens route category, status and confidence through that one file,
so a label cannot be green on the queue and amber on the detail screen.

*The detail screen is tabbed:* Overview · Extracted data · Sources · AI calls · Audit, beside a
source pane that never scrolls away. A reviewer asks one question at a time, and the answer to
"why does it say that?" is always a place in a document, so the document stays on screen.

**Decisions worth recording**

- **Tabs own only the strip, not the panels.** The parent switches with `@switch`. Content
  projection would keep every panel instantiated, and these panels are heavy — a 4 MB page render,
  long tables — so they genuinely want destroying when hidden.
- **`ui-meter` visualises the confidence chain, not just the number.** When code lowered the
  model's own figure the bar is striped and a ghost tick marks where the model had claimed to be.
  The adjustment was previously a `↓` in a tooltip; it is the most interesting thing on the row.
- **The webfonts are gone.** `index.html` pulled Roboto and Material Icons from Google. The demo
  has to run offline, and an icon font that fails to load renders ligature text ("search_off")
  mid-screen. Type is the system stack; icons are inline SVG.
- **Boolean attributes need `transform: booleanAttribute`.** A bare `padded` passes `""` to a
  signal input, which is a type error against `boolean`. Caught at build time.
- **Fixed a rendering bug carried over from the old templates:** cost cells were written
  `\${{ ... }}`, and a backslash is not an HTML escape, so the UI displayed `\$1.47`.

**Verified in a real browser, not by compiling.** Driven over the DevTools protocol against the
running backend: queue renders 38 rows with live KPIs; all five tabs return content
(4 cards / 20 field rows / 13 AI calls / audit); filtering and row navigation work; the theme
toggle flips `data-theme` and the body background with it; **zero console errors or exceptions**.
The highlight overlay was measured on `messages/426` — a 56×15 px box at 50.8%/27.6% inside the
1654×2339 page render, quoting "58-year-old".

A note on method: `--dump-dom` reported the detail screen as completely empty and I briefly
believed the route was broken. It snapshots before the lazy chunk and its XHRs resolve. The
DevTools-protocol probe showed the screen rendering correctly all along — the tool was wrong,
not the application.

**Open, deliberately not changed:** the category filter is `categories LIKE ?`, so selecting
`ICSR` also returns the 22 `ICSR_INCOMPLETE` rows (32 of 38). That may well be intended — an
incomplete ICSR is still an ICSR — so it is a product call, not a bug to fix inside a UI task.

**Affects:** `styles.scss`, `index.html`, `app.ts`, `theme.service.ts`, `src/app/ui/*`,
both screen components.

---

### D-022 · Every reviewer action was posting to `/messages/undefined/review` · 4 Sep 2026

Clicking **Accept** produced:

```
Method parameter 'id': Failed to convert value of type 'java.lang.String'
to required type 'long'; For input string: "undefined"
```

The detail response mixes two casings. The **top level is camelCase** (`id`, `subject`,
`status`, `senderName`, `receivedAt`, `needsAttention`, `attentionReason`); every **nested**
collection is raw `UPPER_SNAKE` column names (`classifications[].CATEGORY`, `documents[].ID`,
`fields[].FIELD_PATH`). The template read the top level as `m.SUBJECT`, `m.STATUS`, `m.ID` — all
`undefined`.

So `decide()`, `reprocess()` and the reload after a field override all sent the literal string
`undefined` as the message id, and the header rendered an empty subject, no status badge and no
attention bar. Accept, Override, Reject and Re-process were **all** non-functional.

This predates the UI rebuild — `m.ID` is in the original component too, so no reviewer decision
has ever been recordable through the UI. It survived because the failure is silent in the two
places you look: the header just renders blank, and the POST error only appears after a click.

**Verified by driving a real browser, not by reading the diff.** After the fix, clicking Accept
on message 446: `POST /api/messages/446/review → HTTP 200`, the header badge flips
`ready for review → reviewed`, `status` in the database becomes `REVIEWED`, and the audit trail
gains a `REVIEW_ACCEPT · reviewer` row (7 → 8). The header now shows subject, sender and the
amber attention bar.

**The lesson worth keeping:** the previous session's verification exercised tabs, filters,
navigation and the highlight overlay — every *read* path — and reported the UI as verified. Not
one *write* path was clicked. Read-only checks cannot see this class of bug at all.

**The underlying wart** is the mixed casing in the response. The right fix is one DTO with one
convention rather than raw column maps, but that changes the API surface and every nested
reference in the UI; it is recorded here as the real cause rather than smuggled into a UI commit.

**Affects:** `frontend/src/app/detail/detail.component.{html,ts}`.

---

### D-023 · Two ways the browser broke the app that the application never saw · 5 Sep 2026

Both reported from a real browser session while the app itself was healthy — `curl` succeeded
throughout, which is exactly why neither showed up in earlier testing.

**1 · A native sign-in dialog over the running application**

The security config was an allow-list of static paths sitting in front of
`.anyRequest().authenticated()`. That challenges every path that is not on the list —
*including paths that do not exist* — and a 401 carrying `WWW-Authenticate: Basic` is an
instruction to the browser to ask the user for a password itself.

Browsers speculatively request a surprising number of things:

```
/.well-known/appspecific/com.chrome.devtools.json   401   ← devtools or an extension attaching
/manifest.json                                      401
/apple-touch-icon.png                               401
/main-<hash>.js.map                                 401   ← devtools open
/robots.txt                                         401
```

Every one produced a credentials prompt. The trigger in practice was opening devtools, or a
browser extension attaching its debugger.

**Fix:** invert the rule. `/api/**` is the security boundary and is authenticated; actuator
beyond `health`/`info` is authenticated; **everything else is `permitAll`**. The Angular bundle
is content-hashed and public regardless, and the SPA owns every non-API route, so nothing is
lost — and a path that simply is not here now answers **404, not a password prompt**.

Verified: the five paths above return 404; `/api/messages` 401 without credentials and 200 with;
`/api/documents/.../image` 401; `/actuator/env` 401. 45 backend tests pass.

**2 · `HTTP Status 400 – Bad Request` on every page**

Tomcat's log:

```
java.lang.IllegalArgumentException: Request header is too large
```

`maxHttpHeaderSize` defaults to 8 KB. **Cookies ignore the port**, so every project ever served
from `localhost` contributes to the jar sent to this app, and a developer machine crosses 8 KB
easily. Reproduced exactly: a 9 KB `Cookie` header returns 400, a small one returns 200.

The failure page says nothing about cookies or headers, so it reads as "the app is broken".
Raised `server.max-http-request-header-size` to 64 KB rather than leave the trap for anyone
cloning the repo. A 20 KB cookie header now returns 200.

**What these have in common:** the application was correct and every server-side check passed.
Both faults live in the space between the browser and the app — speculative requests, and state
the browser accumulated from unrelated software. `curl` cannot see either one, and neither can a
test suite. Only a real browser session found them.

**Affects:** `SecurityConfig.java`, `application.yml`.

---

### D-024 · The test suite destroyed the demo corpus, and six defects a browser found · 5 Sep 2026

An external agent was asked to click through the whole application and report. It opened with
"the backend died ~5 minutes in and came back with an empty database". Measured: 1 message,
2 documents, 0 cases — where there had been 38 / 59 / 35. `aiCalls` survived at 414.

**That was not a crash. It was `./mvnw test`.**

```java
@BeforeEach
void clean() {
  jdbc.update("DELETE FROM job WHERE subject_type = 'DOCUMENT' AND subject_id < 900000");
  jdbc.update("DELETE FROM inbox_message");   // ← no predicate
}
```

`IngestionIntegrationTest` emptied the table the running application serves from, cascading
through documents, cases, extracted fields and evidence, then left its own last fixture behind as
the single surviving row. AI_CALL_LOG has no foreign key to the message, which is why the spend
and cache figures stayed pinned at their pre-wipe values — the "stats from a dataset that no
longer exists" the report flagged as a UI bug was the audit log correctly outliving what it
described.

The README says the Java tests run against the real Oracle *on purpose*. It never said they would
destroy whatever was in it. Anyone cloning this repo, seeding the corpus and running the suite
would have found the reviewer UI empty, with nothing on screen to explain why.

**The fix, in three parts**

1. Cleanup deletes only rows a test can prove it inserted — and only when the ingest was not a
   duplicate, because a duplicate returns the id of a row that was *already there*.
2. Fixtures get a per-run `Message-ID`. That is the primary dedupe key, so each run works on its
   own copy of a corpus email instead of colliding with the seeded one. (No `saveChanges()`
   afterwards — `MimeMessage.updateHeaders()` regenerates the Message-ID and would undo it.)
3. `wholeCorpusIngests` genuinely cannot share a database: it asserts global counts and needs to
   start from empty. It is now opt-in behind `-Dinbox.test.exclusive-db=true` and says so in its
   skip reason.

Verified: 45 tests pass, 1 skipped, and the corpus is untouched afterwards — 38 messages,
35 cases, 98.7% verified, before and after.

**Recovery.** GreenMail restarted, corpus re-seeded, pipeline re-run: 38/38 ready, 35 cases,
529/536 evidence verified (98.7%), 0 dead jobs — statistically identical to the original run.
The disk parse cache earned its place here: the re-run cost **$1.11** against the original
$1.47, because the expensive vision transcription of scanned pages was served from cache.

**Six real defects the same report found**, none of which any server-side check would have caught:

- **Decisions were possible on a message still being processed.** Accept/Override/Reject were
  live while the pipeline was mid-flight, so a case could be signed off with no classification
  and no extracted fields. Now gated on `isDecidable(status)` with the reason shown.
- **Reject left the status reading `REVIEWED`.** `apply_override` set that for every decision, so
  a rejected message was indistinguishable from an accepted one. V6 adds `REJECTED` and sets it.
  Existing rows keep the status they were given — the audit is append-only and a migration must
  not restate history it did not witness.
- **Override recorded that an override happened without asking what it was an override *to***.
  It re-sent the AI's own categories. There is now a dialog to choose the correct ones.
- **The reviewer note went nowhere.** The box said "recorded in the audit trail"; the note reached
  REVIEW_DECISION but the audit row's `after_json` carried only the category list. V6 records
  decision, categories, status and notes; the trail renders the note under its row; and the box
  clears after submitting instead of inviting a second send.
- **An unknown message id rendered a blank white page.** The error markup was nested *inside* the
  success branch of the template, so a failed load had no branch at all. `/api/messages/{id}` also
  answered **500** for a missing row via a bare `orElseThrow()`; it is now a 404.
- **Unknown routes leaked Spring's Whitelabel error page** — my own regression from D-023, where
  unknown paths stopped being 401 and started being an unhandled 404. `SpaErrorController` now
  forwards extension-less 404s to the SPA and returns JSON for `/api`. A missing `.js` still 404s:
  answering a missing asset with an HTML page and a 200 turns a clear failure into a confusing one.

And one my own verification of the fix turned up: a classification the reviewer had just set was
labelled **"model"**, because the template read `RULE ? 'decided by rule' : 'model'`. In an audit
trail whose purpose is recording who decided what, that is exactly backwards.

**The lesson.** D-022 already noted that the previous session tested only read paths. This round
repeats it one level up: I ran a destructive test suite against the live demo, during someone
else's test session, without checking what it deleted — and then reported "45 tests pass" as
reassurance. The suite was passing *because* it wiped the database first.

**Affects:** `IngestionIntegrationTest`, `V6__rejected_status.sql`, `SpaErrorController`,
`ReviewController`, `domain.ts`, `detail.component.{ts,html,scss}`.

**Follow-up, same day:** the queue tests had a quieter version of the same fault. They cleared
their synthetic rows (`subject_id >= 900000`) in `@BeforeEach` but not afterwards, so those rows
sat in the shared queue between runs and the running application counted them — the reviewer UI
reported jobs pending and running against documents that never existed, and with the worker pool
disabled they could never drain. That is precisely the "JOBS RUNNING only ever climbs" symptom in
the report, which I had attributed entirely to the extension clicking Re-process. Both tests now
clean up on the way out as well; after a run the queue holds 0 synthetic jobs.

---

### D-025 · The screen contradicting its own rule, and six more from a second browser pass · 5 Sep 2026

The re-run reached everything the first pass could not. Its verdict on the central claim: on
digital PDFs the highlight box lands on the quoted text *precisely* — eight chips across four
messages, every one accurate. The 0.40 cap, the striped bars, conflict adjudication across two
documents, the three distinct parse-failure reasons and the non-English rendering all behaved.

**1 · The Classification card printed the opposite of its own checklist**

On message 624 the prose read "All four ICSR minimum criteria are present" directly above a
widget reading **1/4**, with three crosses. Not a wording bug — the two disagreed in the database:

```
REASON   : All four ICSR minimum criteria are present… (from Meldebogen_AER-2026-00301.pdf)
VALIDITY : patient=N reporter=Y product=N event=N  → 1/4
```

`apply_rules` cannot produce that: the count and the sentence come from the same dict. The fault
was one line in `ClassifyMessageHandler`:

```java
JsonNode elements = response.path("units").path(0).path("icsr_elements");
```

A message is classified unit by unit and the message label is the strongest of them (E25). On a
covering email with a completed form attached the label comes from the **attachment**; `units[0]`
is the **email body**. So the card showed one document's label above another document's
checklist, quoting the wrong source — which is also why a criterion marked absent still displayed
a supporting quote. The reviewer was being asked to check a claim against evidence for a
different claim.

The unit was knowable all along, but only as prose: `roll_up_message` interpolated it into the
reason as "(from x.pdf)". Readable, and useless to code. `Label` now carries `source_unit`, and
the handler selects that unit's checklist, falling back to the first for older responses.

**Repaired without re-running the model.** Every per-unit classification response is already in
`AI_CALL_LOG`; units are built from `document ORDER BY id` and called in that order, so the Nth
call is the Nth unit and the reason names which one to use. `scripts/backfill_icsr_validity.py`
corrected 11 messages — 624 went 1/4 → 4/4 and now quotes the German PDF it was decided from.
Cost: nothing.

**2 · Override appended instead of replacing (V7)**

Unticking a category left it live. `apply_override` superseded rows with
`decided_by <> 'REVIEWER'` — a guard meant to spare the rows the same call had just inserted,
which also spared every label an *earlier* override had set. Overriding twice produced two live
labels at 100%, both "set by reviewer", contradicting the dialog's own promise. V7 records the
highest classification id before inserting and supersedes rows at or below it: the old rows, and
only the old rows, whoever decided them. Verified: `[ICSR INCOMPLETE, PQC]` → override to MI →
`[MI]`.

**3 · Override silently un-rejected a rejected message.** An override after a rejection is a
legitimate change of mind, so the status change stays — but it is no longer silent. The dialog
now says the rejection will stop being the status and remain in the audit trail.

**4 · The amber unverified chip destroyed the page image.** An unverified citation often carries
no usable page number — the model named a source that could not be found. Setting `activePage`
to it left `currentPage()` undefined, so neither the image nor the text fallback rendered and the
pane went blank white. The worst possible answer to "show me where you claim this came from".
`showEvidence` now lands only on a page that exists.

**5 · The highlight persisted onto the wrong page.** Paging away redrew the box at the same
coordinates over unrelated content — a highlight over blank space implying the quote was there.
`highlightStyle()` now returns null unless the box belongs to the current document *and* page.

**6 · Scanned pages gave a green chip and no box.** Correct — a scan has no text geometry — but
silently. The evidence bar now says the quote was found in the transcribed text and there are no
coordinates to draw at.

**7 · Three field types were exempt from the product's central principle.** `product.role`,
`product.route` and `narrative` render 90–95% confidence with no citation at all. The cause is in
`schemas.py`: `product_role: ProductRole` and `route: Route` are bare enums, not `Fact`, so no
quote is ever requested; role also *inherits* the product name's confidence rather than earning
one. This is not a quick fix — adding quote fields grows a schema that OpenRouter silently drops
past ~4 KB (D-013), which is why it is already split — so the honest step now is to stop
presenting them as verified: a dashed "no citation" chip explaining, per field, why nothing
checked it. **The schema change remains the real fix and is not done.**

**Also from the ⚠️ list:** the focus indicator was a 22%-opacity glow at roughly 1.2:1, under the
3:1 WCAG 2.2 requires — now a solid 2px outline; queue rows gained `aria-label`s; the subtitle
claimed "worst first" whatever the sort said; a failed load showed the raw string "Http failure
response for …: 404"; narratives wrapped into a ribbon in a 170px cell.

**Two corrections to my own record.** The report's "JOBS RUNNING only ever climbs" was real and I
had blamed it on the extension clicking Re-process — it was the queue tests leaving synthetic rows
behind (D-024 follow-up). And its keyboard-accessibility finding was withdrawn on re-check: rows
are reachable in five tab stops and Enter activates them. Asking it to re-examine a disagreement
rather than assuming it was wrong produced both the withdrawal and a sharper, correct finding
about contrast.

**Affects:** `classify.py`, `ClassifyMessageHandler`, `V7__override_supersedes.sql`,
`scripts/backfill_icsr_validity.py`, `detail.component.{ts,html,scss}`,
`queue.component.{ts,html,scss}`, `styles.scss`.

---

### D-026 · Product role and route are now verified like every other fact · 5 Sep 2026

D-025 named these two as the last values in the system that nothing checked, and stopped at
making the gap visible. This closes it.

`product_role` and `route` were bare enums on `ProductBlock`, so the model was never asked where
it got them. `role` then took the product **name's** confidence, and `route` did the same: a route
the model had inferred from what is usual for that medicine displayed at 95% beside a name that
had earned it. Against a system whose stated principle is that every extracted fact carries a
verified quote, three field types were exempt.

**Why it was not done sooner, and why that reasoning was wrong.** Adding fields risks D-013's
silent-drop cliff: a `response_format` schema over ~4 KB is discarded by OpenRouter with no error
at all. I treated that as a reason to leave it. Measuring first would have taken a minute:

```
P2b_extract_products   2,316 B   headroom 1,684
```

There was never a shortage of room. The fix costs **+400 B** — 2,716 B, still 1,284 B under the
cliff and below the 3,500 B warn threshold.

**The shape was already in the codebase.** `ReporterBlock.role` and `ReactionBlock.outcome` are
enums with `_confidence` and `_quote` alongside them. Product simply never got the same treatment,
which is what made it look like a design decision rather than an omission.

**Verified against the live model, not just the type system.** A dropped schema is invisible from
inside the process, so the only proof is a real call:

```
prompt_version  P2b_extract_products@v2
prompt_tokens   5,350          (a dropped schema collapses this to ~14)
repaired        False
role   SUSPECT 0.95  quote 'Suspect medicine: Velmoradine'
route  ORAL    0.95  quote 'taken by mouth'
```

Both quotes are verbatim from the source, so both verify. Cost of that check: $0.003.

**Prompt v2, not an edit to v1.** Prompts resolve to their highest version and every call records
which one it used, so v1 remains the exact text that produced the existing corpus. v2 asks for the
two quotes, says an empty quote is preferable to an invented one, and states that a quote which
cannot be found caps the field at 0.40 — the incentive stated plainly rather than left implicit.

**There were no tests for product extraction at all.** That is why this survived a full build and
two external test passes. `tests/test_product_fields.py` covers the verified path, the capped path,
abstention, and specifically that role's confidence is no longer equal to the name's — the exact
shape of the old defect, so a regression fails rather than merely looking odd.

One of those tests initially passed for the wrong reason: `Evidence.verified` is a `'Y'`/`'N'`
flag, and `assert evidence[0].verified` is true for both values. Only the negative case exposed
it. Both assertions now compare explicitly.

**Affects:** `schemas.py`, `extract.py`, `prompts/P2b_extract_products/v2.md`,
`tests/test_product_fields.py`.

---

### D-027 · The AI service started happily without an API key · 5 Sep 2026

`on_startup` logged a warning and carried on:

```python
if not settings.openrouter_api_key:
    log.warning("OPENROUTER_API_KEY is not set; LLM-backed endpoints will fail")
```

So the service came up, `/health` answered **`UP`** with `api_key_configured: false` beside it,
the queue handed it work, and every model call failed — each burning its retry budget (E36)
before the job dead-lettered. From the outside a missing key was indistinguishable from a broken
model, and the cost of finding out was a corrupted run.

Reporting the key state in a subordinate field of a healthy response is the specific mistake.
Nothing reads a sub-field to decide whether a dependency is usable; a health check is consulted
for its status.

**Fix.** `settings.api_key_problem()` returns why the key is unusable, or None:

- missing or blank
- still the `.env.example` placeholder — the likeliest way to arrive here
- not starting `sk-or-` — an OpenAI or Anthropic key will not work against OpenRouter
  (CLAUDE.md constraint 1), and that mistake would otherwise surface as a 401 per job

Startup raises, so uvicorn exits non-zero with a message naming the fix. `/health` reports
`DOWN` with **HTTP 503** and the reason, so compose and the Spring client both see it. Only the
key's shape is checked — whether it authenticates is OpenRouter's to say. The message never
contains the key, which is a test rather than a convention.

**Verified against a placeholder key:** exit code 3, `Refusing to start: OPENROUTER_API_KEY still
holds the placeholder…`, and the port never bound.

**Then it caught a real one immediately.** The next start failed with "OPENROUTER_API_KEY is not
set" — the key was present in `.env` but **commented out**. Under the old code that would have
started cleanly and failed one expensive job at a time.

Six tests in `tests/test_api_key_guard.py`; 141 Python tests pass.

**Affects:** `app/settings.py`, `app/main.py`, `app/routes/meta.py`.

---

### D-028 · Hosting: one OCI Ampere A1 Always Free VM, because PL/SQL is not portable · 5 Sep 2026

The requirement was a free public URL instead of `localhost`. The obvious modern answer — frontend
on Vercel, database on a free Postgres tier — **is not available to this project**, and the reason
is a deliberate earlier choice rather than an oversight.

`V3__packages.sql` puts the job queue (`FOR UPDATE SKIP LOCKED`), the append-only audit
(`AUTONOMOUS_TRANSACTION`) and the reviewer-override supersede in **PL/SQL packages**, because the
brief's stated stack is "Oracle (PL/SQL)" and PROJECT_PLAN §5.1/§9.4 build on it. Nothing on a free
Postgres/MySQL tier runs that schema. Rewriting the queue in Java to reach a cheaper host would
throw away the part of the build the brief asked for most explicitly. So the host has to be one
that can run an Oracle container, and the only genuinely free, non-expiring one of those is
**Oracle Cloud's own Ampere A1 Always Free shape** — 4 OCPU / 24 GB, no time limit.

**This also retires D-019.** The 8 GB development machine could not hold the full stack; the OS
killed the backend twice at 0.7 GB free, and `docs/RUNNING.md` still documents a low-memory
browse-only mode as a workaround. 24 GB removes the constraint entirely — the deployed stack runs
four workers with Oracle at its full 2 GB licence cap and no `-Xmx320m` juggling.

**The one risk, checked before committing to it.** Ampere A1 is arm64, and an image without an
arm64 manifest would have sunk the plan after the VM was already created. All six were verified
against the registry first:

| Image | Architectures |
|---|---|
| `gvenzl/oracle-free:23-slim-faststart` | amd64, **arm64** |
| `greenmail/standalone:latest` | amd64, **arm64** |
| `maven:3.9-eclipse-temurin-21` | amd64, **arm64**, ppc64le, riscv64, s390x |
| `eclipse-temurin:21-jre` | amd64, **arm64**, ppc64le, s390x |
| `python:3.12-slim` | 386, amd64, arm, **arm64**, ppc64le, riscv64, s390x |
| `caddy:2-alpine` | amd64, arm, **arm64**, ppc64le, riscv64, s390x |

No x86 fallback is needed, and the Autonomous Database alternative — managed, but requiring a
wallet and mTLS for a JDBC URL that currently needs neither — was dropped once Oracle 23ai Free
was confirmed to run on arm64.

**Shape of the deployment.** Caddy terminates TLS on 80/443 and is the only thing the internet can
reach. The backend, AI service, Oracle and GreenMail have no published ports; Oracle and GreenMail
bind to `127.0.0.1` so `sqlplus` and `scripts/seed_mailbox.py` still work over SSH. The backend
already serves the committed Angular bundle from `src/main/resources/static`, so there is one
public origin, no CORS, and no separate frontend host.

Three details that are not obvious and each cost a deploy if missed:

- **`SERVER_FORWARD_HEADERS_STRATEGY=framework`.** Behind Caddy, Tomcat sees plain HTTP. Without
  this Spring builds redirects as `http://` and the browser blocks them as mixed content.
- **OCI's Ubuntu image REJECTs everything but SSH in its own iptables.** Opening 80/443 in the
  console security list alone leaves the site unreachable with no error anywhere — the single most
  common reason a first OCI deploy looks like it hung. `scripts/deploy.sh` opens and persists them.
- **DNS is checked before the first build.** Let's Encrypt rate-limits failures, so the script
  compares the VM's public IP against `SITE_ADDRESS` and stops rather than burning attempts.

**Verified locally before writing any of this down.** `docker compose -f docker-compose.prod.yml
config` validates; both images build (backend 769 MB, ai-service 874 MB); the AI service refuses to
start with no key and answers `/health` 200 with one; the backend boots on Java 21 as uid 10001 and
fails exactly where it should, on `ORA-12541: no listener`. The Oracle-backed path could not be
exercised on this machine — that is D-019 again, and it is the thing the VM fixes.

**Affects:** new `docker-compose.prod.yml`, `backend/Dockerfile`, `ai-service/Dockerfile`,
`infra/caddy/Caddyfile`, `.env.prod.example`, `scripts/deploy.sh`, `docs/DEPLOY.md`.

---

### D-029 · The eval was reporting the sum of every run ever made · 5 Sep 2026

Chasing the brief's "report how long each one takes" (§3.E) turned up something worse than the
missing feature.

`AI_CALL_LOG` and `PROCESSING_METRIC` are append-only and survive a re-seed. `INBOX_MESSAGE` does
not — `seed_mailbox.py` creates fresh rows with new ids, orphaning every metric row from the run
before. Nothing joins them back, and `score_performance` summed the tables unfiltered. So the
report did not describe the corpus in the database; it described every corpus that had ever been
in the database.

It went unnoticed because the first report was generated minutes after the first clean run, when
the two happened to be the same thing.

**What it was actually reporting.** Unscoped: 966 calls, $3.7191, **$0.0630 per document** — a
failure against the ≤ $0.05 target stated in the plan. Scoped to the live corpus: 276 calls,
$1.1344, **$0.0192 per document**. The committed report's $0.0249 was run one alone. Re-running
the eval during the live walkthrough would have shown the cost target failing, with no way to
explain it on the spot.

**Fix.** A job's subject is the join back to the corpus, so every performance figure is now scoped
through it, and rows whose subject has been deleted are excluded. The report states the scoping
inline and prints the unscoped totals beside it, so the number is checkable rather than trusted.
`main()` was also overwriting the scoped `by_purpose` with an unscoped re-query; that is gone.

**The timing report itself.** `PROCESSING_METRIC` always held the data — `ProcessingMetricService`
even cites R17 in its javadoc — but nothing reported it per item, only averaged per stage, which
answers a different question. Every message now gets a row: parse time, pipeline time, total.
38 messages / 59 documents, mean **32.6 s** per message and **21.0 s** per document.

**A trap inside that table.** The `PARSE_DOCUMENT` job returns in ~180 ms for these documents,
which would let the report claim PDF understanding is nearly free. It is not: E9 addresses parse
results by content hash, so PDFs already parsed under an earlier corpus id came back from cache
without reaching the model — 8 model calls covered 59 documents. `DOCUMENT.PARSE_MS` records the
real work, a mean of 6.7 s over the 18 documents that did any. Both are now reported, because
only publishing the warm number would have been a false claim about the system's cost.

**Re-measuring moved the headline numbers, mostly up.** Verification 98.7% → **99.0%**, category
F1 micro 0.952 → **1.000** (every category now 1.000; the old ICSR precision of 0.875 was closed
by the product-role verification of D-026), exact-set accuracy 89.5% → **100.0%**, ICSR element
agreement 71.9% → **88.4%**. README, WRITEUP and PHASES quoted the stale set and would not have
matched a live re-run; all three are updated.

**One number moved down, and it is the one that matters most.** Abstention correctness
47.5% → **35.3%**. The mechanism is in the outcome counts: explicit `NOT_STATED` answers fell
65 → 36 while "expected field not produced at all" rose 23 → 40. False-confident assertions also
fell, 72 → 66, so this is not new fabrication — the model stopped *declaring* unknowns and started
*omitting* them. Against a rule that says "say unknown, never guess", a silently absent field is
the worse of the two, because only one of them is visible to a reviewer.

Root-causing that needs a pipeline re-run, which costs OpenRouter credit that was explicitly
withheld for this session. It is recorded here and in the write-up's limitations as an open issue
rather than quietly left in a metrics table.

**Addendum, same day — the same bug was on screen.** Screenshotting the review queue for the
submission showed the header reading **$3.72 AI SPEND** and 70% cache hit.
`ReviewController.overview()` summed `AI_CALL_LOG` unfiltered exactly as the eval had, so the
reviewer UI was quoting the spend of every corpus ever loaded — against a write-up that claims
$0.0192 per document. Anyone comparing the screenshot with the report would have found the
contradiction before we did. Scoped through the job's subject like the eval, the header now reads
**$1.13 / 276 calls / 65% cache**, and the three surfaces finally agree. Found only because the
screenshots were reviewed rather than filed.

**Affects:** `eval/run_eval.py`, `eval/report.md`, `eval/report.json`, `README.md`,
`docs/WRITEUP.md`, `docs/PHASES.md`, `backend/.../api/ReviewController.java`.

# Handoff prompt

Paste the block below into a fresh Claude Code session started in
`D:\Prep\Clinevo_Technologies\smart-inbox`.

---

```
You are taking over implementation of the Smart Inbox project — a live assignment for Clinevo
Technologies, due 11 September 2026. The planning phase is complete. Your job is to BUILD IT, END TO
END, WITHOUT ASKING ME QUESTIONS.

## First, orient yourself (do this before anything else)

Read these four files in order. Do not skip them and do not skim.
  1. CLAUDE.md                — constraints, stack, environment facts, conventions
  2. docs/PHASES.md           — the live task tracker: P0-P7, every task, exit criteria per phase
  3. docs/DECISIONS.md        — decisions already made while building, with evidence (D-001..D-006)
  4. docs/PROJECT_PLAN.md     — the full spec: 22 sections, requirements-to-rubric map, architecture,
                                Oracle schema, prompt catalogue, UI design, and 40 numbered edge
                                cases E1-E40 which are the entire point of this submission

docs/PROJECT_PLAN.md is the single source of truth for scope. docs/PHASES.md tells you where we are.

## Your operating loop — repeat until every phase is done

For each unfinished task in docs/PHASES.md, in order:

  1. Re-read the matching section of docs/PROJECT_PLAN.md. Every task references the edge cases it
     must satisfy, e.g. "(E14)". Go read E14 in plan section 3 before writing the code. The edge
     cases are what this submission is graded on and they are very easy to forget.
  2. Implement it properly. No stubs, no TODOs, no "left as an exercise".
  3. Write tests as you go — unit tests for logic, integration tests for anything touching Oracle,
     the mailbox, or the AI service. A task is not done until its tests pass.
  4. Actually run the thing. Start the services, process real documents from the corpus, look at the
     output. "It compiles" is not evidence it works.
  5. Tick the task off in docs/PHASES.md and append to its session log.
  6. If you made a non-obvious choice, or discovered something that contradicts the plan, add a
     numbered entry to docs/DECISIONS.md with the evidence.
  7. Commit with a clear message and push to origin main.

At each phase boundary, verify the phase's stated exit criteria are genuinely met, then post a short
progress report and immediately continue to the next phase. Do not wait for me to reply.

## Autonomy — this is the important part

Do NOT ask me for permission, confirmation, or direction on ordinary engineering work. Install
packages, create files, run migrations, restart containers, change configuration, refactor, delete
your own scratch files, retry failed commands, choose libraries within the plan's stack — just do it.
If you hit an error, diagnose and fix it yourself. If an approach fails twice, choose a different
approach and record why in DECISIONS.md.

Stop and ask me ONLY if:
  - You need a secret or credential that does not already exist in .env
  - Something requires a Windows UAC prompt or a GUI click you cannot perform
  - You would have to delete or overwrite MY data (not project files you created)
  - A genuine scope decision arises that the plan does not answer and getting it wrong would waste
    more than an hour

Everything else: decide, act, record it, move on. Batch any questions and keep them short.

## Hard constraints — never violate these

  1. ONE AI model only: anthropic/claude-haiku-4.5 via OpenRouter, using the `openai` Python SDK
     against https://openrouter.ai/api/v1. No other model, no other provider, for any purpose.
     In particular NEVER enable OpenRouter's PDF `file-parser` plugin — its default engine is
     mistral-ocr, a different vendor's model. Parse PDFs locally with PyMuPDF and send scanned pages
     to Claude vision as rendered PNG images.
  2. No real patient data, ever. Everything comes from testdata/generator/ and must be obviously
     fictional.
  3. No secrets in git. .env is gitignored and must stay that way. .env.example holds placeholders
     only. Never print the API key. Never commit Clinevo_Assignment.pdf.
  4. Say "unknown", never guess. status: NOT_STATED must always be a valid, unpenalised model answer.
  5. Every extracted fact needs VERIFIED evidence. The model's self-reported citation is never
     trusted — it is proven against the source text by pipeline/verify.py. Unverified means
     confidence is capped at 0.40. This is the differentiator of the whole submission; do not
     weaken it.
  6. Append-only audit. Reviewer overrides supersede rows; they never overwrite them.
  7. temperature=0 on every LLM call. This is extraction, not generation.

## Current state (4 Sep 2026)

DONE (phase P0, mostly):
  - Planning complete; all four governing docs written and committed
  - Git repo initialised, pushed to https://github.com/Ganesh-Mk/smart-inbox (branch main)
  - .env exists with a working OPENROUTER_API_KEY — verified against claude-haiku-4.5, returns
    schema-valid structured JSON and exact per-call cost in usage.cost
  - Docker daemon healthy (28.3.3, 6 CPU / 4 GB cap). greenmail/standalone:latest pulled.

IN PROGRESS / NEXT:
  - `docker pull gvenzl/oracle-free:23-slim-faststart` may still be running or may need re-running.
    Check `docker images` first.
  - DONE: the project directory on disk is now `smart-inbox\` (it had been left as `firstpass\`
    because something held a lock on it). Contents, git history and the GitHub remote are unaffected.
    An empty `..\firstpass\` husk may still sit next to it until the locking process exits — delete it.
  - Then start phase P1 in docs/PHASES.md and work straight through to P7.

## Environment gotchas already discovered — do not rediscover these the hard way

  - Maven and the Angular CLI are NOT installed. Use ./mvnw (get the wrapper from a start.spring.io
    zip) and npx. Java is 24 but target 21 via <java.version>21</java.version>.
  - Tesseract and poppler are NOT installed and are NOT needed. PyMuPDF plus Claude vision replaces
    them. Do not add a native OCR dependency.
  - 8 GB RAM total. Cap the Oracle SGA around 1.2 GB and the JVM at -Xmx512m. For demos, build the
    Angular bundle and serve it from Spring Boot rather than running `ng serve` alongside everything.
  - Bash heredocs in this harness choke on large markdown payloads — use the Write tool for big files.
  - NEVER write a config file consumed by a Go program using PowerShell's `-Encoding utf8`; it emits
    a UTF-8 BOM and the parser rejects it. Write bytes from Python instead. (This broke Docker once.)
  - robocopy exit code 1 means SUCCESS, not failure. Only codes >= 8 are errors.
  - A background command whose cwd is inside a directory will lock that directory against rename.

## Definition of done for the whole project

docs/PHASES.md section 22 lists it. Summarised: a clean clone plus `docker compose up` plus the seed
script produces a populated review queue; all four PDF flavours handled including a hybrid document;
tables extracted as tables; meaningful images described and logos ignored; multi-label classification
with per-label confidence and reasons; ICSR/PQC/MI fields extracted with NOT_STATED where absent;
every STATED field carrying verified evidence that highlights the exact source text on click;
reviewer accept/override fully audited; a batch report over 15+ documents with per-document timings,
tokens and cost; eval/report.md with F1, abstention correctness, evidence verification rate and a
calibration curve; the literature-screening bonus splitting a case-series article into separate
cases; and README plus a 2-5 page write-up plus architecture diagram plus sample JSON outputs plus
screenshots.

If you run short of time, the cut order is: bonus first, then reduce the evaluation harness to F1
plus timings, then UI polish. Never sacrifice P0-P5 — that is 85% of the score.

Start now. Begin by reading the four files, then report in two or three sentences what phase you are
starting and what you will build first. Then build it.
```

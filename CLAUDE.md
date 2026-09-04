# FirstPass — Smart Inbox Assistant

> Clinevo Technologies live assignment. **Read this file first, every session.**

## What this is

An AI first-pass triage system for a pharmacovigilance shared mailbox. It reads incoming email +
PDF attachments over IMAP, works out what each message is about, extracts the key facts with a
confidence score and a **verified** pointer back to the exact source, and hands it to a human
reviewer to accept or override.

**Name:** *FirstPass* — it is literally the automated first pass over the mailbox, and "first-pass"
is a pharmacology term (first-pass metabolism), which a pharma audience will catch.

**Deadline:** 11 September 2026. Started 4 September 2026.

## The three documents that govern this build

| File | Role | Update cadence |
|---|---|---|
| `docs/PROJECT_PLAN.md` | The spec. 22 sections. Requirements → rubric map, 40 numbered edge cases (E1–E40), architecture, DB schema, prompts, UI, schedule. **The single source of truth for scope.** | Only when scope genuinely changes |
| `docs/PHASES.md` | The live progress tracker. Every task, its status, and what "done" means. | **After every work session** |
| `docs/DECISIONS.md` | Running log of decisions made *during* implementation, with evidence. | Whenever a non-obvious choice is made |

**Standing rule:** before starting any work, read `docs/PHASES.md` to see where we are. After
finishing any unit of work, tick it off there and note anything discovered. Never start coding a
component without re-reading its section in `docs/PROJECT_PLAN.md` first — the edge cases (E1–E40)
are the whole point and are easy to forget.

## Hard constraints — do not violate

1. **One AI model only: `anthropic/claude-haiku-4.5` via OpenRouter.** No other model, no other
   provider. In particular, **never** enable OpenRouter's PDF `file-parser` plugin — it defaults to
   `mistral-ocr`, a different vendor. PDFs are parsed locally by PyMuPDF; scanned pages go to Claude
   vision as rendered PNGs.
2. **No real patient data, ever.** Everything comes from `testdata/generator/`.
3. **No secrets in git.** `.env` is gitignored. `.env.example` holds placeholders only. No key in
   any log, commit, or `AI_CALL_LOG` row.
4. **Say "unknown", never guess.** `status: NOT_STATED` is always a valid model answer.
5. **Every extracted fact needs verified evidence.** The model's citation is never trusted — it is
   proven against the source text by `pipeline/verify.py`. Unverified ⇒ confidence capped at 0.40.
6. **Append-only audit.** Reviewer overrides supersede rows; they never overwrite them.

## Stack (versions pinned in the plan, §6)

```
Angular 22  →  Spring Boot 3.5.16 (Java 21 target, JDK 24 host)  →  Python 3.12 FastAPI  →  Oracle 23ai Free
                        │                                                    │
                   Oracle JOB queue (SKIP LOCKED)              OpenRouter → claude-haiku-4.5
GreenMail (Docker) provides the real IMAP test mailbox.
```

## Local environment facts (verified 4 Sep 2026)

- Java 24.0.2 · Node 22.18.0 · Python 3.12.7 · Docker 28.3.3
- **Maven and Angular CLI are NOT installed** — use `./mvnw` and `npx`
- **Tesseract and poppler are NOT installed and are NOT needed** — by design
- 8 GB RAM, 8 cores. Cap Oracle SGA ~1.2 GB, JVM `-Xmx512m`. Do not run everything at once carelessly.
- Docker data lives on C: (`AppData\Local\Docker\wsl`); C: has ~29.5 GB free after reclaiming 22.5 GB
- An orphaned 22.5 GB `docker_data.vhdx` holding the user's *old* images sits unused at
  `D:\DockerData\wsl\disk\` — awaiting their decision to restore or delete (see DECISIONS D-006)
- **Never** write a Windows config file consumed by a Go program using PowerShell `-Encoding utf8` — it
  adds a UTF-8 BOM and the parser rejects it. Write bytes from Python instead.
- Fonts available for the corpus generator: Segoe Script, Ink Free, Bradley Hand, Free Script
  (handwriting); MS Gothic, Yu Gothic, SimSun (CJK); Nirmala (Devanagari)

## Commands

```bash
docker compose up -d                      # Oracle + GreenMail
cd backend  && ./mvnw spring-boot:run     # :8080
cd ai-service && uvicorn app.main:app --reload --port 8000
cd frontend && npx ng serve               # :4200
python scripts/seed_mailbox.py            # push synthetic corpus into GreenMail
python eval/run_eval.py                   # metrics report
```

## Conventions

- Java: records for DTOs, constructor injection, no field `@Autowired`.
- Python: pydantic models are the single definition of every LLM schema — the JSON Schema is
  generated from them, never hand-written twice.
- Prompts live in `ai-service/app/llm/prompts/<id>/v<N>.md`, are versioned in git, and every AI call
  records its `prompt_version`. Changing a prompt must be a visible diff.
- SQL: Flyway migrations only. Never hand-edit a schema.
- `temperature=0` on every LLM call — this is extraction, not generation.

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

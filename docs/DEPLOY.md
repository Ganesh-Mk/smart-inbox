# Deploying Smart Inbox

The whole stack runs on one **Oracle Cloud Ampere A1 Always Free** VM behind a single HTTPS
domain, at no cost and with no time limit.

## Why this shape

`backend/src/main/resources/db/migration/V3__packages.sql` puts the job queue (`FOR UPDATE SKIP
LOCKED`), the append-only audit (`AUTONOMOUS_TRANSACTION`) and the reviewer-override supersede
logic in **PL/SQL packages**. That is deliberate — the brief's stated stack is "Oracle (PL/SQL)"
and PROJECT_PLAN §5.1/§9.4 build on it — but it means Oracle is not swappable. No free
Postgres/MySQL tier can run this schema, so the usual split of "frontend on Vercel, database on
Supabase" is not available. One VM that can run an Oracle container is the option that works.

It is also the option that fixes DECISIONS **D-019**: 24 GB of RAM means no low-memory mode, no
`-Xmx320m`, and the full pipeline running as designed for the first time.

```
Internet ──► :443 Caddy (TLS, auto Let's Encrypt)
                └─► backend:8080     Spring Boot — /api + the Angular reviewer UI
                      ├─► ai-service:8000   FastAPI + claude-haiku-4.5   (not published)
                      ├─► oracle:1521       Oracle 23ai Free             (not published)
                      └─► greenmail:3143    IMAP demo mailbox            (not published)
```

Only 80 and 443 are reachable from the internet. Oracle and GreenMail bind to `127.0.0.1` so
`sqlplus` and `scripts/seed_mailbox.py` still work over SSH, and nothing else can reach them.

## Cost

| | |
|---|---|
| Ampere A1 VM — 4 OCPU / 24 GB / 100 GB disk | Always Free, no expiry |
| Oracle 23ai Free (container on the VM) | free licence, 2 GB RAM / 12 GB data cap |
| DuckDNS subdomain | free |
| Let's Encrypt certificate | free |
| OpenRouter — claude-haiku-4.5 | usage only; a full corpus run is a few cents |

## What is in the repo

| File | Purpose |
|---|---|
| `docker-compose.prod.yml` | the five-service production stack |
| `backend/Dockerfile` | Maven build → JRE runtime; no Node needed, the Angular bundle is committed |
| `ai-service/Dockerfile` | Python 3.12 slim; pure wheels, so it builds on arm64 unchanged |
| `infra/caddy/Caddyfile` | TLS termination and the only public surface |
| `.env.prod.example` | every value the stack needs; copy to `.env` on the server |
| `scripts/deploy.sh` | installs Docker, opens the firewall, checks DNS, builds, starts, waits |

Both third-party images were checked against the registry and publish `linux/arm64` manifests, so
nothing here needs an x86 fallback.

---

# Part 1 — what you have to do by hand

These need your identity, your card, or a browser. Roughly 30 minutes.

### 1. Oracle Cloud account

<https://signup.cloud.oracle.com> — a card is required for identity verification and is **not**
charged on Always Free resources.

**Choose the home region carefully: it cannot be changed later.** Ampere A1 capacity is the
scarce thing; India South (Hyderabad) and India West (Mumbai) are often full. A quieter region
works just as well — latency to a demo UI is irrelevant.

### 2. Create the VM

Compute → Instances → Create instance:

| Field | Value |
|---|---|
| Image | **Canonical Ubuntu 22.04** |
| Shape | **VM.Standard.A1.Flex** — 4 OCPU, 24 GB (the whole Always Free ARM allowance) |
| Boot volume | 100 GB |
| SSH key | *Generate a key pair* and **download the private key** |
| Networking | assign a public IPv4 address |

If you get **"Out of host capacity"**, that is normal — retry over a few hours, or try a
different availability domain.

### 3. Open 80 and 443 in the security list

Networking → Virtual Cloud Networks → your VCN → Security Lists → Default → **Add Ingress Rules**:

| Source | Protocol | Port |
|---|---|---|
| `0.0.0.0/0` | TCP | 80 |
| `0.0.0.0/0` | TCP | 443 |

`scripts/deploy.sh` handles the VM's own iptables, but it cannot touch this — it is cloud-side.

### 4. A domain

Free option: <https://www.duckdns.org> — sign in with GitHub, create a subdomain such as
`smart-inbox`, set its IP to the VM's public IP. You get `smart-inbox.duckdns.org`.

If you own a real domain, an `A` record to the VM's IP is better and reads better in a
submission.

### 5. An OpenRouter key with credit

<https://openrouter.ai/keys>. A few dollars is far more than a corpus run needs.

### 6. SSH in and run one command

```bash
chmod 600 ~/Downloads/ssh-key-*.key
ssh -i ~/Downloads/ssh-key-*.key ubuntu@<VM_PUBLIC_IP>
```

Then, on the VM:

```bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/Ganesh-Mk/smart-inbox.git
cd smart-inbox
cp .env.prod.example .env
nano .env                      # fill in every REPLACE_ME, set SITE_ADDRESS
bash scripts/deploy.sh
```

Generate the passwords instead of inventing them:

```bash
openssl rand -base64 24 | tr -d '/+=' | head -c 24; echo
```

---

# Part 2 — what the script does for you

1. Refuses to continue if `.env` is missing or still contains `REPLACE_ME`.
2. Installs Docker Engine and the compose plugin from Docker's own apt repository.
3. Opens 80/443 in the VM's iptables and persists them. **OCI's Ubuntu image REJECTs everything
   but SSH by default** — opening the console security list alone leaves the site unreachable,
   and this is the single most common reason a first deploy appears to hang.
4. Compares the VM's public IP against what `SITE_ADDRESS` resolves to, and stops if they
   differ. Let's Encrypt rate-limits failures, so this check is worth the ten seconds.
5. Builds both images and starts all five services in dependency order.
6. Waits for the backend healthcheck and prints the URL.

First run takes 15–20 minutes, nearly all of it pulling Oracle and warming its datafiles.

---

# Part 3 — after it is up

The mailbox starts empty; the corpus is generated, never committed (CLAUDE.md constraint 2).
On the VM:

```bash
sudo apt-get install -y python3-venv
python3 -m venv .venv && . .venv/bin/activate
pip install -r ai-service/requirements.txt
python -m testdata.generator.build     # only if testdata/ is absent
python scripts/seed_mailbox.py         # posts to 127.0.0.1:3025
python scripts/seed_mailbox.py --check # reads it back over IMAP
```

The poller picks messages up within ten seconds. A full corpus run is 8–10 minutes on four
workers. Then open `https://<your-domain>` and log in with the `REVIEWER_*` credentials from
`.env`.

## Operating it

```bash
DC="sudo docker compose -f docker-compose.prod.yml"

$DC ps                       # what is running and healthy
$DC logs -f backend          # follow one service
$DC logs -f                  # follow everything
$DC restart backend          # restart one service
$DC stop                     # stop, keeping the database volume
git pull && bash scripts/deploy.sh   # deploy new commits
```

## When something is wrong

| Symptom | Cause |
|---|---|
| Site does not load at all | Security list ingress (Part 1 step 3) — check that before anything else |
| `no certificate available` | DNS is not pointing at the VM yet; `$DC logs caddy` says so plainly |
| Backend restarts in a loop | Oracle is not ready. First start builds the schema; `$DC logs oracle` |
| AI service exits at startup | `OPENROUTER_API_KEY` missing or out of credit — it refuses to start rather than fail per-call (commit 6415811) |
| Backend healthy, queue never drains | `$DC logs ai-service`; usually an exhausted OpenRouter balance |
| Mailbox stays empty | The seed script was not run, or was run before GreenMail was healthy |

## Security notes for a public URL

- Change `REVIEWER_PASSWORD` and `ADMIN_PASSWORD` from the demo values. The stack refuses to
  start without them being set, but it cannot tell whether you chose something good.
- The corpus is entirely synthetic (CLAUDE.md constraint 2). Nothing real is exposed by this
  deployment, which is exactly why it is safe to put on a public URL at all.
- `.env` lives only on the server and is gitignored. Confirm with `git status` before pushing.
- Document content still goes to OpenRouter — the cloud-AI trade-off in PROJECT_PLAN §16 and
  `docs/WRITEUP.md`. Hosting does not change that analysis; it is the same data path as local.

#!/usr/bin/env bash
# Smart Inbox — one-shot deployment on a fresh Ubuntu 22.04 host (OCI Ampere A1 Always Free).
#
#   git clone https://github.com/Ganesh-Mk/smart-inbox.git && cd smart-inbox
#   cp .env.prod.example .env && nano .env      # fill in every REPLACE_ME
#   bash scripts/deploy.sh
#
# Idempotent: safe to re-run after editing .env or pulling new commits.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
fail() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# --------------------------------------------------------------------------------------
# 1. Configuration must exist and be filled in before anything is built.
# --------------------------------------------------------------------------------------
say "Checking .env"
[[ -f .env ]] || fail ".env not found. Run: cp .env.prod.example .env && nano .env"

if grep -q 'REPLACE_ME' .env; then
  grep -n 'REPLACE_ME' .env >&2
  fail "The lines above still hold REPLACE_ME. Fill them in first."
fi

SITE_ADDRESS="$(grep -E '^SITE_ADDRESS=' .env | cut -d= -f2- | tr -d '"'"'"' ')"
[[ -n "$SITE_ADDRESS" ]] || fail "SITE_ADDRESS is not set in .env"
echo "    site: $SITE_ADDRESS"

# --------------------------------------------------------------------------------------
# 2. Docker.
# --------------------------------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  say "Installing Docker"
  sudo apt-get update -y
  sudo apt-get install -y ca-certificates curl gnupg
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
                          docker-buildx-plugin docker-compose-plugin
  sudo usermod -aG docker "$USER"
  echo "    added $USER to the docker group"
else
  say "Docker already installed — skipping"
fi

# Fresh group membership is not active in this shell yet, so this run uses sudo throughout.
DC="sudo docker compose -f docker-compose.prod.yml"

# --------------------------------------------------------------------------------------
# 3. Firewall. OCI's Ubuntu image ships an INPUT chain that REJECTs everything except SSH;
#    opening the ports in the console's security list alone is not enough.
# --------------------------------------------------------------------------------------
say "Opening ports 80 and 443"
for port in 80 443; do
  if sudo iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null; then
    echo "    $port already open"
  else
    sudo iptables -I INPUT -p tcp --dport "$port" -j ACCEPT
    echo "    $port opened"
  fi
done
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent >/dev/null 2>&1 || true
sudo netfilter-persistent save >/dev/null 2>&1 || echo "    (could not persist rules; they will not survive a reboot)"

echo "    NOTE: the OCI console security list for this subnet must ALSO allow 80 and 443"
echo "          from 0.0.0.0/0 — that part cannot be done from inside the VM."

# --------------------------------------------------------------------------------------
# 4. DNS sanity. Let's Encrypt will fail if the domain does not point here yet, and a few
#    failed attempts count against the rate limit — so check before building.
# --------------------------------------------------------------------------------------
say "Checking DNS for $SITE_ADDRESS"
PUBLIC_IP="$(curl -fsS --max-time 10 https://api.ipify.org || echo '')"
RESOLVED="$(getent hosts "$SITE_ADDRESS" | awk '{print $1}' | head -1 || echo '')"
echo "    this VM resolves as: ${PUBLIC_IP:-unknown}"
echo "    $SITE_ADDRESS resolves to: ${RESOLVED:-nothing}"
if [[ -n "$PUBLIC_IP" && -n "$RESOLVED" && "$PUBLIC_IP" != "$RESOLVED" ]]; then
  echo
  read -r -p "    They do not match. TLS issuance will fail. Continue anyway? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || fail "Point $SITE_ADDRESS at $PUBLIC_IP, then re-run."
elif [[ -z "$RESOLVED" ]]; then
  echo
  read -r -p "    Does not resolve yet. Continue anyway? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || fail "Create the DNS record, then re-run."
fi

# --------------------------------------------------------------------------------------
# 5. Build and start.
# --------------------------------------------------------------------------------------
say "Building images (first run pulls Oracle — allow 10-15 minutes)"
$DC build

say "Starting the stack"
$DC up -d

say "Waiting for the backend to report healthy"
for i in $(seq 1 120); do
  status="$(sudo docker inspect -f '{{.State.Health.Status}}' smart-inbox-backend 2>/dev/null || echo starting)"
  printf '\r    [%3ds] backend: %s          ' "$((i*5))" "$status"
  [[ "$status" == "healthy" ]] && break
  sleep 5
done
echo

if [[ "${status:-}" != "healthy" ]]; then
  echo
  echo "Backend is not healthy yet. Oracle's first start creates the schema and is slow."
  echo "Watch it with:  sudo docker compose -f docker-compose.prod.yml logs -f backend"
  exit 1
fi

# --------------------------------------------------------------------------------------
# 6. Done.
# --------------------------------------------------------------------------------------
say "Deployed"
cat <<SUMMARY

    URL       https://$SITE_ADDRESS
    Login     the REVIEWER_USER / REVIEWER_PASSWORD you set in .env

    The mailbox starts empty. To load the synthetic corpus:

        sudo docker compose -f docker-compose.prod.yml exec backend true   # confirm it is up
        python3 -m venv .venv && . .venv/bin/activate
        pip install -r ai-service/requirements.txt
        python -m testdata.generator.build       # only if testdata/ is missing
        python scripts/seed_mailbox.py           # posts to 127.0.0.1:3025

    The poller picks it up within ten seconds; a full corpus run takes 8-10 minutes.

    Logs      sudo docker compose -f docker-compose.prod.yml logs -f
    Stop      sudo docker compose -f docker-compose.prod.yml stop
    Update    git pull && bash scripts/deploy.sh

SUMMARY

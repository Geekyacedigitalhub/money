#!/usr/bin/env bash
# One-shot installer for Option A: your own link on a fresh Ubuntu/Debian VPS.
#
#   sudo bash deploy/setup-vps.sh --domain bot.geekyacedigitalhub.com
#
# It installs Docker, checks DNS, writes deploy/.env (with a hashed password),
# builds and starts the bot + Caddy, then waits until HTTPS answers.
# Safe to re-run: it pulls, rebuilds and restarts instead of starting over.
#
# Flags:
#   --domain <host>     hostname to serve, e.g. bot.geekyacedigitalhub.com
#   --password <pass>   password for the site (generated if omitted)
#   --user <name>       basic-auth username (default: ace)
#   --dir <path>        where to clone if this is not already a checkout
#   --repo <url>        git remote to clone
#   --skip-dns-check    continue even if DNS does not point here yet
#   --dry-run           print what would happen, change nothing
#   -h, --help

set -Eeuo pipefail

DOMAIN="${DOMAIN:-}"
PASSWORD="${PASSWORD:-}"
AUTH_USER="${AUTH_USER:-ace}"
TARGET_DIR="${DIR:-/opt/money}"
REPO="${REPO:-https://github.com/Geekyacedigitalhub/money.git}"
SKIP_DNS=0
DRY_RUN=0

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  !\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m  ✗ %s\033[0m\n' "$*" >&2; exit 1; }

usage() {
	sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' | sed '/^set -/d'
	exit 0
}

while [ $# -gt 0 ]; do
	case "$1" in
		--domain) DOMAIN="${2:-}"; shift 2 ;;
		--password) PASSWORD="${2:-}"; shift 2 ;;
		--user) AUTH_USER="${2:-}"; shift 2 ;;
		--dir) TARGET_DIR="${2:-}"; shift 2 ;;
		--repo) REPO="${2:-}"; shift 2 ;;
		--skip-dns-check) SKIP_DNS=1; shift ;;
		--dry-run) DRY_RUN=1; shift ;;
		-h|--help) usage ;;
		*) die "unknown argument: $1 (see --help)" ;;
	esac
done

run() {
	if [ "$DRY_RUN" = "1" ]; then
		printf '  [dry-run] %s\n' "$*"
	else
		"$@"
	fi
}

# ---------------------------------------------------------------- environment

if [ "$DRY_RUN" != "1" ] && [ "$(id -u)" != "0" ]; then
	die "run it with sudo: sudo bash deploy/setup-vps.sh --domain bot.example.com"
fi

command -v curl >/dev/null 2>&1 || die "curl is required (apt-get install -y curl)"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ ! -d "$ROOT/.git" ]; then
	info "No checkout here — the repo will be cloned into $TARGET_DIR"
	ROOT="$TARGET_DIR"
fi
COMPOSE="$ROOT/deploy/docker-compose.yml"
ENV_FILE="$ROOT/deploy/.env"
CONFIG="$ROOT/config.toml"

if [ -z "$DOMAIN" ]; then
	die "pass your hostname: --domain bot.geekyacedigitalhub.com"
fi
case "$DOMAIN" in
	*.*) ;;
	*) die "--domain must be a full hostname, e.g. bot.geekyacedigitalhub.com" ;;
esac
if [ "$DOMAIN" = "bot.example.com" ]; then
	die "replace bot.example.com with the domain you actually control"
fi

info "Domain:  $DOMAIN"
info "Checkout: $ROOT"

# ---------------------------------------------------------------- public IP

public_ip() {
	for url in https://api.ipify.org https://ifconfig.me/ip https://icanhazip.com; do
		ip="$(curl -4 -fsS --max-time 5 "$url" 2>/dev/null | tr -d '[:space:]')" || true
		case "$ip" in
			[0-9]*.[0-9]*.[0-9]*.[0-9]*) printf '%s' "$ip"; return 0 ;;
		esac
	done
	return 1
}

SERVER_IP="$(public_ip || true)"

# ---------------------------------------------------------------- DNS

dns_check() {
	resolved="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1{print $1}')" || true
	if [ -z "$resolved" ]; then
		warn "$DOMAIN does not resolve yet"
		printf '    Add this at your registrar / Cloudflare:\n\n'
		printf '      Type  Name  Value\n'
		printf '      A     %-5s %s\n\n' "${DOMAIN%%.*}" "${SERVER_IP:-<your server IPv4>}"
		return 1
	fi
	if [ -n "$SERVER_IP" ] && [ "$resolved" != "$SERVER_IP" ]; then
		warn "$DOMAIN currently points to $resolved, not $SERVER_IP"
		warn "If Cloudflare's proxy (orange cloud) is on, this is expected — use --skip-dns-check"
		return 1
	fi
	ok "$DOMAIN → $resolved"
	return 0
}

info "Checking DNS"
if ! dns_check; then
	if [ "$SKIP_DNS" = "1" ]; then
		warn "continuing anyway (--skip-dns-check) — Caddy may fail to get a certificate"
	else
		printf '    Fix the record, wait a minute, then run this script again.\n'
		printf '    Or re-run with --skip-dns-check if you know DNS is fine.\n'
		exit 1
	fi
fi

# ---------------------------------------------------------------- docker

info "Docker"
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
	ok "already installed"
else
	warn "installing Docker (once, ~1 minute)"
	if [ "$DRY_RUN" = "1" ]; then
		printf '  [dry-run] curl -fsSL https://get.docker.com | sh\n'
	else
		curl -fsSL https://get.docker.com | sh
		systemctl enable --now docker >/dev/null 2>&1 || true
	fi
fi

# ---------------------------------------------------------------- code

info "Code"
if [ -d "$ROOT/.git" ]; then
	run git -C "$ROOT" pull --ff-only || warn "pull skipped (local changes?)"
else
	run mkdir -p "$(dirname "$ROOT")"
	run git clone --depth 1 "$REPO" "$ROOT"
fi

if [ ! -f "$CONFIG" ]; then
	warn "config.toml is missing — copying config.example.toml"
	run cp "$ROOT/config.example.toml" "$CONFIG"
	warn "it has NO keys yet: the bot will start in rehearsal (renders, never posts)"
	warn "paste your keys into $CONFIG, then re-run this script"
fi
if [ "$DRY_RUN" != "1" ]; then
	chmod 600 "$CONFIG" 2>/dev/null || true
fi
if grep -qE '^\s*upload_post_api_key\s*=\s*""' "$CONFIG" 2>/dev/null; then
	warn "config.toml has no Upload-Post key yet — the bot will rehearse, not publish"
fi
if grep -qE '^\s*demo_mode\s*=\s*true' "$CONFIG" 2>/dev/null; then
	warn "demo_mode = true — videos are placeholders until you switch it off"
fi

# ---------------------------------------------------------------- password

if [ -z "$PASSWORD" ]; then
	PASSWORD="$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 20)"
	GENERATED=1
else
	GENERATED=0
fi
[ -n "$PASSWORD" ] || die "could not generate a password"

info "Hashing the password with Caddy"
if [ "$DRY_RUN" = "1" ]; then
	HASH='$2a$14$<hash>'
	printf '  [dry-run] docker run --rm caddy:2 caddy hash-password --plaintext <generated>\n'
else
	HASH="$(docker run --rm caddy:2 caddy hash-password --plaintext "$PASSWORD" 2>/dev/null | tr -d '\r')"
	case "$HASH" in
		\$2*) ok "password hashed" ;;
		*) die "could not hash the password (is Docker running?)" ;;
	esac
fi

info "Writing deploy/.env"
if [ "$DRY_RUN" = "1" ]; then
	printf '  [dry-run] %s\n' "$ENV_FILE"
else
	umask 077
	{
		printf 'DOMAIN=%s\n' "$DOMAIN"
		printf 'PUBLIC_URL=https://%s\n' "$DOMAIN"
		printf "BASIC_AUTH_HASH='%s'\n" "$HASH"
		printf 'AUTH_USER=%s\n' "$AUTH_USER"
	} >"$ENV_FILE"
	chmod 600 "$ENV_FILE"
	ok "$ENV_FILE (0600)"
fi

# ---------------------------------------------------------------- compose

info "Building and starting (first build takes a few minutes)"
run docker compose -f "$COMPOSE" --env-file "$ENV_FILE" pull caddy --quiet || true
run docker compose -f "$COMPOSE" --env-file "$ENV_FILE" up -d --build
run docker compose -f "$COMPOSE" --env-file "$ENV_FILE" ps

# ---------------------------------------------------------------- verify

if [ "$DRY_RUN" = "1" ]; then
	info "Dry run finished — nothing was changed"
	printf '\n  Would serve: https://%s/bot  (user: %s)\n\n' "$DOMAIN" "$AUTH_USER"
	exit 0
fi

info "Waiting for HTTPS (certificate + first boot)"
URL="https://$DOMAIN/api/bot/health"
for attempt in $(seq 1 40); do
	if curl -fsS -u "$AUTH_USER:$PASSWORD" --max-time 8 "$URL" >/dev/null 2>&1; then
		ok "answering over HTTPS"
		break
	fi
	if [ "$attempt" = "40" ]; then
		warn "HTTPS not answering yet — this is normal for 1–2 minutes"
		printf '    watch: docker compose -f deploy/docker-compose.yml logs -f\n'
		printf '    caddy:  docker compose -f deploy/docker-compose.yml logs caddy | tail -30\n'
	fi
	sleep 6
done

LOCAL_OK=0
if curl -fsS --max-time 5 http://127.0.0.1:8080/api/bot/health >/dev/null 2>&1; then
	LOCAL_OK=1
fi
# Port 8080 is not published in compose; check from inside the network instead.
if [ "$LOCAL_OK" = "0" ]; then
	docker compose -f "$COMPOSE" --env-file "$ENV_FILE" exec -T bot \
		python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/api/bot/health')" \
		>/dev/null 2>&1 && LOCAL_OK=1 || true
fi

printf '\n'
printf '──────────────────────────────────────────────\n'
printf '  Your bot is up\n'
printf '──────────────────────────────────────────────\n'
printf '  URL       https://%s/bot\n' "$DOMAIN"
printf '  Username  %s\n' "$AUTH_USER"
if [ "$GENERATED" = "1" ]; then
	printf '  Password  %s\n' "$PASSWORD"
	printf '\n  Save that password now — only its hash is stored on the server.\n'
fi
printf '\n  Bot health: %s\n' "$([ "$LOCAL_OK" = "1" ] && echo "ok" || echo "still booting — check logs")"
printf '\n  Next:\n'
printf '    1. open the URL on your phone, add it to your home screen\n'
printf '    2. send /link  — it should answer with https://%s/bot\n' "$DOMAIN"
printf '    3. send /auto  — it shows the schedule and any blockers\n'
printf '    4. docker compose -f deploy/docker-compose.yml logs -f bot\n\n'

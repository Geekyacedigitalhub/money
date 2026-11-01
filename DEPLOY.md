# Your own link

Right now the bot is reachable at the **sandbox preview**:

```
https://8080-i8kpg0z73822g0yyo0bld.e2b.app
```

That link works today, but it belongs to this workspace session — it disappears
when the session ends. A link you actually own looks like:

```
https://bot.geekyacedigitalhub.com
```

Same app, same chat, same autopilot — just on your domain, on a machine that stays
on. Three ways to get there; pick one.

| | Route | Cost | Time | Best if |
|---|---|---|---|---|
| **A** | VPS + your domain (Caddy does HTTPS) | ~$5/mo + domain you already own | 20 min | you want it permanent and independent |
| **B** | Cloudflare Tunnel from a PC you leave on | free | 10 min | you want it now, no server bill |
| **C** | Render / Railway / Fly | free tier sleeps, paid ~$5–7/mo | 15 min | you don't want to touch a server |

Everything needed is in `deploy/`. You do **not** need to change code.

---

## Route A — VPS + `bot.geekyacedigitalhub.com` (recommended)

### 1. Point DNS at the server

At your domain registrar (or Cloudflare), add:

| Type | Name | Value | TTL |
|---|---|---|---|
| `A` | `bot` | your server's public IPv4 | Auto |

Check it resolved before continuing:

```bash
dig +short bot.geekyacedigitalhub.com
```

If that prints nothing, wait — Caddy cannot get a certificate until DNS is right.
(Using Cloudflare? Keep the record **DNS only** / grey cloud on the first run, or
turn on "Full (strict)" *after* Caddy has its certificate.)

### 2. Install Docker on the server

```bash
curl -fsSL https://get.docker.com | sh
```

### 3. Get the code and your config onto the server

```bash
git clone https://github.com/Geekyacedigitalhub/money.git /opt/money
cd /opt/money
cp config.example.toml config.toml      # then paste your keys, exactly as locally
```

You need the same `config.toml` you use now: `[app]` upload-post keys, `[bot]`
settings, autopilot block, `public_url`.

### 4. Create `deploy/.env`

```bash
cp deploy/.env.example deploy/.env
```

```dotenv
DOMAIN=bot.geekyacedigitalhub.com
PUBLIC_URL=https://bot.geekyacedigitalhub.com
BASIC_AUTH_HASH=$2a$14$...
```

Generate the password hash (this is what keeps strangers out of a URL that can
spend your credits):

```bash
docker run --rm caddy:2 caddy hash-password --plaintext 'a-long-password-you-choose'
```

Paste the whole `$2a$14$…` line. Username is `ace` (change it in `deploy/Caddyfile`
if you like).

### 5. Start it

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

First build takes a few minutes. Then:

```bash
docker compose -f deploy/docker-compose.yml logs -f bot
```

Look for:

```
autopilot started (live) — at 08:00, 13:00, 19:00, max 3/day
Uvicorn running on http://0.0.0.0:8080
```

### 6. Prove the link

```bash
curl -u ace:your-password https://bot.geekyacedigitalhub.com/api/bot/health
```

Then open it in a browser — it will ask for the same username and password once.
Tapping **Add to Home Screen** turns it into an app-like icon on your phone.

Set the link in your config so the bot itself quotes it:

```toml
[bot]
public_url = "https://bot.geekyacedigitalhub.com"
```

Restart (`docker compose -f deploy/docker-compose.yml restart bot`) and send
**`/link`** in the chat — it will answer with your domain.

---

## Route B — Cloudflare Tunnel (free, no server)

Use a machine you already leave on (your PC, a mini-PC, even this sandbox while it
lives). Nothing is exposed inbound; Cloudflare dials out.

```bash
# 1. install (Debian/Ubuntu; other OSes: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o cf.deb
sudo dpkg -i cf.deb

# 2. log in (opens a browser) and create a tunnel
cloudflared tunnel login
cloudflared tunnel create post-bot

# 3. point your domain at it — no manual DNS record
cloudflared tunnel route dns post-bot bot.geekyacedigitalhub.com

# 4. run the bot locally first
cd /opt/money && python -m app.bot.cli serve --autopilot

# 5. in another terminal, run the tunnel with the config in this repo
sudo cp deploy/cloudflared.yml /etc/cloudflared/config.yml
sudo cloudflared service install
```

`geekyacedigitalhub.com` must be on Cloudflare (free plan) for step 3. If your DNS
is elsewhere, either move the nameservers to Cloudflare or use Route A.

> **Protect it:** tunnels have no password prompt by default. Either enable
> **Cloudflare Access** on the hostname (Zero Trust → Access → Applications, one
> email rule), or run Caddy locally with the `deploy/Caddyfile`.

Quick throwaway test (no domain, no account, URL changes every run):

```bash
cloudflared tunnel --url http://localhost:8080
```

---

## Route C — Render / Railway / Fly

Works because the bot is just a Python process.

1. New **Web Service** → connect `Geekyacedigitalhub/money`.
2. Build: `pip install -r requirements.txt` (or use the repo `Dockerfile`).
3. Start command:
   ```
   python -m app.bot.cli serve --port $PORT --host 0.0.0.0 --trust-proxy --autopilot
   ```
4. Add your `config.toml` contents as **environment variables** (`MPT_BOT_*`) or mount
   a disk with the file — do not commit it.
5. Add a custom domain `bot.geekyacedigitalhub.com` in the dashboard, then follow
   the CNAME it shows you.

Free tiers **sleep**; the autopilot only runs while the instance is awake, so use a
paid instance (or Route A) if you want the schedule to be dependable.

---

## After it is live — the 6-line checklist

```toml
[bot]
public_url = "https://bot.geekyacedigitalhub.com"
```

```bash
# 1. the bot answers
curl -u ace:your-password https://bot.geekyacedigitalhub.com/api/bot/health
# 2. it agrees on the schedule and the connection state
python -m app.bot.cli status
python -m app.bot.cli doctor
# 3. the phone can actually post
#    open the link → send "/auto" → tap "Run one now"
```

- ✅ `/link` shows your domain.
- ✅ `/auto` shows `live`, not `rehearsal`.
- ✅ One test post returns a real platform URL.

## Security, because this URL spends money

| Risk | Guard already in the bundle |
|---|---|
| Anyone generating videos on your box | **Caddy basic auth** in front of everything |
| REST/WebUI API wide open | set `[app] api_key` — protects `/api/v1` and `/tasks` |
| Telegram bot used by strangers | `[bot] allowed_users = ["your-id"]` |
| Keys leaking into git | `config.toml` is git-ignored; mount it, never commit it |
| Losing receipts/videos on redeploy | `storage/` is a mounted volume |

Do not skip the password step "just to test" — a public URL with no auth is found
by scanners within hours.

## Updating later

```bash
cd /opt/money
git pull
docker compose -f deploy/docker-compose.yml up -d --build
```

State (`storage/bot/autopilot/state.json`, videos, receipts) lives in the volume and
survives the rebuild — the schedule picks up exactly where it stopped.

## If it doesn't work

| Symptom | Cause | Fix |
|---|---|---|
| Caddy logs `no such host` / cert fails | DNS not pointing at the server | fix the A record, wait, `docker compose restart caddy` |
| `502 Bad Gateway` | bot container down | `docker compose logs bot`, usually a missing key in `config.toml` |
| Browser: `403 cross-origin browser request` | origin not allowed | `CORS_ALLOWED_ORIGINS=https://bot.geekyacedigitalhub.com` and `--trust-proxy` |
| Password prompt loops | hash pasted with `$` expanded by the shell | wrap it in single quotes in `deploy/.env` |
| `/bot` blank, assets 404 | opened a deep path without the SPA fallback | use the root URL |
| Autopilot says `rehearsal` on the server | keys not in the server's `config.toml` | copy the exact file, check `cli status` |
| Uploads over ~100 MB fail | proxy body limit | raise `max_size` in `deploy/Caddyfile` |

More: [AUTOPILOT.md](AUTOPILOT.md) · [CONNECTING.md](CONNECTING.md) · [docs/bot.md](docs/bot.md)

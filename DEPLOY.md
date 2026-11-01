# Your own link — Option A (VPS + your domain)

Target: **`https://bot.geekyacedigitalhub.com/bot`**, running on a server that stays
on, with HTTPS and a password in front of it.

Two things you do by hand (only you can), then one command does the rest.

---

## 1. The server

| | |
|---|---|
| Spec | **2 vCPU · 4 GB RAM · 40 GB disk · Ubuntu 24.04** |
| Any provider works | Hetzner CX22 (~€4), DigitalOcean ($6), Contabo, Vultr |
| Why 4 GB | moviepy + Whisper subtitles; 1 GB will swap and crawl |
| Login | SSH key or root password — you need SSH access |

Save the **public IPv4** it gives you. Everything below uses `root@YOUR_IP`.

> Don't overspend on CPU. You post 3 videos a day, one render at a time; 2 vCPU is
> plenty and 8 vCPU changes nothing.

## 2. One DNS record

At the registrar where `geekyacedigitalhub.com` lives (or in Cloudflare):

| Type | Name | Value | TTL |
|---|---|---|---|
| `A` | `bot` | `YOUR_IP` | Auto |

Check from your laptop:

```bash
dig +short bot.geekyacedigitalhub.com     # must print the server IP
```

**If you use Cloudflare**, turn the orange cloud **off** for this record while
Caddy gets its certificate (or keep it on and pass `--skip-dns-check` later).

## 3. The one command

```bash
ssh root@YOUR_IP
git clone --branch arena/b977c1a2-money --depth 1 \
  https://github.com/Geekyacedigitalhub/money.git /opt/money
cd /opt/money
sudo bash deploy/setup-vps.sh --domain bot.geekyacedigitalhub.com
```

> The bot's code lives on the `arena/b977c1a2-money` branch until the pull request
> is merged. Once it is on `main`, drop `--branch …` and the command stays the
> same. The installer runs from whatever checkout you call it from.

It will:

1. verify `bot.geekyacedigitalhub.com` really points at this machine (and prints the exact record if not)
2. install Docker if it isn't there
3. clone the repo to `/opt/money`
4. generate a strong password, hash it, and write `deploy/.env` with mode `600`
5. build and start the bot + Caddy
6. wait until HTTPS answers, then print the URL and password

At the end it prints something like:

```
──────────────────────────────────────────────
  Your bot is up
──────────────────────────────────────────────
  URL       https://bot.geekyacedigitalhub.com/bot
  Username  ace
  Password  Xk7pQ2mR9vLt4sB1nWca

  Save that password now — only its hash is stored on the server.
```

First build takes 3–6 minutes; after that, restarts are seconds. Re-running the
script is safe — it pulls, rebuilds and restarts.

### Want to see it before running it?

```bash
sudo bash deploy/setup-vps.sh --domain bot.geekyacedigitalhub.com --dry-run
```

Prints every step and changes nothing. `--password '…'` uses your own instead of
generating one, `--user ace2` changes the username.

## 4. Your keys go on the server

The script copies `config.example.toml` → `config.toml` if it's missing, and warns
you that it has no keys. Two ways to fix that:

**From your laptop** (recommended — it's the same file you already use):

```bash
scp config.toml root@YOUR_IP:/opt/money/config.toml
```

**Or edit on the server:**

```bash
nano /opt/money/config.toml
```

Fill in at minimum:

```toml
[app]
upload_post_enabled = true
upload_post_api_key = "…"
upload_post_username = "default"

[bot]
demo_mode = false
public_url = "https://bot.geekyacedigitalhub.com"
```

Then restart just the bot:

```bash
cd /opt/money && docker compose -f deploy/docker-compose.yml restart bot
```

`screencap` of the truth afterwards:

```bash
docker compose -f deploy/docker-compose.yml exec bot python -m app.bot.cli status
```

`publishing : LIVE via upload-post` and `autopilot : live` mean it's actually
posting. Anything else names the missing key.

## 5. Prove it from the outside

```bash
# HTTPS + password + app answering
curl -u ace:'your-password' https://bot.geekyacedigitalhub.com/api/bot/health

# what the scheduler will do next
curl -u ace:'your-password' https://bot.geekyacedigitalhub.com/api/bot/autopilot
```

Then, on your phone: open the URL, log in once, **Add to Home Screen**. Send
**`/link`** — it must answer with `https://bot.geekyacedigitalhub.com/bot`. Send
**`/auto`** — it shows `live`, the next run time and any remaining blockers.

## 6. Security — this URL spends money

| Already handled by the bundle | You still do |
|---|---|
| Basic auth on the whole site | keep the password in your password manager |
| HTTPS + HSTS, auto-renewed by Caddy | — |
| Bot port `8080` not published to the internet | — |
| `config.toml` mounted read-only, `600` on host | never commit it (it's git-ignored) |
| `storage/` on a volume, survives rebuilds | back it up if you care about receipts |

Optional, still worth it: `[bot] allowed_users` for Telegram, and `[app] api_key`
if you expose the original REST API.

## 7. Updating

```bash
cd /opt/money
git pull
docker compose -f deploy/docker-compose.yml up -d --build
```

The autopilot state (`storage/bot/autopilot/state.json`), videos and receipts live
in the volume — the schedule resumes exactly where it stopped.

## 8. When something is wrong

| Symptom | Cause | Fix |
|---|---|---|
| Script stops at DNS | record missing/wrong | add the A record, wait, re-run |
| Caddy: `no such host` / no certificate | DNS changed after start | `docker compose -f deploy/docker-compose.yml restart caddy` |
| Cloudflare 522 / 1016 | proxy on, origin not reachable | turn proxy off or fix the A record |
| `502 Bad Gateway` | bot container down | `docker compose … logs bot`, usually a broken `config.toml` |
| Password prompt loops | `$` in the hash got expanded by a shell | rewrite `deploy/.env` via the script, keep the hash in single quotes |
| `/bot` blank | opened `/` instead of `/bot` | use the full URL from step 3 |
| Uploads over ~100 MB fail | proxy body limit | raise `max_size` in `deploy/Caddyfile`, restart caddy |
| Autopilot says `rehearsal` on the server | keys not in the server's `config.toml`, or `demo_mode` still true | step 4 |

Logs, in the order you'll need them:

```bash
docker compose -f deploy/docker-compose.yml logs -f bot
docker compose -f deploy/docker-compose.yml logs caddy | tail -40
```

## What actually costs money

**One thing: the VPS.** Everything else has a $0 path, and this repo is already
wired for them.

| Piece | Free option | When you'd pay |
|---|---|---|
| **VPS (2 vCPU / 4 GB)** | — | **always — this is the only required purchase, ~$5/mo** |
| Domain | you already own `geekyacedigitalhub.com` | only if you buy another one |
| HTTPS certificate | Let's Encrypt, automatic via Caddy | never |
| Stock footage | **Pexels / Pixabay free API key** | never for short-form |
| Script writing (LLM) | **Ollama on the server** (`llm_provider = "ollama"`, no key at all) or a **free Gemini key** | ~$1–2/month only if you prefer a hosted model's quality |
| Publishing | Upload-Post Free (10 publishes/cycle) | only for TikTok or >10 posts/cycle |
| Telegram | free, @BotFather | never |
| This software | yours, MIT | never |

### The strictly-$0 configuration

Nothing below needs a card:

```toml
[app]
llm_provider = "ollama"                  # free, no API key, runs on the VPS
ollama_model_name = "qwen2.5:3b"         # ~2 GB RAM, fine for 60-second scripts
ollama_base_url = ""                     # empty = http://127.0.0.1:11434/v1
pexels_api_keys = ["your-free-pexels-key"]

upload_post_enabled = true
upload_post_api_key = "…"                # Free plan key
upload_post_username = "default"

[bot]
demo_mode = false
public_url = "https://bot.geekyacedigitalhub.com"
autopilot_max_per_day = 1                # stay under the Free API cap
```

```bash
# one-off, on the server, for the free local model
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:3b
```

**The honest trade-off:** on 4 GB the local 3B model writes plainer scripts than a
hosted model, and rendering competes with it for RAM. If quality matters more than
the $1–2, take the free Gemini key instead — it costs nothing, uses no server RAM,
and reads noticeably better. Ollama is the answer when you want *zero accounts* as
well as zero cost.

### Where money actually buys something

| Spend | Buys you |
|---|---|
| VPS $5/mo | the bot running 24/7 — **required** |
| Upload-Post paid | TikTok connection + more than 10 publishes per cycle |
| Hosted LLM ~$1–2/mo | better hooks, better retention, less editing |

Nothing in this repo is gated. The free path is the same code with different
values in `config.toml`.

---|---|
| VPS (2 vCPU / 4 GB) | ~$5 |
| Domain | already yours |
| TLS certificate | free (Let's Encrypt, automatic) |
| LLM for ~90 scripts | ~$1–2 |
| Pexels footage | free |
| Upload-Post | their plan — Free caps at 10 publishes/cycle |

Roughly **$6–7/month** for fully unattended posting, plus whatever Upload-Post
plan you choose.

---

## Alternatives (if the VPS falls through)

- **Cloudflare Tunnel** — free, no server, but only runs while a machine you own is
  on. Config: `deploy/cloudflared.yml`.
- **Render / Railway / Fly** — no server admin, free tiers sleep (which stops the
  schedule), paid tiers ≈ the VPS price.

Both use the same `config.toml` and the same container; only the host changes.

**Related:** [AUTOPILOT.md](AUTOPILOT.md) · [CONNECTING.md](CONNECTING.md) · [START-HERE.md](START-HERE.md)

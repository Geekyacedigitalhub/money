# Get everything working — start to finish

Follow this in order. Each step says **what you do**, **what you should see**, and
**what to do if it doesn't work**. Check the box as you go.

**Time:** ~60 minutes, most of it waiting for a build.
**Money:** one VPS (~$5–6/month). Everything else below is free.

When you finish, you will have:

```
https://bot.geekyacedigitalhub.com/bot
```

…a chat on your phone that writes, voices, edits and publishes videos to
Facebook, Instagram and YouTube **3 times a day on its own**.

---

## Before you start

Gather these, or the steps will stall:

- [ ] **A card** for the VPS (any provider; ~$5–6/month)
- [ ] **Login to your domain** — whoever manages `geekyacedigitalhub.com`
      (registrar, Namecheap, GoDaddy, Cloudflare…)
- [ ] **Your Upload-Post key** — https://app.upload-post.com/api-keys
      (you already have `Geekyacedigitalhub`, Active)
- [ ] **Your phone**, for the last step

Costs, so there are no surprises:

| Item | Cost | Required? |
|---|---|---|
| VPS 2 vCPU / 4 GB | ~$5–6/month | **yes — the only purchase** |
| Domain / TLS / Telegram / this software | $0 | — |
| Footage (Pexels) | $0 | yes, free key |
| Script writing | $0 with Ollama or a free Gemini key | your choice |
| Upload-Post | Free tier | upgrade only for TikTok or >10 posts/cycle |

---

## Step 1 — Fix the two broken accounts (10 min, free)

These are the only parts no code can do for you: each platform needs a human to
approve the login once.

- [ ] Open **https://app.upload-post.com/manage-users**
- [ ] Open the profile named **`default`**

**Instagram** — you will see *Geekyace Digital Hub* with a **red ✕** next to
Instagram (Facebook on the same row is green):

- [ ] Click **Reconnect** on Instagram → log in → approve
- [ ] It must be a **Creator or Business** account
      (Instagram app → *Settings → Account type and tools*)
- [ ] Back on the page: Instagram shows a **green tick** and an `@handle`

**YouTube:**

- [ ] Click **Connect** → Google sign-in → pick the channel
- [ ] Before this works, that Google account needs **phone verification** and
      **2-Step Verification** (Google rejects uploads otherwise)

**TikTok:** nothing to do. Upload-Post blocks it on the Free plan — the bot will
say so and skip it. It starts working the day you upgrade, with no code change.

**You should see:** Instagram ✅, Facebook ✅, YouTube ✅, TikTok ❌ (plan).

---

> **Doing this without a card?** Then skip steps 3–7 entirely and use
> [SCHEDULING.md](SCHEDULING.md) instead: make the videos on your own computer with
> `python -m app.bot.cli week`, then schedule them with Meta Business Suite,
> YouTube Studio and TikTok. ₦0/month, and TikTok works too.

## Step 2 — Get the two free keys (10 min, $0)

**Footage (required):**

- [ ] https://www.pexels.com/api/ → sign up → copy the key
- [ ] Keep it somewhere safe; it goes in `config.toml` in step 6

**Script writing — pick one:**

| Option | Cost | Do this |
|---|---|---|
| **A. Ollama** (local, no account) | $0 | nothing yet — installed in step 5 |
| **B. Gemini free tier** (better writing) | $0 | https://aistudio.google.com/app/apikey → copy key |

Pick **B** unless you specifically want no third-party account. On a 4 GB server
the local model writes noticeably plainer scripts.

---

## Step 3 — Buy the server (5 min)

- [ ] Sign up at any of: **Hetzner** (CX22, ~€4), **DigitalOcean** ($6),
      **Vultr**, **Contabo**
- [ ] Create a server with:

| Setting | Value |
|---|---|
| OS | **Ubuntu 24.04** |
| Size | **2 vCPU / 4 GB RAM** (1 GB will fail on subtitles) |
| Disk | 40 GB |
| SSH key | add yours, or use the emailed root password |

- [ ] **Copy the server's public IPv4.** You need it twice more.

> Don't buy 8 vCPU "for speed". You post 3 videos a day, one at a time.

---

## Step 4 — Point your domain at it (2 min + waiting)

- [ ] Log in where `geekyacedigitalhub.com` is managed
- [ ] Add a DNS record:

| Type | Name | Value | TTL |
|---|---|---|---|
| `A` | `bot` | the IPv4 from step 3 | Auto / 300 |

- [ ] Wait, then verify from your own computer:

```bash
dig +short bot.geekyacedigitalhub.com
```

**You should see:** the server IP. **If blank:** wait 5 minutes and check again.
Do not continue until it resolves — the HTTPS certificate cannot be issued before.

> **On Cloudflare?** Set the record to **DNS only** (grey cloud) for now.

---

## Step 5 — Install the bot (15 min, mostly waiting)

- [ ] Connect to the server:

```bash
ssh root@YOUR_IP
```

- [ ] Download the code and run the installer:

```bash
git clone --depth 1 https://github.com/Geekyacedigitalhub/money.git /opt/money
cd /opt/money
sudo bash deploy/setup-vps.sh --domain bot.geekyacedigitalhub.com
```

The script checks DNS, installs Docker, generates and hashes a password, builds the
bot + HTTPS proxy, and waits until the site answers.

**You should see, at the end:**

```
──────────────────────────────────────────────
  Your bot is up
──────────────────────────────────────────────
  URL       https://bot.geekyacedigitalhub.com/bot
  Username  ace
  Password  Xk7pQ2mR9vLt4sB1nWca

  Save that password now — only its hash is stored on the server.
```

- [ ] **Save the password** in your password manager

**If it fails:**

| Message | Fix |
|---|---|
| stops at DNS | the `A` record is missing or wrong — step 4 |
| `could not hash the password` | Docker didn't start: `systemctl start docker` |
| finishes but HTTPS won't answer | normal for 1–2 min; `docker compose -f deploy/docker-compose.yml logs caddy` |
| `address already in use` | something else on :80/:443 — `ss -ltnp` |

Only want to look first? Add `--dry-run` — it prints every action and changes nothing.

**Optional, free, only if you chose Ollama in step 2:**

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:3b
```

---

## Step 6 — Fill in `config.toml` (10 min)

The installer copied `config.example.toml` → `/opt/money/config.toml`, which has no
keys. Edit it **on the server**:

```bash
nano /opt/money/config.toml
```

Replace the `[app]` and `[bot]` sections with this, filling the `…` values:

```toml
[app]
# --- script writing ---
llm_provider = "gemini"                     # or "ollama" for the fully local, keyless option
gemini_api_key = "…"                        # https://aistudio.google.com/app/apikey
# llm_provider = "ollama"
# ollama_model_name = "qwen2.5:3b"

# --- footage (free) ---
video_source = "pexels"
pexels_api_keys = ["…"]                     # https://www.pexels.com/api/

# --- publishing (your existing Upload-Post account) ---
upload_post_enabled = true
upload_post_api_key = "…"                   # the key named Geekyacedigitalhub
upload_post_username = "default"            # the PROFILE name, not your email
upload_post_platforms = ["facebook", "instagram", "youtube", "tiktok"]
upload_post_youtube_privacy_status = "unlisted"   # flip to "public" when happy
upload_post_youtube_made_for_kids = false

[bot]
owner_name = "GeekyAce"
demo_mode = false                           # ← real videos, not placeholders
auto_publish = false                        # you still approve inside the chat

# --- the scheduler ---
autopilot_enabled = true
autopilot_times = ["08:00", "13:00", "19:00"]
autopilot_interval_minutes = 0
autopilot_max_per_day = 1                   # 1 while on the Free Upload-Post plan
autopilot_min_gap_minutes = 90
autopilot_niche = "online income with AI tools in Nigeria"
autopilot_timezone = "Africa/Lagos"
autopilot_publish = true

public_url = "https://bot.geekyacedigitalhub.com"
video_aspect = "9:16"
voice_name = "en-US-AriaNeural-Female"
subtitle_enabled = true
```

- [ ] Save (`Ctrl+O`, `Enter`, `Ctrl+X` in nano)
- [ ] Restart so it picks up the file:

```bash
cd /opt/money && docker compose -f deploy/docker-compose.yml restart bot
```

> Prefer editing on your laptop? `scp config.toml root@YOUR_IP:/opt/money/config.toml`
> then restart. Same result.

---

## Step 7 — Verify, in this exact order (5 min)

- [ ] **1. Is the config complete?**

```bash
docker compose -f deploy/docker-compose.yml exec bot python -m app.bot.cli status
```

**You should see:** `publishing : LIVE via upload-post` and
`autopilot : live · at 08:00, 13:00, 19:00 · 0/1 today`.
Anything else names the exact missing key.

- [ ] **2. Are the accounts actually connected?** (costs 1 API call, cached 5 min)

```bash
docker compose -f deploy/docker-compose.yml exec bot python -m app.bot.cli doctor
```

**You should see:** ✅ Facebook, ✅ Instagram, ✅ YouTube, ❌ TikTok (paid plan).
`⚠️ login expired` means reconnect that one platform in step 1.

- [ ] **3. Does a real post land?** (costs 1 API call)

```bash
docker compose -f deploy/docker-compose.yml exec bot \
  python -m app.bot.cli doctor --test-post facebook
```

**You should see:** `✅ Facebook: posted — https://www.facebook.com/reel/…`

**Open that URL.** If it plays, the whole chain is proven. This is the only check
that can't be faked.

---

## Step 8 — Put it on your phone (2 min)

- [ ] Open **https://bot.geekyacedigitalhub.com/bot** in your phone browser
- [ ] Log in once with `ace` + your password
- [ ] **Add to Home Screen** (iOS: Share → *Add to Home Screen*;
      Android: ⋮ → *Add to Home screen*)
- [ ] Send **`/link`** → it must answer with your domain
- [ ] Send **`/auto`** → it shows `live`, today's count, next run, next topic
- [ ] Tap **▶️ Run one now** and watch a video arrive in the chat

That last tap is the whole product: a finished video, posted, with links.

---

## Step 9 — Leave it alone for three days

- [ ] Day 1–3: check `/auto` once a day
- [ ] Watch the first three videos properly

**If something is off, change one value and it applies from the next cycle:**

| Problem | Change |
|---|---|
| Voice sounds wrong | `[bot] voice_name` (try `en-GB-RyanNeural-Male`) |
| Too short | `[bot] paragraph_number = 2` |
| Topics feel generic | `[bot] autopilot_niche`, or your own list: `python -m app.bot.cli topics add "…"` |
| Same time daily feels spammy | `autopilot_times = ["09:00", "18:00"]` |

- [ ] Add your own topics (best single improvement — these are what make a channel
      look human rather than templated):

```bash
docker compose -f deploy/docker-compose.yml exec bot \
  python -m app.bot.cli topics add "Why your first 100 customers come from WhatsApp" \
                                   "The 3 invoices every freelancer should send"
```

---

## Step 10 — Optional: the same bot on Telegram (5 min)

- [ ] Telegram → **@BotFather** → `/newbot` → pick a name → copy the token
- [ ] Add it to `config.toml`:

```toml
[bot]
telegram_token = "123456789:AA…"
```

- [ ] Restart — Telegram starts by itself when a token is present, no extra flag:

```bash
cd /opt/money && docker compose -f deploy/docker-compose.yml restart bot
```

- [ ] Message your bot anything. It replies with your **numeric user id**.
- [ ] Put that id in `allowed_users` and restart, so nobody else can use it:

```toml
allowed_users = ["123456789"]
```

Now `/auto`, **Run one now** and **Pause** work from your phone without opening a
browser, and every scheduled post reports its links straight into the chat.

---

## Done — what you now have

| | |
|---|---|
| Link | `https://bot.geekyacedigitalhub.com/bot` |
| Schedule | 08:00 / 13:00 / 19:00 Lagos, 1/day |
| Platforms | Facebook, Instagram, YouTube (TikTok when the plan allows) |
| Cost | VPS ~$5–6/month + $0 elsewhere |
| Your work | none — check `/auto` when you feel like it |

Proof it's alive: `docker compose -f deploy/docker-compose.yml logs -f bot`

---

## When you get stuck

Send me any of these and I'll tell you exactly what's wrong:

- the output of `python -m app.bot.cli status`
- the output of `python -m app.bot.cli doctor`
- the last 30 lines of `docker compose -f deploy/docker-compose.yml logs bot`
- or just the error text

Detailed references: [DEPLOY.md](DEPLOY.md) (server/link) ·
[CONNECTING.md](CONNECTING.md) (accounts) · [AUTOPILOT.md](AUTOPILOT.md)
(schedule) · [docs/bot.md](docs/bot.md) (code).

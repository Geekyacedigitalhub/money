# START HERE

This is **your** bot. Six steps, in order. Each one tells you exactly what to type
and what you should see. Stop reading anything else until you finish step 2.

---

## Step 0 — Open the chat (already works, costs nothing)

The bot is running. Open the preview link (or `http://127.0.0.1:8080/bot`) and type:

```
5 things to check before renting in Lagos
```

**You should see:** a video appear in ~5 seconds, with captions for TikTok,
Instagram, Facebook and YouTube, and buttons underneath.

That's demo mode — a placeholder render so you can see the whole flow before
spending anything. Nothing is published in this mode.

> Want it to say your name? Edit `config.toml`:
> ```toml
> [bot]
> owner_name = "Ace"
> bot_name = ""      # empty = "Ace's Post Bot"
> ```
> Then restart with `python -m app.bot.cli serve`.

---

## Step 1 — Run it on your own machine

The sandbox won't last. Install it where you work every day.

```bash
# 1. get the code (your fork)
git clone https://github.com/Geekyacedigitalhub/money.git
cd money

# 2. install (pick ONE)
uv sync --frozen                     # if you have uv  (recommended)
pip install -r requirements.txt      # if you prefer pip

# 3. start
python -m app.bot.cli serve
```

Then open **http://127.0.0.1:8080/bot**.

Check everything with:

```bash
python -m app.bot.cli status
```

**You should see** `generation mode : DEMO` and `publishing : SIMULATED`. That's
correct at this stage — the next two steps flip them.

*Needs Python 3.11+ and FFmpeg. On Ubuntu: `sudo apt install ffmpeg`. On Windows,
FFmpeg is usually found automatically; if not, set `[app] ffmpeg_path`.*

---

## Step 2 — Make real videos (2 keys, ~10 minutes)

Get these two, then paste them into `config.toml`:

**1. Pexels footage key — free**
Go to https://www.pexels.com/api/ → sign up → copy your key.

**2. An LLM key — cheap, picks the script**

| Provider | Get key at | Cost per video |
|---|---|---|
| DeepSeek | https://platform.deepseek.com/api_keys | ~$0.001 |
| Google Gemini | https://aistudio.google.com/app/apikey | free tier works |
| Moonshot/Kimi | https://platform.kimi.ai | ~$0.002 |

Paste into `config.toml`:

```toml
[bot]
demo_mode = false          # ← turn this off

[app]
llm_provider = "deepseek"
deepseek_api_key = "sk-..."       # ← step 2 key
deepseek_model_name = "deepseek-chat"
pexels_api_keys = ["..."]         # ← step 1 key
```

Restart, then send a topic again.

**You should see:** a real video with real stock footage, an English voiceover
(Edge TTS — no key needed) and burned-in subtitles. Takes 2–5 minutes.

> Voice: `[bot] voice_name`. Length: raise `[bot] paragraph_number`.
> "AI footage" instead of stock? `[bot] video_source` — but those bill per second.

---

## Step 3 — Connect your accounts (10 minutes, once)

You connect the accounts **once, at upload-post.com** — not to this code. Then the
bot posts through it with a single key.

**Your account is already set up** (`Geekyacedigitalhub`, profile `default`, API key
`Geekyacedigitalhub`). Only two things are left:

1. **Fix Instagram** — go to **https://app.upload-post.com/manage-users**, open the
   profile **`default`**. Instagram shows *Geekyace Digital Hub* with a **red ✕**
   (Facebook is green). Click **Reconnect** on Instagram and approve. It must be a
   Creator/Business account.
2. **Connect YouTube** — same page, same profile → **Connect** → Google sign-in.
   The Google account needs **phone verification + 2-Step Verification**.

**TikTok is not available on the Free plan.** Upload-Post says so in a yellow
banner. Either upgrade the plan, or keep posting to TikTok by hand from the
**download** button — the bot handles Facebook, Instagram and YouTube either way.

Then put the key in `config.toml`:

```toml
[app]
upload_post_enabled = true
upload_post_api_key = "eyJhbGciOi…"      # from https://app.upload-post.com/api-keys
upload_post_username = "default"          # the PROFILE name — not your email
upload_post_platforms = ["facebook", "instagram", "youtube", "tiktok"]
upload_post_youtube_privacy_status = "unlisted"   # flip to "public" once you trust it
```

**Watch your budget:** the Free plan gives **10 API calls per cycle** (resets on the
1st) and 30 minutes of video processing. One publish = 1 call; `/connect` is cached
for 5 minutes so it doesn't burn calls when you re-check.

**Now verify it — this is the part that matters** (1 call, cached):

```bash
python -m app.bot.cli doctor
```

```
✅ Connected to profile `default`
Plan allows 2 profile(s).

✅ Facebook  — connected as Geekyace Digital Hub
❌ Instagram — connection incomplete
❌ YouTube   — never connected
❌ TikTok    — Upload-Post does not allow connecting TikTok on the Free plan
```

Then prove it end-to-end with one real post (1 call):

```bash
python -m app.bot.cli doctor --test-post facebook
```

## Step 4 — Publish for the first time

Send a topic, wait for the preview, then tap:

- **🚀 Publish now** — posts to every selected platform
- **📡 Change platforms** — untick the ones you don't want yet

**Do your first test with one platform selected** — Facebook, since it is already
connected. Confirm it appears live, then enable Instagram and YouTube.

> Everything is off by default except your tap. `auto_publish = false` means
> nothing goes out without you pressing the button. Keep it that way until you
> trust the output.

---

## Step 5 — Put it on your phone (2 minutes)

This is how you'll actually use it every day.

1. Open Telegram → message **@BotFather** → `/newbot` → pick a name → copy the token.
2. In `config.toml`:
   ```toml
   [bot]
   telegram_token = "123456789:AA..."
   allowed_users = []        # leave empty for now
   ```
3. Restart with `python -m app.bot.cli serve --telegram`
4. Message your new bot anything. It replies with your **numeric user id**.
5. Paste that id into `allowed_users` and restart:
   ```toml
   allowed_users = ["123456789"]
   ```

Now you can send a topic from your phone and get a publishable video back. No
public IP, domain or certificate needed.

---

## Step 6 — Hands off (already configured)

You do not have to send topics one by one. A scheduler is built in and enabled:

```toml
[bot]
autopilot_enabled = true
autopilot_times = ["08:00", "13:00", "19:00"]   # Lagos time
autopilot_max_per_day = 3
autopilot_niche = "online income with AI tools in Nigeria"
```

Start everything with one command:

```bash
python -m app.bot.cli serve
```

It picks the next topic, writes the script, voices it, cuts the video and publishes
— three times a day — then logs the result and moves on.

**Right now it runs in rehearsal**: your `demo_mode` is still `true` and no LLM /
Pexels / Upload-Post keys are set, so it renders and records but never posts. That
is intentional. `python -m app.bot.cli autopilot` lists the exact four blockers and
how to fix each.

Control it from chat with **`/auto`** or the **🤖** button: status, *Run one now*,
*Pause*. Add your own topics with `python -m app.bot.cli topics add "…"`.

> **Free-plan reality check:** 3 posts/day is ~90/month, but Upload-Post Free gives
> **10 API calls per cycle**. Everything renders; only publishing stops after 10.
> Either set `autopilot_max_per_day = 1` and top up, or upgrade before scaling.

Full guide, including running it 24/7 with systemd or cron:
[AUTOPILOT.md](AUTOPILOT.md).

---

---

## Step 7 — Your own link

The sandbox preview URL dies with the session. For a link you own:

```toml
[bot]
public_url = "https://bot.geekyacedigitalhub.com"
```

Then send **`/link`** in the chat — it answers with that address, ready to save to
your phone's home screen.

Deploying is one command on the server:

```bash
git clone --branch arena/b977c1a2-money https://github.com/Geekyacedigitalhub/money.git /opt/money
cd /opt/money && sudo bash deploy/setup-vps.sh --domain bot.geekyacedigitalhub.com
```

It checks DNS, installs Docker, generates and hashes a password, starts the bot
behind HTTPS and waits until it answers. Full walkthrough:
[DEPLOY.md](DEPLOY.md).

---

## What you just built

| | |
|---|---|
| **Cost per 60-second video** | under 1 cent (Edge TTS + Pexels + cheap LLM) |
| **Time per video** | 2–5 minutes of waiting, ~1 minute of your attention |
| **Where it posts** | Facebook, YouTube, TikTok, Instagram |
| **Your daily loop** | send 3 topics → approve 3 previews → done |

## Three rules that decide whether this makes money

1. **Never auto-publish untrusted output.** One bad video on a client's account
   costs more than a month of saved time.
2. **TikTok will not pay you from Nigeria.** Creator Rewards is 8 countries only.
   Use TikTok for reach; put your monetised effort into **Facebook Reels** and
   **long-form YouTube** (Nigeria is eligible, paid via AdSense).
3. **You still have to write the angle.** YouTube demonetises channels of
   mass-produced, templated videos. Change the hook, the footage choice or the
   caption on every video and you stay on the right side of the "inauthentic
   content" policy.

---

## When something breaks

Run this first — it tells you exactly which part is misconfigured:

```bash
python -m app.bot.cli status
```

| Message | Fix |
|---|---|
| `SIMULATED (missing upload_post_…)` | Publishing keys not set — step 3 |
| `doctor` says "Profile not found" | `upload_post_username` must be the profile name, not your email |
| A platform is "skipped" on publish | That account isn't connected to your profile — run `doctor` |
| `Generation failed … api_key is not set` | LLM key missing — step 2 |
| `ffmpeg is not available` | `sudo apt install ffmpeg`, or set `[app] ffmpeg_path` |
| `restricted to an allow-list` | Add your id to `[bot] allowed_users` |
| Video is a title card with "DEMO PREVIEW" | `[bot] demo_mode` is still `true` |
| `403 cross-origin browser request` | Start with `--trust-proxy`, or set `CORS_ALLOWED_ORIGINS` |

Full reference: [docs/bot.md](docs/bot.md).

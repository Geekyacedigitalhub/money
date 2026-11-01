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

## Step 3 — Connect your 4 accounts (one signup)

You have two options. Take the first unless you have a reason not to.

### Option A — Upload-Post (one key covers all four)

1. Sign up at https://upload-post.com
2. In their dashboard, **connect TikTok, Instagram, Facebook and YouTube**
3. Copy your API key + username into `config.toml`:

```toml
[app]
upload_post_enabled = true
upload_post_api_key = "..."
upload_post_username = "..."
upload_post_platforms = ["tiktok", "instagram", "facebook", "youtube"]
```

Restart. `python -m app.bot.cli status` should now say **`publishing : LIVE via upload-post`**.

**Account requirements they cannot skip:**

| Platform | You need |
|---|---|
| TikTok | A TikTok account |
| Instagram | A **Creator or Business** account (not personal) |
| Facebook | A **Page** (not your personal profile) |
| YouTube | A channel with phone verification + 2-step verification |

### Option B — Free, but you do the last step yourself

Send yourself the video from the chat, then upload it manually to each app. Zero
cost, no signup. Use this until you're posting daily.

---

## Step 4 — Publish for the first time

Send a topic, wait for the preview, then tap:

- **🚀 Publish now** — posts to every selected platform
- **📡 Change platforms** — untick the ones you don't want yet

**Do your first test with one platform selected** (say TikTok). Confirm it appears
live. Then enable the rest.

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
| `Generation failed … api_key is not set` | LLM key missing — step 2 |
| `ffmpeg is not available` | `sudo apt install ffmpeg`, or set `[app] ffmpeg_path` |
| `restricted to an allow-list` | Add your id to `[bot] allowed_users` |
| Video is a title card with "DEMO PREVIEW" | `[bot] demo_mode` is still `true` |
| `403 cross-origin browser request` | Start with `--trust-proxy`, or set `CORS_ALLOWED_ORIGINS` |

Full reference: [docs/bot.md](docs/bot.md).

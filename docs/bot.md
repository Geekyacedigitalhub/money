# Social Publishing Bot

A chat bot that turns **a topic** into a finished vertical video and posts it to
**Facebook, YouTube, TikTok and Instagram**.

It is not a separate app: it sits on top of MoneyPrinterTurbo's existing pipeline
(script → voice → subtitles → footage → render) and its existing
[Upload-Post](https://upload-post.com) publishing integration. Everything you
configure in `config.toml` for the WebUI / API / CLI is reused, so you only
maintain one set of keys.

```
you: "5 things to check before renting in Lagos"
bot: 🎬 writing script → voicing → subtitles → footage → rendering
bot: [sends the MP4 + per-platform captions + buttons]
you: tap "🚀 Publish"
bot: ✅ Published: TikTok, Instagram, Facebook, YouTube
```

---

## 1. Quick start (2 minutes, no API keys)

The bot ships in **demo mode**: it renders a clearly-labelled placeholder video
with FFmpeg only, so you can verify the whole flow — chat, preview, platform
selection, publish receipts — before spending a naira on API keys.

```bash
# from the repo root
uv sync --frozen

python -m app.bot.cli status      # shows exactly which parts are live vs simulated
python -m app.bot.cli serve       # then open http://127.0.0.1:8080/bot
```

Or, if you want to skip the WebUI / REST stack and run only the bot (much
lighter — no Streamlit, no MoviePy at startup):

```bash
python -m app.bot.cli serve --bot-only --port 8080
```

Try it: type a topic, wait ~5 seconds, watch the preview appear with four
platform sections and a **🧪 Simulate publish** button.

> `cli status` is the first thing to run whenever something is wrong. It prints
> generation mode, publishing mode and the exact reason publishing is simulated.

---

## 2. Commands

| Command | What it does |
|---|---|
| *(just type text)* | Generate a video from that topic |
| `/help` | Command list + platform keyboard |
| `/platforms` | Show and toggle the four targets, with live/simulated status each |
| `/how` | What each platform accepts: max length, account type, how it pays |
| `/status` | Progress of the current job |
| `/preview` | Resend the last video with its buttons |
| `/caption <text>` | Override the caption for every selected platform |
| `/cancel` | Stop the running job |
| `/jobs` | Recent job history |
| `/mode` | Demo vs live, and whether publishing is real |
| `/new` | Start a fresh video |

You can also **send the bot an MP4** (any short video you already made) and it
will caption and publish that instead of generating anything.

### Buttons

Every preview carries the same actions:

- **🚀 Publish now** / **🧪 Simulate publish** — posts to the selected platforms
- **🗑 Discard** — drops the job
- **🔁 Regenerate video** — same topic, new render
- **✏️ New captions** — regenerate the per-platform copy
- **📡 Change platforms** — toggle targets

Nothing is published without your tap unless you explicitly enable
`auto_publish`.

---

## 3. Going live

Demo → real is three config blocks. Edit `config.toml`:

### 3.1 Stop demo mode

```toml
[bot]
demo_mode = false
```

### 3.2 Keys for generation

The bot uses the **same** settings as the WebUI, so configuration is shared:

```toml
[app]
llm_provider = "moonshot"        # or "deepseek", "gemini", "ollama"…
moonshot_api_key = "sk-…"        # whichever provider you chose

pexels_api_keys = ["…"]          # free key: https://www.pexels.com/api/
```

You need at minimum:

| Piece | Why | Free option? |
|---|---|---|
| An LLM key | writes the script + search terms | DeepSeek / Gemini Flash are fractions of a cent per video; `ollama` is fully local |
| A stock-footage key | the B-roll | Pexels / Pixabay API keys are free |
| TTS | the voiceover | The default Edge TTS needs **no key at all** |
| FFmpeg | the render | Auto-detected, or bundled via `imageio-ffmpeg` |

If you would rather generate AI footage than use stock, set `video_source` to any
of the supported providers (`volcengine_seedance`, `ofox`, `muapi`, …). Those
bill per second — see §7.

### 3.3 Publishing credentials

One Upload-Post account covers all four platforms:

```toml
[app]
upload_post_enabled = true
upload_post_api_key = "…"
upload_post_username = "…"
upload_post_platforms = ["tiktok", "instagram", "facebook", "youtube"]
upload_post_youtube_privacy_status = "public"
upload_post_youtube_made_for_kids = false
```

Do that and `cli status` flips from `SIMULATED` to `LIVE via upload-post`.

**Account requirements on the platform side** (Upload-Post cannot bypass these):

| Platform | Needs |
|---|---|
| TikTok | A TikTok account; business accounts are excluded from rewards |
| Instagram | A **Professional** (Creator/Business) account |
| Facebook | A **Page**, not a personal profile |
| YouTube | A channel with phone verification and 2-step verification |

---

## 4. Telegram

Telegram is the most convenient front-end: free, no app review, works from your
phone anywhere.

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
2. Add it to `config.toml`:
   ```toml
   [bot]
   telegram_token = "123456789:AA…"
   ```
   (or export `MPT_BOT_TELEGRAM_TOKEN`)
3. Lock it down — an open bot will spend your API credits:
   ```toml
   [bot]
   allowed_users = ["123456789"]   # your numeric Telegram id
   ```
   Send the bot any message while `allowed_users` is empty and it replies with
   your id so you can paste it in.
4. Start:
   ```bash
   python -m app.bot.cli serve --telegram
   # or run Telegram alone, without the web server:
   python -m app.bot.telegram
   ```

The bot uses **long polling**, not webhooks, so no public IP, domain or HTTPS
certificate is required — it works from a laptop on a home connection.

---

## 5. Web chat UI

`http://127.0.0.1:8080/bot` — a mobile-first chat page served by the same
FastAPI process. It needs no build step and no extra service.

It exposes a small JSON API you can script against:

| Endpoint | Purpose |
|---|---|
| `GET /api/bot/health` | demo/live state, publisher, platform list |
| `POST /api/bot/message` | `{chat_id, text}` → replies |
| `POST /api/bot/callback` | `{chat_id, data}` → button clicks |
| `GET /api/bot/feed?chat_id=` | async work that finished (poll this) |
| `POST /api/bot/upload` | multipart MP4 you already made |
| `GET /api/bot/jobs/{id}` | job status |
| `GET /api/bot/media/{id}` | the rendered MP4 (supports HTTP Range) |

The `chat_id` is any stable string — the browser generates one and keeps it in
`localStorage`.

---

## 6. Architecture

```
app/bot/
├── models.py      Platform / Job / Session / Reply  (no framework imports)
├── config.py      [bot] settings + env overrides
├── captions.py    per-platform copy, reusing app/services/llm.py
├── generation.py  demo renderer  |  real MoneyPrinterTurbo pipeline
├── publisher.py   Upload-Post  |  simulated receipts
├── core.py        the conversational brain (channel-agnostic)
├── web.py         FastAPI router: /bot + /api/bot
├── telegram.py    long-polling adapter
├── cli.py         status / serve / ask / publish / demo
└── static/        the chat UI (vanilla HTML/CSS/JS)
```

The split that matters: **`core.py` never calls a chat platform's API.** It takes
a `ChatMessage` and returns `Reply` objects. Adapters render those replies. That
is why adding Slack, Discord or WhatsApp later is an adapter, not a rewrite.

The bot also does not reimplement video generation. `generation.py` calls
`app/services/task.py::start()` — the exact same function the WebUI, REST API and
CLI call — so improvements to the pipeline land in the bot automatically.

Long work runs on a bounded thread pool. When it finishes, the result is pushed
back through a notifier (Telegram) or drained by polling (web). Publishing is a
separate stage that runs *after* the job is marked ready, so a slow upload never
blocks the preview.

---

## 7. Costs

Per 60-second video, with the free stack:

| Item | Cost |
|---|---|
| Script + keywords (2 small LLM calls) | ~$0.0005 – $0.003 |
| Voiceover (Edge TTS) | $0.00 |
| Stock footage (Pexels) | $0.00 |
| Subtitles (local Whisper, CPU) | $0.00 |
| Render (FFmpeg, CPU) | ~$0.001 |
| **Total** | **well under 1 cent** |

Watch out for:

- **AI footage sources** (`volcengine_seedance`, `ofox`, `muapi`, …) bill per
  second of generated video. A batch run can quietly cost more than a client is
  paying. Keep `[bot] video_source = "pexels"` unless you mean it.
- **Upload-Post** is a paid service (it is the thing that makes one key cover
  four platforms). Budget for it, or write direct platform adapters — the
  publisher interface is designed for that (§9).
- **Whisper on CPU** is the slowest stage. For speed set
  `[app] subtitle_provider = "edge"`, or lower `[whisper] model_size`.

---

## 8. Security

Read this before exposing the bot to the internet.

1. **Set an API key.** `[app] api_key` ships empty, which means the REST API and
   the bot's `/api/bot` endpoints are unauthenticated. It also leaves
   `/tasks/<file>` downloadable by anyone who can reach the port.
2. **Use the allow-list.** `[bot] allowed_users` restricts the bot by user id.
   Empty means anyone.
3. **Keep `auto_publish = false`** unless you genuinely want unattended posting.
   A wrong video published to a client's account cannot be un-published.
4. **Behind a reverse proxy**, run with `--trust-proxy` so forwarded
   `X-Forwarded-*` headers are honoured; the app's same-origin guard compares
   the browser's `Origin` against the request URL and will reject legitimate
   same-origin POSTs if the scheme/host is not forwarded correctly. Alternatively
   declare your public origin explicitly:
   ```bash
   CORS_ALLOWED_ORIGINS="https://bot.example.com" python -m app.bot.cli serve --trust-proxy
   ```
5. **Uploads** are capped by `[bot] max_upload_mb` (default 200) and restricted to
   video extensions; served files are confined to the `storage/` directory.

---

## 9. Extending

**Add a platform.** `app/bot/models.py` keeps the three IDs that differ between
services in one place (`social_key` for captions, `upload_post_id` for
publishing, `label` for the UI). Add a member to `Platform`, then extend
`_SOCIAL_KEYS` / `_UPLOAD_POST_IDS` / `_LABELS` / `_EMOJI`. The buttons, caption
generation, delivery guide and readiness report follow automatically.

**Publish somewhere else.** `publisher.py` defines a two-method protocol
(`live`, `publish`). Implement it — for example a native YouTube Data API
publisher — and pass it to `BotCore(publisher=…)`. Nothing else changes.

**Generate differently.** `generation.py::generate()` is injected into
`BotCore(generator=…)`. Point it at a hosted API, a different model, or a
pre-rendered template library.

**New chat channel.** Translate your platform's event into
`ChatMessage(chat_id=…, text=…, callback=…, attachment=…)`, call
`core.handle()`, render the returned `Reply` objects. That is the entire
Telegram adapter.

---

## 10. Tests

```bash
pytest test/bot -q
```

24 tests, no API keys required — they exercise the real demo renderer (Pillow +
FFmpeg), platform toggling, caption limits, simulated publishing and the
receipt/attachment flows.

---

## 11. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `cli status` says `SIMULATED (missing upload_post_enabled = true, …)` | Publishing credentials are not set — expected until §3.3 |
| "Generation dependencies are missing" | Run `uv sync --frozen`; or use `demo_mode = true` |
| `ffmpeg is not available` | Install FFmpeg or set `[app] ffmpeg_path` |
| Video generates but looks like a title card | You are in demo mode — set `demo_mode = false` and configure keys |
| Bot replies "🚫 This bot is restricted to an allow-list" | Add your id to `[bot] allowed_users` |
| Telegram bot silent | Wrong token, or you messaged a different bot; check `python -m app.bot.telegram` logs for `getUpdates` errors |
| HTTP 403 `cross-origin browser request is not allowed` | See §8.4 (`--trust-proxy` + `CORS_ALLOWED_ORIGINS`) |
| Upload fails on every platform | Verify the account types in §3.3 — Upload-Post rejects business TikTok accounts / personal Instagram accounts |
| Job hangs at 40% | Footage download is rate-limited; check `pexels_api_keys` and the log tail |

Logs go to the console. Add `[diagnostics] log_requests = true` for HTTP-level
detail.

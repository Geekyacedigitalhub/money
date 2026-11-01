# Autopilot — post without touching anything

One command, then it runs on its own:

```bash
python -m app.bot.cli serve --autopilot
```

Or, because `autopilot_enabled = true` is already set in your `config.toml`, just:

```bash
python -m app.bot.cli serve
```

That single process gives you the web chat **and** the scheduler. It posts at
**08:00, 13:00 and 19:00 Lagos time**, up to **3 videos a day**, picks the next
topic itself, and keeps a journal of what it did.

```
topic → script → voice → stock footage → subtitles → 9:16 video → Upload-Post → links
        (all automatic, no taps)
```

---

## 1. Where you are right now

```
$ python -m app.bot.cli autopilot        # prints the plan, then runs
Autopilot  ·  rehearsal  ·  at 08:00, 13:00, 19:00  ·  max 3/day
timezone   : Africa/Lagos
queue      : 9 topics (built-in)
next run   : 2026-11-02T08:00+01:00

REHEARSAL: videos are generated and recorded but NOT published.
To go fully live, fix these:
  • demo_mode = true: it renders placeholder slides, not real videos
    → set [bot] demo_mode = false once your LLM + footage keys work
  • no LLM key: it cannot write a script
    → set [app] moonshot_api_key (Moonshot, OpenAI, Gemini… all work)
  • no stock-footage key: there is nothing to put behind the voice
    → get a free key at https://www.pexels.com/api/
  • Upload-Post is not configured (missing upload_post_enabled = true, …)
    → see CONNECTING.md
```

**Rehearsal is deliberate.** With placeholder renders or no publishing
credentials it still runs the whole pipeline and records the result, but it will
never make you think something was posted when it wasn't. It also refuses to
publish a demo video even if you turn publishing on — a grey slide with "DEMO" on
it going to your real Facebook page is worse than no post.

## 2. The four things between you and hands-off posting

| # | What | Cost | Where |
|---|---|---|---|
| 1 | **`demo_mode = false`** | — | `[bot]` in `config.toml` |
| 2 | **An LLM key** (writes the script) | ~½–2 US cents per video | `[app] moonshot_api_key` (or `openai_api_key`, `gemini_api_key`…) |
| 3 | **Pexels key** (the footage) | free | <https://www.pexels.com/api/> → `[app] pexels_api_keys = ["…"]` |
| 4 | **Upload-Post** (the publishing) | free tier 10 posts/cycle | [CONNECTING.md](CONNECTING.md) |

Nothing else. No cron job, no server, no dashboard clicking.

> **The 10-call limit is the real ceiling on the free plan.** 3 posts a day is
> ~90 posts a month; the free tier allows 10 API calls per cycle. Autopilot will
> start failing after 10 publishes with *"API call limit reached"*. Two honest
> options: set `autopilot_max_per_day = 1` and top up when needed, or move to a
> paid Upload-Post plan before you turn the volume up. Everything keeps rendering
> in rehearsal either way — only the publishing is capped.

## 3. What it posts about

Built-in rotation, seeded from `autopilot_niche`:

```
online income with AI tools in Nigeria: 3 mistakes to avoid
online income with AI tools in Nigeria: how to start with no money (for beginners)
online income with AI tools in Nigeria: 5 free tools I actually use (Nigeria edition)
…
```

Change the direction in one line:

```toml
[bot]
autopilot_niche = "small business bookkeeping for Lagos traders"
```

Or take over the queue completely — your lines win, and the built-in rotation is
only used when the file is empty:

```bash
python -m app.bot.cli topics add "Why your first 100 customers come from WhatsApp" \
                                 "The 3 invoices every freelancer should send"
python -m app.bot.cli topics                       # see the queue and what's next
python -m app.bot.cli topics remove --index 2
python -m app.bot.cli topics clear                 # back to the built-in rotation
```

The queue is a plain text file — `storage/bot/autopilot/topics.txt`, one topic per
line. Edit it in any editor; `#` lines are ignored. It **rotates**, so it never
runs out of topics, and it never repeats a topic back-to-back.

## 4. When it runs

```toml
[bot]
autopilot_times = ["08:00", "13:00", "19:00"]  # local times in autopilot_timezone
autopilot_max_per_day = 3                      # hard ceiling
autopilot_min_gap_minutes = 90                 # never two posts back-to-back
autopilot_timezone = "Africa/Lagos"
```

Prefer "every 6 hours" over clock times?

```toml
autopilot_interval_minutes = 360   # ignores autopilot_times
```

Command line overrides, for testing without editing config:

```bash
python -m app.bot.cli autopilot --once                    # one cycle now, then exit
python -m app.bot.cli autopilot --once --topic "Test topic"
python -m app.bot.cli autopilot --interval 15             # every 15 minutes
python -m app.bot.cli autopilot --max-per-day 1
python -m app.bot.cli autopilot --pause                   # stop the schedule
python -m app.bot.cli autopilot --resume
python -m app.bot.cli autopilot --no-publish              # render only, forever
```

## 5. Watching it without logging into a box

Anywhere the chat is:

- **`/auto`** — plan, today's count, next run, next topic, last three results
- **🤖 button** in the web chat — same, plus ▶️ *Run one now* and ⏸ *Pause*
- **`python -m app.bot.cli status`** — one line with the schedule and next run

Everything it did is on disk, so nothing is hidden:

| File | Contents |
|---|---|
| `storage/bot/autopilot/state.json` | cursor, today's count, last 60 runs, paused flag |
| `storage/bot/autopilot/topics.txt` | your topics (if you added any) |
| `storage/bot/<job>/final-1.mp4` | every video it rendered |
| `storage/bot/receipts/*.json` | the exact publishing payload it sent |

A run journal entry looks like this:

```json
{"at": "2026-11-02T08:00:12+01:00", "topic": "online income with AI tools in Nigeria: 3 mistakes to avoid",
 "ok": true, "published": true, "simulated": false,
 "results": {"facebook": "https://www.facebook.com/reel/123…"}}
```

## 6. Keeping it running for weeks

**A small VPS / always-on box** (recommended):

```bash
nohup python -m app.bot.cli serve --autopilot >> bot.log 2>&1 &
```

With systemd, so it survives reboots:

```ini
[Unit]
Description=MoneyPrinterTurbo bot + autopilot
After=network-online.target

[Service]
WorkingDirectory=/opt/money
ExecStart=/opt/money/.venv/bin/python -m app.bot.cli serve --autopilot
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Already have cron?** Skip the daemon and let cron drive single cycles — the state
file keeps the daily cap and the cursor correct either way:

```cron
0 8,13,19 * * * cd /opt/money && .venv/bin/python -m app.bot.cli autopilot --once >> bot.log 2>&1
```

**Docker:** same image, override the command:

```yaml
command: python -m app.bot.cli serve --autopilot
```

## 7. The safety rails (why it won't embarrass you)

| Rail | What it prevents |
|---|---|
| Demo renders are never published | placeholder slides on a real account |
| `autopilot_max_per_day` | a loop bug posting 40 times |
| `autopilot_min_gap_minutes` | restart → immediate duplicate |
| Cursor persisted on disk | re-posting yesterday's topic after a reboot |
| Serial execution (one job at a time) | two renders fighting for the CPU |
| Per-platform skip | one unconnected platform killing the whole post |
| Failed run is recorded, not retried blindly | burning API calls on a broken key |
| `--no-publish` / `autopilot_publish = false` | total off-switch for publishing |

Pause it instantly: `/auto` → ⏸, or `cli autopilot --pause`.

## 8. What "fully automatic" still doesn't cover

- **Connecting the accounts** — OAuth needs a human once per platform.
- **Quality judgement** — watch the first 3–5 videos. If the voice or pacing is
  wrong, change `[bot] voice_name` / `[bot] paragraph_number` and re-run with
  `--once`; the schedule picks the change up on the next cycle.
- **YouTube's "inauthentic content" policy** — mass-produced, near-identical
  videos get demonetised. The rotating hooks help, but real variety (your own
  topics, your own voiceovers) is what keeps a channel monetisable.
- **TikTok** — still needs a paid Upload-Post plan; until then it is skipped, not
  faked.

---

Read next: [CONNECTING.md](CONNECTING.md) for the publishing side,
[START-HERE.md](START-HERE.md) for the whole setup, [docs/bot.md](docs/bot.md) for
the code.

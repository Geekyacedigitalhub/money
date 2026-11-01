# Free daily posting (no card, no subscription)

The bot writes, voices and edits the videos. You upload them with each platform's
**own free scheduler**. One sitting a week, about 30 minutes, and all four
platforms post daily — **including TikTok**, which the paid API route can't do on
its free plan anyway.

```
Sunday, 30 min                     Mon–Sun
┌──────────────────────┐          ┌─────────────────────────────┐
│ cli week  →  7 videos │  ─────►  │ Meta Business Suite  (IG+FB)│
│ + captions + times    │          │ YouTube Studio              │
└──────────────────────┘          │ TikTok scheduler            │
                                  └─────────────────────────────┘
                                     nothing to do all week
```

---

## 1. Make the batch (run once a week)

```bash
python -m app.bot.cli week            # 7 videos, topics from the queue
python -m app.bot.cli week --count 5  # fewer
python -m app.bot.cli week "Your topic one" "Your topic two"
```

Each video takes a few minutes to render, so expect 20–40 minutes for seven — leave
it running.

You get a folder (and a zip next to it) under `storage/bot/exports/week-YYYY-MM-DD/`:

| File | What it is |
|---|---|
| `01-topic.mp4` | the finished vertical video |
| `01-topic.jpg` | thumbnail/poster |
| `01-topic.txt` | the caption, split into `=== INSTAGRAM ===`, `=== FACEBOOK ===`, `=== YOUTUBE ===`, `=== TIKTOK ===` blocks — copy-paste each one |
| `schedule.csv` | which video goes at which time |
| `README.txt` | the same instructions, inside the folder |

From the chat UI, send **`/week`** instead — it builds the batch in the background
and replies with a **download link** you can tap on your phone.

> Set `delivery = "manual"` in `config.toml`. It tells the bot no Upload-Post key
> is needed, so it won't keep asking for one during a batch.

## 2. Upload to Instagram + Facebook (free, one place for both)

1. Open **https://business.facebook.com/latest/scheduler** on a computer
2. **Create post** → **Reels** (for video) → upload `01-topic.mp4`
3. Tick **Instagram** and **Facebook Page** (both), paste each caption from the
   `.txt` file
4. **Schedule** → pick the time from `schedule.csv`
5. Repeat for the other six

Requirements: an Instagram **Creator/Business** account, and a **Facebook Page**
(personal profiles can't be posted to).

## 3. Upload to YouTube (free)

1. **https://studio.youtube.com** → **Create** → **Upload videos**
2. Pick `01-topic.mp4`, use the `=== YOUTUBE ===` block as the **title**, the rest
   as **description**
3. **Visibility** → **Schedule** → the time from `schedule.csv`
4. Shorts are automatic: any vertical video under 3 minutes becomes a Short

## 4. Upload to TikTok (free — the thing the paid API can't do for you)

1. Go to **https://www.tiktok.com/tiktokstudio/upload** on a computer
   (the phone app can also schedule: *Upload → Next → Schedule*)
2. Upload `01-topic.mp4`, paste the `=== TIKTOK ===` block as the caption
3. **Schedule** → the time from `schedule.csv`
4. TikTok's own scheduler allows up to **10 days ahead**

## 5. Then forget it for the week

The platforms post on their own schedule. Nothing needs to stay switched on — not
your laptop, not a server.

---

## What this costs you

| | |
|---|---|
| Money | **₦0** |
| Time | ~30 min/week to make + schedule, 0 min on other days |
| Server | **not needed** — run the batch on your own computer |
| Upload-Post | **not needed** — and TikTok works, which it doesn't on their free plan |

## Honest comparison with the paid route

| | Free (this page) | Paid (~$24/mo + server) |
|---|---|---|
| Daily posting to 4 platforms | ✅ | ✅ |
| TikTok | ✅ | needs the paid Upload-Post plan |
| Your time | 30 min/week | ~0 |
| Needs a card | no | yes |
| Needs a machine on 24/7 | no | yes |

The only thing you buy with money here is **your own time**. If 30 minutes a week
is worth ₦40,000/month to you later, switch to [AUTOPILOT.md](AUTOPILOT.md) — the
videos are made exactly the same way.

---

## Getting the batch off a phone (if you don't use a computer)

- Chat UI → `/week` → tap the download link → the zip lands in your phone's
  Downloads
- Or run `cli week` on a computer and send yourself the `.mp4` files on WhatsApp
  (**Document**, not Photo, so it isn't compressed)

## Troubleshooting

| Problem | Fix |
|---|---|
| `week` stops partway | it keeps going per topic; failed topics are listed in `README.txt` |
| Captions look generic | no LLM key — add one (`[app] gemini_api_key`) or accept the fallback |
| Video is a plain title card | `[bot] demo_mode` is still `true` |
| Instagram rejects the upload | account is Personal — switch to Creator/Business |
| Facebook "no pages" | create a Page first; personal profiles can't receive posts |
| YouTube won't schedule | the Google account needs phone + 2-step verification |
| TikTok upload greyed out | verify the account by email first; scheduling needs a complete profile |

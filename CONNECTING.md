# Connecting your accounts — and proving it worked

Written for **your** Upload-Post account, as it looks right now:

| What you already have | Value |
|---|---|
| Account | `Geekyacedigitalhub` |
| Profile | **`default`** ← this is `upload_post_username` |
| Connected | ✅ **Facebook** · ⚠️ **Instagram** (shows a red ✕ — needs reconnecting) |
| Blocked | ❌ **TikTok** — *"Connecting to TikTok is not available for users on the Free plan"* |
| Not connected | ❌ YouTube |
| API key | `Geekyacedigitalhub` — **Active** |
| API calls this cycle | **0 / 10** (resets on the 1st) |
| Video processing | 0.0 / 30.0 min |
| Plan | Free — max **2 profiles** (you use 1) |

Two parts: [connect](#part-1--connecting) (one time), [prove it](#part-2--proving-it-works) (30 seconds, any time).

---

# Part 1 — Connecting

## The one thing to understand first

You don't put social passwords in `config.toml`. You connect each account **once**
inside Upload-Post, then give this bot the key to that profile:

```
Your accounts ──(you click Connect once)──► Upload-Post ◄──(API key)── this bot
```

Three objects live in the dashboard:

| Object | What it is | Where it goes |
|---|---|---|
| **Account** | Your login at upload-post.com | Nowhere — dashboard only |
| **Profile** | A named container of connected accounts | `[app] upload_post_username` — **yours is `default`** |
| **API key** | Password for the bot | `[app] upload_post_api_key` |

> The classic mistake: putting your **email** or a social handle in
> `upload_post_username`. It must be the profile name — `default`. The bot detects
> this and lists the real profile names.

## Step 1 — Fix the Instagram red ✕

On **https://app.upload-post.com/manage-users**:

1. Open the profile **`default`**.
2. Find the **Instagram** row — it displays *Geekyace Digital Hub* with a **red ✕**.
   Facebook on the same row is green. That means the Instagram authorisation was
   started and never completed, so Instagram uploads will be **skipped**.
3. Click **Connect / Reconnect** next to Instagram → log in → approve.
4. Instagram must be a **Creator or Business** account (in the Instagram app:
   *Settings → Account type and tools → Switch to professional account*).
   Personal accounts cannot be posted to by any API.

Back on the page, Instagram must show a **green tick** and an `@handle`.

## Step 2 — Connect YouTube

Same page, same profile: click **Connect** on **YouTube** → Google sign-in →
pick the channel.

Before you do, make sure the Google account has:

- **Phone verification**, and
- **2-Step Verification**

Without both, Google accepts the connection and then rejects the upload.

## Step 3 — Decide about TikTok (read this once, it saves an hour)

**TikTok cannot be connected on the Free plan.** Upload-Post says so in a yellow
banner, and no amount of re-clicking changes it. Your options:

| Option | Cost | Result |
|---|---|---|
| **Upgrade Upload-Post** | paid plan | TikTok connects and the bot publishes to it automatically |
| **Stay on Free** | ₦0 | Bot publishes to Facebook / Instagram / YouTube; you post to TikTok manually from the download button (≈2 min) |
| **Wait** | ₦0 | Nothing changes — it is a plan gate, not a bug |

The bot is honest about this: `/connect` shows TikTok with
*"requires a paid Upload-Post plan"*, not *"never connected"*.

## Step 4 — Paste two values into `config.toml`

```toml
[app]
upload_post_enabled = true
upload_post_api_key = "eyJhbGciOi…"      # the key named Geekyacedigitalhub
upload_post_username = "default"          # ← the PROFILE name, not your email
upload_post_platforms = ["facebook", "instagram", "youtube", "tiktok"]

# YouTube-only
upload_post_youtube_privacy_status = "unlisted"   # use "public" once you trust it
upload_post_youtube_made_for_kids = false
```

Then restart the bot:

```bash
python -m app.bot.cli serve
```

> **Use `unlisted` for the first YouTube test.** The upload is real, the link
> works, and nothing lands on your public channel until you change it.

## Your call budget — the part that bites people

You get **10 API calls per cycle**, separate from your 30 minutes of video
processing. Every publish costs **1 call per upload batch**. So:

- Never spam `/connect` — the bot **caches the result for 5 minutes** and reuses it.
- `/connect` buttons refresh only when *you* tap them.
- A test post costs **1 call**. Budget it, don't loop it.
- 10 calls is roughly *10 posts*, not 10 checks. Verify with the free levels first.

---

# Part 2 — Proving it works

Never judge by "check your feed". Four levels, cheapest first.

## Level 0 — Free: is the config complete? (0 API calls)

```bash
python -m app.bot.cli status
```

Expect `publishing : LIVE via upload-post`. If it says `SIMULATED (...)`, the
bracketed text names the exact missing setting.

## Level 1 — Free: what does the bot *think* is connected? (0 or 1 call)

In the web chat tap **🔌**, in Telegram send **`/connect`**, or run:

```bash
python -m app.bot.cli doctor          # add --refresh to ignore the cache
```

Against your account it should look like:

```
✅ Connected to profile `default`
Plan allows 2 profile(s).

✅ Facebook  — connected as Geekyace Digital Hub
❌ Instagram — connection incomplete
❌ YouTube   — never connected
❌ TikTok    — Upload-Post does not allow connecting TikTok on the Free plan —
               upgrade the plan, or post to TikTok manually
```

| Symbol | Meaning | Fix |
|---|---|---|
| ✅ | Connected and usable | nothing |
| ❌ `connection incomplete` | Authorisation started, never finished | click Connect again |
| ❌ `never connected` | Never authorised on this profile | click Connect |
| ❌ `…Free plan…` | Provider refuses it on your plan | upgrade, or post manually |
| ⚠️ `login expired` | Token died (happens after months) | reconnect that one platform |
| `Profile not found` | `upload_post_username` is wrong | it lists the real names |
| `Could not reach Upload-Post` | Network/proxy — key not judged yet | check internet, `[proxy]` |

## Level 2 — 1 call: does a real post land?

```bash
python -m app.bot.cli doctor --test-post facebook
```

It renders a 6-second clip titled **CONNECTION TEST**, publishes it for real, and
prints the platform's own URL:

```
✅ Facebook: posted — https://www.facebook.com/reel/123…
```

**Open that URL.** If it loads, the whole chain is proven: key → profile →
account → upload → publication. The same thing is available as a
**🧪 Test** button after `/connect` in chat.

Do this on **Facebook or Instagram only after Step 1** — a test post to a
half-connected Instagram just burns a call.

## Level 3 — the real thing

Generate something you'd actually publish, then tap **🚀 Publish now**. The reply
lists one link per platform:

```
✅ Published: Facebook, Instagram

👥 Facebook — https://www.facebook.com/reel/123…
📸 Instagram — https://www.instagram.com/reel/Cx…
🎵 TikTok — not connected — run /connect
```

A platform with **no link** was skipped, not failed. `/connect` says why.

---

## Not failures

- **A platform is "skipped".** By design: Upload-Post posts to connected accounts
  and marks the rest `skipped`. The rest still goes out.
- **Instagram/TikTok take a minute to appear.** Accepted ≠ finished processing;
  the URL usually works before the profile grid refreshes.
- **YouTube says "processing".** Normal for a few minutes.
- **First post to a new platform is slower** — one-time token exchange.

## Real failures

| Symptom | Fix |
|---|---|
| `Profile not found` | use the exact name it lists (`default`), not your email |
| Instagram refuses | switch the IG account to Creator/Business |
| Facebook: "no pages found" | create a Facebook **Page** — personal profiles can't be posted to |
| YouTube rejected | enable phone verification + 2-Step Verification |
| `API call limit reached` | you've used the 10 calls — wait for the reset on the 1st |
| Everything ✅ but no post appears | run Level 2. If the test also fails, it's the platform, not the bot |

---

## No Upload-Post at all (zero cost)

Generate the video, tap **download** on the preview, upload it yourself in each
app. Free, ~2 minutes per platform, nothing to connect. Fine until you post daily.

Direct platform APIs are the third route: free of Upload-Post, but each needs its
own developer app and review (YouTube: hours; Meta and TikTok: weeks). The
publishing layer is isolated so that can be added without touching the bot — see
§9 of [docs/bot.md](docs/bot.md).

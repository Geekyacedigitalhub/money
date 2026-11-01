# Connecting your social media (and knowing it worked)

Two questions, answered in order:

1. [How do I connect the accounts?](#part-1--connecting) — 10 minutes, once
2. [How do I know it's working?](#part-2--knowing-it-worked) — 30 seconds, any time

---

# Part 1 — Connecting

## The one thing to understand first

You are not connecting your accounts to this code. You are connecting them **once**
to a service called Upload-Post, and then giving this bot the key to that service.

```
Your accounts  ──(you click "connect" once)──►  Upload-Post  ◄──(API key)──  this bot
```

That is why one key covers all four platforms, and why you never put a TikTok
password into `config.toml`.

There are three objects in Upload-Post:

| Object | What it is | Where it goes |
|---|---|---|
| **Account** | Your login at upload-post.com | Nowhere — just the dashboard |
| **API key** | A password for the bot | `[app] upload_post_api_key` |
| **Profile** | A named container holding your connected accounts | `[app] upload_post_username` |

> The single most common mistake: putting **your email** or the profile's display
> name in `upload_post_username`. It must be the profile's **exact name**, e.g.
> `ace_brand`. If you get this wrong the bot will tell you — see Part 2.

---

## Step 1 — Create the account and API key

1. Go to **https://app.upload-post.com** and sign up.
2. Open **https://app.upload-post.com/api-keys**
3. Click **Create API key**, name it something like `money-bot`, and **copy it now** —
   some dashboards only show it once.

The free tier includes **10 uploads per month**, which is enough to test all four
platforms and still have spare.

## Step 2 — Create a profile

1. Open **https://app.upload-post.com/manage-users**
2. Click **Create / Add user** (Upload-Post calls these "users", but they are really
   publishing profiles).
3. Name it something short and memorable, all lowercase: `ace_brand`
4. **Copy that exact name.** This is your `upload_post_username`.

## Step 3 — Connect the four platforms to that profile

Still on **Manage Users**, open the profile you just made. You will see a list of
platforms with a **Connect** button next to each.

Click each one, log in on the platform's own popup, and approve. You return to
Upload-Post with a green tick and usually an `@handle`.

| Platform | What you'll be asked | Before you start, make sure… |
|---|---|---|
| **TikTok** | Log in, authorise | The account exists. Business accounts are allowed but are excluded from TikTok's rewards programme |
| **Instagram** | Log in via Facebook, pick the account | It is a **Creator or Business** account. Personal accounts cannot be posted to by any API — switch it in the Instagram app under *Settings → Account type* |
| **Facebook** | Log in, **select a Page** | You have a **Page**. Personal profiles cannot be posted to. If you have no Page, create one (free, 2 minutes) |
| **YouTube** | Google sign-in, pick the channel | The channel has **phone verification** and **2-Step Verification** enabled, or Google will refuse the upload |

If a platform says *"no pages found"* on Facebook, you have no Page yet — that is
the usual blocker, not a bot problem.

## Step 4 — Tell the bot about them

Open `config.toml` and fill in the `[app]` block:

```toml
[app]
upload_post_enabled = true
upload_post_api_key = "paste-the-key-from-step-1"
upload_post_username = "ace_brand"        # ← the PROFILE name, not your email
upload_post_platforms = ["tiktok", "instagram", "facebook", "youtube"]

# YouTube-only settings
upload_post_youtube_privacy_status = "public"   # use "unlisted" while testing
upload_post_youtube_made_for_kids = false
```

Restart whatever is running (`python -m app.bot.cli serve`).

> **Testing tip:** set `upload_post_youtube_privacy_status = "unlisted"` for the
> first run. The video uploads, you get a working link to verify, and nothing
> appears on your channel's public feed. Change it to `public` when you're happy.

---

# Part 2 — Knowing it worked

Don't guess. There are three levels of proof, weakest to strongest.

## Level 1 — Is the config complete?

```bash
python -m app.bot.cli status
```

Look for this line:

```
publishing      : LIVE via upload-post
```

If it still says `SIMULATED (...)`, the bracketed text names the exact missing key.

## Level 2 — Are the accounts actually connected? ← the real answer

```bash
python -m app.bot.cli doctor
```

This asks Upload-Post which accounts are attached to your profile and prints one
line per platform:

```
Checking your connections…

✅ Connected to profile `ace_brand`
Plan allows 3 profile(s).

✅ TikTok — connected as @ace
✅ Instagram — connected as @ace.ig
❌ Facebook — never connected
⚠️ YouTube — connected but the login expired — reconnect it
```

Read it like this:

| Symbol | Meaning | Fix |
|---|---|---|
| ✅ | Connected and usable | Nothing |
| ❌ `never connected` | The platform was never authorised on this profile | Click Connect in Manage Users |
| ❌ `connection incomplete` | You started the authorisation and abandoned it | Click Connect again |
| ⚠️ `login expired` | The token died (common with Instagram/TikTok after a few months) | Reconnect that one platform |
| `Profile not found` | Your `upload_post_username` ≠ the profile name | It lists the real profile names for you |

You can get the same information from your phone — send **`/connect`** to the bot,
or tap the **🔌** button in the web chat. Both give buttons to fire a test post at
any platform that is connected.

## Level 3 — Does a real post actually appear? ← the proof

```bash
python -m app.bot.cli doctor --test-post tiktok
```

What happens:

1. It renders a 6-second clip labelled **CONNECTION TEST**
2. It uploads that clip to the platform you named — **a real public post**
3. It prints the platform's own URL for the post

```
✅ TikTok: posted — https://www.tiktok.com/@ace/video/7401...
```

**Open that URL.** If it loads, everything works end to end: key valid, profile
correct, account connected, upload accepted, platform published.

If it fails, the message tells you which layer broke:

| Message | Meaning |
|---|---|
| `SIMULATED — nothing was uploaded` | Credentials aren't set yet (Level 1) |
| `is not connected yet` | `doctor` found the account missing — connect it first |
| `the API key was rejected (401/403)` | Key wrong or deleted — make a new one |
| `profile has no account for that platform` | Connected on a *different* profile |
| `account_reauth_required` | Reconnect that platform in Manage Users |
| `video too short` / `aspect ratio` | The platform rejected the file — try `--test-post youtube` to isolate |

From the chat bot you can do the same thing by tapping
**🧪 Send test to TikTok** after `/connect`.

## Level 4 — The real thing

Once Level 3 passes, generate something you actually like and publish it:

```
5 things to check before renting in Lagos
```

Then tap **🚀 Publish now**. The bot replies with a link per platform:

```
✅ Published: TikTok, Instagram, Facebook, YouTube

🎵 TikTok — https://www.tiktok.com/@ace/video/7401...
📸 Instagram — https://www.instagram.com/reel/Cx...
👥 Facebook — https://www.facebook.com/reel/123...
▶️ YouTube — https://youtu.be/abc...
```

No link for a platform means that platform was **skipped** — it wasn't connected.
The bot says which, and `/connect` tells you why.

---

## Things that are not failures

- **A platform is "skipped" instead of failing.** By design: Upload-Post publishes
  to the connected platforms and marks the rest `skipped`. Your post still goes
  out elsewhere.
- **Instagram/TikTok takes a minute to appear on the profile.** The upload is
  accepted before the platform finishes processing. The URL usually works first.
- **YouTube shows "processing".** Normal for a few minutes on short uploads.
- **The first post to a new platform is slower.** Every platform does a one-time
  token exchange.

## Things that are failures, and what to do

| Symptom | Fix |
|---|---|
| `doctor` says profile not found | Use the exact profile name it lists, not your email |
| Facebook: "no pages found" | Create a Facebook Page first |
| Instagram refuses to connect | The account is Personal — switch to Creator/Business |
| YouTube upload rejected | Enable phone verification + 2-Step Verification on the Google account |
| Token expired after weeks | Expected; reconnect that platform. `doctor` warns you before it bites |
| Everything ✅ but posts don't appear | Post the test clip (Level 3). If that fails too, it's the platform, not the bot |

---

## Connecting without Upload-Post (free, manual)

If you would rather not pay for a middleman: generate the video in the bot, tap the
**download** button on the preview, and upload it yourself in each app. Zero cost,
about 2 minutes per platform, and nothing to connect. Good enough until you're
posting daily; painful after that.

Direct platform APIs are the third option — free, but each one needs its own
developer app and review. YouTube is a few hours; Meta and TikTok are weeks. The
publishing layer in this repo is written so that can be added without touching the
bot — see §9 of [docs/bot.md](docs/bot.md).

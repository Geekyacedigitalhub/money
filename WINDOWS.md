# Windows setup — make your first batch of videos

Everything here is copy-paste into **PowerShell**. Total ~15 minutes, most of it
waiting for downloads.

You do **not** need: a credit card, a server, ffmpeg, or an API key. (FFmpeg ships
inside a Python package, and the first run is a demo render.)

---

## Step 1 — Install two things

Most Windows machines already have a usable Python. Check what you have:

```powershell
py -0p
```

**Use 3.12 or 3.13** from that list — either is fine. Skip to step 2.

- **Nothing listed?** Install **3.11 or 3.12** from
  https://www.python.org/downloads/windows/ → *Windows installer (64-bit)* →
  tick **“Add python.exe to PATH”** on the first screen → *Install Now*.
- **Only 3.14?** Don't use it. The video stack (moviepy, av, faster-whisper) has no
  3.14 builds yet: the demo test would pass, then step 6 would fail with no obvious
  reason. Install 3.12 alongside it — Windows keeps both, nothing breaks.
- **Microsoft Store Python?** Avoid it; it behaves differently with virtual
  environments.

**Git for Windows** — https://git-scm.com/download/win → *“Click here to download”* →
next-next-finish (all defaults are fine).

Then close **all** PowerShell windows and open a fresh one (so PATH is picked up).
Check both:

```powershell
py -0p          # every Python on this machine, with its path
git --version
```

`py -0p` should list at least one of `-V:3.12` or `-V:3.13`. Use the newest one
that is **not** 3.14 in the commands below — everywhere this page says `-3.12`,
substitute yours.

---

## Step 2 — Get the code

```powershell
cd $HOME
git clone https://github.com/Geekyacedigitalhub/money.git
cd money
```

You now have a folder `C:\Users\<you>\money`.

> Check your prompt says `PS C:\Users\<you>\money>`. If it still says
> `C:\WINDOWS\system32`, the `cd` did not run — do not clone inside system32.

---

## Step 3 — Create the environment (once)

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
```

If PowerShell refuses with **“running scripts is disabled on this system”**, run this
once and try again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Your prompt should now start with `(.venv)`.

Install the small set of packages needed to render a video:

```powershell
pip install pillow imageio-ffmpeg loguru toml requests
```

Takes under a minute. (Only these five are needed to render videos; `fastapi` is
only for the optional chat server in the last section.)

---

## Step 4 — Make your first two videos

```powershell
python -m app.bot.cli week --count 2 --name first-test
```

Each video takes a few seconds in demo mode (a real one takes minutes). When it
finishes:

```
✅ 2 video(s) → C:\Users\<you>\money\storage\bot\exports\first-test
📦 ...\storage\bot\exports\first-test.zip
```

`config.toml` is optional at this stage — without it the bot uses its defaults
(demo render, built-in topics). You only create it in step 6.

Open the folder:

```powershell
start storage\bot\exports\first-test
```

You'll see `01-*.mp4`, `01-*.txt` (the caption), `schedule.csv`, `README.txt`.

**Play the MP4.** It will be a plain title card with `DEMO` on it — that is correct
for this step. It proves the whole chain works on your machine.

> To be clear: this is a placeholder video, not a real one. Step 6 makes real ones.

---

## Step 5 — Point it at your topic

```powershell
python -m app.bot.cli topics add "Why your first 100 customers come from WhatsApp" "The 3 invoices every freelancer should send"
python -m app.bot.cli topics
```

`topics` lists what the batch will work through. Add as many as you like — one per
line, saved to `storage\bot\autopilot\topics.txt`.

---

## Step 6 — Real videos (needs the full install + 2 free keys)

Stop and do this only when step 4 worked.

**a) The full package set** (bigger — 5–10 minutes):

```powershell
pip install -r requirements.txt
```

**b) Two free keys**

- Footage: https://www.pexels.com/api/ → copy the key
- Scripts: https://aistudio.google.com/app/apikey → *Create API key* → copy

**c) Put them in the config**

```powershell
notepad config.toml
```

Find `[app]` and set (keep everything else as it is):

```toml
llm_provider = "gemini"
gemini_api_key = "paste-the-gemini-key"

video_source = "pexels"
pexels_api_keys = ["paste-the-pexels-key"]
```

Find `[bot]` and set:

```toml
owner_name = "GeekyAce"
demo_mode = false
delivery = "manual"
```

Save, close Notepad.

**d) Make the real batch**

```powershell
python -m app.bot.cli week --count 7
```

Now the videos have a real voiceover, real stock footage and subtitles. Expect
**3–6 minutes per video** — leave it running and check back.

---

## Step 7 — Post them (free, ~30 minutes once a week)

Full instructions with the links: [SCHEDULING.md](SCHEDULING.md)

Short version:

| Where | Link |
|---|---|
| Instagram + Facebook | https://business.facebook.com/latest/scheduler |
| YouTube | https://studio.youtube.com |
| TikTok | https://www.tiktok.com/tiktokstudio/upload |

Each video has a `.txt` with the caption already split by platform, and
`schedule.csv` tells you which video at which time. Upload them all in one sitting,
schedule them, done for the week.

---

## Everyday commands (after setup)

Open PowerShell in the folder, activate, run:

```powershell
cd $HOME\money
.venv\Scripts\Activate.ps1

python -m app.bot.cli status                 # is everything configured?
python -m app.bot.cli week --count 7         # this week's batch
python -m app.bot.cli topics add "another topic"
start storage\bot\exports                    # open the finished videos
```

Prefer a chat window instead of the command line?

```powershell
python -m app.bot.cli serve --bot-only
```

Then open http://127.0.0.1:8080/bot in your browser — send `/week` there and it
gives you a download link instead.

---

## If something goes wrong

| Problem | Fix |
|---|---|
| `python` is not recognised | Python installed without PATH — reinstall and tick the box, or use `py -3.12` instead of `python` |
| `MoviePy`/`av`/`faster-whisper` fails to build | you are on Python 3.14 — install 3.11 and `py -3.12 -m venv .venv` |
| Prompt shows `>>` instead of running | an unfinished command; press `Ctrl+C` then paste the single line again |
| `Activate.ps1` cannot be loaded, running scripts is disabled | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `ffmpeg is not available` | only happens if `imageio-ffmpeg` didn't install — `pip install imageio-ffmpeg` |
| Video is a title card with `DEMO` | `[bot] demo_mode` is still `true` — step 6 |
| `Generation failed … api_key is not set` | the Gemini key is missing or has a typo |
| Videos are slow | normal for real renders; close other heavy apps |
| `pip` is slow or fails | `python -m pip install --upgrade pip` then retry |
| Antivirus flags the ffmpeg download | allow it; it's the official `imageio-ffmpeg` binary |

Still stuck? Paste the last 20 lines of the error and the output of
`python -m app.bot.cli status`.

---

**Next:** [SCHEDULING.md](SCHEDULING.md) (posting) · [docs/bot.md](docs/bot.md)
(the code) · [AUTOPILOT.md](AUTOPILOT.md) (if you later get a server and want it
fully automatic)

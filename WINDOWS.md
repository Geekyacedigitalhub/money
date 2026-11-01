# Windows setup — make your first batch of videos

Everything here is copy-paste into **PowerShell**. Total ~15 minutes, most of it
waiting for downloads.

You do **not** need: a credit card, a server, ffmpeg, or an API key. (FFmpeg ships
inside a Python package, and the first run is a demo render.)

---

## Step 1 — Install two things

**Python 3.12** — https://www.python.org/downloads/windows/

- Download **Windows installer (64-bit)**
- On the first screen, **tick “Add python.exe to PATH”** ← the one thing people miss
- Then *Install Now*

> Don't use the Microsoft Store version. It behaves differently with virtual
> environments.

**Git for Windows** — https://git-scm.com/download/win → *“Click here to download”* →
next-next-finish (all defaults are fine).

Then close **all** PowerShell windows and open a fresh one (so PATH is picked up).
Check both:

```powershell
python --version
git --version
```

You should see `Python 3.12.x` and `git version 2.x`.

---

## Step 2 — Get the code

```powershell
cd $HOME
git clone https://github.com/Geekyacedigitalhub/money.git
cd money
```

You now have a folder `C:\Users\<you>\money`.

---

## Step 3 — Create the environment (once)

```powershell
python -m venv .venv
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
pip install fastapi "uvicorn[standard]" pydantic loguru toml requests python-multipart pillow imageio-ffmpeg
```

Takes 1–3 minutes.

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
| `python` is not recognised | Python installed without PATH — reinstall and tick the box, or use `py` instead of `python` |
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

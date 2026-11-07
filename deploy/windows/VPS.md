# Run the bot on a Windows VPS (RDP)

Your server is Windows, not Linux, so `deploy/setup-vps.sh` and everything in
`DEPLOY.md` does not apply. Nothing is lost: you already installed this on your
own PC, and the server takes the same steps. The difference is that it never
turns off.

> **Change the password first.** It arrived in an email, and if that email (or a
> screenshot of it) has been shared, anyone can log in as Administrator. From
> inside the server: `Ctrl+Alt+End` -> "Change a password", or
> `net user Administrator *` in PowerShell. Do it before anything else.

---

## 1. Connect and check the machine

Open **Remote Desktop Connection** on your PC, enter the address **including the
port** (`1.2.3.4:48006`), user `Administrator`, and the password from the email.

Once the desktop is up, open PowerShell and check what you are working with:

```powershell
Get-CimInstance Win32_ComputerSystem | Select-Object @{n='RAM_GB';e={[math]::Round($_.TotalPhysicalMemory/1GB,1)}}
Get-PSDrive C | Select-Object @{n='Free_GB';e={[math]::Round($_.Free/1GB,1)}}
```

| RAM | Verdict |
|---|---|
| 8 GB+ | everything works, use `whisper_model_size=small` and forget it |
| 4 GB | fine with `small` |
| 2 GB | tight. Render one video at a time, expect slow |
| 1 GB | not enough for video rendering. Resize the plan |

Free disk under 10 GB is a problem: the repo, the models and the output need
roughly 6 GB together.

## 2. Install Python 3.12

```powershell
winget install --id Python.Python.3.12 -e --source winget
```

If `winget` is missing (older Server images):

```powershell
Invoke-WebRequest https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe -OutFile $env:TEMP\py.exe
& $env:TEMP\py.exe /quiet InstallAllUsers=1 PrependPath=1 Include_pip=1
```

Close and reopen PowerShell, then confirm:

```powershell
py -0p
```

## 3. Install git, then the project

```powershell
winget install --id Git.Git -e --source winget
```

Reopen PowerShell again, then:

```powershell
cd C:\
git clone https://github.com/Geekyacedigitalhub/money.git
cd C:\money
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

That is the same 10–20 minute download you did on your PC.

## 4. Copy your settings across

Your `config.toml` already has the keys. Rather than typing them again, open it
on your PC, copy the whole file, and paste it into `C:\money\config.toml` on the
server (`notepad C:\money\config.toml`, paste, save).

Then set the two things that only matter on a small server:

```powershell
python -m app.bot.cli set whisper_model_size=small whisper_compute_type=int8
```

```powershell
python -m app.bot.cli status
```

It should say `Ready` — or name exactly what is still missing.

## 5. Prove it renders, once, while you watch

```powershell
python -m app.bot.cli week --count 1
```

This is the moment of truth for the RAM question. Watch it in Task Manager.

*If it dies with no message, or Windows freezes*, the problem is memory, not the
code: add swap or resize the plan. Tell me what you see and I will say which.

## 6. Make it run by itself, forever

The point of the server. One command, from an **Administrator** PowerShell:

```powershell
cd C:\money
powershell -ExecutionPolicy Bypass -File deploy\windows\install-autostart.ps1
```

It registers a scheduled task called `PostBot` that starts at boot, runs whether
or not anyone is logged in, and restarts if the bot crashes. Closing the RDP
window no longer stops anything.

Check it is alive:

```powershell
Invoke-RestMethod http://localhost:8080/api/bot/health
Get-Content C:\money\storage\logs\bot.log -Tail 20
```

## 7. Connect YouTube and Facebook (free, no subscription)

```powershell
python -m app.bot.cli connect youtube
python -m app.bot.cli connect facebook
```

Both open a browser flow. In an RDP session the browser opens **on the server**,
which is fine. If you would rather use your own PC's browser for the Google
consent, copy the printed URL across and paste the `code=` value back on the
server.

Then:

```powershell
python -m app.bot.cli set delivery=direct
python -m app.bot.cli status
```

## 8. Checking on it without RDP

The bot serves a chat UI on port 8080. To reach it from your phone or laptop, open
port 8080 in the provider's firewall panel, then browse to
`http://YOUR-IP:8080/bot`.

Do that only with the password set (`[bot] web_password`), and prefer a domain
with HTTPS later — a bare port with no TLS sends your password in clear text.

---

## Day-to-day

| Want to | Command (over RDP) |
|---|---|
| see what the scheduler will do | `python -m app.bot.cli status` |
| watch it work | `Get-Content C:\money\storage\logs\bot.log -Tail 40 -Wait` |
| stop it | `Stop-ScheduledTask -TaskName PostBot` |
| start it again | `Start-ScheduledTask -TaskName PostBot` |
| remove autostart | `.\deploy\windows\install-autostart.ps1 -Uninstall` |
| make a batch by hand | `python -m app.bot.cli week --count 7` |

## When something is wrong

| Symptom | Cause | Fix |
|---|---|---|
| task state `Ready`, nothing happens | wrong `-RootDir` | re-run the installer with `-RootDir C:\money` |
| `ModuleNotFoundError` in the log | venv half-installed | `pip install -r requirements.txt` again |
| bot dies during render | RAM | lower `whisper_model_size`, close other apps, resize |
| YouTube `quotaExceeded` | 6 uploads/day | let the rest go tomorrow |
| nothing posts | `delivery` still `manual` or `upload_post` | `cli set delivery=direct` |

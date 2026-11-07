"""机器人命令行入口。

    python -m app.bot.cli status
    python -m app.bot.cli serve --port 8080 --telegram
    python -m app.bot.cli ask "Inflation in Nigeria" --yes
    python -m app.bot.cli publish ./my-video.mp4 --platforms tiktok,instagram

``serve`` 会同时提供原有的 REST API、WebUI 静态资源和新的 ``/bot`` 聊天页面。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from loguru import logger

from app.bot import doctor
from app.bot.config import load_settings, publish_account_ready
from app.bot.models import (
    ALL_PLATFORMS,
    ChatMessage,
    JobState,
    Platform,
    parse_platform,
)


def _platform_list(value: str) -> list[Platform]:
    found: list[Platform] = []
    for token in str(value or "").replace(";", ",").split(","):
        token = token.strip()
        if not token:
            continue
        platform = parse_platform(token)
        if platform is None:
            raise argparse.ArgumentTypeError(f"unknown platform: {token}")
        if platform not in found:
            found.append(platform)
    return found or list(ALL_PLATFORMS)


def cmd_status(_: argparse.Namespace) -> int:
    settings = load_settings()
    ready, reason = publish_account_ready()

    print(settings.display_name)
    print("─" * 46)
    if settings.greeting_name:
        print(f"owner           : {settings.greeting_name}")
    print(f"generation mode : {'DEMO (placeholder renders)' if settings.demo_mode else 'LIVE'}")
    print(f"video source    : {settings.video_source}")
    print(f"voice           : {settings.voice_name}")
    print(f"aspect          : {settings.video_aspect}")
    print(f"auto-publish    : {'on' if settings.auto_publish else 'off (you approve each post)'}")
    print(f"telegram token  : {'configured' if settings.telegram_token else 'missing'}")
    print(
        "publishing      : "
        + ("LIVE via upload-post" if ready else f"SIMULATED ({reason})")
    )
    print("default targets : " + ", ".join(p.value for p in settings.platforms))
    from app.bot.config import is_temporary_url

    if not settings.public_url:
        link_note = "(not set — see DEPLOY.md)"
    elif is_temporary_url(settings.public_url):
        link_note = f"{settings.public_url}  ← temporary preview, NOT your link"
    else:
        link_note = settings.public_url
    print("public link     : " + link_note)
    if settings.allowed_users:
        print("allow-list      : " + ", ".join(settings.allowed_users))
    print()
    try:
        from app.bot.autopilot import Autopilot

        status = Autopilot(settings=settings).status()
        plan = (
            f"every {status['interval_minutes']} min"
            if status["interval_minutes"]
            else "at " + ", ".join(status["times"])
        )
        print(
            f"autopilot       : {status['mode']} · {plan} · "
            f"{status['runs_today']}/{status['max_per_day']} today"
            + (" · PAUSED" if status["paused"] else "")
        )
        if status["next_run"]:
            print(f"next run        : {status['next_run']} ({status['timezone']})")
        blockers = [item for item in status.get("blockers") or [] if item["key"] != "delivery"]
        if blockers:
            print("to go live      :")
            for item in blockers:
                print(f"  • {item['why']}")
    except Exception as exc:  # noqa: BLE001 - status 不应因为自动运营出错而失败
        print(f"autopilot       : unavailable ({exc})")
    print()
    print("Open the chat UI at  http://127.0.0.1:8080/bot  after `serve`.")
    return 0


def _build_bot_only_app():
    from fastapi import FastAPI

    from app.bot.web import router as bot_router

    instance = FastAPI(
        title="MoneyPrinterTurbo Bot",
        description="Chat bot that generates and publishes short videos.",
        version="1.0.0",
    )
    instance.include_router(bot_router)
    return instance


def cmd_serve(args: argparse.Namespace) -> int:
    settings = load_settings()
    host = args.host or os.getenv("MPT_BOT_HOST", "0.0.0.0")
    port = int(args.port or os.getenv("MPT_BOT_PORT", "8080"))

    if getattr(args, "autopilot", False) or settings.autopilot_enabled:
        from app.bot.autopilot import Autopilot

        auto = Autopilot(settings=settings, publish=settings.autopilot_publish)
        _show_info_logs()
        auto.start_background()
        status = auto.status()
        print(
            f"Autopilot started ({status['mode']}, max {status['max_per_day']}/day, "
            f"next {status['next_run'] or 'paused'})",
            flush=True,
        )

    # 配了 token 就自动开 Telegram：少一步手动操作，也不会出现“配好了却没生效”。
    want_telegram = bool(getattr(args, "telegram", False) or settings.telegram_token)
    if want_telegram:
        from app.bot.telegram import TelegramBot

        bot = TelegramBot(settings)
        threading_import = __import__("threading")
        worker = threading_import.Thread(target=bot.run, name="mpt-bot-telegram", daemon=True)
        worker.start()
        print("Telegram polling started (see logs).", flush=True)

    import uvicorn

    if args.bot_only:
        # 只跑聊天页面和它的接口。不加载 Streamlit / MoviePy / Whisper 这一整套
        # 媒体依赖，因此在 512MB 的小机器上也能启动；需要真实生成视频时再用完整
        # 模式（``serve`` 不带 --bot-only）。
        app_target = _build_bot_only_app()
        print("Mode: bot-only (no WebUI / REST API)", flush=True)
    else:
        app_target = "app.asgi:app"

    print(f"Chat UI  →  http://127.0.0.1:{port}/bot", flush=True)
    if not args.bot_only:
        print(f"API docs →  http://127.0.0.1:{port}/docs")
    uvicorn.run(
        app_target,
        host=host,
        port=port,
        log_level="warning",
        # 反向代理（含云沙箱预览域名）会改写 scheme/host；信任转发头之后
        # 同源校验才能正确匹配浏览器发来的 Origin。
        proxy_headers=bool(args.trust_proxy),
        forwarded_allow_ips="*" if args.trust_proxy else "127.0.0.1",
    )
    return 0


def _wait_for(job_id: str, core, timeout: float = 1800) -> None:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        job = core.job(job_id)
        if job is None:
            break
        if job.state != last:
            last = job.state
            print(f"  … {job.state.value} {job.progress}%")
        if job.state in {JobState.ready, JobState.published, JobState.failed}:
            break
        time.sleep(1.5)


def cmd_ask(args: argparse.Namespace) -> int:
    from app.bot.core import build_core

    core = build_core()
    replies = core.handle(ChatMessage(chat_id="cli", text=args.topic, user_id="cli"))
    for reply in replies:
        if reply.text:
            print(reply.text)
    job_id = replies[0].job_id if replies and replies[0].job_id else ""
    if not job_id:
        return 1
    _wait_for(job_id, core)
    job = core.job(job_id)
    if job is None or job.state is JobState.failed:
        print(f"failed: {job.error if job else 'unknown'}", file=sys.stderr)
        return 1
    print(f"\nvideo: {job.video}")
    if args.yes:
        core.handle(ChatMessage(chat_id="cli", callback=f"pub:{job.id}", user_id="cli"))
        _wait_for(job.id, core)
        for line in (core.job(job.id).results or {}).items():
            print(f"  {line[0]}: {line[1]}")
    return 0


def cmd_publish(args: argparse.Namespace) -> int:
    from app.bot.core import build_core

    path = os.path.abspath(args.video)
    if not os.path.isfile(path):
        print(f"error: file not found: {path}", file=sys.stderr)
        return 2

    core = build_core()
    session = core.session("cli")
    session.platforms = [item.value for item in _platform_list(args.platforms)]
    replies = core.handle(ChatMessage(chat_id="cli", attachment=path, user_id="cli"))
    job_id = replies[0].job_id if replies and replies[0].job_id else ""
    if not job_id:
        print("error: could not prepare the upload", file=sys.stderr)
        return 1
    for line in replies[0].text.splitlines():
        print(line)
    if args.yes:
        core.handle(ChatMessage(chat_id="cli", callback=f"pub:{job_id}", user_id="cli"))
        _wait_for(job_id, core)
        job = core.job(job_id)
        for platform, status in (job.results or {}).items():
            print(f"  {platform}: {status}")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """强制在演示模式下跑一次端到端，用于验证安装。"""
    os.environ["MPT_BOT_DEMO_MODE"] = "true"
    from app.bot.core import build_core

    core = build_core()
    topic = args.topic or "Demo: how the bot works"
    replies = core.handle(ChatMessage(chat_id="cli", text=topic, user_id="cli"))
    job_id = replies[0].job_id if replies else ""
    if not job_id:
        return 1
    _wait_for(job_id, core)
    job = core.job(job_id)
    if job is None or job.state is not JobState.ready:
        print(f"failed: {job.error if job else 'unknown'}", file=sys.stderr)
        return 1
    print(f"\ndemo video: {job.video}")
    print("\ncaptions:")
    for platform, caption in job.captions.items():
        print(f"  [{platform}] {caption.title}")
    return 0


def _show_info_logs() -> None:
    """常驻模式下把 INFO 日志放出来，否则用户看不到“下一次什么时候跑”。"""
    try:
        logger.remove()
        logger.add(sys.stderr, level="INFO")
    except Exception:  # noqa: BLE001, S110 - 日志系统不可用时保持默认
        pass


def _autopilot(args: argparse.Namespace):
    from app.bot.autopilot import Autopilot

    settings = load_settings()
    overrides: dict = {}
    if args.interval is not None:
        overrides["autopilot_interval_minutes"] = max(0, args.interval)
    if args.max_per_day is not None:
        overrides["autopilot_max_per_day"] = max(1, args.max_per_day)
    if args.topic_file:
        overrides["autopilot_topics_file"] = args.topic_file
    if overrides:
        settings = settings.with_overrides(**overrides)

    return Autopilot(
        settings=settings,
        topics_path=args.topic_file,
        publish=(not args.no_publish) and settings.autopilot_publish,
        dry_run=args.dry_run,
    )


def cmd_autopilot(args: argparse.Namespace) -> int:
    """按计划自动生成并发布，直到 Ctrl+C。"""
    auto = _autopilot(args)
    if args.pause or args.resume:
        auto.state.paused = bool(args.pause)
        auto.save()
        print("paused" if args.pause else "resumed")
        return 0
    status = auto.status()

    if not (args.once or args.topic):
        plan = (
            f"every {status['interval_minutes']} minutes"
            if status["interval_minutes"]
            else "at " + ", ".join(status["times"])
        )
        print(f"Autopilot  ·  {status['mode']}  ·  {plan}  ·  max {status['max_per_day']}/day")
        print(f"timezone   : {status['timezone']}")
        print(f"queue      : {status['queue_size']} topics ({'yours' if status['custom_topics'] else 'built-in'})")
        print(f"next run   : {status['next_run'] or 'paused'}")
        if status["mode"] == "rehearsal":
            print()
            print("REHEARSAL: videos are generated and recorded but NOT published.")
            blockers = status.get("blockers") or []
            if blockers:
                print("To go fully live, fix these:")
                for item in blockers:
                    print(f"  • {item['why']}")
                    print(f"    → {item['fix']}")
        print()
        print("Ctrl+C to stop.")

    if not (args.once or args.topic):
        _show_info_logs()

    stop = __import__("threading").Event()
    if args.once or args.topic:
        result = auto.run_once(args.topic or "")
        if result.get("skipped"):
            print(f"skipped: {result.get('reason')}")
            return 0
        if not result.get("ok"):
            print(f"❌ {result.get('error') or 'failed'}", file=sys.stderr)
            return 1
        print(f"topic   : {result.get('topic')}")
        print(f"video   : {result.get('video')}")
        if result.get("published"):
            for platform, value in (result.get("results") or {}).items():
                print(f"  {platform}: {value}")
        else:
            print(f"publish : no ({result.get('note') or 'not published'})")
        return 0

    try:
        auto.loop(stop)
    except KeyboardInterrupt:
        stop.set()
        print("\nstopped")
    return 0


def cmd_topics(args: argparse.Namespace) -> int:
    """管理自动运营的选题清单。"""
    auto = _autopilot(args)
    action = args.action
    if action == "add":
        added = auto.add_topics(args.topic or [])
        print(f"saved. {added} topics queued at {auto.topics_path}")
    elif action == "remove":
        remaining = auto.remove_topic(int(args.index or 0))
        print(f"removed. {remaining} left")
    elif action == "clear":
        open(auto.topics_path, "w", encoding="utf-8").close()
        print("cleared — the built-in rotating topics will be used again")

    pool = auto.topics()
    source = "your file" if auto._read_topic_file() else "built-in rotation"
    print(f"\n{len(pool)} topics ({source}):")
    for index, topic in enumerate(pool[:40], start=1):
        marker = "→" if topic == auto.next_topic() and auto._read_topic_file() else " "
        print(f" {marker} {index:2d}. {topic}")
    if len(pool) > 40:
        print(f"    … and {len(pool) - 40} more")
    return 0


def cmd_week(args: argparse.Namespace) -> int:
    """一次做出几天的量，打包成可直接上传的文件夹。"""
    from app.bot import export
    from app.bot.autopilot import Autopilot
    from app.bot.core import build_core

    settings = load_settings()
    count = max(1, int(args.count))

    # 真实渲染动辄几分钟一条。缺 Key 时先停下来，而不是让它跑到一半失败。
    from app.bot.autopilot import preflight

    if not settings.demo_mode:
        blockers = {item["key"]: item for item in preflight(settings)}
        fatal = [
            blockers[key]
            for key in ("llm", "footage")
            if key in blockers
        ]
        if fatal:
            print("Cannot make real videos yet — demo_mode is off but:", file=sys.stderr)
            for item in fatal:
                print(f"  • {item['why']}", file=sys.stderr)
                print(f"    → {item['fix']}", file=sys.stderr)
            print()
            print(
                "Fix those in config.toml, or set [bot] demo_mode = true for "
                "placeholder renders.",
                file=sys.stderr,
            )
            return 2

    topics = list(args.topic or [])
    if not topics:
        auto = Autopilot(settings=settings)
        pool = auto.topics()
        start = int(auto.state.cursor) % len(pool) if pool else 0
        topics = [pool[(start + index) % len(pool)] for index in range(count)]

    core = build_core()

    def progress(index: int, total: int, topic: str) -> None:
        print(f"[{index}/{total}] {topic}", flush=True)

    print(f"Making {len(topics)} video(s). Each one takes a few minutes.\n")
    result = export.batch_from_topics(core, topics, name=args.name, on_progress=progress)

    print()
    for failure in result.get("errors") or []:
        print(f"✗ {failure['topic']}")
        for line in str(failure["error"]).splitlines():
            print(f"    {line}")

    if not result["count"]:
        print(f"✗ No videos were made ({result['failed']} failed).")
        print(f"  Folder: {result['folder']}")
        print()
        print("Common causes:")
        print("  • the LLM rejected the request — check the model your provider key can")
        print("    actually use (free Gemini tiers exclude the pro models):")
        print("    python -m app.bot.cli set gemini_model_name=gemini-3.8-flash")
        print("  • the footage search returned nothing — try a broader [bot] autopilot_niche")
        print("  • the voice service was unreachable — run it again")
        return 1

    print(f"✅ {result['count']} video(s) → {result['folder']}")
    if result["zip"]:
        print(f"📦 {result['zip']}")
    if result["failed"]:
        print(f"⚠️  {result['failed']} failed")
    print()
    print("Next: upload them with the platforms' own free schedulers —")
    print("  Instagram + Facebook  business.facebook.com/latest/scheduler")
    print("  YouTube               studio.youtube.com")
    print("  TikTok                tiktok.com/upload")
    print("Times are in schedule.csv. Walkthrough: SCHEDULING.md")
    return 0 if result["count"] else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    """检查每个平台到底连上了没有，可选发一条真实的测试帖。"""
    settings = load_settings()
    print("Checking your connections…\n")
    report = doctor.check_connection(settings, refresh=args.refresh)
    print(doctor.format_report(report, markdown=False))
    print()

    if not args.test_post:
        if report.all_connected:
            print("Verified. To prove it end-to-end, post a real test clip:")
            print("  python -m app.bot.cli doctor --test-post tiktok")
        return 0 if report.all_connected else 1

    platform = parse_platform(args.test_post)
    if platform is None:
        print(f"error: unknown platform '{args.test_post}'", file=sys.stderr)
        print("choose one of: " + ", ".join(item.value for item in ALL_PLATFORMS))
        return 2

    status = report.status_for(platform)
    if not status.connected or status.needs_reauth:
        print(f"{platform.label} is not connected yet — connect it first:")
        print(f"  {doctor.DASHBOARD_MANAGE_USERS}")
        return 1

    print(f"Posting a short test clip to {platform.label}… (this is a real post)")
    result = doctor.send_test_post(settings, platform)
    print("  " + result.summary)
    return 0 if result.success else 1


# 这些键属于 [app]，其余属于 [bot]。用户不必知道 900 行的 config.toml 长什么样。
_APP_KEYS = {
    "llm_provider",
    "video_source",
    "video_aspect",
    "voice_name",
    "subtitle_enabled",
    "paragraph_number",
    "pexels_api_keys",
    "pixabay_api_keys",
    "coverr_api_keys",
}
_APP_SUFFIXES = ("_api_key", "_api_keys", "_model_name", "_base_url")

# 直连发布用的应用凭据不属于 [app]，也不属于 [bot]。
_DIRECT_KEYS = {
    "youtube_client_id",
    "youtube_client_secret",
    "youtube_channel_id",
}

# 友好名字 → (真正的段落, 真正的键)。用户在 VPS 上没有编辑器可用，只能靠这一条命令。
_ALIASES: dict[str, tuple[str, str]] = {
    "whisper_model_size": ("whisper", "model_size"),
    "whisper_device": ("whisper", "device"),
    "whisper_compute_type": ("whisper", "compute_type"),
}


def _toml_value(key: str, value: str) -> str:
    text = value.strip()
    low = text.lower()
    if low in {"true", "false"}:
        return low
    if key.endswith("_keys"):
        items = [part.strip().strip('"').strip("'") for part in text.split(",")]
        items = [item for item in items if item]
        return "[" + ", ".join(f'"{item}"' for item in items) + "]"
    if text.replace(".", "", 1).isdigit():
        return text
    return '"' + text.replace('"', "'") + '"'


def _section_for(key: str) -> str:
    if key in _DIRECT_KEYS:
        return "direct"
    if key in _ALIASES:
        return _ALIASES[key][0]
    if key in _APP_KEYS or key.endswith(_APP_SUFFIXES):
        return "app"
    return "bot"


def _config_path() -> Path:
    from app.config import config as runtime_config

    root = getattr(runtime_config, "root_dir", None) or os.getcwd()
    return Path(root) / "config.toml"


def _llm_provider_for(key: str) -> str | None:
    """``gemini_api_key`` 说明用户想用 gemini —— 顺手把 llm_provider 也改掉。

    否则会出现最迷惑的情况：Key 明明填了，检查还说“没有 LLM Key”，因为
    llm_provider 还停在默认的 moonshot 上。
    """
    if not key.endswith("_api_key"):
        return None
    candidate = key[: -len("_api_key")]
    from app.models.llm_provider import LLM_PROVIDERS
    return candidate if candidate in LLM_PROVIDERS else None


def set_config_values(pairs: list[str]) -> list[tuple[str, str]]:
    """把 key=value 写进 config.toml 正确的段落，返回改动了什么。"""
    explicit = {pair.split("=", 1)[0].strip() for pair in pairs if "=" in pair}
    implied = [
        (key, _llm_provider_for(key))
        for key in (pair.split("=", 1)[0].strip() for pair in pairs if "=" in pair)
    ]
    for key, provider in implied:
        if provider and "llm_provider" not in explicit:
            pairs = list(pairs) + [f"llm_provider={provider}"]
            explicit.add("llm_provider")
    from app.config import config as runtime_config

    path = _config_path()
    if not path.exists():
        example = path.parent / "config.example.toml"
        path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")

    lines = path.read_text(encoding="utf-8").splitlines()
    changed: list[tuple[str, str]] = []

    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"expected key=value, got {pair!r}")
        key, value = pair.split("=", 1)
        key = key.strip()
        section = _section_for(key)
        key = _ALIASES.get(key, (section, key))[1]
        rendered = f"{key} = {_toml_value(key, value)}"

        current = None
        replaced = False
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                current = stripped[1:-1].strip()
                continue
            if current == section and stripped.split("=")[0].strip() == key:
                indent = line[: len(line) - len(line.lstrip())]
                lines[index] = f"{indent}{rendered}"
                replaced = True
                break

        if not replaced:
            header = f"[{section}]"
            if header not in [line.strip() for line in lines]:
                lines.extend(["", header])
            at = max(index for index, line in enumerate(lines) if line.strip() == header)
            lines.insert(at + 1, rendered)

        changed.append((key, rendered))

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _refresh(runtime_config, path)
    return changed


def _refresh(runtime_config, path: Path) -> None:
    """改完文件后把内存里的配置同步过来，同一条命令里 status 才是准的。"""
    import toml

    parsed = toml.load(str(path))
    for section in ("app", "bot", "whisper", "direct"):
        loaded = parsed.get(section)
        current = getattr(runtime_config, section, None)
        if isinstance(loaded, dict) and isinstance(current, dict):
            current.clear()
            current.update(loaded)


_PLACEHOLDERS = ("YOUR-", "YOUR_", "PASTE", "XXXX", "<", "...")


def _looks_like_placeholder(value: str) -> bool:
    text = value.strip().strip('"').strip("'").upper()
    return any(marker in text for marker in _PLACEHOLDERS)


def cmd_set(args: argparse.Namespace) -> int:
    """第一次配置只需要一条命令，不用在 Notepad 里翻 900 行。"""
    if not args.pairs:
        return cmd_status(args)

    for pair in args.pairs:
        if "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        if _looks_like_placeholder(value):
            print(f"✗ {key.strip()} still looks like a placeholder: {value.strip()}")
            print("  Paste the real value — no quotes, no <brackets>.")
            return 2

    changed = set_config_values(list(args.pairs))
    for key, rendered in changed:
        print(f"  {rendered}")

    from app.bot.autopilot import preflight
    from app.bot.config import load_settings

    settings = load_settings()
    blockers = [item for item in preflight(settings) if item["key"] != "delivery"]
    print()
    if blockers:
        print("Still needed:")
        for item in blockers:
            print(f"  • {item['why']}")
            print(f"    → {item['fix']}")
    else:
        print("Ready — no missing keys.")
    return 0


def cmd_connect(args: argparse.Namespace) -> int:
    """把平台账号连到机器人上（免费直连模式）。

    在 VPS 上没有浏览器，所以 YouTube 用回环地址授权、把 code 粘回来；
    Facebook 直接粘一个 Page Token 就行。
    """
    from app.bot import direct

    platform = args.platform

    if not platform or platform == "status":
        rows = direct.status_lines()
        if not rows:
            print("nothing to connect")
            return 0
        for item, ok, reason in rows:
            mark = "✓" if ok else "✗"
            print(f"  {mark} {item.value:<10} {reason or 'connected'}")
        print()
        print("Connect with:  python -m app.bot.cli connect youtube|facebook")
        return 0

    if platform == "youtube":
        return _connect_youtube(args)
    if platform == "facebook":
        return _connect_facebook(args)
    if platform in {"instagram", "tiktok"}:
        print(f"{platform} is not on the free path yet.")
        print("Use /week and upload it yourself, or connect Upload-Post for it.")
        return 2

    print(f"unknown platform: {platform}")
    return 2


def _connect_youtube(args: argparse.Namespace) -> int:
    from app.bot.direct import youtube

    client_id, client_secret = youtube.client_credentials()
    if not client_id or not client_secret:
        print("First, create a free Google OAuth client and save its two values:")
        print()
        print("  1. console.cloud.google.com -> new project")
        print("  2. APIs & Services -> Library -> enable 'YouTube Data API v3'")
        print("  3. APIs & Services -> OAuth consent screen -> External -> add your")
        print("     own Google account as a Test user")
        print("  4. Credentials -> Create credentials -> OAuth client ID ->")
        print("     application type 'Desktop app' -> copy the client id and secret")
        print()
        print("Then run:")
        print("  python -m app.bot.cli set youtube_client_id=... youtube_client_secret=...")
        return 2

    if args.code:
        youtube.exchange_code(args.code)
        print("✓ YouTube connected. The refresh token is stored on this machine.")
        return 0

    print("Open this address in a browser on any device:")
    print()
    print("  " + youtube.authorize_url(client_id))
    print()
    print("Sign in, approve, and the browser will land on a page that fails to load")
    print("(http://localhost:8765/?code=...). That is expected: copy the `code=`")
    print("value out of the address bar and paste it back here:")
    print()
    print("  python -m app.bot.cli connect youtube --code THE-CODE")
    return 0


def _connect_facebook(args: argparse.Namespace) -> int:
    from app.bot.direct import facebook

    if not args.page_id:
        print("Two values, both from Meta, five minutes total:")
        print()
        print("  1. developers.facebook.com -> Create app -> type 'Business'")
        print("  2. Open the Graph API Explorer, choose your app, and request the")
        print("     permissions: pages_show_list, pages_manage_posts, pages_read_engagement")
        print("  3. Generate an access token, then exchange it for a long-lived Page")
        print("     token (GET /me/accounts with your user token)")
        print("  4. Copy the Page ID and that Page token")
        print()
        print("Then run:")
        print("  python -m app.bot.cli connect facebook --page-id 123456789 --token THE-TOKEN")
        return 2

    try:
        record = facebook.connect(args.page_id, args.token or "")
    except RuntimeError as exc:
        print(f"✗ {exc}")
        return 2

    print(f"✓ connected to the Page '{record.get('page_name')}' ({args.page_id})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.bot.cli",
        description="MoneyPrinterTurbo publishing bot (Facebook / YouTube / TikTok / Instagram).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="show bot configuration")
    status.set_defaults(func=cmd_status)

    set_cmd = sub.add_parser(
        "set",
        help="write settings into config.toml (e.g. set gemini_api_key=... demo_mode=false)",
    )
    set_cmd.add_argument("pairs", nargs="*", help="key=value pairs")
    set_cmd.set_defaults(func=cmd_set)

    serve = sub.add_parser("serve", help="run the chat UI and API")
    serve.add_argument("--host", default="", help="bind address (default 0.0.0.0)")
    serve.add_argument("--port", type=int, default=0, help="port (default 8080)")
    serve.add_argument(
        "--telegram",
        action="store_true",
        help="start Telegram polling (automatic when [bot] telegram_token is set)",
    )
    serve.add_argument(
        "--bot-only",
        action="store_true",
        help="serve only the chat UI, without the WebUI / REST API stack (much lighter)",
    )
    serve.add_argument(
        "--autopilot",
        action="store_true",
        help="also run the scheduler that generates and posts on its own",
    )
    serve.add_argument(
        "--trust-proxy",
        action="store_true",
        help="trust X-Forwarded-* headers (needed behind a reverse proxy or sandbox preview)",
    )
    serve.set_defaults(func=cmd_serve)

    doctor_cmd = sub.add_parser(
        "doctor", help="check which social platforms are really connected"
    )
    doctor_cmd.add_argument(
        "--refresh",
        action="store_true",
        help="ignore the cached result and call the API again",
    )
    doctor_cmd.add_argument(
        "--test-post",
        default="",
        metavar="PLATFORM",
        help="also publish a short test clip to one platform (tiktok/instagram/facebook/youtube)",
    )
    doctor_cmd.set_defaults(func=cmd_doctor)

    auto = sub.add_parser(
        "autopilot", help="generate and publish on a schedule (runs until Ctrl+C)"
    )
    auto.add_argument("--once", action="store_true", help="run one cycle and exit")
    auto.add_argument("--topic", default="", help="use this topic for this cycle")
    auto.add_argument(
        "--interval",
        type=int,
        default=None,
        help="minutes between posts (0 = use autopilot_times)",
    )
    auto.add_argument("--max-per-day", type=int, default=None, help="daily cap")
    auto.add_argument("--topic-file", default="", help="path to a topics file")
    auto.add_argument("--pause", action="store_true", help="stop the schedule")
    auto.add_argument("--resume", action="store_true", help="restart the schedule")
    auto.add_argument(
        "--no-publish", action="store_true", help="generate only, never publish"
    )
    auto.add_argument(
        "--dry-run", action="store_true", help="force rehearsal mode even if configured"
    )
    auto.set_defaults(func=cmd_autopilot)

    topics = sub.add_parser("topics", help="manage the autopilot topic queue")
    topics.add_argument(
        "action", nargs="?", default="list", choices=["list", "add", "remove", "clear"]
    )
    topics.add_argument("topic", nargs="*", help="topic text for 'add'")
    topics.add_argument("--index", type=int, default=None, help="1-based index for 'remove'")
    topics.add_argument("--topic-file", default="", help="path to a topics file")
    topics.add_argument("--interval", type=int, default=None)
    topics.add_argument("--max-per-day", type=int, default=None)
    topics.add_argument("--no-publish", action="store_true")
    topics.add_argument("--dry-run", action="store_true")
    topics.set_defaults(func=cmd_topics)

    connect = sub.add_parser(
        "connect",
        help="link a platform account for free direct publishing (no subscription)",
    )
    connect.add_argument(
        "platform", nargs="?", default="status", help="youtube | facebook | status"
    )
    connect.add_argument("--code", default="", help="the code= value from the Google redirect")
    connect.add_argument("--page-id", default="", help="Facebook Page id")
    connect.add_argument("--token", default="", help="Facebook Page access token")
    connect.set_defaults(func=cmd_connect)

    week = sub.add_parser(
        "week", help="make a batch of videos and package them for manual scheduling"
    )
    week.add_argument("--count", type=int, default=7, help="how many videos (default 7)")
    week.add_argument("--name", default="", help="folder/batch name")
    week.add_argument(
        "topic", nargs="*", help="explicit topics (default: the autopilot queue)"
    )
    week.set_defaults(func=cmd_week)

    ask = sub.add_parser("ask", help="generate one video from a topic")
    ask.add_argument("topic")
    ask.add_argument("--yes", action="store_true", help="publish right after generating")
    ask.set_defaults(func=cmd_ask)

    publish = sub.add_parser("publish", help="publish an existing video file")
    publish.add_argument("video")
    publish.add_argument(
        "--platforms",
        default="tiktok,instagram,facebook,youtube",
        help="comma separated",
    )
    publish.add_argument("--yes", action="store_true", help="publish without confirmation")
    publish.set_defaults(func=cmd_publish)

    demo = sub.add_parser("demo", help="end-to-end demo render (no API keys needed)")
    demo.add_argument("topic", nargs="?", default="")
    demo.set_defaults(func=cmd_demo)

    return parser


def _sink(message) -> None:
    # 每次写入时才去取 sys.stderr：被进程内调用（测试、嵌入）时，直接绑定
    # sys.stderr 会把 handler 钉在一个随后会被关闭的流上，之后所有日志都会
    # 抛 "I/O operation on closed file"，把别人的输出搅乱。
    sys.stderr.write(message)


# 只有真的从命令行启动（python -m app.bot.cli）时才动全局日志配置。进程内调用
# ——测试、或别的程序 import 之后调用——必须保持调用方的日志原样。
_CLI_ENTRY_POINT = __name__ == "__main__"


def main(argv: list[str] | None = None) -> int:
    # 命令行输出要能被脚本解析，因此把 loguru 的 INFO 噪音降到 stderr 且只保留
    # 警告以上；真正的错误仍然可见。
    if _CLI_ENTRY_POINT:
        try:
            logger.remove()
            logger.add(_sink, level="WARNING")
        except Exception:  # noqa: BLE001, S110 - 日志系统不可用时也要能继续执行
            pass

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

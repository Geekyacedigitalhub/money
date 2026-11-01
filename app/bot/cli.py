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
    if settings.allowed_users:
        print("allow-list      : " + ", ".join(settings.allowed_users))
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

    if args.telegram:
        token = settings.telegram_token
        if not token:
            print(
                "error: --telegram needs [bot] telegram_token in config.toml\n"
                "Get one from @BotFather and add it, then retry.",
                file=sys.stderr,
            )
            return 2
        from app.bot.telegram import TelegramBot

        bot = TelegramBot(settings)
        threading_import = __import__("threading")
        worker = threading_import.Thread(target=bot.run, name="mpt-bot-telegram", daemon=True)
        worker.start()
        print("Telegram polling started (see logs).")

    import uvicorn

    if args.bot_only:
        # 只跑聊天页面和它的接口。不加载 Streamlit / MoviePy / Whisper 这一整套
        # 媒体依赖，因此在 512MB 的小机器上也能启动；需要真实生成视频时再用完整
        # 模式（``serve`` 不带 --bot-only）。
        app_target = _build_bot_only_app()
        print("Mode: bot-only (no WebUI / REST API)")
    else:
        app_target = "app.asgi:app"

    print(f"Chat UI  →  http://127.0.0.1:{port}/bot")
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


def cmd_doctor(args: argparse.Namespace) -> int:
    """检查每个平台到底连上了没有，可选发一条真实的测试帖。"""
    settings = load_settings()
    print("Checking your connections…\n")
    report = doctor.check_connection(settings)
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.bot.cli",
        description="MoneyPrinterTurbo publishing bot (Facebook / YouTube / TikTok / Instagram).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="show bot configuration")
    status.set_defaults(func=cmd_status)

    serve = sub.add_parser("serve", help="run the chat UI and API")
    serve.add_argument("--host", default="", help="bind address (default 0.0.0.0)")
    serve.add_argument("--port", type=int, default=0, help="port (default 8080)")
    serve.add_argument("--telegram", action="store_true", help="also start Telegram polling")
    serve.add_argument(
        "--bot-only",
        action="store_true",
        help="serve only the chat UI, without the WebUI / REST API stack (much lighter)",
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
        "--test-post",
        default="",
        metavar="PLATFORM",
        help="also publish a short test clip to one platform (tiktok/instagram/facebook/youtube)",
    )
    doctor_cmd.set_defaults(func=cmd_doctor)

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


def main(argv: list[str] | None = None) -> int:
    # 命令行输出要能被脚本解析，因此把 loguru 的 INFO 噪音降到 stderr 且只保留
    # 警告以上；真正的错误仍然可见。
    try:
        logger.remove()
        logger.add(sys.stderr, level="WARNING")
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

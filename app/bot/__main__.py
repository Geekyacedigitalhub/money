"""Allow ``python -m app.bot`` as a shortcut for the bot CLI."""

from app.bot.cli import main

if __name__ == "__main__":
    raise SystemExit(main())

"""Command line entry point.

    python -m scanner_bot -c config.yaml             run the bot
    python -m scanner_bot -c config.yaml --dry-run   run, log decisions, never reply
    python -m scanner_bot -c config.yaml --check     validate config and exit
    python -m scanner_bot -c config.yaml --test "some text" [--group CHAT]
                                                      show the decision for a text
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys

from . import __version__
from .bot import ScannerBot
from .config import ConfigError, load_config
from .deciders import build_decider
from .telegram import TelegramClient, TelegramError


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scanner_bot", description="Telegram group scanner bot")
    ap.add_argument("-c", "--config", default=os.environ.get("SCANNER_BOT_CONFIG", "config.yaml"))
    ap.add_argument("--dry-run", action="store_true", help="log decisions but never send replies")
    ap.add_argument("--check", action="store_true", help="validate the config and exit")
    ap.add_argument("--test", metavar="TEXT", help="print the decision for TEXT and exit")
    ap.add_argument("--group", help="chat id or @username for --test (default: every group)")
    ap.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    ap.add_argument("--version", action="version", version=__version__)
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    if args.check:
        print(f"config OK: {len(cfg.groups)} group(s), default mode {cfg.mode}")
        for g in cfg.groups:
            print(f"  {g.label}: mode={cfg.mode_for(g)} threshold={g.alert_threshold:g} "
                  f"words={len(g.vocabulary)} answers={len(g.answers)}")
        return 0

    if args.test is not None:
        groups = cfg.groups
        if args.group:
            groups = [g for g in groups if str(g.chat).lower() == args.group.lower()]
            if not groups:
                print(f"no group {args.group} in config", file=sys.stderr)
                return 2
        for g in groups:
            d = build_decider(cfg, g).decide(args.test)
            print(f"{g.label}: reply={d.reply} [{d.source}] {d.reason}")
        return 0

    if args.dry_run:
        cfg.dry_run = True

    bot = ScannerBot(cfg, TelegramClient(cfg.telegram.token, cfg.telegram.api_base), config_path=args.config)

    def _stop(*_):
        logging.getLogger(__name__).info("stopping...")
        bot.stop()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:
        bot.run()
    except TelegramError as e:
        print(f"telegram error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

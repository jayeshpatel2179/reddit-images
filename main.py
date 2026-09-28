"""Reddit -> Telegram forwarder.

  python main.py              run forever, every INTERVAL_HOURS (what Railway runs)
  python main.py --once       single run, then exit
  python main.py --dry-run    fetch + show what would be sent, no Telegram needed
  python main.py --chat-id    print chat ids the bot has seen (to fill TELEGRAM_CHAT_ID)
  python main.py --test       send a test message to TELEGRAM_CHAT_ID
"""
import argparse
import logging
import os
import sys
import time
from logging.handlers import RotatingFileHandler

from bot.config import load_config
from bot.job import fetch_pending, make_source, run_once
from bot.sender import PostSender
from bot.storage import Storage
from bot.telegram_client import TelegramClient

log = logging.getLogger("bot")


def setup_logging(log_dir) -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    file = RotatingFileHandler(
        log_dir / "bot.log",
        maxBytes=int(os.getenv("LOG_MAX_KB", "500")) * 1000,
        backupCount=int(os.getenv("LOG_BACKUPS", "10")),
        encoding="utf-8",
    )
    file.setFormatter(fmt)
    root.addHandler(console)
    root.addHandler(file)


def show_chat_ids(tg: TelegramClient) -> None:
    updates = tg.get_updates()
    chats = {}
    for u in updates:
        msg = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
        chat = msg.get("chat")
        if chat:
            chats[chat["id"]] = chat.get("title") or chat.get("username") or chat.get("first_name")
    if not chats:
        print("No chats found. Send a message to your bot (or add it to the group and send a message there), then retry.")
    for chat_id, name in chats.items():
        print(f"{chat_id}\t{name}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--chat-id", action="store_true")
    ap.add_argument("--test", action="store_true")
    args = ap.parse_args()

    cfg = load_config(require_telegram=not (args.dry_run or args.chat_id))
    setup_logging(cfg.log_dir)
    if args.chat_id:
        show_chat_ids(TelegramClient(cfg.telegram_token, ""))
        return

    store = Storage(cfg.db_path)
    source = make_source(cfg)

    if args.dry_run:
        pending, _ = fetch_pending(cfg, source, store)
        for p in pending:
            when = time.strftime("%H:%M", time.gmtime(p.created_utc))
            print(f"{when} [{p.kind:12}] {p.id} items={len(p.items)} {p.title[:60]}")
            for item in p.items:
                print(f"      {item.kind}: {item.url[:100]}")
        return

    tg = TelegramClient(cfg.telegram_token, cfg.telegram_chat_id)
    if args.test:
        tg.send_message(f"✅ Bot connected. Watching r/{cfg.subreddit} every {cfg.interval_hours:g}h.")
        print("Test message sent.")
        return

    sender = PostSender(tg, cfg)
    if args.once:
        run_once(cfg, source, sender, store)
        return

    interval = cfg.interval_hours * 3600
    log.info("Starting loop: r/%s every %.1f hours", cfg.subreddit, cfg.interval_hours)
    while True:
        started = time.time()
        try:
            run_once(cfg, source, sender, store)
        except Exception:
            log.exception("Run failed; will retry at next interval")
        sleep_for = max(60, interval - (time.time() - started))
        log.info("Next run in %.0f minutes", sleep_for / 60)
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()

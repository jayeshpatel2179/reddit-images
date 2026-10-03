# r/EASportsFC → Telegram bot

Every 2 hours it reads new posts from r/EASportsFC through Reddit's public **RSS feed** (no Reddit API key needed) and sends them to a Telegram chat or group. No duplicates, no missed posts.

## What gets sent

| Reddit post | Telegram |
|---|---|
| Image (`i.redd.it`) | photo + title |
| Video (`v.redd.it`) | video **with sound** (yt-dlp + ffmpeg merge Reddit's separate audio/video) |
| GIF | animation |
| Text post | title + body text |
| Text post with pasted images | photo / album + title + body excerpt |
| Gallery | title + "Gallery post" note + Reddit link (RSS doesn't list gallery images) |
| External link (YouTube, etc.) | title + link with preview |

`SEND_TYPES` picks what gets forwarded (default `video`: only Reddit videos, everything else is recorded as `skipped` and never sent). Example: `SEND_TYPES=video,animation,photo,gallery` for all media.

Every message ends with the author and a **View on Reddit** link. If media fails to send, the post falls back to a text message, so nothing is silently skipped.

## How it works

1. Reads the checkpoint (time of the last successful run) from `data/state.db`.
2. One request to `https://www.reddit.com/r/EASportsFC/new/.rss?limit=100`. 100 posts cover roughly a day on this subreddit, so 2-hour runs never miss anything. If needed it pages further (max 3 requests, 15 s apart).
3. Keeps posts newer than `checkpoint - 60 min` and skips every post id already in `sent_posts`. First run: last 2 hours only.
4. Sends oldest → newest, 3.5 s apart (Telegram allows ~20 msgs/min per group).
5. Saves each post id right after Telegram confirms, then moves the checkpoint.

**Rate limits:** the RSS feed returns `429` after ~5 quick requests. The bot makes 1 request per run (12/day), waits ≥15 s between requests and backs off on 429.

## Step 1: Telegram bot token

1. In Telegram, open **@BotFather** → `/newbot`.
2. Pick a name and a username ending in `bot`.
3. Copy the token (`7123456789:AAH...`) into `TELEGRAM_BOT_TOKEN`.

## Step 2: Chat id

1. Open your bot in Telegram and press **Start** (for a group: add the bot to the group and send a message there).
2. `python main.py --chat-id` → prints the id (private chat: positive number; group: `-100...`).
3. Put it in `TELEGRAM_CHAT_ID`, then `python main.py --test` → you should get "✅ Bot connected".

## Step 3: Run locally

```bash
pip install -r requirements.txt     # plus ffmpeg: winget install Gyan.FFmpeg
cp .env.example .env                # fill in the two Telegram values
python main.py --dry-run            # shows what would be sent, sends nothing
python main.py --once               # one real run
python main.py                      # loop forever, every 2 hours
```

## Step 4: Deploy on Railway

1. Push this folder to a **private** GitHub repo (`.env` is git-ignored; never commit it).
2. railway.com → **New Project → Deploy from GitHub repo**. It builds the `Dockerfile` (includes ffmpeg).
3. **Variables**: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SUBREDDIT`, `REDDIT_USER_AGENT`.
4. **Attach a Volume at `/app/data`**. Without it the database is wiped on every redeploy and the bot forgets what it already sent.
   Optional: `LOG_DIR=/app/data/logs` keeps the log file on the volume too (500 KB per file, last 10 copies kept; change with `LOG_MAX_KB` / `LOG_BACKUPS`).
5. Deploy → **Logs** should show `Reddit source: public RSS feed` and `Starting loop`.

To move from your private chat to the group later, change `TELEGRAM_CHAT_ID` in Railway; history stays on the volume.

If the logs show `Reddit blocked the RSS feed (403)`, Reddit is blocking Railway's IP range. Run the bot from a home machine instead, or switch to the API (below).

## Optional: official API mode

If Reddit ever approves an API application for you (reddit.com/prefs/apps; approval is required since Nov 2025), set `REDDIT_SOURCE=api`, `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`. That mode also sends full gallery albums and uses the 100 req/min OAuth limit.

## Folder structure

```
main.py                 entry point + CLI flags (--once, --dry-run, --chat-id, --test)
bot/config.py           settings from environment / .env
bot/rss_client.py       RSS reader + parser (default source)
bot/reddit_client.py    official API reader (optional source)
bot/media.py            post model + API post parser
bot/downloader.py       file + Reddit video download (yt-dlp + ffmpeg)
bot/telegram_client.py  Telegram API calls, 429 flood-control retry
bot/sender.py           per-type sending with fallbacks, captions
bot/storage.py          SQLite: sent posts, failures, checkpoint
bot/job.py              one run: fetch → filter → send → checkpoint
Dockerfile, railway.json
```

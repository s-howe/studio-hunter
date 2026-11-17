# studio-hunter

Automated Kleinanzeigen watcher for rental listings (originally built for
studio/office rooms in Berlin). It polls listing sites on a schedule, applies a
cheap keyword prefilter, then a Claude-based semantic filter against your
criteria, and sends matches as Telegram alerts.

**This is a template.** `config.yaml` ships with placeholder criteria and
search URLs. Start with [ADAPTING.md](ADAPTING.md), which lists exactly what to
change for your own search (it also works as a briefing for your AI assistant).

## Setup

```bash
uv sync
uv run playwright install --with-deps chromium
cp .env.example .env   # then fill in the values below
```

Edit `.env`:

- `ANTHROPIC_API_KEY` — used by the semantic filter stage (`filter.py`).
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` — see below.

### Telegram bot setup (one-time, manual)

1. Message [@BotFather](https://t.me/BotFather) on Telegram, run `/newbot`, follow the prompts. You'll get a bot token.
2. Send any message to your new bot (so it has a chat to talk back to).
3. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser — the JSON response contains your `chat_id` under `message.chat.id`.
4. Put both values in `.env`.

## Configuration

Everything else — search criteria, per-source polling intervals, enable/
disable toggles, search URLs, CSS selectors — lives in
[`config.yaml`](config.yaml). Notable toggles:

- `criteria.districts.fallback_enabled` — fallback districts are suppressed
  from notifications by default; flip to `true` (or pass `--include-adjacent`
  at runtime) once the primary districts have stalled for a few weeks.
- `sources.kleinanzeigen.search_urls` — the saved searches to poll. Placeholder
  URLs are skipped, so nothing runs until you fill these in.
- `filter.claude_model` — defaults to `claude-haiku-4-5` for cost, since the
  semantic filter runs as a batched job many times a day. Bump to a stronger
  model if match quality on ambiguous listings becomes a problem.

## Running locally

```bash
uv run main.py                    # normal run
uv run main.py --dry-run          # log matches instead of sending Telegram messages
uv run main.py --include-adjacent # also surface fallback-district matches
```

The orchestrator tracks per-source last-run timestamps in SQLite, so it's
safe to invoke frequently (e.g. every 15 min via cron) — sources that aren't
due for their configured polling interval are skipped as a no-op.

## Logging and reporting

Every invocation logs to both stdout and a rotating file
(`data/studio-hunter.log`, path/size configurable under `logging:` in
`config.yaml`) — same mounted volume as the SQLite db, so it persists across
container restarts. Each run also records its own stats (listings fetched/
new/prefilter-rejected/semantic-matched/notified, per source, plus any fetch
errors) to SQLite for later review — this happens regardless of `--dry-run`,
so testing doesn't pollute the notification channel but still shows up in
reporting.

```bash
uv run main.py --report        # summary for the last 7 days (default)
uv run main.py --report 30     # last 30 days
```

Prints run counts, fetch volume per source (useful for spotting a source
that's silently started failing), and the full list of matched listings with
confidence/tier — the fact base for a periodic process review rather than
scrolling raw logs.

## Docker deployment

```bash
docker compose up -d --build
```

Runs on a schedule via cron *inside* the container (`docker/crontab`, every
15 minutes). Swap for an external scheduler if that fits your server better.
The SQLite db lives in `./data`, mounted as a volume so it survives container
restarts. Edit `config.yaml` on the host; it is mounted read-only, so restart
the container (`docker compose restart`) after changes.

## Sources

Kleinanzeigen is the only source: a bespoke Playwright fetcher
(`sources/kleinanzeigen.py`) that polls the saved-search URLs in `config.yaml`.
The `Fetcher` base class in `sources/base.py` is the extension point if you
ever want to add another site.

## Testing

```bash
uv run pytest
```

Covers everything that's genuinely testable offline: the keyword prefilter
(`test_filter_prefilter.py`), district tiering, the SQLite dedup/state layer
(`test_store.py`), and config sanity (`test_config.py`). **Not** covered
(and not fakeable) by this suite: live Playwright selector behavior against
real pages, semantic filter output quality against real Claude API calls,
and real Telegram delivery — verify those by hand with `--dry-run` first.

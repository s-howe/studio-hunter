# Adapting studio-hunter to your own search

You can give this file to your AI assistant along with the repo. It describes
what the project does, which parts are yours to change, and what to leave alone.

## How it works

Every 15 minutes cron runs `main.py`, which:

1. **Fetches** Kleinanzeigen when its poll interval is due (`sources/kleinanzeigen.py`).
2. **Dedupes** against SQLite (`store.py`); only never-seen listings continue.
3. **Prefilters** with cheap deterministic rules (`filter.py: keyword_prefilter`).
   It rejects only on strong signals. Missing data always survives.
4. **Semantic filter**: Claude reads the survivors in batches of 15 and returns
   match / confidence / reason per listing (`filter.py: semantic_filter`).
5. **Notifies** via Telegram (`notify_telegram.py`). Districts outside
   `primary` are stored but suppressed unless `fallback_enabled` is true.

Key design rule: an unstated detail never disqualifies a listing. It becomes a
low-confidence match with "check manually" in the reason.

## What you change (almost everything is in `config.yaml`)

| Goal | Where |
|---|---|
| Size, price, districts, heating, access, lease types | `criteria:` in `config.yaml`. Size, price, districts and `hard_exclude_keywords` are also enforced by code; everything else is read by Claude. |
| Describe who you are and what you need | `criteria.context_notes`. This is the most important field. The semantic filter treats it as ground truth. |
| Add a criterion of your own | Add any new key under `criteria:` (e.g. `pets_allowed: true`) and mention it in `context_notes`. The whole block is sent to Claude as JSON. No code change needed. |
| Instant-reject phrases | `criteria.hard_exclude_keywords` (case-insensitive substring match on title and description). Use sparingly; the semantic filter handles nuance. |
| Which Kleinanzeigen searches run | `sources.kleinanzeigen.search_urls`. Build each URL in the site's own UI, copy it, and read the IMPORTANT comment about location IDs. |
| Another city | Change the districts and URLs. Everything else is city-agnostic. |
| Model and cost | `filter.claude_model`. Haiku is the cheap default; use Sonnet if borderline listings are misjudged. |
| Poll frequency | `sources.<name>.poll_interval_hours`, plus the cron line in `docker/crontab`. |
| Another site (optional) | Write a fetcher: subclass `Fetcher` in `sources/base.py`, return `Listing` objects, and register it in `SOURCE_REGISTRY` in `main.py` plus a block under `sources:`. `sources/kleinanzeigen.py` is the worked example. |

## What to leave alone unless you have a reason

- `store.py` (SQLite dedup and run stats) and its tests.
- The cron and Docker setup (`Dockerfile`, `docker/crontab`). It has hard-won
  fixes: `--frozen` so `uv run` never re-resolves dependencies at runtime, and
  explicit `PATH` and `PLAYWRIGHT_BROWSERS_PATH` because cron does not inherit
  the container environment. Comments in the files explain each one.
- The Kleinanzeigen parsing. The site rewrote its markup once already, so if it
  breaks, debug against the live page rather than guessing selectors.

## Setup checklist

1. `cp .env.example .env` and fill in `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`
   and `TELEGRAM_CHAT_ID` (BotFather steps are in `README.md`).
2. Edit `config.yaml`: criteria, `context_notes`, and Kleinanzeigen URLs.
3. `uv sync && uv run playwright install --with-deps chromium`
4. `uv run pytest`
5. `uv run main.py --dry-run`. Matches are logged instead of sent. Check that
   results look right and tune `context_notes` before going live.
6. On the server: `docker compose up -d --build`. Check the logs with
   `docker compose logs -f` and `uv run main.py --report` for a summary.

## Gotchas

- The first real run treats every currently-listed ad as new. Expect a large
  first batch of alerts, or do a first `--dry-run` to seed the database.
- Scraping is fragile. If a source silently returns zero listings, check
  `--report`: a source with many runs and `fetched=0` has probably broken.
- Keep the Anthropic key and Telegram token out of git (`.env` is ignored).
- Respect each site's terms of service and keep polling intervals modest.

from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import os

import yaml
from dotenv import load_dotenv

from filter import keyword_prefilter, semantic_filter, tier_listing
from notify_telegram import TelegramNotifier
from sources.base import Fetcher
from sources.kleinanzeigen import KleinanzeigenFetcher
from store import (
    finish_run,
    get_run_report,
    init_db,
    is_source_due,
    mark_notified,
    mark_rejected,
    record_source_fetch,
    record_source_run,
    set_match_result,
    start_run,
    upsert_listing,
)

logger = logging.getLogger(__name__)

SOURCE_REGISTRY: dict[str, type[Fetcher]] = {
    "kleinanzeigen": KleinanzeigenFetcher,
}


async def run(config: dict, include_adjacent: bool, dry_run: bool, force_sources: set[str] | None = None) -> None:
    conn = init_db(config["storage"]["db_path"])
    run_id = start_run(conn, dry_run, include_adjacent)
    force_sources = force_sources or set()

    new_listings = []
    total_fetched = 0
    for source_name, source_cfg in config.get("sources", {}).items():
        if not source_cfg.get("enabled", True):
            continue

        fetcher_cls = SOURCE_REGISTRY.get(source_name)
        if fetcher_cls is None:
            logger.warning("no fetcher registered for source '%s', skipping", source_name)
            continue

        poll_interval = source_cfg.get("poll_interval_hours", 4)
        if source_name not in force_sources and not is_source_due(conn, source_name, poll_interval):
            logger.info("source '%s' not due yet, skipping", source_name)
            continue

        fetcher = fetcher_cls(source_cfg)
        try:
            listings = await fetcher.fetch()
        except Exception as exc:
            logger.exception("fetch failed for source='%s'", source_name)
            record_source_fetch(conn, run_id, source_name, fetched_count=0, new_count=0, error=str(exc))
            continue

        record_source_run(conn, source_name)
        logger.info("source '%s' returned %d listing(s)", source_name, len(listings))
        total_fetched += len(listings)

        new_count = 0
        for listing in listings:
            if upsert_listing(conn, listing):
                new_listings.append(listing)
                new_count += 1

        record_source_fetch(conn, run_id, source_name, fetched_count=len(listings), new_count=new_count)

    logger.info("%d newly-seen listing(s) across all sources", len(new_listings))

    criteria = config.get("criteria", {})
    survivors = []
    prefilter_rejected = 0
    for listing in new_listings:
        ok, reason = keyword_prefilter(listing, criteria)
        if ok:
            survivors.append(listing)
        else:
            mark_rejected(conn, listing.id, reason)
            logger.info("prefilter rejected '%s': %s", listing.title, reason)
            prefilter_rejected += 1

    if not survivors:
        logger.info("no listings survived the prefilter, nothing to notify")
        finish_run(conn, run_id, prefilter_rejected, semantic_evaluated=0, semantic_matched=0, notified=0, suppressed_tier=0)
        return

    import anthropic  # lazy import/construct so a placeholder-only run needs no API key

    client = anthropic.Anthropic()
    verdicts = await semantic_filter(
        survivors, criteria, client, config.get("filter", {}).get("claude_model", "claude-haiku-4-5")
    )

    notifier = None
    if not dry_run:
        notifier = TelegramNotifier(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"])

    verdicts_by_id = {verdict.id: verdict for verdict in verdicts}

    semantic_matched = notified = suppressed_tier = 0
    for listing in survivors:
        verdict = verdicts_by_id.get(listing.id)
        if verdict is None:
            logger.error("no semantic verdict returned for listing '%s' (id=%s), rejecting", listing.title, listing.id)
            mark_rejected(conn, listing.id, "no semantic verdict returned")
            continue

        tier = tier_listing(listing, criteria, include_adjacent)
        set_match_result(conn, listing.id, verdict.confidence, verdict.reason, tier)

        if verdict.match:
            semantic_matched += 1

        if verdict.match and tier != "suppressed":
            if dry_run:
                logger.info("[dry-run] would notify: %s (%s, %s)", listing.title, tier, verdict.confidence)
            else:
                notifier.send_listing(listing, verdict)
            # Marked 'notified' even in dry-run: a listing is only ever
            # evaluated once (upsert_listing returns True on first sighting
            # only), so this never suppresses a real future notification —
            # it just keeps the status column, and therefore the --report
            # output, accurate about what actually matched.
            mark_notified(conn, listing.id)
            notified += 1
        else:
            if verdict.match:
                suppressed_tier += 1
            mark_rejected(conn, listing.id, verdict.reason if not verdict.match else f"suppressed tier={tier}")

    finish_run(conn, run_id, prefilter_rejected, len(survivors), semantic_matched, notified, suppressed_tier)
    logger.info(
        "run complete: %d fetched, %d new, %d prefilter-rejected, %d semantic-matched, %d notified, %d suppressed",
        total_fetched, len(new_listings), prefilter_rejected, semantic_matched, notified, suppressed_tier,
    )


def print_report(config: dict, since_days: int) -> None:
    conn = init_db(config["storage"]["db_path"])
    report = get_run_report(conn, since_days)
    totals = report["totals"]

    print(f"\n=== studio-hunter report: last {since_days} day(s) ===\n")
    print(f"Runs: {totals['run_count']}")
    print(f"Fetched: {totals['total_fetched']}  |  New: {totals['total_new']}")
    print(f"Prefilter-rejected: {totals['prefilter_rejected']}")
    print(f"Semantic-evaluated: {totals['semantic_evaluated']}  |  Semantic-matched: {totals['semantic_matched']}")
    print(f"Notified: {totals['notified']}  |  Suppressed (adjacent tier): {totals['suppressed_tier']}")

    print("\n--- Per source ---")
    if not report["per_source"]:
        print("(no runs in this window)")
    for row in report["per_source"]:
        print(
            f"{row['source']:20} runs={row['run_count']:<4} fetched={row['fetched_count']:<5} "
            f"new={row['new_count']:<5} errors={row['error_count']}"
        )

    print(f"\n--- Notified listings ({len(report['notified_listings'])}) ---")
    if not report["notified_listings"]:
        print("(none)")
    for listing in report["notified_listings"]:
        price = f"€{listing['price_eur']:.0f}" if listing["price_eur"] is not None else "price unstated"
        size = f"{listing['size_m2']:.0f}m²" if listing["size_m2"] is not None else "size unstated"
        print(
            f"- [{listing['source']}] {listing['title']} ({price}, {size}, "
            f"confidence={listing['match_confidence']}, tier={listing['tier']})"
        )
        print(f"  {listing['url']}")
    print()


def _configure_logging(config: dict) -> None:
    log_cfg = config.get("logging", {})
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler()]

    log_file = log_cfg.get("log_file")
    if log_file:
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=log_cfg.get("max_bytes", 5_000_000),
                backupCount=log_cfg.get("backup_count", 5),
            )
        )

    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)


def main() -> None:
    parser = argparse.ArgumentParser(description="studio-hunter: rental listing watcher")
    parser.add_argument("--include-adjacent", action="store_true", help="surface fallback-district matches too")
    parser.add_argument("--dry-run", action="store_true", help="skip Telegram sends, log matches instead")
    parser.add_argument(
        "--force-sources", metavar="SOURCE,...",
        help="comma-separated source names to run even if not due yet (e.g. kleinanzeigen)",
    )
    parser.add_argument("--config", default="config.yaml", help="path to config.yaml")
    parser.add_argument(
        "--report", type=int, metavar="DAYS", nargs="?", const=7,
        help="print a summary report for the last DAYS days (default 7) instead of running the pipeline",
    )
    args = parser.parse_args()

    load_dotenv()

    with open(args.config) as f:
        config = yaml.safe_load(f)

    _configure_logging(config)

    if args.report is not None:
        print_report(config, args.report)
        return

    force_sources = set(args.force_sources.split(",")) if args.force_sources else None
    asyncio.run(run(config, args.include_adjacent, args.dry_run, force_sources))


if __name__ == "__main__":
    main()


import pytest
from freezegun import freeze_time

import store
from sources.base import Listing


@pytest.fixture
def conn():
    return store.init_db(":memory:")


def make_listing(**overrides) -> Listing:
    defaults = {"url": "https://example.com/1", "source": "test", "title": "A room"}
    defaults.update(overrides)
    return Listing(**defaults)


def test_upsert_inserts_new_listing(conn):
    listing = make_listing()
    is_new = store.upsert_listing(conn, listing)
    assert is_new is True

    row = conn.execute("SELECT * FROM listings WHERE url_hash = ?", (listing.id,)).fetchone()
    assert row["status"] == "new"
    assert row["title"] == "A room"


def test_upsert_existing_listing_updates_not_reinserts(conn):
    listing = make_listing(title="Original title")
    store.upsert_listing(conn, listing)

    updated = make_listing(title="Updated title")
    is_new = store.upsert_listing(conn, updated)
    assert is_new is False

    row = conn.execute("SELECT * FROM listings WHERE url_hash = ?", (listing.id,)).fetchone()
    assert row["title"] == "Updated title"
    count = conn.execute("SELECT COUNT(*) c FROM listings").fetchone()["c"]
    assert count == 1


def test_mark_notified_and_rejected(conn):
    listing = make_listing()
    store.upsert_listing(conn, listing)

    store.mark_notified(conn, listing.id)
    row = conn.execute("SELECT status FROM listings WHERE url_hash = ?", (listing.id,)).fetchone()
    assert row["status"] == "notified"

    store.mark_rejected(conn, listing.id, "out of budget")
    row = conn.execute("SELECT status, match_reason FROM listings WHERE url_hash = ?", (listing.id,)).fetchone()
    assert row["status"] == "rejected"
    assert row["match_reason"] == "out of budget"


def test_is_source_due_when_never_run(conn):
    assert store.is_source_due(conn, "kleinanzeigen", 3) is True


def test_is_source_due_respects_interval():
    with freeze_time("2026-01-01 12:00:00") as frozen:
        conn = store.init_db(":memory:")
        store.record_source_run(conn, "kleinanzeigen")

        assert store.is_source_due(conn, "kleinanzeigen", 3) is False

        frozen.move_to("2026-01-01 14:00:00")  # +2h, still within 3h interval
        assert store.is_source_due(conn, "kleinanzeigen", 3) is False

        frozen.move_to("2026-01-01 15:00:01")  # +3h1s, now due
        assert store.is_source_due(conn, "kleinanzeigen", 3) is True


def test_record_source_run_upserts(conn):
    store.record_source_run(conn, "kleinanzeigen")
    store.record_source_run(conn, "kleinanzeigen")
    count = conn.execute("SELECT COUNT(*) c FROM source_state").fetchone()["c"]
    assert count == 1


def test_finish_run_sums_fetch_stats_from_run_sources(conn):
    run_id = store.start_run(conn, dry_run=True, include_adjacent=False)
    store.record_source_fetch(conn, run_id, "kleinanzeigen", fetched_count=10, new_count=4)
    store.record_source_fetch(conn, run_id, "other_source", fetched_count=5, new_count=1, error="timeout")
    store.finish_run(conn, run_id, prefilter_rejected=2, semantic_evaluated=3, semantic_matched=1, notified=1, suppressed_tier=0)

    row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    assert row["total_fetched"] == 15
    assert row["total_new"] == 5
    assert row["prefilter_rejected"] == 2
    assert row["semantic_matched"] == 1
    assert row["notified"] == 1
    assert row["finished_at"] is not None


def test_get_run_report_aggregates_within_window():
    with freeze_time("2026-01-01 12:00:00") as frozen:
        conn = store.init_db(":memory:")

        # Old run, should fall outside a 7-day window measured from "now".
        old_run_id = store.start_run(conn, dry_run=False, include_adjacent=False)
        store.record_source_fetch(conn, old_run_id, "kleinanzeigen", fetched_count=999, new_count=999)
        store.finish_run(conn, old_run_id, prefilter_rejected=0, semantic_evaluated=0, semantic_matched=0, notified=0, suppressed_tier=0)

        frozen.move_to("2026-01-10 12:00:00")  # "now" — 9 days later

        run_id = store.start_run(conn, dry_run=False, include_adjacent=False)
        store.record_source_fetch(conn, run_id, "kleinanzeigen", fetched_count=10, new_count=4)
        store.finish_run(conn, run_id, prefilter_rejected=1, semantic_evaluated=3, semantic_matched=2, notified=1, suppressed_tier=1)

        listing = make_listing(url="https://example.com/notified", title="Great room")
        store.upsert_listing(conn, listing)
        store.mark_notified(conn, listing.id)
        store.set_match_result(conn, listing.id, "high", "good fit", "primary")

        report = store.get_run_report(conn, since_days=7)

    assert report["totals"]["run_count"] == 1
    assert report["totals"]["total_fetched"] == 10
    assert report["totals"]["notified"] == 1
    assert len(report["per_source"]) == 1
    assert report["per_source"][0]["source"] == "kleinanzeigen"
    assert report["per_source"][0]["fetched_count"] == 10
    assert len(report["notified_listings"]) == 1
    assert report["notified_listings"][0]["title"] == "Great room"

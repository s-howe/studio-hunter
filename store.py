from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime, timedelta

from sources.base import Listing

_SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    url_hash         TEXT PRIMARY KEY,
    source           TEXT NOT NULL,
    url              TEXT NOT NULL,
    title            TEXT,
    price_eur        REAL,
    price_note       TEXT,
    size_m2          REAL,
    district         TEXT,
    heating          INTEGER,
    access_hours     TEXT,
    lease_type       TEXT,
    description      TEXT,
    status           TEXT NOT NULL DEFAULT 'new',
    match_confidence TEXT,
    match_reason     TEXT,
    tier             TEXT,
    first_seen       TEXT NOT NULL,
    last_seen        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_state (
    source      TEXT PRIMARY KEY,
    last_run_at TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at          TEXT NOT NULL,
    finished_at         TEXT,
    dry_run             INTEGER NOT NULL DEFAULT 0,
    include_adjacent    INTEGER NOT NULL DEFAULT 0,
    total_fetched       INTEGER NOT NULL DEFAULT 0,
    total_new           INTEGER NOT NULL DEFAULT 0,
    prefilter_rejected  INTEGER NOT NULL DEFAULT 0,
    semantic_evaluated  INTEGER NOT NULL DEFAULT 0,
    semantic_matched    INTEGER NOT NULL DEFAULT 0,
    notified            INTEGER NOT NULL DEFAULT 0,
    suppressed_tier     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS run_sources (
    run_id          INTEGER NOT NULL REFERENCES runs(id),
    source          TEXT NOT NULL,
    fetched_count   INTEGER,
    new_count       INTEGER,
    error           TEXT,
    PRIMARY KEY (run_id, source)
);
"""


def init_db(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now(UTC).isoformat()


def upsert_listing(conn: sqlite3.Connection, listing: Listing) -> bool:
    """Insert if new, else refresh mutable fields + last_seen. Returns True
    if this listing was newly inserted (i.e. genuinely new this run).
    """
    now = _now()
    existing = conn.execute(
        "SELECT url_hash FROM listings WHERE url_hash = ?", (listing.id,)
    ).fetchone()

    if existing is None:
        conn.execute(
            """
            INSERT INTO listings (
                url_hash, source, url, title, price_eur, price_note, size_m2,
                district, heating, access_hours, lease_type, description,
                status, first_seen, last_seen
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)
            """,
            (
                listing.id, listing.source, listing.url, listing.title,
                listing.price_eur, listing.price_note, listing.size_m2,
                listing.district, listing.heating, listing.access_hours,
                listing.lease_type, listing.description, now, now,
            ),
        )
        conn.commit()
        return True

    conn.execute(
        """
        UPDATE listings SET
            title = ?, price_eur = ?, price_note = ?, size_m2 = ?,
            district = ?, heating = ?, access_hours = ?, lease_type = ?,
            description = ?, last_seen = ?
        WHERE url_hash = ?
        """,
        (
            listing.title, listing.price_eur, listing.price_note, listing.size_m2,
            listing.district, listing.heating, listing.access_hours, listing.lease_type,
            listing.description, now, listing.id,
        ),
    )
    conn.commit()
    return False


def is_new(conn: sqlite3.Connection, listing_id: str) -> bool:
    row = conn.execute(
        "SELECT status FROM listings WHERE url_hash = ?", (listing_id,)
    ).fetchone()
    return row is not None and row["status"] == "new"


def mark_notified(conn: sqlite3.Connection, listing_id: str) -> None:
    conn.execute(
        "UPDATE listings SET status = 'notified' WHERE url_hash = ?", (listing_id,)
    )
    conn.commit()


def mark_rejected(conn: sqlite3.Connection, listing_id: str, reason: str) -> None:
    conn.execute(
        "UPDATE listings SET status = 'rejected', match_reason = ? WHERE url_hash = ?",
        (reason, listing_id),
    )
    conn.commit()


def set_match_result(
    conn: sqlite3.Connection,
    listing_id: str,
    confidence: str | None,
    reason: str | None,
    tier: str | None,
) -> None:
    conn.execute(
        "UPDATE listings SET match_confidence = ?, match_reason = ?, tier = ? WHERE url_hash = ?",
        (confidence, reason, tier, listing_id),
    )
    conn.commit()


def is_source_due(conn: sqlite3.Connection, source: str, poll_interval_hours: float) -> bool:
    row = conn.execute(
        "SELECT last_run_at FROM source_state WHERE source = ?", (source,)
    ).fetchone()
    if row is None or row["last_run_at"] is None:
        return True

    last_run = datetime.fromisoformat(row["last_run_at"])
    return datetime.now(UTC) >= last_run + timedelta(hours=poll_interval_hours)


def record_source_run(conn: sqlite3.Connection, source: str) -> None:
    conn.execute(
        """
        INSERT INTO source_state (source, last_run_at) VALUES (?, ?)
        ON CONFLICT(source) DO UPDATE SET last_run_at = excluded.last_run_at
        """,
        (source, _now()),
    )
    conn.commit()


def start_run(conn: sqlite3.Connection, dry_run: bool, include_adjacent: bool) -> int:
    """Record the start of an orchestrator invocation. Returns the run id,
    used to correlate per-source fetch stats and the eventual finish_run
    call — one row per `uv run main.py` invocation, not per source.
    """
    cursor = conn.execute(
        "INSERT INTO runs (started_at, dry_run, include_adjacent) VALUES (?, ?, ?)",
        (_now(), int(dry_run), int(include_adjacent)),
    )
    conn.commit()
    return cursor.lastrowid


def record_source_fetch(
    conn: sqlite3.Connection,
    run_id: int,
    source: str,
    fetched_count: int,
    new_count: int,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO run_sources (run_id, source, fetched_count, new_count, error)
        VALUES (?, ?, ?, ?, ?)
        """,
        (run_id, source, fetched_count, new_count, error),
    )
    conn.commit()


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    prefilter_rejected: int,
    semantic_evaluated: int,
    semantic_matched: int,
    notified: int,
    suppressed_tier: int,
) -> None:
    total_fetched, total_new = conn.execute(
        "SELECT COALESCE(SUM(fetched_count), 0), COALESCE(SUM(new_count), 0) FROM run_sources WHERE run_id = ?",
        (run_id,),
    ).fetchone()

    conn.execute(
        """
        UPDATE runs SET
            finished_at = ?, total_fetched = ?, total_new = ?,
            prefilter_rejected = ?, semantic_evaluated = ?, semantic_matched = ?,
            notified = ?, suppressed_tier = ?
        WHERE id = ?
        """,
        (
            _now(), total_fetched, total_new,
            prefilter_rejected, semantic_evaluated, semantic_matched,
            notified, suppressed_tier, run_id,
        ),
    )
    conn.commit()


def get_run_report(conn: sqlite3.Connection, since_days: int) -> dict:
    """Aggregate run history + notified listings over the trailing window,
    for a periodic (e.g. weekly) process review — not meant for machine
    consumption, just enough structure for main.py to print a summary.
    """
    since = (datetime.now(UTC) - timedelta(days=since_days)).isoformat()

    totals = conn.execute(
        """
        SELECT
            COUNT(*) AS run_count,
            COALESCE(SUM(total_fetched), 0) AS total_fetched,
            COALESCE(SUM(total_new), 0) AS total_new,
            COALESCE(SUM(prefilter_rejected), 0) AS prefilter_rejected,
            COALESCE(SUM(semantic_evaluated), 0) AS semantic_evaluated,
            COALESCE(SUM(semantic_matched), 0) AS semantic_matched,
            COALESCE(SUM(notified), 0) AS notified,
            COALESCE(SUM(suppressed_tier), 0) AS suppressed_tier
        FROM runs WHERE started_at >= ?
        """,
        (since,),
    ).fetchone()

    per_source = conn.execute(
        """
        SELECT
            rs.source AS source,
            COUNT(*) AS run_count,
            COALESCE(SUM(rs.fetched_count), 0) AS fetched_count,
            COALESCE(SUM(rs.new_count), 0) AS new_count,
            SUM(CASE WHEN rs.error IS NOT NULL THEN 1 ELSE 0 END) AS error_count
        FROM run_sources rs
        JOIN runs r ON r.id = rs.run_id
        WHERE r.started_at >= ?
        GROUP BY rs.source
        ORDER BY rs.source
        """,
        (since,),
    ).fetchall()

    notified_listings = conn.execute(
        """
        SELECT title, source, price_eur, size_m2, match_confidence, tier, first_seen, url
        FROM listings
        WHERE status = 'notified' AND first_seen >= ?
        ORDER BY first_seen DESC
        """,
        (since,),
    ).fetchall()

    return {
        "since_days": since_days,
        "totals": dict(totals),
        "per_source": [dict(row) for row in per_source],
        "notified_listings": [dict(row) for row in notified_listings],
    }

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime

PLACEHOLDER_PREFIX = "PLACEHOLDER_FILL_IN_"


def is_placeholder(url: str | None) -> bool:
    """True if a configured URL is still an unfilled placeholder."""
    return url is None or url.startswith(PLACEHOLDER_PREFIX)


@dataclass
class Listing:
    """A single scraped listing. Filterable fields are Optional/tri-state:
    None means "unstated by the source", not "no" — downstream filtering
    must never auto-disqualify on missing data, only on explicit conflicts.
    """

    url: str
    source: str
    title: str

    price_eur: float | None = None
    price_note: str | None = None
    size_m2: float | None = None
    district: str | None = None
    heating: bool | None = None
    access_hours: str | None = None
    lease_type: str | None = None
    power_note: str | None = None

    description: str = ""

    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    raw: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        """Stable dedup key — hash of the canonical URL."""
        return hashlib.sha256(self.url.encode("utf-8")).hexdigest()


class Fetcher(ABC):
    """One instance per configured source, constructed from that source's
    config.yaml block. Implementations must not let a single malformed
    listing abort the whole fetch — log and skip it instead.
    """

    name: str

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    async def fetch(self) -> list[Listing]: ...

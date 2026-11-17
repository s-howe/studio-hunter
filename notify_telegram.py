from __future__ import annotations

import html
import logging

import httpx

from filter import SemanticVerdict
from sources.base import Listing

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Immediate per-match Telegram push via the plain Bot HTTP API — no
    bot-framework SDK needed for a single sendMessage call.
    """

    def __init__(self, bot_token: str, chat_id: str):
        self._base = f"https://api.telegram.org/bot{bot_token}"
        self._chat_id = chat_id

    def send_listing(self, listing: Listing, verdict: SemanticVerdict) -> None:
        text = self._format_message(listing, verdict)
        try:
            response = httpx.post(
                f"{self._base}/sendMessage",
                data={
                    "chat_id": self._chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                },
                timeout=10.0,
            )
            response.raise_for_status()
        except Exception:
            logger.exception("telegram: failed to send notification for %s", listing.url)

    @staticmethod
    def _format_message(listing: Listing, verdict: SemanticVerdict) -> str:
        price = f"€{listing.price_eur:.0f}" if listing.price_eur is not None else "price unstated"
        size = f"{listing.size_m2:.0f}m²" if listing.size_m2 is not None else "size unstated"

        lines = [
            f"<b>{html.escape(listing.title or 'Untitled listing')}</b>",
            f"{price} · {size} · {html.escape(listing.source)}",
            listing.url,
        ]

        if verdict.confidence != "high":
            lines.append(f"⚠️ confidence: {verdict.confidence} — {html.escape(verdict.reason)}")

        return "\n".join(lines)

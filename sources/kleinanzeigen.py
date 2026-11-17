from __future__ import annotations

import logging
import re

from playwright.async_api import async_playwright

from sources.base import Fetcher, Listing, is_placeholder

logger = logging.getLogger(__name__)

_SIZE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s?m(?:²|2\b)")
_PRICE_RE = re.compile(r"(\d[\d.,]*)\s?€")

# Safety cap on pages followed per search (results are sorted newest-first,
# so this bounds worst-case run time/request volume against a search with
# an unexpectedly large result count rather than paginating forever).
_MAX_PAGES = 10


class KleinanzeigenFetcher(Fetcher):
    """Bespoke fetcher for Kleinanzeigen — highest volume, highest value
    source. Config supplies a list of saved-search URLs, one per
    category/district combination you care about.
    """

    name = "kleinanzeigen"

    async def fetch(self) -> list[Listing]:
        search_urls = self.config.get("search_urls", [])
        listings: list[Listing] = []

        active_urls = [su for su in search_urls if not is_placeholder(su.get("url"))]
        if not active_urls:
            logger.warning(
                "kleinanzeigen: all search_urls are unfilled placeholders, skipping fetch"
            )
            return []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                for search in active_urls:
                    listings.extend(await self._fetch_search(browser, search))
            finally:
                await browser.close()

        return listings

    async def _fetch_search(self, browser, search: dict) -> list[Listing]:
        # Confirmed live: reusing one Page for sequential navigations
        # (across searches, and across pages within a paginated search)
        # causes every navigation after the first to get silently blocked
        # (real listings never render). A fresh Page per navigation, same
        # browser instance, doesn't trigger this — no added delay needed.
        name = search.get("name", "unnamed search")
        next_url = search["url"]
        out: list[Listing] = []

        for page_num in range(1, _MAX_PAGES + 1):
            page = await browser.new_page()
            try:
                await page.goto(next_url, timeout=30_000)
                await page.wait_for_selector("article[data-adid]", timeout=15_000)
                cards = await page.query_selector_all("article[data-adid]")

                for card in cards:
                    try:
                        out.append(await self._parse_card(card))
                    except Exception:
                        logger.warning(
                            "kleinanzeigen: failed to parse a card in '%s' page %d, skipping",
                            name, page_num, exc_info=True,
                        )
                        continue

                # Kleinanzeigen's frontend rewrite (Astro/Tailwind, confirmed
                # live 2026-09-01) dropped stable BEM class names in favor of
                # generated utility classes — aria-label is the one thing on
                # the "next page" control that's still semantic/stable.
                next_link = await page.query_selector('a[aria-label="Nächste"]')
                next_href = await next_link.get_attribute("href") if next_link else None
            except Exception:
                logger.exception("kleinanzeigen: failed to load search '%s' page %d (%s)", name, page_num, next_url)
                break
            finally:
                await page.close()

            if not next_href:
                break
            next_url = f"https://www.kleinanzeigen.de{next_href}" if next_href.startswith("/") else next_href

        return out

    async def _parse_card(self, card) -> Listing:
        # Card markup as of the 2026-09-01 Astro/Tailwind rewrite: the
        # <article> itself carries data-adid/data-href (stable, semantic —
        # unlike the generated utility classes everything else uses). Size
        # and price each live in their own single-line <p>, so matching each
        # paragraph's full text against the size/price patterns (rather than
        # picking a paragraph by class) survives future class churn; the
        # first paragraph that's neither is the description.
        href = await card.get_attribute("data-href")
        listing_url = f"https://www.kleinanzeigen.de{href}" if href and href.startswith("/") else (href or "")

        title_el = await card.query_selector("h3 a")
        title = (await title_el.inner_text()).strip() if title_el else ""

        location_el = await card.query_selector('svg[data-title="locationOutline"] + span')
        district = (await location_el.inner_text()).strip() if location_el else None

        paragraphs = await card.query_selector_all("p")
        texts = [(await p.inner_text()).strip() for p in paragraphs]
        texts = [t for t in texts if t]

        # Price must be the paragraph's *entire* text (fullmatch) — some
        # cards show a combined "63 m² · 15 €/m²" paragraph, and a lenient
        # search there would wrongly grab the €/m² rate as the listing
        # price. Size uses search instead, precisely to still match that
        # combined paragraph.
        description = ""
        price_eur = price_note = None
        size_m2 = None
        for text in texts:
            price_match = _PRICE_RE.fullmatch(text)
            if price_match and price_eur is None:
                price_eur = _to_float(price_match.group(1))
                price_note = text
                continue
            size_match = _SIZE_RE.search(text)
            if size_match and size_m2 is None:
                size_m2 = _to_float(size_match.group(1))
                continue
            if not description:
                description = text

        return Listing(
            url=listing_url,
            source=self.name,
            title=title,
            price_eur=price_eur,
            price_note=price_note,
            size_m2=size_m2,
            district=district,
            description=description,
        )


def _to_float(text: str) -> float | None:
    try:
        return float(text.replace(".", "").replace(",", "."))
    except ValueError:
        return None

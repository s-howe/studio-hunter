from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Literal

import anthropic

from sources.base import Listing

logger = logging.getLogger(__name__)

BATCH_SIZE = 15

_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "match": {"type": "boolean"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "reason": {"type": "string"},
                },
                "required": ["id", "match", "confidence", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


@dataclass
class SemanticVerdict:
    id: str
    match: bool
    confidence: Literal["high", "medium", "low"]
    reason: str


def keyword_prefilter(listing: Listing, criteria: dict) -> tuple[bool, str]:
    """Cheap, deterministic, no I/O. Only rejects on strong signals; missing
    data always survives to the semantic stage rather than being
    auto-disqualified (per spec: informal listings under-specify).
    """
    haystack = f"{listing.title}\n{listing.description}".lower()

    for keyword in criteria.get("hard_exclude_keywords", []):
        if keyword.lower() in haystack:
            return False, f"hard-exclude keyword matched: '{keyword}'"

    price_max = criteria.get("price_eur_max")
    if listing.price_eur is not None and price_max is not None and listing.price_eur > price_max * 1.5:
        return False, f"price {listing.price_eur} far exceeds cap {price_max}"

    size_cfg = criteria.get("size_m2", {})
    size_min, size_max = size_cfg.get("min"), size_cfg.get("max")
    if (
        listing.size_m2 is not None
        and size_min is not None
        and size_max is not None
        and (listing.size_m2 < size_min * 0.5 or listing.size_m2 > size_max * 2)
    ):
        return False, f"size {listing.size_m2}m² way outside range {size_min}-{size_max}m²"

    districts = criteria.get("districts", {})
    target_districts = set(districts.get("primary", [])) | set(districts.get("fallback", []))
    if (
        listing.district
        and target_districts
        and not any(d.lower() in listing.district.lower() for d in target_districts)
    ):
        return False, f"district '{listing.district}' is not a target district"

    return True, "survived prefilter"


def tier_listing(listing: Listing, criteria: dict, include_adjacent: bool) -> Literal["primary", "adjacent", "suppressed"]:
    """Primary districts always notify. Fallback/adjacent districts are
    fetched+stored but suppressed from notification unless explicitly
    enabled (config toggle or --include-adjacent). Unknown district is
    treated as primary-eligible — never suppress on missing info.
    """
    districts = criteria.get("districts", {})
    primary = [d.lower() for d in districts.get("primary", [])]
    fallback = [d.lower() for d in districts.get("fallback", [])]
    fallback_enabled = districts.get("fallback_enabled", False) or include_adjacent

    if not listing.district:
        return "primary"

    district_lower = listing.district.lower()
    if any(d in district_lower for d in primary):
        return "primary"
    if any(d in district_lower for d in fallback):
        return "primary" if fallback_enabled else "suppressed"

    return "adjacent" if fallback_enabled else "suppressed"


def _build_system_prompt(criteria: dict) -> str:
    context_notes = criteria.get("context_notes", "")
    return (
        "You are screening rental listings against a fixed set of criteria. "
        "The Context section below describes who is searching and where.\n\n"
        f"Context:\n{context_notes}\n\n"
        f"Criteria (JSON):\n{json.dumps(criteria, ensure_ascii=False, indent=2)}\n\n"
        "For each listing, decide whether it plausibly matches these criteria.\n"
        "IMPORTANT: if a detail (price, heating, access, lease type, etc.) is not "
        "mentioned in the listing, do NOT disqualify it for that reason. Instead "
        "return match=true with confidence='low' and note in 'reason' what is "
        "unverified and should be checked manually. Only return match=false when "
        "something explicitly stated in the listing conflicts with a hard "
"requirement stated in the criteria or context (e.g. explicitly "
        "restricted access hours, explicitly hourly-only rental, explicitly "
        "no electricity, or a size/price far outside range).\n"
        "Return one verdict per listing id, in the same order given."
    )


def _listing_prompt_block(listing: Listing) -> str:
    return (
        f"id: {listing.id}\n"
        f"source: {listing.source}\n"
        f"title: {listing.title}\n"
        f"price_eur: {listing.price_eur}\n"
        f"price_note: {listing.price_note}\n"
        f"size_m2: {listing.size_m2}\n"
        f"district: {listing.district}\n"
        f"heating: {listing.heating}\n"
        f"access_hours: {listing.access_hours}\n"
        f"lease_type: {listing.lease_type}\n"
        f"description: {listing.description[:2000]}"
    )


async def semantic_filter(
    listings: list[Listing],
    criteria: dict,
    client: anthropic.Anthropic,
    model: str = "claude-haiku-4-5",
) -> list[SemanticVerdict]:
    if not listings:
        return []

    system_prompt = _build_system_prompt(criteria)
    verdicts: list[SemanticVerdict] = []

    for i in range(0, len(listings), BATCH_SIZE):
        batch = listings[i : i + BATCH_SIZE]
        user_content = "\n\n---\n\n".join(_listing_prompt_block(listing) for listing in batch)

        try:
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
                output_config={"format": {"type": "json_schema", "schema": _VERDICT_SCHEMA}},
            )
            payload = json.loads(response.content[0].text)
            for result in payload.get("results", []):
                verdicts.append(SemanticVerdict(**result))
        except Exception:
            logger.exception("semantic_filter: batch starting at index %d failed, treating as no matches", i)
            for listing in batch:
                verdicts.append(
                    SemanticVerdict(
                        id=listing.id,
                        match=False,
                        confidence="low",
                        reason="semantic filter call failed — not evaluated",
                    )
                )

    return verdicts

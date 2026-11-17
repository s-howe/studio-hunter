import pytest

from filter import keyword_prefilter, tier_listing
from sources.base import Listing

CRITERIA = {
    "price_eur_max": 1200,
    "size_m2": {"min": 15, "max": 30},
    "hard_exclude_keywords": ["nur tagsüber", "stundenweise", "kein Strom"],
    "districts": {
        "primary": ["Neukölln", "Kreuzberg"],
        "fallback": ["Mitte", "Friedrichshain", "Tempelhof"],
        "fallback_enabled": False,
    },
}


def make_listing(**overrides) -> Listing:
    defaults = {"url": "https://example.com/1", "source": "test", "title": "Raum in Neukölln"}
    defaults.update(overrides)
    return Listing(**defaults)


def test_hard_exclude_keyword_in_title_rejects():
    listing = make_listing(title="Proberaum nur tagsüber verfügbar")
    ok, reason = keyword_prefilter(listing, CRITERIA)
    assert ok is False
    assert "hard-exclude" in reason


def test_hard_exclude_keyword_in_description_rejects():
    listing = make_listing(description="Vermietung nur stundenweise möglich")
    ok, _reason = keyword_prefilter(listing, CRITERIA)
    assert ok is False


def test_price_far_over_cap_rejects():
    listing = make_listing(price_eur=2500)
    ok, reason = keyword_prefilter(listing, CRITERIA)
    assert ok is False
    assert "price" in reason


def test_price_moderately_over_cap_survives():
    # spec: flexible above cap if otherwise a strong fit — prefilter should
    # not reject a price that's only somewhat over budget.
    listing = make_listing(price_eur=1300)
    ok, _ = keyword_prefilter(listing, CRITERIA)
    assert ok is True


def test_missing_price_survives():
    listing = make_listing(price_eur=None)
    ok, _ = keyword_prefilter(listing, CRITERIA)
    assert ok is True


def test_size_way_out_of_range_rejects():
    listing = make_listing(size_m2=200)
    ok, reason = keyword_prefilter(listing, CRITERIA)
    assert ok is False
    assert "size" in reason


def test_missing_size_survives():
    listing = make_listing(size_m2=None)
    ok, _ = keyword_prefilter(listing, CRITERIA)
    assert ok is True


def test_wrong_district_rejects():
    listing = make_listing(district="Charlottenburg")
    ok, reason = keyword_prefilter(listing, CRITERIA)
    assert ok is False
    assert "district" in reason


def test_missing_district_survives():
    listing = make_listing(district=None)
    ok, _ = keyword_prefilter(listing, CRITERIA)
    assert ok is True


def test_primary_district_survives():
    listing = make_listing(district="Neukölln")
    ok, _ = keyword_prefilter(listing, CRITERIA)
    assert ok is True


@pytest.mark.parametrize(
    "district,fallback_enabled,include_adjacent,expected",
    [
        ("Neukölln", False, False, "primary"),
        (None, False, False, "primary"),
        ("Mitte", False, False, "suppressed"),
        ("Mitte", True, False, "primary"),
        ("Mitte", False, True, "primary"),
        ("Charlottenburg", False, False, "suppressed"),
    ],
)
def test_tier_listing(district, fallback_enabled, include_adjacent, expected):
    criteria = {
        "districts": {
            "primary": ["Neukölln", "Kreuzberg"],
            "fallback": ["Mitte", "Friedrichshain", "Tempelhof"],
            "fallback_enabled": fallback_enabled,
        }
    }
    listing = make_listing(district=district)
    assert tier_listing(listing, criteria, include_adjacent) == expected

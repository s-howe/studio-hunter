from sources.base import Listing, is_placeholder


def test_listing_id_is_stable_hash_of_url():
    a = Listing(url="https://example.com/1", source="test", title="A")
    b = Listing(url="https://example.com/1", source="test", title="different title")
    c = Listing(url="https://example.com/2", source="test", title="A")

    assert a.id == b.id
    assert a.id != c.id
    assert len(a.id) == 64  # sha256 hex digest


def test_is_placeholder():
    assert is_placeholder("PLACEHOLDER_FILL_IN_SOMETHING") is True
    assert is_placeholder(None) is True
    assert is_placeholder("https://real-site.example/search") is False

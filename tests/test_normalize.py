"""Normalization: folding, location parsing, URL canonicalization."""

from hunt.normalize import board_of, dedup_key, fold, normalize_url, parse_location

DJINNI_URL = (
    "https://djinni.co/jobs/847334-junior-penetration-tester/?utm_source=freehire.me"
)


def test_fold_handles_turkish_diacritics() -> None:
    assert fold("İstanbul") == "istanbul"
    assert fold("SİBER GÜVENLİK") == "siber guvenlik"
    assert fold("Ankara") == "ankara"


def test_parses_city_and_country() -> None:
    parsed = parse_location("İstanbul, Türkiye")
    assert parsed.country == "TR"
    assert parsed.city == "istanbul"
    assert parsed.remote is False


def test_parses_slash_separated_turkish_location() -> None:
    parsed = parse_location("Ankara / Türkiye")
    assert parsed.country == "TR"
    assert parsed.city == "ankara"


def test_detects_remote_in_turkish_and_english() -> None:
    assert parse_location("Uzaktan / Remote").remote is True
    assert parse_location("Remote — Türkiye").remote is True
    assert parse_location("Remote").remote is True


def test_unresolvable_location_resolves_to_nothing() -> None:
    for text in ("", "   ", None, "Somewhere Nice"):
        parsed = parse_location(text)
        assert parsed.country is None
        assert parsed.city is None


def test_ukrainian_cities_map_to_ua() -> None:
    for text in ("Kyiv, Ukraine", "Lviv", "Odessa"):
        assert parse_location(text).country == "UA"


def test_board_of_strips_www_prefix() -> None:
    assert board_of(DJINNI_URL) == "djinni.co"
    assert board_of("https://www.kariyer.net/is-ilani/x-1") == "kariyer.net"


def test_normalize_url_drops_tracking_query() -> None:
    assert normalize_url(DJINNI_URL) == (
        "https://djinni.co/jobs/847334-junior-penetration-tester/"
    )


def test_dedup_key_is_stable_across_query_and_case_noise() -> None:
    noisy = (
        "https://DJINNI.co/jobs/847334-junior-penetration-tester/#details"
    )
    assert dedup_key(DJINNI_URL) == dedup_key(noisy)

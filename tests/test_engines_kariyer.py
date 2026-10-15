"""kariyer.net engine behavior against checked-in pages (no network)."""

from pathlib import Path

from hunt.engines.kariyer import KariyerScraper

FIXTURES = Path(__file__).parent / "fixtures"


def _scraper_serving(pages: list[tuple[str, int, str | None]]) -> KariyerScraper:
    """Build an engine whose HTTP layer serves fixture pages in order."""
    scraper = KariyerScraper(delay_range=None)
    remaining = list(pages)

    def fake_get(_url: str) -> tuple[int, str | None]:
        if not remaining:
            return 200, ""
        _label, status, fixture = remaining.pop(0)
        body = (
            FIXTURES.joinpath(fixture).read_text(encoding="utf-8")
            if status == 200 and fixture
            else None
        )
        return status, body

    scraper._get = fake_get  # type: ignore[method-assign]
    return scraper


def test_collects_cards_from_search_pages() -> None:
    scraper = _scraper_serving(
        [
            ("search", 200, "kariyer_search_page.html"),
            ("search", 200, "kariyer_search_empty.html"),
            ("detail", 200, "kariyer_detail_page.html"),
            ("detail", 200, "kariyer_detail_page.html"),
        ]
    )

    candidates = scraper.scrape("siber güvenlik", 50)

    assert [(candidate.title, candidate.company) for candidate in candidates] == [
        ("Siber Güvenlik Uzmanı", "HAVELSAN A.Ş."),
        ("SOC Analisti", "Teknoloji A.Ş."),
    ]
    first = candidates[0]
    assert first.url == (
        "https://www.kariyer.net/is-ilani/havelsan-siber-guvenlik-uzmani-4570999"
    )
    assert first.location_raw == "Ankara"
    assert first.remote is False
    assert "Siber Güvenlik Uzmanı" in (first.description or "")
    assert candidates[1].remote is True


def test_drops_cards_that_cannot_be_identified() -> None:
    scraper = _scraper_serving(
        [
            ("search", 200, "kariyer_search_page.html"),
            ("search", 200, "kariyer_search_empty.html"),
        ]
    )
    # Card three has no company and must not survive; the cap keeps the run
    # from fetching its detail page.
    scraper.detail_cap = 0

    candidates = scraper.scrape("siber güvenlik", 50)

    assert len(candidates) == 2


def test_blocked_page_stops_collection_instead_of_failing() -> None:
    scraper = _scraper_serving([("search", 403, None)])

    candidates = scraper.scrape("siber güvenlik", 50)

    assert candidates == []
    assert scraper.warnings == ["kariyer.net stopped at page 1 (HTTP 403)"]


def test_remote_only_keeps_remote_cards() -> None:
    scraper = _scraper_serving(
        [
            ("search", 200, "kariyer_search_page.html"),
            ("search", 200, "kariyer_search_empty.html"),
        ]
    )

    candidates = scraper.scrape("siber güvenlik", 50, is_remote=True)

    assert [candidate.title for candidate in candidates] == ["SOC Analisti"]


def test_repeated_sponsored_cards_are_kept_once() -> None:
    scraper = _scraper_serving(
        [
            ("search", 200, "kariyer_search_page.html"),
            ("search", 200, "kariyer_search_page.html"),
            ("search", 200, "kariyer_search_empty.html"),
        ]
    )
    scraper.detail_cap = 0

    candidates = scraper.scrape("siber güvenlik", 50)

    assert len(candidates) == 2
    assert len({candidate.url for candidate in candidates}) == 2


def test_results_wanted_caps_the_collection() -> None:
    scraper = _scraper_serving(
        [
            ("search", 200, "kariyer_search_page.html"),
            ("search", 200, "kariyer_search_empty.html"),
        ]
    )
    scraper.detail_cap = 0

    candidates = scraper.scrape("siber güvenlik", 1)

    assert len(candidates) == 1

"""techcareer.net engine behavior against checked-in pages (no network)."""

from pathlib import Path

from hunt.engines.techcareer import TechCareerScraper

FIXTURES = Path(__file__).parent / "fixtures"


def _scraper_serving(pages: list[tuple[int, str | None]]) -> TechCareerScraper:
    """Build an engine whose HTTP layer serves fixture pages in order."""
    scraper = TechCareerScraper(delay_range=None)
    remaining = list(pages)

    def fake_get(_url: str) -> tuple[int, str | None]:
        if not remaining:
            return 200, ""
        status, fixture = remaining.pop(0)
        body = (
            FIXTURES.joinpath(fixture).read_text(encoding="utf-8")
            if status == 200 and fixture
            else None
        )
        return status, body

    scraper._get = fake_get  # type: ignore[method-assign]
    return scraper


def test_filters_listings_by_keyword() -> None:
    scraper = _scraper_serving(
        [
            (200, "techcareer_list_page.html"),
            (200, "techcareer_list_empty.html"),
            (200, "techcareer_detail_page.html"),
        ]
    )

    candidates = scraper.scrape("siber güvenlik", 50)

    assert [(candidate.title, candidate.company) for candidate in candidates] == [
        ("Siber Güvenlik Uzmanı", "HAVELSAN")
    ]
    first = candidates[0]
    assert first.url == (
        "https://www.techcareer.net/jobs/detail/siber-guvenlik-uzmani-4573483"
    )
    assert first.location_raw == "Ankara / Türkiye"
    assert "SIEM deneyimi" in (first.description or "")


def test_matches_english_titles_and_turkish_diacritics() -> None:
    soc_scraper = _scraper_serving(
        [
            (200, "techcareer_list_page.html"),
            (200, "techcareer_list_empty.html"),
        ]
    )
    soc_scraper.detail_cap = 0
    uppercase_scraper = _scraper_serving(
        [
            (200, "techcareer_list_page.html"),
            (200, "techcareer_list_empty.html"),
        ]
    )
    uppercase_scraper.detail_cap = 0

    soc_candidates = soc_scraper.scrape("SOC analyst", 50)
    uppercase_candidates = uppercase_scraper.scrape("SİBER GÜVENLİK", 50)

    assert [candidate.title for candidate in soc_candidates] == ["SOC Analisti"]
    assert [candidate.title for candidate in uppercase_candidates] == [
        "Siber Güvenlik Uzmanı"
    ]


def test_remote_only_keeps_remote_listings() -> None:
    scraper = _scraper_serving(
        [
            (200, "techcareer_list_page.html"),
            (200, "techcareer_list_empty.html"),
        ]
    )
    scraper.detail_cap = 0

    candidates = scraper.scrape("soc", 50, is_remote=True)

    assert [candidate.title for candidate in candidates] == ["SOC Analisti"]


def test_blocked_page_stops_collection_instead_of_failing() -> None:
    scraper = _scraper_serving([(403, None)])

    candidates = scraper.scrape("siber güvenlik", 50)

    assert candidates == []
    assert scraper.warnings == ["techcareer.net stopped at page 1 (HTTP 403)"]


def test_page_without_job_list_stops_collection() -> None:
    scraper = _scraper_serving([(200, "kariyer_search_empty.html")])

    assert scraper.scrape("siber güvenlik", 50) == []

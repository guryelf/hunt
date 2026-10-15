"""kariyer.net listing engine built on its server-rendered search pages.

kariyer.net is protected by PerimeterX, so this module behaves like a slow
human reader: a browser user-agent, Turkish Accept-Language, and
multi-second random gaps between requests. A blocked page (HTTP 403/429)
ends pagination with whatever was already collected instead of failing
the whole run.

Ported from the tracker template (`ai-job-scraper`), now emitting gate
Candidates instead of JobPostings.
"""

from __future__ import annotations

import logging
import random
import time
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from ..gate import Candidate

logger = logging.getLogger(__name__)

_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0"
_REMOTE_WORK_MODELS = {"uzaktan", "remote"}
_MAX_PAGES = 10


class KariyerScraper:
    """Collect kariyer.net postings for one keyword at a time."""

    def __init__(
        self,
        *,
        base_url: str = "https://www.kariyer.net",
        detail_cap: int = 10,
        request_timeout: float = 30.0,
        delay_range: tuple[float, float] | None = (5.0, 15.0),
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.detail_cap = detail_cap
        self.request_timeout = request_timeout
        self.delay_range = delay_range
        # Non-fatal problems from the latest scrape, for the orchestrator.
        self.warnings: list[str] = []
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": _USER_AGENT,
                "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
            }
        )

    def scrape(
        self,
        keyword: str,
        results_wanted: int,
        *,
        is_remote: bool = False,
    ) -> list[Candidate]:
        """Collect postings for one keyword, stopping at blocks or the end.

        Blocked pages, empty pages, and unreadable rows degrade to partial
        results; only a keyword with zero collectable rows returns nothing.
        """
        self.warnings = []
        cards: dict[str, dict[str, object]] = {}
        page = 1
        while len(cards) < results_wanted and page <= _MAX_PAGES:
            status, html = self._get(self._search_url(keyword, page))
            if status != 200:
                logger.warning(
                    "kariyer.net stopped at page %d with HTTP %d; keeping %d jobs",
                    page,
                    status,
                    len(cards),
                )
                self.warnings.append(
                    f"kariyer.net stopped at page {page} (HTTP {status})"
                )
                break
            page_cards = self._parse_cards(html or "")
            if not page_cards:
                break
            for card in page_cards:
                if is_remote and not card["is_remote"]:
                    continue
                # Sponsored cards repeat at the top of every page; keep first.
                cards.setdefault(str(card["link"]), card)
            page += 1
            self._polite_delay()

        candidates = self._build_candidates(list(cards.values())[:results_wanted])
        return self._hydrate_descriptions(candidates)

    def _search_url(self, keyword: str, page: int) -> str:
        return f"{self.base_url}/is-ilanlari?kw={quote(keyword)}&cp={page}"

    def _get(self, url: str) -> tuple[int, str | None]:
        """Perform one HTTP GET, returning its status and body."""
        response = self._session.get(url, timeout=self.request_timeout)
        if not response.ok:
            return response.status_code, None
        return response.status_code, response.text

    def _polite_delay(self) -> None:
        if self.delay_range is None:
            return
        time.sleep(random.uniform(*self.delay_range))

    def _parse_cards(self, html: str) -> list[dict[str, object]]:
        """Extract raw fields from one page of listing cards."""
        soup = BeautifulSoup(html, "html.parser")
        cards: list[dict[str, object]] = []
        for item in soup.select('a[data-test="ad-card-item"]'):
            link = item.get("href") or ""
            if not link:
                continue
            link = link if link.startswith("http") else f"{self.base_url}{link}"
            work_model = _text(item.select_one('[data-test="work-model"]'))
            cards.append(
                {
                    "title": _text(item.select_one('[data-test="ad-card-title"]')),
                    "company": _text(item.select_one('[data-test="subtitle"]')),
                    "location": _text(item.select_one('[data-test="location"]')),
                    "link": link,
                    "is_remote": work_model.lower() in _REMOTE_WORK_MODELS,
                }
            )
        return cards

    def _build_candidates(self, cards: list[dict[str, object]]) -> list[Candidate]:
        candidates: list[Candidate] = []
        for card in cards:
            title = str(card["title"]).strip()
            company = str(card["company"]).strip()
            if not title or not company:
                # The template dropped these at JobPosting validation; a card
                # we cannot attribute is not a candidate.
                logger.warning(
                    "Skipping unusable kariyer.net card: missing title or company"
                )
                continue
            candidates.append(
                Candidate(
                    title=title,
                    company=company,
                    url=str(card["link"]),
                    location_raw=str(card["location"]) or None,
                    remote=bool(card["is_remote"]),
                )
            )
        return candidates

    def _hydrate_descriptions(self, candidates: list[Candidate]) -> list[Candidate]:
        """Fetch detail pages for the first candidates to collect descriptions."""
        for candidate in candidates[: self.detail_cap]:
            status, html = self._get(candidate.url)
            if status == 200 and html:
                description = _text(
                    BeautifulSoup(html, "html.parser").select_one(
                        '[data-test="qualifications-and-job-description"]'
                    )
                )
                if description:
                    candidate.description = description
            self._polite_delay()
        return candidates


def _text(element) -> str:
    """Normalize one element's visible text."""
    if element is None:
        return ""
    return element.get_text("\n", strip=True)

"""techcareer.net listing engine built on its Next.js server rendering.

techcareer.net renders its job list into the page's ``__NEXT_DATA__``
JSON, so one HTTP GET per page is enough. Keyword filtering happens on
our side against the listing fields, and detail pages provide the full
description the same way. Requests stay spaced to read like a polite
human reader.

Ported from the tracker template (`ai-job-scraper`), now emitting gate
Candidates instead of JobPostings.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from ..gate import Candidate
from ..normalize import fold

logger = logging.getLogger(__name__)

_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0"
_NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)
_MAX_PAGES = 10
_REMOTE_WORK_PLACES = {"uzaktan", "remote"}


class TechCareerScraper:
    """Collect techcareer.net postings for one keyword at a time."""

    def __init__(
        self,
        *,
        base_url: str = "https://www.techcareer.net",
        detail_cap: int = 10,
        request_timeout: float = 30.0,
        delay_range: tuple[float, float] | None = (2.0, 6.0),
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
        """Collect postings for one keyword across listing pages."""
        self.warnings = []
        jobs: dict[str, dict] = {}
        page = 1
        while len(jobs) < results_wanted and page <= _MAX_PAGES:
            status, html = self._get(self._list_url(page))
            if status != 200:
                logger.warning(
                    "techcareer.net stopped at page %d with HTTP %d; keeping %d jobs",
                    page,
                    status,
                    len(jobs),
                )
                self.warnings.append(
                    f"techcareer.net stopped at page {page} (HTTP {status})"
                )
                break
            items = self._listing_items(html or "")
            if not items:
                break
            for item in items:
                if item.get("isDisabledJob"):
                    continue
                if is_remote and not self._is_remote(item):
                    continue
                if not self._matches(item, keyword):
                    continue
                # Sponsored cards repeat at the top of every page; keep first.
                jobs.setdefault(self._link(item), item)
            page += 1
            self._polite_delay()

        candidates = self._build_candidates(list(jobs.values())[:results_wanted])
        return self._hydrate_descriptions(candidates)

    def _list_url(self, page: int) -> str:
        return (
            f"{self.base_url}/jobs?jobs%5BisCompleted%5D=false"
            f"&jobs%5Bpage%5D={quote(str(page))}&searchIdIsFiltered=false"
        )

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

    def _listing_items(self, html: str) -> list[dict]:
        """Return the server-rendered job list from one listing page."""
        match = _NEXT_DATA.search(html)
        if not match:
            return []
        try:
            payload = json.loads(match.group(1))
            job_list = payload["props"]["pageProps"]["initialJobList"]
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.warning("techcareer.net page without a readable job list")
            return []
        return list(job_list.get("jobListItems", []))

    @staticmethod
    def _is_remote(item: dict) -> bool:
        work_places = item.get("workPlaces") or []
        return any(
            str(place).casefold() in _REMOTE_WORK_PLACES for place in work_places
        )

    @staticmethod
    def _matches(item: dict, keyword: str) -> bool:
        """Match a keyword against the listing's searchable fields."""
        owner = item.get("owner") or {}
        searchable = " ".join(
            str(field)
            for field in (
                item.get("title"),
                item.get("jobTitleEn"),
                owner.get("name"),
                item.get("location"),
            )
            if field
        )
        return fold(keyword) in fold(searchable)

    @staticmethod
    def _link(item: dict) -> str:
        slug = str(item.get("slug") or item.get("id") or "")
        return f"https://www.techcareer.net/jobs/detail/{slug}"

    def _build_candidates(self, items: list[dict]) -> list[Candidate]:
        candidates: list[Candidate] = []
        for item in items:
            owner = item.get("owner") or {}
            title = str(item.get("title") or "").strip()
            company = str(owner.get("name") or "").strip()
            if not title or not company:
                # The template dropped these at JobPosting validation; a row
                # we cannot attribute is not a candidate.
                logger.warning(
                    "Skipping unusable techcareer.net row: missing title or company"
                )
                continue
            candidates.append(
                Candidate(
                    title=title,
                    company=company,
                    url=self._link(item),
                    location_raw=str(item.get("location") or "") or None,
                    remote=self._is_remote(item),
                )
            )
        return candidates

    def _hydrate_descriptions(self, candidates: list[Candidate]) -> list[Candidate]:
        """Fetch detail pages for the first candidates to collect descriptions."""
        for candidate in candidates[: self.detail_cap]:
            status, html = self._get(candidate.url)
            description = self._detail_description(html or "") if status == 200 else ""
            if description:
                candidate.description = description
            self._polite_delay()
        return candidates

    @staticmethod
    def _detail_description(html: str) -> str:
        """Extract the description HTML from a detail page."""
        match = _NEXT_DATA.search(html)
        if not match:
            return ""
        try:
            detail = json.loads(match.group(1))["props"]["pageProps"]["jobDetail"]
            description_html = detail["content"]["description"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return ""
        if not description_html:
            return ""
        return BeautifulSoup(str(description_html), "html.parser").get_text(
            "\n", strip=True
        )

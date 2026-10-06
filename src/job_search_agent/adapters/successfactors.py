from __future__ import annotations

import html as html_module
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ..models import JobPosting, PageDocument

SUCCESSFACTORS_MARKERS = (
    "/platform/js/j2w/",
    "rmkcdn.successfactors.com",
    "/services/recruiting/v1/jobs",
    "careerSiteCompanyId",
)
TOTAL_RE = re.compile(r"\b(?:of|to)\s+(\d[\d,]*)\s+(?:results|job postings?)\b", re.IGNORECASE)
EXTERNAL_ID_RE = re.compile(r"/(\d+)(?:-[a-z]{2}_[A-Z]{2})?/?(?:[?#].*)?$")
REMOTE_MARKERS = ("remote", "virtual", "work from home", "wfh")


@dataclass(slots=True)
class SuccessFactorsConfig:
    search_url: str


@dataclass(slots=True)
class SuccessFactorsExtraction:
    jobs: list[JobPosting]
    total_hits: int
    raw_job_count: int


def successfactors_config(html: str, page_url: str) -> SuccessFactorsConfig | None:
    """Detect an SAP SuccessFactors Recruiting Marketing careers site."""

    lowered = html.casefold()
    if not any(marker.casefold() in lowered for marker in SUCCESSFACTORS_MARKERS):
        return None
    parsed = urlsplit(page_url)
    if parsed.path.rstrip("/").casefold() == "/search":
        return SuccessFactorsConfig(search_url=page_url)
    return SuccessFactorsConfig(search_url=urljoin(page_url, "/search/"))


def _total_hits(soup: BeautifulSoup, raw_count: int) -> int:
    for element in soup.select('[aria-label*="job posting"]'):
        match = re.search(
            r"\bof\s+(\d[\d,]*)\b", str(element.get("aria-label") or ""), re.IGNORECASE
        )
        if match:
            return int(match.group(1).replace(",", ""))
    match = TOTAL_RE.search(soup.get_text(" ", strip=True))
    return int(match.group(1).replace(",", "")) if match else raw_count


def _company_name(soup: BeautifulSoup, page_url: str) -> str:
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    title = re.sub(r"\s+(?:careers|jobs)\s*$", "", title, flags=re.IGNORECASE).strip()
    if title:
        return title
    hostname = (urlsplit(page_url).hostname or "").removeprefix("www.")
    return hostname.split(".")[0].replace("-", " ").title()


def extract_successfactors_jobs(page: PageDocument) -> SuccessFactorsExtraction | None:
    """Extract rendered SAP SuccessFactors job cards without an LLM call."""

    soup = BeautifulSoup(page.html, "lxml")
    cards = soup.select('[data-testid="jobCard"]')
    if not cards:
        return None
    company = _company_name(soup, str(page.url))
    jobs: list[JobPosting] = []
    for card in cards:
        title_link = card.select_one('[data-testid^="jobCardTitle_"][href]')
        if title_link is None:
            title_link = card.select_one('a[href*="/job/"]')
        if title_link is None:
            continue
        title = title_link.get_text(" ", strip=True)
        job_url = urljoin(str(page.url), html_module.unescape(str(title_link.get("href") or "")))
        if not title or not job_url:
            continue
        location_element = card.select_one('[data-testid="jobCardLocation"]')
        location = location_element.get_text(" ", strip=True) if location_element else ""
        footer_values = [
            element.get_text(" ", strip=True)
            for element in card.select('[data-help-id^="jobCardFooterValue_"]')
            if element.get_text(" ", strip=True)
        ]
        if footer_values and footer_values[0] not in location:
            location = ", ".join(filter(None, (location.rstrip(" ,"), footer_values[0])))
        posted_at = footer_values[-1] if len(footer_values) > 1 else None
        match = EXTERNAL_ID_RE.search(job_url)
        combined = f"{title} {location}".casefold()
        jobs.append(
            JobPosting(
                title=title,
                company=company,
                location=location,
                work_mode=(
                    "remote" if any(marker in combined for marker in REMOTE_MARKERS) else "unknown"
                ),
                description=card.get_text(" ", strip=True),
                url=job_url,
                source_url=str(page.source_url),
                external_id=match.group(1) if match else None,
                posted_at=posted_at,
            )
        )
    return SuccessFactorsExtraction(
        jobs=jobs,
        total_hits=_total_hits(soup, len(jobs)),
        raw_job_count=len(cards),
    )

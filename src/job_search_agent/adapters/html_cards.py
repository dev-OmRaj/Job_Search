from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

from ..models import JobPosting, PageDocument

CARD_SELECTOR = ".job-div .search_job_box"
EXTERNAL_ID_RE = re.compile(r"/(\d+)$")


@dataclass(slots=True)
class HtmlCardExtraction:
    jobs: list[JobPosting]
    total_hits: int
    raw_job_count: int


def is_structured_html_job_listing(html: str) -> bool:
    soup = BeautifulSoup(html, "lxml")
    return bool(soup.select(CARD_SELECTOR))


def html_job_complete_url(html: str, page_url: str) -> str | None:
    """Return a server-rendered show-all link exposed by an HTML job board."""

    soup = BeautifulSoup(html, "lxml")
    if not soup.select(CARD_SELECTOR):
        return None
    link = soup.select_one("a.loadmore[href]")
    return urljoin(page_url, str(link.get("href"))) if link else None


def _element_text(card, selector: str) -> str:
    element = card.select_one(selector)
    return element.get_text(" ", strip=True) if element else ""


def _company_name(soup: BeautifulSoup, page_url: str) -> str:
    site_name = soup.find("meta", attrs={"property": "og:site_name"})
    if site_name and str(site_name.get("content") or "").strip():
        return str(site_name.get("content")).strip()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    if " - " in title:
        return title.rsplit(" - ", 1)[-1].strip()
    hostname = (urlsplit(page_url).hostname or "").removeprefix("www.")
    return hostname.split(".")[0].upper()


def extract_structured_html_jobs(page: PageDocument) -> HtmlCardExtraction | None:
    """Extract conventional server-rendered cards with explicit job fields."""

    soup = BeautifulSoup(page.html, "lxml")
    cards = soup.select(CARD_SELECTOR)
    if not cards:
        return None
    company = _company_name(soup, str(page.url))
    jobs: list[JobPosting] = []
    for card in cards:
        title = _element_text(card, "h3")
        link = card.select_one("a.job-apply[href], a[href]")
        job_url = urljoin(str(page.url), str(link.get("href") or "")) if link else ""
        if not title or not job_url:
            continue
        location = _element_text(card, ".job-loaction, .job-location")
        employment_type = _element_text(card, ".job-time")
        experience = re.sub(
            r"^\s*Exp(?:erience)?\s*",
            "",
            _element_text(card, ".job-exp"),
            flags=re.IGNORECASE,
        ).strip()
        skills_text = _element_text(card, ".skills-txt")
        skills = []
        if skills_text and skills_text.casefold() not in {"na", "n/a", "none"}:
            skills = [part.strip() for part in skills_text.split(",") if part.strip()]
        decoded_url = unquote(job_url)
        id_match = EXTERNAL_ID_RE.search(decoded_url)
        description = "; ".join(
            filter(
                None,
                (
                    f"Experience: {experience}" if experience else "",
                    f"Employment type: {employment_type}" if employment_type else "",
                    f"Skills: {skills_text}" if skills else "",
                ),
            )
        )
        jobs.append(
            JobPosting(
                title=title,
                company=company,
                location=location,
                description=description,
                url=job_url,
                source_url=str(page.source_url),
                external_id=id_match.group(1) if id_match else None,
                employment_type=employment_type or None,
                experience_level=experience or None,
                skills=skills,
            )
        )
    return HtmlCardExtraction(jobs=jobs, total_hits=len(cards), raw_job_count=len(cards))

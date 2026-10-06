from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from ..models import JobPosting, PageDocument

PHENOM_DDO_ASSIGNMENT = re.compile(r"\bphApp\.ddo\s*=\s*")
PHENOM_CONFIG_ASSIGNMENT = re.compile(r"\b(?:var\s+)?phApp\s*=\s*phApp\s*\|\|\s*")
PHENOM_URL_MAP_ASSIGNMENT = re.compile(r"\bphApp\.urlMap\s*=\s*")
UNRESOLVED_TEMPLATE_MARKERS = ("${", "{{", "%24%7b", "%7b%7b")


@dataclass(slots=True)
class PhenomExtraction:
    jobs: list[JobPosting]
    total_hits: int
    raw_job_count: int


def _assigned_json_object(html: str, assignment: re.Pattern[str]) -> dict | None:
    decoder = json.JSONDecoder()
    for match in assignment.finditer(html):
        try:
            value, _ = decoder.raw_decode(html, match.end())
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    return None


def _phenom_search_payload(html: str) -> dict | None:
    """Decode the search payload assigned to Phenom's global page-data variable."""

    ddo = _assigned_json_object(html, PHENOM_DDO_ASSIGNMENT)
    search = ddo.get("eagerLoadRefineSearch") if ddo else None
    if isinstance(search, dict) and isinstance(search.get("data"), dict):
        return search
    return None


def is_phenom_listing(html: str) -> bool:
    return _phenom_search_payload(html) is not None


def phenom_next_page_urls(html: str, base_url: str) -> list[str]:
    """Return the next Phenom result-page URL.

    Some Phenom portals expose an HTML ``rel=next`` link. Others, including
    GE Aerospace, only put ``hits`` and ``totalHits`` in ``phApp.ddo`` and
    expect their JavaScript to request the next ``from`` offset. Support both
    forms so the crawler does not silently stop at the initial ten jobs.
    """

    search = _phenom_search_payload(html)
    if search is None:
        return []
    soup = BeautifulSoup(html, "lxml")
    links: list[str] = []
    for element in soup.find_all("link", href=True):
        rel = {str(value).lower() for value in element.get("rel", [])}
        if "next" not in rel:
            continue
        absolute = urljoin(base_url, str(element.get("href", "")).strip())
        if absolute and not any(
            marker in absolute.lower() for marker in UNRESOLVED_TEMPLATE_MARKERS
        ):
            links.append(absolute)
    if links:
        return list(dict.fromkeys(links))

    data = search.get("data", {})
    raw_jobs = data.get("jobs", []) if isinstance(data, dict) else []
    try:
        total_hits = max(0, int(search.get("totalHits", len(raw_jobs))))
    except (TypeError, ValueError):
        total_hits = len(raw_jobs)
    try:
        page_size = max(0, int(search.get("hits", len(raw_jobs))))
    except (TypeError, ValueError):
        page_size = len(raw_jobs)
    if page_size <= 0:
        page_size = len(raw_jobs)
    if page_size <= 0:
        return []

    parsed = urlsplit(base_url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    current_offset = 0
    for key, value in query:
        if key.lower() != "from":
            continue
        try:
            current_offset = max(0, int(value))
        except ValueError:
            current_offset = 0
        break
    next_offset = current_offset + page_size
    if next_offset >= total_hits:
        return []

    next_query = [(key, value) for key, value in query if key.lower() != "from"]
    next_query.append(("from", str(next_offset)))
    if not any(key.lower() == "s" for key, _ in next_query):
        next_query.append(("s", "1"))
    next_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(next_query), ""))
    return [next_url]


def _company_name(page: PageDocument) -> str:
    soup = BeautifulSoup(page.html, "lxml")
    site_name = soup.find("meta", attrs={"property": "og:site_name"})
    if site_name:
        return str(site_name.get("content") or "").strip()
    return ""


def _work_mode(raw_job: dict) -> str:
    text = " ".join(
        str(raw_job.get(key) or "")
        for key in ("title", "location", "workLocation", "descriptionTeaser")
    ).lower()
    if "remote" in text or "telecommut" in text:
        return "remote"
    if "hybrid" in text:
        return "hybrid"
    if "on-site" in text or "onsite" in text:
        return "onsite"
    return "unknown"


def _description(raw_job: dict) -> str:
    teaser = str(raw_job.get("descriptionTeaser") or "").strip()
    if teaser:
        return teaser
    parser_data = raw_job.get("ml_job_parser")
    if isinstance(parser_data, dict):
        for key in ("descriptionTeaser", "descriptionTeaser_keyword", "descriptionTeaser_ats"):
            value = str(parser_data.get(key) or "").strip()
            if value:
                return value
    return ""


def _slug(value: object) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")


def _phenom_detail_url(raw_job: dict, page: PageDocument) -> str:
    config = _assigned_json_object(page.html, PHENOM_CONFIG_ASSIGNMENT)
    url_map = _assigned_json_object(page.html, PHENOM_URL_MAP_ASSIGNMENT)
    base_url = str(config.get("baseUrl") or "").strip() if config else ""
    route = str(url_map.get("job") or "").strip() if url_map else ""
    if not base_url or not route:
        return ""

    values = {
        "jobSeqNo": str(raw_job.get("jobSeqNo") or "").strip(),
        "jobId": str(raw_job.get("jobId") or raw_job.get("reqId") or "").strip(),
        "reqId": str(raw_job.get("reqId") or raw_job.get("jobId") or "").strip(),
        "title": _slug(raw_job.get("title")),
    }

    def replace_parameter(match: re.Match[str]) -> str:
        return values.get(match.group(1), "")

    resolved_route = re.sub(r":([A-Za-z][A-Za-z0-9_]*)", replace_parameter, route)
    if not resolved_route or ":" in resolved_route or not values["title"]:
        return ""
    return urljoin(base_url.rstrip("/") + "/", resolved_route.lstrip("/"))


def _job_url(raw_job: dict, page: PageDocument) -> str:
    for key in ("jobUrl", "url"):
        value = str(raw_job.get(key) or "").strip()
        if value and not any(marker in value.lower() for marker in UNRESOLVED_TEMPLATE_MARKERS):
            return urljoin(str(page.url), value)
    detail_url = _phenom_detail_url(raw_job, page)
    if detail_url:
        return detail_url
    value = str(raw_job.get("applyUrl") or "").strip()
    if value and not any(marker in value.lower() for marker in UNRESOLVED_TEMPLATE_MARKERS):
        return urljoin(str(page.url), value)
    return ""


def extract_phenom_jobs(page: PageDocument) -> PhenomExtraction | None:
    """Extract job cards embedded in a Phenom `phApp.ddo` search payload."""

    search = _phenom_search_payload(page.html)
    if search is None:
        return None
    data = search.get("data", {})
    raw_jobs = data.get("jobs", []) if isinstance(data, dict) else []
    if not isinstance(raw_jobs, list):
        raw_jobs = []
    try:
        total_hits = max(0, int(search.get("totalHits", len(raw_jobs))))
    except (TypeError, ValueError):
        total_hits = len(raw_jobs)

    company = _company_name(page)
    jobs: list[JobPosting] = []
    for raw_job in raw_jobs:
        if not isinstance(raw_job, dict):
            continue
        title = str(raw_job.get("title") or "").strip()
        job_url = _job_url(raw_job, page)
        if not title or not job_url:
            continue
        skills = raw_job.get("ml_skills", [])
        if not isinstance(skills, list):
            skills = []
        try:
            jobs.append(
                JobPosting(
                    title=title,
                    company=str(raw_job.get("company") or company).strip(),
                    location=str(
                        raw_job.get("location")
                        or raw_job.get("cityStateCountry")
                        or raw_job.get("workLocation")
                        or ""
                    ).strip(),
                    work_mode=_work_mode(raw_job),
                    description=_description(raw_job),
                    url=job_url,
                    source_url=str(page.source_url),
                    external_id=str(
                        raw_job.get("jobSeqNo")
                        or raw_job.get("jobId")
                        or raw_job.get("reqId")
                        or ""
                    ).strip()
                    or None,
                    employment_type=str(raw_job.get("type") or "").strip() or None,
                    skills=[str(skill).strip() for skill in skills if str(skill).strip()],
                    posted_at=str(raw_job.get("postedDate") or "").strip() or None,
                )
            )
        except ValueError:
            continue
    return PhenomExtraction(
        jobs=jobs,
        total_hits=total_hits,
        raw_job_count=len(raw_jobs),
    )

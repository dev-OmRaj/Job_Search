from __future__ import annotations

import html as html_module
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from ..models import JobPosting, PageDocument

GROUP_ID_RE = re.compile(r'window\._EF_GROUP_ID\s*=\s*["\']([^"\']+)["\']')


@dataclass(slots=True)
class EightfoldConfig:
    origin: str
    group_id: str

    @property
    def search_url(self) -> str:
        return f"{self.origin}/api/pcsx/search"

    @property
    def detail_url(self) -> str:
        return f"{self.origin}/api/pcsx/position_details"

    def search_page_url(self, start: int) -> str:
        return f"{self.search_url}?{urlencode({'domain': self.group_id, 'start': start})}"


@dataclass(slots=True)
class EightfoldExtraction:
    jobs: list[JobPosting]
    total_hits: int
    raw_job_count: int
    missing_detail_count: int


def eightfold_config(html: str, page_url: str) -> EightfoldConfig | None:
    parsed = urlsplit(page_url)
    match = GROUP_ID_RE.search(html)
    looks_like_eightfold = (
        "api/pcsx/search" in html
        or "static.vscdn.net" in html
        or "eightfold.ai" in html
    )
    if not match or not looks_like_eightfold or not parsed.scheme or not parsed.netloc:
        return None
    return EightfoldConfig(
        origin=f"{parsed.scheme}://{parsed.netloc}",
        group_id=match.group(1).strip(),
    )


def _plain_text(value: object) -> str:
    decoded = html_module.unescape(str(value or ""))
    return BeautifulSoup(decoded, "lxml").get_text(" ", strip=True)


def _location(raw: dict) -> str:
    locations = raw.get("locations") or raw.get("standardizedLocations") or []
    if isinstance(locations, str):
        return locations.strip()
    if isinstance(locations, list):
        return "; ".join(dict.fromkeys(str(value).strip() for value in locations if str(value).strip()))
    return ""


def _work_mode(raw: dict, description: str) -> str:
    explicit = str(raw.get("workLocationOption") or raw.get("locationFlexibility") or "").casefold()
    text = f"{explicit} {description[:1000]}".casefold()
    if any(marker in text for marker in ("remote", "virtual", "work from home", "wfh")):
        return "remote"
    if "hybrid" in text:
        return "hybrid"
    if any(marker in text for marker in ("onsite", "on-site", "in office")):
        return "onsite"
    return "unknown"


def _posted_at(value: object) -> str | None:
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return str(value).strip() or None
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()


def extract_eightfold_jobs(page: PageDocument) -> EightfoldExtraction | None:
    try:
        payload = json.loads(page.html)
    except (json.JSONDecodeError, TypeError):
        return None
    eightfold = payload.get("eightfold") if isinstance(payload, dict) else None
    if not isinstance(eightfold, dict) or not isinstance(eightfold.get("positions"), list):
        return None

    origin = str(eightfold.get("origin") or "").strip()
    company = str(eightfold.get("company") or "").strip()
    jobs: list[JobPosting] = []
    for raw in eightfold["positions"]:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("name") or raw.get("title") or "").strip()
        position_url = str(raw.get("positionUrl") or "").strip()
        if not title or not position_url:
            continue
        description = _plain_text(raw.get("jobDescription"))
        external_id = raw.get("displayJobId") or raw.get("atsJobId") or raw.get("id")
        jobs.append(
            JobPosting(
                title=title,
                company=company,
                location=_location(raw),
                work_mode=_work_mode(raw, description),
                description=description,
                description_complete=bool(description),
                url=urljoin(f"{origin}/", position_url),
                source_url=str(page.source_url),
                external_id=str(external_id).strip() if external_id is not None else None,
                employment_type=str(raw.get("employmentType") or "").strip() or None,
                posted_at=_posted_at(raw.get("postedTs")),
            )
        )
    return EightfoldExtraction(
        jobs=jobs,
        total_hits=int(eightfold.get("total") or len(eightfold["positions"])),
        raw_job_count=int(eightfold.get("raw_count") or len(eightfold["positions"])),
        missing_detail_count=int(eightfold.get("missing_detail_count") or 0),
    )

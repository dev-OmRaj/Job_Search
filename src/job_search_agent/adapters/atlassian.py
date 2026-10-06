from __future__ import annotations

import html as html_module
import json
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ..models import JobPosting, PageDocument


@dataclass(slots=True)
class AtlassianConfig:
    endpoint_url: str


@dataclass(slots=True)
class AtlassianExtraction:
    jobs: list[JobPosting]
    total_hits: int
    raw_job_count: int


def atlassian_config(html: str, page_url: str) -> AtlassianConfig | None:
    parsed = urlsplit(page_url)
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    is_atlassian_careers = (
        hostname in {"atlassian.com", "www.atlassian.com"}
        and "/company/careers/all-jobs" in parsed.path.rstrip("/")
    )
    has_careers_component = '"type": "Careers"' in html or '"type":"Careers"' in html
    if not is_atlassian_careers or not has_careers_component:
        return None
    return AtlassianConfig(endpoint_url=urljoin(page_url, "/endpoint/careers/listings"))


def _plain_text(value: object) -> str:
    decoded = html_module.unescape(str(value or ""))
    return BeautifulSoup(decoded, "lxml").get_text(" ", strip=True)


def _location(raw: dict) -> str:
    locations = raw.get("locations")
    if isinstance(locations, list):
        return "; ".join(
            dict.fromkeys(str(location).strip() for location in locations if str(location).strip())
        )
    return str(locations or "").strip()


def _work_mode(location: str) -> str:
    lowered = location.casefold()
    if "remote" in lowered:
        return "remote"
    if "hybrid" in lowered:
        return "hybrid"
    if any(marker in lowered for marker in ("on-site", "onsite", "office")):
        return "onsite"
    return "unknown"


def _description(raw: dict) -> str:
    values = [
        _plain_text(raw.get(field))
        for field in ("overview", "responsibilities", "qualifications", "compensation")
    ]
    return "\n\n".join(dict.fromkeys(value for value in values if value))


def extract_atlassian_jobs(page: PageDocument) -> AtlassianExtraction | None:
    try:
        payload = json.loads(page.html)
    except (json.JSONDecodeError, TypeError):
        return None
    atlassian = payload.get("atlassian") if isinstance(payload, dict) else None
    if not isinstance(atlassian, dict) or not isinstance(atlassian.get("jobs"), list):
        return None

    jobs: list[JobPosting] = []
    for raw in atlassian["jobs"]:
        if not isinstance(raw, dict):
            continue
        portal = raw.get("portalJobPost")
        if not isinstance(portal, dict):
            portal = {}
        title = str(raw.get("title") or "").strip()
        url = str(portal.get("portalUrl") or raw.get("applyUrl") or "").strip()
        job_id = raw.get("id") or portal.get("id")
        if not title or not url or job_id is None:
            continue
        location = _location(raw)
        jobs.append(
            JobPosting(
                title=title,
                company="Atlassian",
                location=location,
                work_mode=_work_mode(location),
                description=_description(raw),
                url=url,
                source_url=str(page.source_url),
                external_id=str(job_id),
                employment_type=str(raw.get("type") or "").strip() or None,
                posted_at=str(portal.get("updatedDate") or "").strip() or None,
            )
        )
    return AtlassianExtraction(
        jobs=jobs,
        total_hits=int(atlassian.get("total") or len(atlassian["jobs"])),
        raw_job_count=int(atlassian.get("raw_count") or len(atlassian["jobs"])),
    )

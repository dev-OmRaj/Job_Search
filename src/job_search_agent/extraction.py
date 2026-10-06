from __future__ import annotations

import json
from collections.abc import Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .models import JobPosting, PageDocument


def _json_ld_objects(value: object) -> Iterable[dict]:
    if isinstance(value, list):
        for item in value:
            yield from _json_ld_objects(item)
    elif isinstance(value, dict):
        graph = value.get("@graph")
        if graph is not None:
            yield from _json_ld_objects(graph)
        item_type = value.get("@type")
        types = item_type if isinstance(item_type, list) else [item_type]
        if "JobPosting" in types:
            yield value


def _plain_text(value: object) -> str:
    if value is None:
        return ""
    return BeautifulSoup(str(value), "lxml").get_text(" ", strip=True)


def _organization_name(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or "")
    return str(value or "")


def _location(value: object) -> str:
    if isinstance(value, list):
        return "; ".join(filter(None, (_location(item) for item in value)))
    if not isinstance(value, dict):
        return str(value or "")
    address = value.get("address", value)
    if not isinstance(address, dict):
        return str(address or "")
    fields = (
        address.get("streetAddress"),
        address.get("addressLocality"),
        address.get("addressRegion"),
        address.get("postalCode"),
        address.get("addressCountry"),
    )
    return ", ".join(str(item) for item in fields if item)


def _skills(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if not value:
        return []
    text = str(value)
    separator = "," if "," in text else ";"
    return [item.strip() for item in text.split(separator) if item.strip()]


def extract_json_ld_jobs(page: PageDocument) -> list[JobPosting]:
    soup = BeautifulSoup(page.html, "lxml")
    jobs: list[JobPosting] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            payload = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        for item in _json_ld_objects(payload):
            title = str(item.get("title") or item.get("name") or "").strip()
            if not title:
                continue
            job_url = urljoin(str(page.url), str(item.get("url") or page.url))
            location_type = str(item.get("jobLocationType") or "").lower()
            work_mode = (
                "remote"
                if "telecommute" in location_type or "remote" in location_type
                else "unknown"
            )
            jobs.append(
                JobPosting(
                    title=title,
                    company=_organization_name(item.get("hiringOrganization")),
                    location=_location(item.get("jobLocation")),
                    work_mode=work_mode,
                    description=_plain_text(item.get("description")),
                    url=job_url,
                    source_url=str(page.source_url),
                    external_id=str(item.get("identifier") or "") or None,
                    employment_type=str(item.get("employmentType") or "") or None,
                    experience_level=str(item.get("experienceRequirements") or "") or None,
                    salary=_plain_text(item.get("baseSalary")) or None,
                    skills=_skills(item.get("skills") or item.get("qualifications")),
                    posted_at=str(item.get("datePosted") or "") or None,
                )
            )
    return jobs


def deduplicate_jobs(jobs: Iterable[JobPosting]) -> list[JobPosting]:
    """Prefer the most descriptive record for a canonical URL/title pair."""

    selected: dict[str, JobPosting] = {}
    for job in jobs:
        key = job.identity_key
        current = selected.get(key)
        if current is None or len(job.description) > len(current.description):
            selected[key] = job
    return list(selected.values())

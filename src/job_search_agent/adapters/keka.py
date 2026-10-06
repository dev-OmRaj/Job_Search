from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import JobPosting, PageDocument

PORTAL_DOCUMENT_RE = re.compile(
    r"fetch\(\s*['\"]([^'\"]*/ats/documents/[^'\"]+/careerportal/[^'\"]+\.html)['\"]",
    re.IGNORECASE,
)
IDENTIFIER_RE = re.compile(r"\bidentifier\s*:\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)
DOMAIN_RE = re.compile(r"\bdomain\s*:\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)
PORTAL_NAME_RE = re.compile(r"\bportalName\s*:\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)
JOB_TYPES = {1: "Part Time", 2: "Full Time"}


@dataclass(slots=True)
class KekaConfig:
    portal_document_url: str | None = None
    domain: str | None = None
    identifier: str | None = None
    portal_name: str = "default"


@dataclass(slots=True)
class KekaExtraction:
    jobs: list[JobPosting]
    total_hits: int
    raw_job_count: int


def keka_config(html: str, page_url: str) -> KekaConfig | None:
    """Detect Keka's bootstrap shell or its generated career-portal document."""

    document_match = PORTAL_DOCUMENT_RE.search(html)
    if document_match:
        return KekaConfig(portal_document_url=urljoin(page_url, document_match.group(1)))
    identifier_match = IDENTIFIER_RE.search(html)
    domain_match = DOMAIN_RE.search(html)
    if identifier_match and domain_match and "embedjobs" in html.casefold():
        portal_match = PORTAL_NAME_RE.search(html)
        return KekaConfig(
            domain=urljoin(page_url, domain_match.group(1)),
            identifier=identifier_match.group(1),
            portal_name=portal_match.group(1) if portal_match else "default",
        )
    return None


def keka_endpoint_urls(config: KekaConfig) -> tuple[str, str]:
    if not config.domain or not config.identifier:
        raise ValueError("Keka endpoint configuration is incomplete")
    domain = config.domain.rstrip("/") + "/"
    jobs_url = urljoin(
        domain,
        f"api/embedjobs/{config.portal_name}/active/{config.identifier}",
    )
    info_url = urljoin(
        domain,
        f"api/organization/{config.portal_name}/careerportalinfo",
    )
    return jobs_url, info_url


def _plain_text(value: object) -> str:
    return BeautifulSoup(str(value or ""), "lxml").get_text(" ", strip=True)


def _work_mode(title: str, description: str) -> str:
    text = f"{title} {description}".casefold()
    if any(term in text for term in ("work from home", "remote", "wfh")):
        return "remote"
    if "hybrid" in text:
        return "hybrid"
    if any(term in text for term in ("work from office", "on-site", "onsite")):
        return "onsite"
    return "unknown"


def extract_keka_jobs(page: PageDocument) -> KekaExtraction | None:
    """Extract complete job records returned by Keka's public active-jobs API."""

    try:
        payload = json.loads(page.html)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or payload.get("provider") != "keka":
        return None
    raw_jobs = payload.get("jobs", [])
    if not isinstance(raw_jobs, list):
        raw_jobs = []
    company = str(payload.get("company") or "").strip()
    domain = str(payload.get("domain") or page.source_url).rstrip("/") + "/"
    portal_name = str(payload.get("portal_name") or "default").strip()
    detail_prefix = "" if portal_name == "default" else f"{portal_name}/"
    jobs: list[JobPosting] = []
    for raw_job in raw_jobs:
        if not isinstance(raw_job, dict):
            continue
        title = str(raw_job.get("title") or "").strip()
        job_id = str(raw_job.get("id") or "").strip()
        if not title or not job_id:
            continue
        raw_locations = raw_job.get("jobLocations", [])
        if not isinstance(raw_locations, list):
            raw_locations = []
        locations: list[str] = []
        for raw_location in raw_locations:
            if not isinstance(raw_location, dict):
                continue
            location = str(raw_location.get("name") or raw_location.get("city") or "").strip()
            country = str(raw_location.get("countryName") or "").strip()
            combined = ", ".join(filter(None, (location, country)))
            if combined and combined not in locations:
                locations.append(combined)
        description = _plain_text(raw_job.get("description") or raw_job.get("excerpt"))
        salary = str(raw_job.get("salaryRangeFormat") or "").strip()
        if salary and re.fullmatch(r"\D*0(?:[.,]0+)?\s*-\s*\D*0(?:[.,]0+)?", salary):
            salary = ""
        job_type = JOB_TYPES.get(raw_job.get("jobType"))
        skills = raw_job.get("skillNames", [])
        if not isinstance(skills, list):
            skills = []
        jobs.append(
            JobPosting(
                title=title,
                company=company,
                location="; ".join(locations),
                work_mode=_work_mode(title, description),
                description=description,
                url=urljoin(domain, f"{detail_prefix}jobdetails/{job_id}"),
                source_url=str(page.source_url),
                external_id=job_id,
                employment_type=job_type,
                experience_level=str(raw_job.get("experience") or "").strip() or None,
                salary=salary or None,
                skills=[str(skill).strip() for skill in skills if str(skill).strip()],
                posted_at=str(raw_job.get("publishedOn") or "").strip() or None,
            )
        )
    return KekaExtraction(jobs=jobs, total_hits=len(raw_jobs), raw_job_count=len(raw_jobs))

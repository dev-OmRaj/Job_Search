from __future__ import annotations

import html as html_module
import json
import re
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

from bs4 import BeautifulSoup

from ..models import JobPosting, PageDocument

WORKDAY_HOST_RE = re.compile(r"^[a-z0-9-]+\.(?:wd\d+\.)?myworkdayjobs\.com$", re.IGNORECASE)
LOCALE_RE = re.compile(r"^[a-z]{2}(?:-[A-Z]{2})?$")


@dataclass(slots=True)
class WorkdayConfig:
    origin: str
    tenant: str
    site: str
    locale: str = "en-US"

    @property
    def endpoint_url(self) -> str:
        return (
            f"{self.origin}/wday/cxs/{quote(self.tenant, safe='')}/"
            f"{quote(self.site, safe='')}/jobs"
        )

    def detail_url(self, external_path: str) -> str:
        return f"{self.origin}/wday/cxs/{quote(self.tenant, safe='')}/{quote(self.site, safe='')}" + (
            external_path if external_path.startswith("/") else f"/{external_path}"
        )

    def public_job_url(self, external_path: str) -> str:
        path = external_path if external_path.startswith("/") else f"/{external_path}"
        return f"{self.origin}/{self.locale}/{quote(self.site, safe='')}{path}"


@dataclass(slots=True)
class WorkdayExtraction:
    jobs: list[JobPosting]
    total_hits: int
    raw_job_count: int


def workday_running_total(
    current_total: int,
    payload: dict,
    *,
    offset: int,
    returned: int,
) -> int:
    """Preserve Workday's first-page total when later pages report zero."""

    try:
        reported_total = int(payload.get("total") or 0)
    except (TypeError, ValueError):
        reported_total = 0
    return max(current_total, reported_total, offset + returned)


def workday_config(page_url: str) -> WorkdayConfig | None:
    parsed = urlsplit(page_url)
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if not WORKDAY_HOST_RE.fullmatch(hostname):
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if parts[:2] == ["wday", "cxs"] or not parts:
        return None
    locale = "en-US"
    if LOCALE_RE.fullmatch(parts[0]):
        locale = parts.pop(0)
    if not parts:
        return None
    tenant = hostname.split(".", 1)[0]
    return WorkdayConfig(
        origin=f"{parsed.scheme}://{parsed.netloc}",
        tenant=tenant,
        site=parts[0],
        locale=locale,
    )


def _plain_text(value: object) -> str:
    decoded = html_module.unescape(str(value or ""))
    return BeautifulSoup(decoded, "lxml").get_text(" ", strip=True)


def _first_text(value: object) -> str:
    if isinstance(value, list):
        for item in value:
            text = _first_text(item)
            if text:
                return text
        return ""
    if isinstance(value, dict):
        return str(value.get("title") or value.get("name") or value.get("value") or "").strip()
    return str(value or "").strip()


def _work_mode(*values: str) -> str:
    text = " ".join(values).casefold()
    if any(marker in text for marker in ("remote", "virtual", "work from home", "wfh")):
        return "remote"
    if "hybrid" in text:
        return "hybrid"
    if any(marker in text for marker in ("on-site", "onsite", "in office")):
        return "onsite"
    return "unknown"


def extract_workday_jobs(page: PageDocument) -> WorkdayExtraction | None:
    try:
        payload = json.loads(page.html)
    except (json.JSONDecodeError, TypeError):
        return None
    workday = payload.get("workday") if isinstance(payload, dict) else None
    if not isinstance(workday, dict) or not isinstance(workday.get("jobs"), list):
        return None

    jobs: list[JobPosting] = []
    for raw in workday["jobs"]:
        if not isinstance(raw, dict):
            continue
        detail = raw.get("jobPostingInfo")
        if not isinstance(detail, dict):
            detail = {}
        title = str(detail.get("title") or raw.get("title") or "").strip()
        url = str(detail.get("externalUrl") or raw.get("publicUrl") or "").strip()
        if not title or not url:
            continue
        location = str(
            detail.get("location") or raw.get("locationsText") or raw.get("location") or ""
        ).strip()
        additional = detail.get("additionalLocations")
        if isinstance(additional, list):
            additions = [str(value).strip() for value in additional if str(value).strip()]
            if additions:
                location = "; ".join(dict.fromkeys([location, *additions]))
        description = _plain_text(detail.get("jobDescription") or raw.get("jobDescription"))
        employment_type = _first_text(detail.get("timeType") or detail.get("jobTypes")) or None
        jobs.append(
            JobPosting(
                title=title,
                company=str(detail.get("company") or workday.get("company") or "").strip(),
                location=location,
                work_mode=_work_mode(title, location, description, employment_type or ""),
                description=description,
                url=url,
                source_url=str(page.source_url),
                external_id=str(
                    detail.get("jobReqId") or _first_text(raw.get("bulletFields")) or ""
                ).strip()
                or None,
                employment_type=employment_type,
                posted_at=str(detail.get("postedOn") or raw.get("postedOn") or "").strip() or None,
            )
        )
    return WorkdayExtraction(
        jobs=jobs,
        total_hits=int(workday.get("total") or len(workday["jobs"])),
        raw_job_count=int(workday.get("raw_count") or len(workday["jobs"])),
    )

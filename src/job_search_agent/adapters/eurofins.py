from __future__ import annotations

import html as html_module
import json
from dataclasses import dataclass
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from ..models import JobPosting, PageDocument

FILTER_FIELDS = {
    "countryId": "countryCode",
    "deptid": "departmentId",
    "subdeptId": "subDepartmentId",
    "businessLineId": "businessLinesId",
    "locationId": "locationId",
    "empType": "jobTypeId",
    "contractId": "contractTypeId",
    "legalId": "legalEntityId",
    "code": "stateCode",
}


@dataclass(slots=True)
class EurofinsConfig:
    endpoint_url: str
    filters: dict[str, tuple[str, ...]]
    keywords: str = ""
    fresh_graduates_only: bool = False


@dataclass(slots=True)
class EurofinsExtraction:
    jobs: list[JobPosting]
    total_hits: int
    raw_job_count: int


def _query_values(query: dict[str, list[str]], key: str) -> tuple[str, ...]:
    values: list[str] = []
    for raw in query.get(key, []):
        values.extend(value.strip().casefold() for value in raw.split(",") if value.strip())
    return tuple(dict.fromkeys(values))


def eurofins_config(html: str, page_url: str) -> EurofinsConfig | None:
    soup = BeautifulSoup(html, "lxml")
    element = soup.select_one("#apiJobsURL")
    endpoint = str(element.get("value") or "").strip() if element else ""
    if not endpoint or "ATSWebService.asmx/GetJobs" not in endpoint:
        return None
    endpoint = urljoin(page_url, html_module.unescape(endpoint))
    parsed_endpoint = urlsplit(endpoint)
    if (parsed_endpoint.hostname or "").casefold() != "atsintegration.eurofins.com":
        return None

    query = parse_qs(urlsplit(page_url).query)
    filters = {
        field: values
        for query_name, field in FILTER_FIELDS.items()
        if (values := _query_values(query, query_name))
    }
    keywords = " ".join(query.get("keywords", [])).replace("+", " ").strip().casefold()
    fresh = any(value.casefold() == "true" for value in query.get("freshgraduate", []))
    return EurofinsConfig(
        endpoint_url=endpoint,
        filters=filters,
        keywords=keywords,
        fresh_graduates_only=fresh,
    )


def filter_eurofins_jobs(jobs: list[dict], config: EurofinsConfig) -> list[dict]:
    filtered: list[dict] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        if config.fresh_graduates_only and str(job.get("freshGraduateId") or "") != "1":
            continue
        if config.keywords:
            searchable = " ".join(
                str(job.get(field) or "")
                for field in ("title", "description", "departmentName", "businessLinesName")
            ).casefold()
            if config.keywords not in searchable:
                continue
        if any(
            str(job.get(field) or "").strip().casefold() not in accepted
            for field, accepted in config.filters.items()
        ):
            continue
        filtered.append(job)
    return filtered


def smartrecruiters_posting_id(url: str) -> str | None:
    parsed = urlsplit(url)
    if (parsed.hostname or "").casefold() != "jobs.smartrecruiters.com":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    posting_id = parts[1].split("-", 1)[0]
    return posting_id if posting_id.isdigit() else None


def smartrecruiters_detail_url(job: dict) -> str | None:
    posting_id = smartrecruiters_posting_id(str(job.get("applyUrl") or ""))
    return (
        f"https://api.smartrecruiters.com/v1/companies/Eurofins/postings/{posting_id}"
        if posting_id
        else None
    )


def _plain_text(value: object) -> str:
    return BeautifulSoup(html_module.unescape(str(value or "")), "lxml").get_text(" ", strip=True)


def _detail_description(detail: dict) -> str:
    job_ad = detail.get("jobAd")
    sections = job_ad.get("sections") if isinstance(job_ad, dict) else None
    if not isinstance(sections, dict):
        return ""
    descriptions: list[str] = []
    for section in sections.values():
        if not isinstance(section, dict):
            continue
        text = _plain_text(section.get("text"))
        if text:
            descriptions.append(text)
    return "\n\n".join(dict.fromkeys(descriptions))


def _location(raw: dict, detail: dict) -> str:
    location = detail.get("location")
    if isinstance(location, dict) and location.get("fullLocation"):
        return str(location["fullLocation"]).strip()
    values = [
        str(raw.get("locationCity") or "").strip(),
        str(raw.get("locationRegion") or "").strip(),
        str(raw.get("countryName") or "").strip(),
    ]
    return ", ".join(dict.fromkeys(value for value in values if value))


def _work_mode(raw: dict, detail: dict) -> str:
    location = detail.get("location")
    if isinstance(location, dict):
        if location.get("remote") is True:
            return "remote"
        if location.get("hybrid") is True:
            return "hybrid"
    if str(raw.get("remoteJob") or "").casefold() == "true":
        return "remote"
    return "unknown"


def extract_eurofins_jobs(page: PageDocument) -> EurofinsExtraction | None:
    try:
        payload = json.loads(page.html)
    except (json.JSONDecodeError, TypeError):
        return None
    eurofins = payload.get("eurofins") if isinstance(payload, dict) else None
    if not isinstance(eurofins, dict) or not isinstance(eurofins.get("jobs"), list):
        return None

    jobs: list[JobPosting] = []
    for raw in eurofins["jobs"]:
        if not isinstance(raw, dict):
            continue
        detail = raw.get("smartrecruitersDetail")
        if not isinstance(detail, dict):
            detail = {}
        title = str(detail.get("name") or raw.get("title") or "").strip()
        job_url = str(detail.get("postingUrl") or raw.get("applyUrl") or "").strip()
        if not title or not job_url:
            continue
        description = _detail_description(detail) or str(raw.get("description") or "").strip()
        external_id = (
            detail.get("refNumber")
            or raw.get("refCode")
            or detail.get("id")
            or smartrecruiters_posting_id(job_url)
        )
        employment = detail.get("typeOfEmployment")
        if isinstance(employment, dict):
            employment = employment.get("label")
        company_info = detail.get("company")
        company = (
            str(company_info.get("name") or "").strip()
            if isinstance(company_info, dict)
            else ""
        ) or "Eurofins"
        jobs.append(
            JobPosting(
                title=title,
                company=company,
                location=_location(raw, detail),
                work_mode=_work_mode(raw, detail),
                description=description,
                url=job_url,
                source_url=str(page.source_url),
                external_id=str(external_id).strip() if external_id is not None else None,
                employment_type=str(employment or raw.get("employmentType") or "").strip() or None,
                experience_level=str(
                    (detail.get("experienceLevel") or {}).get("label")
                    if isinstance(detail.get("experienceLevel"), dict)
                    else ""
                ).strip()
                or None,
                posted_at=str(detail.get("releasedDate") or raw.get("submittedDate") or "").strip()
                or None,
            )
        )
    return EurofinsExtraction(
        jobs=jobs,
        total_hits=int(eurofins.get("total") or len(eurofins["jobs"])),
        raw_job_count=int(eurofins.get("raw_count") or len(eurofins["jobs"])),
    )

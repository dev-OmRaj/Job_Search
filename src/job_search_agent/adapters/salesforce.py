from __future__ import annotations

import html as html_module
import json
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, urlsplit

from bs4 import BeautifulSoup

from ..models import JobPosting, PageDocument

FEED_ROOT = "https://a.sfdcstatic.com/digital/xsf/careers/prod"


@dataclass(slots=True)
class SalesforceConfig:
    board_url: str


@dataclass(slots=True)
class SalesforceExtraction:
    jobs: list[JobPosting]
    total_hits: int
    raw_job_count: int
    coverage_complete: bool


def salesforce_config(html: str, page_url: str) -> SalesforceConfig | None:
    parsed = urlsplit(page_url)
    if parsed.hostname not in {"salesforce.com", "www.salesforce.com"}:
        return None
    path = parsed.path.rstrip("/")
    board_path = "/jp/company/careers/jobs" if path.startswith("/jp/") else "/company/careers/jobs"
    if path not in {board_path, board_path.removesuffix("/jobs")}:
        return None
    if "xsf/components/" not in html or "careers.js" not in html:
        return None
    return SalesforceConfig(board_url=f"https://www.salesforce.com{board_path}")


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _plain_text(value: object) -> str:
    return BeautifulSoup(html_module.unescape(_text(value)), "lxml").get_text(" ", strip=True)


def _values(value: object) -> list[str]:
    return [_text(item) for item in value if _text(item)] if isinstance(value, list) else []


def locations(raw: dict) -> list[str]:
    primary = _text(raw.get("Job_Requisition_Primary_Location"))
    additional = _text(raw.get("Job_Requisition_Additional_Locations"))
    values = [primary] + [part.strip() for part in additional.split(";") if part.strip()]
    if not any(values):
        values = [
            _text(item) if isinstance(item, str) else _text(item.get("display"))
            for item in raw.get("locations", []) if isinstance(item, (str, dict))
        ]
    return list({value.casefold(): value for value in values if value}.values())


def filter_jobs(records: list[dict], source_url: str) -> list[dict]:
    """Match the original site's filters; page selects a display slice, not scan scope."""
    query = parse_qs(urlsplit(source_url).query)
    allowed = {"country", "region", "location", "team", "contractTypes", "employeeTypes", "search", "page", "bc"}
    unsupported = [key for key in query if key not in allowed and not key.startswith("utm_")]
    if unsupported:
        raise ValueError("Unsupported Salesforce URL filters: " + ", ".join(unsupported))

    def matches(raw: dict) -> bool:
        places = locations(raw)
        for param, field in (("country", "Countries"), ("region", "Regions"), ("location", "Locations")):
            selected = [_text(value).casefold() for value in query.get(param, []) if _text(value)]
            if not selected:
                continue
            structured = _values(raw.get(field) or raw.get(field.lower()))
            if structured:
                if not set(selected) & {value.casefold() for value in structured}:
                    return False
            elif not any(value in place.casefold() for value in selected for place in places):
                return False
        for param, field in (("team", "Job_Family_Group"), ("contractTypes", "Time_Type"), ("employeeTypes", "Employee_Type")):
            selected = [value.casefold() for value in query.get(param, []) if value]
            if selected and _text(raw.get(field)).casefold() not in selected:
                return False
        searchable = " ".join([
            _text(raw.get("Job_Requisition_Ref_ID")), _text(raw.get("Job_Posting_Title")),
            _text(raw.get("Job_Family_Group")), *places,
        ]).casefold()
        return all(value.casefold().strip() in searchable for value in query.get("search", []) if value.strip())

    return [raw for raw in records if matches(raw)]


def original_job_url(board_url: str, job_id: str, title: str) -> str:
    normalized = unicodedata.normalize("NFKD", title)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char)).lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", normalized)
    slug = re.sub(r"\s+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-") or "job-details"
    return f"{board_url.rstrip('/')}/{quote(job_id, safe='')}/{slug}/"


def extract_salesforce_jobs(page: PageDocument) -> SalesforceExtraction | None:
    try:
        payload = json.loads(page.html)
    except (ValueError, TypeError):
        return None
    data = payload.get("salesforce") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        return None
    jobs = []
    for raw in data["jobs"]:
        if not isinstance(raw, dict):
            continue
        job_id = _text(raw.get("Job_Requisition_Ref_ID"))
        title = _text(raw.get("Job_Posting_Title"))
        if not job_id or not title:
            continue
        parts = [_plain_text(raw.get("Job_Description"))]
        parts.extend(
            _plain_text(value) for key, value in sorted(raw.items())
            if any(marker in key.lower() for marker in ("pay_transparency_text", "job_posting_end_date_text", "_statement"))
        )
        description = "\n\n".join(dict.fromkeys(part for part in parts if part))
        location = "; ".join(locations(raw))
        employee_type = _text(raw.get("Employee_Type"))
        employment_type = "internship" if employee_type.casefold() == "intern" else _text(raw.get("Time_Type"))
        employment_type = {"full time": "full-time", "part time": "part-time"}.get(employment_type.casefold(), employment_type)
        jobs.append(JobPosting(
            title=title, company="Salesforce", location=location,
            work_mode="remote" if re.search(r"\b(remote|wfh)\b", location, re.IGNORECASE) else "unknown",
            description=description,
            description_complete=bool(parts[0]) and bool(raw.get("_full_details", False)),
            url=original_job_url(str(data["board_url"]), job_id, title),
            source_url=str(page.source_url), external_id=job_id,
            employment_type=employment_type or None,
            experience_level="entry" if employee_type.casefold() == "new grads" else None,
            posted_at=_text(raw.get("External_Job_Posting_Start_Date")) or None,
        ))
    return SalesforceExtraction(
        jobs=jobs, total_hits=int(data.get("total", len(data["jobs"]))),
        raw_job_count=len(data["jobs"]),
        coverage_complete=bool(data.get("coverage_complete")) and all(job.description_complete for job in jobs),
    )

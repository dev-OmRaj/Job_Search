from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from ..models import JobPosting, PageDocument

NEXT_FLIGHT_PREFIX = "self.__next_f.push("
JOB_ARRAY_KEY_RE = re.compile(r'"jobs"\s*:')


@dataclass(slots=True)
class NextJsExtraction:
    jobs: list[JobPosting]
    total_hits: int
    raw_job_count: int


def _flight_chunks(html: str) -> list[str]:
    chunks: list[str] = []
    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script"):
        text = script.string or script.get_text()
        if not text.startswith(NEXT_FLIGHT_PREFIX) or not text.endswith(")"):
            continue
        try:
            payload = json.loads(text[len(NEXT_FLIGHT_PREFIX) : -1])
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, list) and len(payload) > 1 and isinstance(payload[1], str):
            chunks.append(payload[1])
    return chunks


def _embedded_job_arrays(html: str) -> list[list[dict]]:
    decoder = json.JSONDecoder()
    arrays: list[list[dict]] = []
    for chunk in _flight_chunks(html):
        for match in JOB_ARRAY_KEY_RE.finditer(chunk):
            try:
                value, _ = decoder.raw_decode(chunk, match.end())
            except json.JSONDecodeError:
                continue
            if not isinstance(value, list) or not value:
                continue
            records = [item for item in value if isinstance(item, dict)]
            if records and all(
                "job_title" in item and ("instance_id" in item or "job_req_id" in item)
                for item in records[:5]
            ):
                arrays.append(records)
    return arrays


def is_nextjs_job_listing(html: str) -> bool:
    """Return whether a Next.js response embeds normalized job records."""

    return bool(_embedded_job_arrays(html))


def _text(value: object) -> str:
    return str(value or "").strip()


def _location(record: dict) -> tuple[str, str, str, str]:
    raw_location = record.get("job_location")
    address = raw_location.get("address", {}) if isinstance(raw_location, dict) else {}
    if not isinstance(address, dict):
        address = {}
    city = _text(address.get("city"))
    region = _text(address.get("region"))
    country = _text(address.get("country"))
    location = ", ".join(dict.fromkeys(filter(None, (city, region, country))))
    return location, city, region, country


def _selected_values(query: dict[str, list[str]], *keys: str) -> list[str]:
    values: list[str] = []
    for key in keys:
        for value in query.get(key, []):
            values.extend(part.strip().casefold() for part in value.split(",") if part.strip())
    return values


def _matches_source_filters(record: dict, query: dict[str, list[str]]) -> bool:
    location, city, region, country = _location(record)
    location_filters = _selected_values(query, "locations", "location", "country")
    if location_filters and not any(
        value in {location.casefold(), city.casefold(), region.casefold(), country.casefold()}
        for value in location_filters
    ):
        return False
    keyword_filters = _selected_values(query, "q", "query", "keyword", "keywords")
    searchable = " ".join(
        _text(record.get(key))
        for key in ("job_title", "job_req_id", "employment_unit", "job_profile")
    ).casefold()
    return not keyword_filters or all(value in searchable for value in keyword_filters)


def _company_name(page: PageDocument, records: list[dict]) -> str:
    for record in records:
        organization = record.get("hiring_organization")
        if isinstance(organization, dict) and _text(organization.get("name")):
            return _text(organization["name"])
    soup = BeautifulSoup(page.html, "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    match = re.search(r"(?:at|careers? at)\s+([^|]+)$", title, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    hostname = (urlsplit(str(page.url)).hostname or "").removeprefix("www.")
    return hostname.split(".")[0].replace("-", " ").upper()


def _work_mode(employment_type: str) -> str:
    lowered = employment_type.casefold()
    if any(term in lowered for term in ("home-based", "home based", "remote", "wfh")):
        return "remote"
    if "hybrid" in lowered:
        return "hybrid"
    if any(term in lowered for term in ("office-based", "office based", "onsite", "on-site")):
        return "onsite"
    return "unknown"


def extract_nextjs_jobs(page: PageDocument) -> NextJsExtraction | None:
    """Extract normalized job records embedded in a Next.js Flight response."""

    arrays = _embedded_job_arrays(page.html)
    if not arrays:
        return None
    records = max(arrays, key=len)
    query = parse_qs(urlsplit(str(page.source_url)).query)
    filtered = [record for record in records if _matches_source_filters(record, query)]
    company = _company_name(page, records)
    parsed = urlsplit(str(page.url))
    path_parts = [part for part in parsed.path.split("/") if part]
    locale = path_parts[0] if path_parts else "en"
    jobs: list[JobPosting] = []
    for record in filtered:
        title = _text(record.get("job_title"))
        instance_id = _text(record.get("instance_id") or record.get("job_req_id"))
        if not title or not instance_id:
            continue
        location, _, _, _ = _location(record)
        employment_type = _text(record.get("employment_type"))
        employment_unit = _text(record.get("employment_unit"))
        job_profile = _text(record.get("job_profile"))
        organization = record.get("hiring_organization")
        organization_name = (
            _text(organization.get("name")) if isinstance(organization, dict) else ""
        )
        jobs.append(
            JobPosting(
                title=title,
                company=organization_name or company,
                location=location,
                work_mode=_work_mode(employment_type),
                description="; ".join(filter(None, (employment_unit, employment_type, job_profile))),
                url=urljoin(str(page.url), f"/{locale}/jobs/{instance_id}"),
                source_url=str(page.source_url),
                external_id=instance_id,
                employment_type=employment_type or None,
                posted_at=_text(record.get("date_posted")) or None,
            )
        )
    return NextJsExtraction(jobs=jobs, total_hits=len(filtered), raw_job_count=len(records))

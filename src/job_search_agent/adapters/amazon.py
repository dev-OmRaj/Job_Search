from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from ..models import JobPosting, PageDocument

AMAZON_CMS_DATA_ID = "jobs-cms-next-data"
AMAZON_PAGE_SIZE = 100
AMAZON_RESULT_CAP = 10_000


@dataclass(slots=True)
class AmazonSearchConfig:
    endpoint_url: str
    api_key: str
    public_origin: str
    locale: str
    request_template: dict


@dataclass(slots=True)
class AmazonExtraction:
    jobs: list[JobPosting]
    total_hits: int
    raw_job_count: int


def _is_amazon_jobs_url(url: str) -> bool:
    hostname = (urlsplit(url).hostname or "").lower().rstrip(".")
    return hostname == "amazon.jobs" or hostname.endswith(".amazon.jobs")


def _add_facet(container: list[dict], name: str, values: list[str]) -> None:
    cleaned = list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
    if not cleaned:
        return
    existing = next((facet for facet in container if facet.get("name") == name), None)
    if existing is None:
        existing = {"name": name, "requestedFacetCount": 9999, "values": []}
        container.append(existing)
    current = {
        str(value.get("name"))
        for value in existing.get("values", [])
        if isinstance(value, dict) and value.get("name")
    }
    existing.setdefault("values", []).extend(
        {"name": value} for value in cleaned if value not in current
    )


def _locale_from_page(content: dict, page_url: str) -> str:
    locale = str(content.get("locale") or "").strip().lower().replace("_", "-")
    if locale:
        return locale
    parts = [part for part in urlsplit(page_url).path.split("/") if part]
    if len(parts) >= 2 and parts[0] == "content":
        return parts[1]
    return "en"


def _is_job_category_directory(page_url: str) -> bool:
    path = urlsplit(page_url).path.rstrip("/")
    return bool(re.fullmatch(r"/content/[a-z-]+/job-categories", path, flags=re.IGNORECASE))


def amazon_search_config(html: str, page_url: str) -> AmazonSearchConfig | None:
    """Read Amazon's public CMS search configuration from a careers page."""

    if not _is_amazon_jobs_url(page_url):
        return None
    soup = BeautifulSoup(html, "lxml")
    script = soup.find("script", id=AMAZON_CMS_DATA_ID)
    if script is None:
        return None
    try:
        next_data = json.loads(script.string or script.get_text())
    except (json.JSONDecodeError, TypeError):
        return None

    runtime = next_data.get("runtimeConfig", {})
    page_props = next_data.get("props", {}).get("pageProps", {})
    content = page_props.get("content", {}) if isinstance(page_props, dict) else {}
    if not isinstance(runtime, dict) or not isinstance(content, dict):
        return None
    if not content.get("renderSearch") and not _is_job_category_directory(page_url):
        return None

    endpoint = str(runtime.get("searchEndpoint") or "").strip()
    api_key = str(runtime.get("searchKey") or "").strip()
    if not endpoint or not api_key:
        return None

    filter_facets: list[dict] = []
    content_filter_facets: list[dict] = [
        {"name": "primarySearchLabel", "requestedFacetCount": 9999}
    ]
    job_type_facets: list[dict] = []
    location_group: list[dict] = [
        {"name": "country", "requestedFacetCount": 9999},
        {"name": "normalizedStateName", "requestedFacetCount": 9999},
        {"name": "normalizedCityName", "requestedFacetCount": 9999},
    ]

    cms_filter_map = {
        "jobCategories": (filter_facets, "category"),
        "businessCategories": (filter_facets, "businessCategory"),
        "teams": (content_filter_facets, "primarySearchLabel"),
        "jobTypes": (job_type_facets, "scheduleTypeId"),
        "countries": (location_group, "country"),
        "states": (location_group, "normalizedStateName"),
        "cities": (location_group, "normalizedCityName"),
    }
    raw_filters = content.get("searchFilters", [])
    if isinstance(raw_filters, list):
        for raw_filter in raw_filters:
            if not isinstance(raw_filter, dict):
                continue
            for key, (container, facet_name) in cms_filter_map.items():
                values = raw_filter.get(key, [])
                if isinstance(values, list):
                    _add_facet(container, facet_name, values)

    query = parse_qs(urlsplit(page_url).query, keep_blank_values=False)
    query_filter_map = {
        "category[]": (filter_facets, "category"),
        "business_category[]": (filter_facets, "businessCategory"),
        "job_type[]": (job_type_facets, "scheduleTypeId"),
        "country[]": (location_group, "country"),
        "state[]": (location_group, "normalizedStateName"),
        "city[]": (location_group, "normalizedCityName"),
    }
    for key, (container, facet_name) in query_filter_map.items():
        _add_facet(container, facet_name, query.get(key, []))

    search_query = ""
    for key in ("query", "base_query"):
        if query.get(key):
            search_query = str(query[key][0]).strip()
            break

    split_url = urlsplit(page_url)
    public_origin = f"{split_url.scheme}://{split_url.netloc}"
    request_template = {
        "accessLevel": "EXTERNAL",
        "contentFilterFacets": content_filter_facets,
        "excludeFacets": [
            {"name": "isConfidential", "values": [{"name": "1"}]},
            {"name": "businessCategory", "values": [{"name": "a-confidential-job"}]},
        ],
        "filterFacets": filter_facets,
        "includeFacets": [],
        "jobTypeFacets": job_type_facets,
        "locationFacets": [location_group],
        "query": search_query,
        "size": AMAZON_PAGE_SIZE,
        "start": 0,
        "treatment": "OM",
        "sort": {"sortOrder": "DESCENDING", "sortType": "SCORE"},
    }
    return AmazonSearchConfig(
        endpoint_url=urljoin(public_origin + "/", endpoint.lstrip("/")) + "?is_als=true",
        api_key=api_key,
        public_origin=public_origin,
        locale=_locale_from_page(content, page_url),
        request_template=request_template,
    )


def amazon_request_payload(config: AmazonSearchConfig, *, start: int) -> dict:
    payload = json.loads(json.dumps(config.request_template))
    payload["start"] = max(0, start)
    return payload


def amazon_result_page_url(config: AmazonSearchConfig, *, start: int) -> str:
    parsed = urlsplit(config.endpoint_url)
    query = parse_qs(parsed.query)
    query["start"] = [str(start)]
    query["size"] = [str(config.request_template.get("size", AMAZON_PAGE_SIZE))]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query, doseq=True), ""))


def amazon_result_counts(payload: dict) -> tuple[int, int]:
    hits = payload.get("searchHits", [])
    raw_count = len(hits) if isinstance(hits, list) else 0
    try:
        total_hits = max(0, int(payload.get("found", raw_count)))
    except (TypeError, ValueError):
        total_hits = raw_count
    return total_hits, raw_count


def _first(fields: dict, key: str) -> str:
    value = fields.get(key)
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value or "").strip()


def _plain_text(value: str) -> str:
    return BeautifulSoup(value, "lxml").get_text(" ", strip=True)


def _locations(fields: dict) -> tuple[str, str]:
    locations: list[str] = []
    location_types: list[str] = []
    raw_locations = fields.get("locations", [])
    if not isinstance(raw_locations, list):
        raw_locations = [raw_locations]
    for raw_location in raw_locations:
        try:
            location = json.loads(str(raw_location))
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(location, dict):
            continue
        label = str(location.get("normalizedLocation") or location.get("location") or "").strip()
        if label:
            locations.append(label)
        location_type = str(location.get("type") or "").strip().lower()
        if location_type:
            location_types.append(location_type)
    if not locations:
        fallback = _first(fields, "location") or _first(fields, "normalizedLocation")
        if fallback:
            locations.append(fallback)
    return "; ".join(dict.fromkeys(locations)), " ".join(location_types)


def _work_mode(title: str, location: str, location_types: str) -> str:
    text = f"{title} {location} {location_types}".lower()
    if "remote" in text or "virtual" in text:
        return "remote"
    if "hybrid" in text:
        return "hybrid"
    if location:
        return "onsite"
    return "unknown"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _posted_at(fields: dict) -> str | None:
    raw = _first(fields, "updatedDate") or _first(fields, "createdDate")
    try:
        return datetime.fromtimestamp(int(raw), tz=UTC).isoformat()
    except (OverflowError, TypeError, ValueError):
        return raw or None


def extract_amazon_jobs(page: PageDocument) -> AmazonExtraction | None:
    """Extract structured jobs returned by Amazon's public CMS search API."""

    try:
        payload = json.loads(page.html)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or "searchHits" not in payload or "found" not in payload:
        return None
    total_hits, raw_job_count = amazon_result_counts(payload)
    raw_jobs = payload.get("searchHits", [])
    if not isinstance(raw_jobs, list):
        raw_jobs = []

    source = urlsplit(str(page.source_url))
    origin = f"{source.scheme}://{source.netloc}"
    source_parts = [part for part in source.path.split("/") if part]
    locale = source_parts[1] if len(source_parts) >= 2 and source_parts[0] == "content" else "en"
    jobs: list[JobPosting] = []
    for raw_job in raw_jobs:
        fields = raw_job.get("fields", {}) if isinstance(raw_job, dict) else {}
        if not isinstance(fields, dict):
            continue
        title = _first(fields, "title")
        external_id = _first(fields, "icimsJobId")
        if not title or not external_id:
            continue
        location, location_types = _locations(fields)
        description_parts = [
            _plain_text(_first(fields, key))
            for key in ("description", "basicQualifications", "preferredQualifications")
        ]
        description = "\n".join(part for part in description_parts if part)
        job_url = urljoin(
            origin + "/",
            f"{locale}/jobs/{external_id}/{_slug(title)}",
        )
        jobs.append(
            JobPosting(
                title=title,
                company=_first(fields, "companyName") or "Amazon",
                location=location,
                work_mode=_work_mode(title, location, location_types),
                description=description,
                url=job_url,
                source_url=str(page.source_url),
                external_id=external_id,
                employment_type=_first(fields, "scheduleTypeId") or None,
                posted_at=_posted_at(fields),
            )
        )
    return AmazonExtraction(
        jobs=jobs,
        total_hits=total_hits,
        raw_job_count=raw_job_count,
    )

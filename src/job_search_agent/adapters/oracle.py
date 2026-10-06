from __future__ import annotations

import html as html_module
import json
from dataclasses import dataclass
from urllib.parse import quote, urljoin, urlsplit

from bs4 import BeautifulSoup

from ..models import JobPosting, PageDocument


@dataclass(slots=True)
class OracleConfig:
    api_origin: str
    public_origin: str
    locale: str
    site_name: str
    company: str

    @property
    def search_url(self) -> str:
        return f"{self.api_origin}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"

    def search_finder(self, *, limit: int, offset: int) -> str:
        return (
            f"findReqs;siteNumber={self.site_name},limit={limit},offset={offset},"
            "sortBy=POSTING_DATES_DESC"
        )

    def public_job_url(self, job_id: object) -> str:
        return (
            f"{self.public_origin}/{quote(self.locale, safe='-')}/sites/"
            f"{quote(self.company, safe='-')}/job/{quote(str(job_id), safe='')}"
        )


@dataclass(slots=True)
class OracleExtraction:
    jobs: list[JobPosting]
    total_hits: int
    raw_job_count: int


def oracle_config(html: str, page_url: str) -> OracleConfig | None:
    soup = BeautifulSoup(html, "lxml")
    base = soup.find("base", attrs={"data-apibaseurl": True, "data-sitenumber": True})
    if base is None:
        return None
    api_origin = str(base.get("data-apibaseurl") or "").strip().rstrip("/")
    site_number = str(base.get("data-sitenumber") or "").strip()
    api = urlsplit(api_origin)
    public = urlsplit(page_url)
    if (
        api.scheme not in {"http", "https"}
        or not api.netloc
        or public.scheme not in {"http", "https"}
        or not public.netloc
        or not site_number
    ):
        return None
    base_path = str(base.get("href") or "").strip()
    parts = [part for part in base_path.split("/") if part]
    if len(parts) < 3 or parts[-2].casefold() != "sites":
        return None
    locale, company = parts[-3], parts[-1]
    vanity = str(base.get("data-vanitybaseurl") or "").strip()
    vanity_url = urlsplit(urljoin(page_url, vanity)) if vanity else public
    public_origin = f"{vanity_url.scheme}://{vanity_url.netloc}"
    return OracleConfig(
        api_origin=f"{api.scheme}://{api.netloc}",
        public_origin=public_origin,
        locale=locale,
        site_name=site_number,
        company=company,
    )


def _plain_text(value: object) -> str:
    decoded = html_module.unescape(str(value or ""))
    return BeautifulSoup(decoded, "lxml").get_text(" ", strip=True)


def _location(raw: dict) -> str:
    values = [str(raw.get("PrimaryLocation") or "").strip()]
    for field in ("secondaryLocations", "otherWorkLocations"):
        locations = raw.get(field)
        if isinstance(locations, list):
            values.extend(
                str(item.get("Name") or "").strip()
                for item in locations
                if isinstance(item, dict)
            )
    return "; ".join(dict.fromkeys(value for value in values if value))


def _description(raw: dict) -> str:
    fields = (
        "ExternalDescriptionStr",
        "ExternalResponsibilitiesStr",
        "ExternalQualificationsStr",
        "ShortDescriptionStr",
    )
    return "\n\n".join(dict.fromkeys(text for field in fields if (text := _plain_text(raw.get(field)))))


def _work_mode(raw: dict, location: str, description: str) -> str:
    explicit = f"{raw.get('WorkplaceType') or ''} {raw.get('WorkplaceTypeCode') or ''}"
    text = f"{explicit} {location} {description[:1500]}".casefold()
    if any(value in text for value in ("remote", "virtual", "work from home", "wfh")):
        return "remote"
    if "hybrid" in text:
        return "hybrid"
    if any(value in text for value in ("on-site", "onsite", "ora_on_site", "in office")):
        return "onsite"
    return "unknown"


def extract_oracle_jobs(page: PageDocument) -> OracleExtraction | None:
    try:
        payload = json.loads(page.html)
    except (json.JSONDecodeError, TypeError):
        return None
    oracle = payload.get("oracle") if isinstance(payload, dict) else None
    if not isinstance(oracle, dict) or not isinstance(oracle.get("jobs"), list):
        return None

    config = OracleConfig(
        api_origin=str(oracle.get("api_origin") or ""),
        public_origin=str(oracle.get("public_origin") or ""),
        locale=str(oracle.get("locale") or "en"),
        site_name=str(oracle.get("site_number") or ""),
        company=str(oracle.get("company") or ""),
    )
    jobs: list[JobPosting] = []
    for raw in oracle["jobs"]:
        if not isinstance(raw, dict):
            continue
        job_id = raw.get("Id")
        title = str(raw.get("Title") or "").strip()
        if job_id is None or not title:
            continue
        location = _location(raw)
        description = _description(raw)
        jobs.append(
            JobPosting(
                title=title,
                company=config.company,
                location=location,
                work_mode=_work_mode(raw, location, description),
                description=description,
                url=config.public_job_url(job_id),
                source_url=str(page.source_url),
                external_id=str(job_id),
                employment_type=str(
                    raw.get("JobSchedule") or raw.get("JobType") or raw.get("WorkerType") or ""
                ).strip()
                or None,
                posted_at=str(
                    raw.get("ExternalPostedStartDate") or raw.get("PostedDate") or ""
                ).strip()
                or None,
            )
        )
    return OracleExtraction(
        jobs=jobs,
        total_hits=int(oracle.get("total") or len(oracle["jobs"])),
        raw_job_count=int(oracle.get("raw_count") or len(oracle["jobs"])),
    )

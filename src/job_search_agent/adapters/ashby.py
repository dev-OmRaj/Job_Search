from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, urljoin, urlsplit

from ..models import JobPosting, PageDocument

BOARD_RE = re.compile(r"^[a-z0-9_-]+$", re.IGNORECASE)
ASHBY_URL_RE = re.compile(
    r"https?://jobs\.ashbyhq\.com/([a-z0-9_-]+)",
    re.IGNORECASE,
)


@dataclass(slots=True)
class AshbyConfig:
    board_name: str

    @property
    def endpoint_url(self) -> str:
        return (
            "https://api.ashbyhq.com/posting-api/job-board/"
            f"{quote(self.board_name, safe='')}"
        )

    @property
    def board_url(self) -> str:
        return f"https://jobs.ashbyhq.com/{quote(self.board_name, safe='')}"


@dataclass(slots=True)
class AshbyExtraction:
    jobs: list[JobPosting]
    total_hits: int
    raw_job_count: int


def _domain_board_candidate(page_url: str) -> str | None:
    hostname = (urlsplit(page_url).hostname or "").casefold().rstrip(".")
    labels = [label for label in hostname.split(".") if label]
    while labels and labels[0] in {"www", "careers", "jobs"}:
        labels.pop(0)
    candidate = labels[0] if labels else ""
    return candidate if BOARD_RE.fullmatch(candidate) else None


def ashby_config(html: str, page_url: str) -> AshbyConfig | None:
    parsed = urlsplit(page_url)
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if hostname == "jobs.ashbyhq.com":
        parts = [part for part in parsed.path.split("/") if part]
        board = parts[0] if parts else ""
        return AshbyConfig(board) if BOARD_RE.fullmatch(board) else None

    match = ASHBY_URL_RE.search(html)
    if match:
        return AshbyConfig(match.group(1))

    # Ashby embeds individual postings on a company's own careers URL with
    # `ashby_jid`. The board commonly matches the registrable domain label.
    if parse_qs(parsed.query).get("ashby_jid"):
        board = _domain_board_candidate(page_url)
        return AshbyConfig(board) if board else None
    return None


def _location(raw: dict) -> str:
    values = [str(raw.get("location") or "").strip()]
    secondary = raw.get("secondaryLocations")
    if isinstance(secondary, list):
        for item in secondary:
            if isinstance(item, dict):
                values.append(str(item.get("location") or item.get("name") or "").strip())
            else:
                values.append(str(item or "").strip())
    return "; ".join(dict.fromkeys(value for value in values if value))


def _work_mode(raw: dict, location: str) -> str:
    if raw.get("isRemote") is True:
        return "remote"
    text = f"{raw.get('workplaceType') or ''} {location}".casefold()
    if "remote" in text:
        return "remote"
    if "hybrid" in text:
        return "hybrid"
    if any(marker in text for marker in ("on-site", "onsite", "in office")):
        return "onsite"
    return "unknown"


def extract_ashby_jobs(page: PageDocument) -> AshbyExtraction | None:
    try:
        payload = json.loads(page.html)
    except (json.JSONDecodeError, TypeError):
        return None
    ashby = payload.get("ashby") if isinstance(payload, dict) else None
    if not isinstance(ashby, dict) or not isinstance(ashby.get("jobs"), list):
        return None

    company = str(ashby.get("company") or ashby.get("board_name") or "").strip()
    jobs: list[JobPosting] = []
    for raw in ashby["jobs"]:
        if not isinstance(raw, dict) or raw.get("isListed") is False:
            continue
        job_id = str(raw.get("id") or "").strip()
        title = str(raw.get("title") or "").strip()
        job_url = str(raw.get("jobUrl") or "").strip()
        if not job_id or not title or not job_url:
            continue
        location = _location(raw)
        jobs.append(
            JobPosting(
                title=title,
                company=company,
                location=location,
                work_mode=_work_mode(raw, location),
                description=str(raw.get("descriptionPlain") or "").strip(),
                url=urljoin("https://jobs.ashbyhq.com/", job_url),
                source_url=str(page.source_url),
                external_id=job_id,
                employment_type=str(raw.get("employmentType") or "").strip() or None,
                posted_at=str(raw.get("publishedAt") or "").strip() or None,
            )
        )
    return AshbyExtraction(
        jobs=jobs,
        total_hits=int(ashby.get("total") or len(ashby["jobs"])),
        raw_job_count=int(ashby.get("raw_count") or len(ashby["jobs"])),
    )

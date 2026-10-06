from __future__ import annotations

import html as html_module
import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, urljoin, urlsplit

from bs4 import BeautifulSoup

from ..models import JobPosting, PageDocument

GREENHOUSE_HOSTS = {
    "boards.greenhouse.io",
    "job-boards.greenhouse.io",
}
REMOTE_MARKERS = ("remote", "virtual", "work from home", "wfh")
BOARD_TOKEN_RE = re.compile(r"^[a-z0-9_-]+$", re.IGNORECASE)


@dataclass(slots=True)
class GreenhouseJobsConfig:
    endpoint_url: str
    board_token: str | None
    source_kind: str


@dataclass(slots=True)
class GreenhouseExtraction:
    jobs: list[JobPosting]
    total_hits: int
    raw_job_count: int


def _board_token_from_url(value: str, base_url: str) -> str | None:
    parsed = urlsplit(urljoin(base_url, html_module.unescape(value.strip())))
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if hostname not in GREENHOUSE_HOSTS:
        return None
    query = parse_qs(parsed.query)
    if parsed.path.rstrip("/").casefold() == "/embed/job_board":
        token = str((query.get("for") or [""])[0]).strip()
    else:
        parts = [part for part in parsed.path.split("/") if part]
        token = parts[0].strip() if parts else ""
    return token if BOARD_TOKEN_RE.fullmatch(token) else None


def greenhouse_jobs_config(html: str, page_url: str) -> GreenhouseJobsConfig | None:
    """Discover a standard Greenhouse board or a local Greenhouse JSON mirror."""

    soup = BeautifulSoup(html, "lxml")
    candidate_values: list[str] = []
    for tag, attribute in (("iframe", "src"), ("a", "href"), ("script", "src")):
        candidate_values.extend(
            str(element.get(attribute) or "")
            for element in soup.find_all(tag, attrs={attribute: True})
        )
    candidate_values.extend(
        match.group(0)
        for match in re.finditer(
            r"https?://(?:job-boards|boards)\.greenhouse\.io/[^\s\"'<>]+",
            html,
            flags=re.IGNORECASE,
        )
    )
    for candidate in candidate_values:
        token = _board_token_from_url(candidate, page_url)
        if token:
            return GreenhouseJobsConfig(
                endpoint_url=(
                    "https://boards-api.greenhouse.io/v1/boards/"
                    f"{quote(token, safe='')}/jobs?content=true"
                ),
                board_token=token,
                source_kind="public_api",
            )

    has_board_container = (
        soup.find(id="gh-board") is not None
        or soup.find(id="embedded_job_board_wrapper") is not None
    )
    has_local_greenhouse_script = any(
        "greenhouse" in str(script.get("src") or "").casefold()
        for script in soup.find_all("script", src=True)
    )
    if has_board_container and has_local_greenhouse_script:
        return GreenhouseJobsConfig(
            endpoint_url=urljoin(page_url, "data/greenhouse_jobs.json"),
            board_token=None,
            source_kind="local_mirror",
        )
    return None


def _plain_text(value: object) -> str:
    if value is None:
        return ""
    decoded = html_module.unescape(str(value))
    return BeautifulSoup(decoded, "lxml").get_text(" ", strip=True)


def _work_mode(title: str, location: str, description: str) -> str:
    text = f"{title} {location} {description[:500]}".casefold()
    return "remote" if any(marker in text for marker in REMOTE_MARKERS) else "unknown"


def _looks_like_greenhouse_payload(payload: dict, page_url: str) -> bool:
    hostname = (urlsplit(page_url).hostname or "").casefold()
    if hostname == "boards-api.greenhouse.io":
        return True
    if "board" in payload and "jobs" in payload:
        return True
    raw_jobs = payload.get("jobs")
    return bool(
        isinstance(raw_jobs, list)
        and any(
            isinstance(job, dict)
            and (
                job.get("gh_jid")
                or "greenhouse.io" in str(job.get("absolute_url") or job.get("url") or "")
            )
            for job in raw_jobs
        )
    )


def extract_greenhouse_jobs(page: PageDocument) -> GreenhouseExtraction | None:
    """Extract jobs from Greenhouse's public API or a compatible local mirror."""

    try:
        payload = json.loads(page.html)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or not _looks_like_greenhouse_payload(
        payload,
        str(page.url),
    ):
        return None
    raw_jobs = payload.get("jobs", [])
    if not isinstance(raw_jobs, list):
        return GreenhouseExtraction(jobs=[], total_hits=0, raw_job_count=0)

    board = payload.get("board", {})
    board_name = str(board.get("name") or "") if isinstance(board, dict) else ""
    jobs: list[JobPosting] = []
    for raw_job in raw_jobs:
        if not isinstance(raw_job, dict):
            continue
        details = raw_job.get("details", {})
        if not isinstance(details, dict):
            details = {}
        title = str(raw_job.get("title") or details.get("title") or "").strip()
        job_url = str(
            raw_job.get("absolute_url") or raw_job.get("url") or details.get("url") or ""
        ).strip()
        if not title or not job_url:
            continue
        location = raw_job.get("location", {})
        if isinstance(location, dict):
            location_text = str(location.get("name") or "").strip()
        else:
            location_text = str(location or "").strip()
        location_text = str(
            raw_job.get("location_text") or details.get("location_text") or location_text
        ).strip()
        description = _plain_text(
            details.get("description_text")
            or details.get("description_html")
            or raw_job.get("content")
        )
        external_id = raw_job.get("id") or raw_job.get("gh_jid")
        jobs.append(
            JobPosting(
                title=title,
                company=str(raw_job.get("company_name") or board_name).strip(),
                location=location_text,
                work_mode=_work_mode(title, location_text, description),
                description=description,
                url=job_url,
                source_url=str(page.source_url),
                external_id=str(external_id) if external_id is not None else None,
                posted_at=str(
                    raw_job.get("first_published")
                    or raw_job.get("updated_at")
                    or details.get("fetched_at")
                    or ""
                )
                or None,
            )
        )

    total_hits = len(raw_jobs)
    counts = payload.get("counts")
    if isinstance(counts, dict):
        for key in ("jobs", "total", "total_jobs"):
            try:
                total_hits = max(total_hits, int(counts.get(key) or 0))
            except (TypeError, ValueError):
                continue
    return GreenhouseExtraction(
        jobs=jobs,
        total_hits=total_hits,
        raw_job_count=len(raw_jobs),
    )

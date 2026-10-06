from __future__ import annotations

import json
import math
import os
import re
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, urldefrag, urlencode, urljoin, urlparse, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from .adapters.amazon import (
    AmazonSearchConfig,
    amazon_request_payload,
    amazon_result_counts,
    amazon_result_page_url,
    amazon_search_config,
)
from .adapters.ashby import AshbyConfig, ashby_config
from .adapters.atlassian import AtlassianConfig, atlassian_config
from .adapters.eightfold import EightfoldConfig, eightfold_config
from .adapters.eurofins import (
    EurofinsConfig,
    eurofins_config,
    filter_eurofins_jobs,
    smartrecruiters_detail_url,
)
from .adapters.greenhouse import GreenhouseJobsConfig, greenhouse_jobs_config
from .adapters.html_cards import html_job_complete_url, is_structured_html_job_listing
from .adapters.keka import KekaConfig, keka_config, keka_endpoint_urls
from .adapters.nextjs import is_nextjs_job_listing
from .adapters.oracle import OracleConfig, oracle_config
from .adapters.phenom import (
    UNRESOLVED_TEMPLATE_MARKERS,
    is_phenom_listing,
    phenom_next_page_urls,
)
from .adapters.salesforce import FEED_ROOT, SalesforceConfig, filter_jobs, salesforce_config
from .adapters.successfactors import SuccessFactorsConfig, successfactors_config
from .adapters.workday import WorkdayConfig, workday_config, workday_running_total
from .models import CrawlerConfig, PageDocument
from .progress import ProgressCallback, emit_progress
from .url_policy import UnsafeUrlError, ensure_public_http_url

JOB_LINK_HINTS = (
    "job",
    "jobs",
    "career",
    "careers",
    "opening",
    "openings",
    "position",
    "positions",
    "vacancy",
    "requisition",
    "opportunit",
)
SKIP_LINK_HINTS = ("login", "signin", "privacy", "terms", "cookie", "mailto:", "tel:")
SKIP_URL_HINTS = (
    "createaccount",
    "create-account",
    "sign-in",
    "signup",
    "sign-up",
    "/register",
    "/auth/",
)
SKIP_FILE_SUFFIXES = (
    ".csv",
    ".doc",
    ".docx",
    ".gif",
    ".jpeg",
    ".jpg",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".svg",
    ".webp",
    ".xls",
    ".xlsx",
    ".zip",
)
KNOWN_ATS_DOMAINS = (
    "ashbyhq.com",
    "greenhouse.io",
    "icims.com",
    "jobs.lever.co",
    "myworkdayjobs.com",
    "smartrecruiters.com",
    "successfactors.com",
)
COMMON_COUNTRY_SECOND_LEVEL_DOMAINS = {"ac", "co", "com", "edu", "gov", "net", "org"}
BROWSER_RECOVERY_STATUS_CODES = {403, 408, 425, 429, 500, 502, 503, 504}
PHENOM_ADAPTIVE_PAGE_CAP = 1000
EIGHTFOLD_ADAPTIVE_PAGE_CAP = 1000
EIGHTFOLD_DETAIL_WORKERS = 4
EIGHTFOLD_DETAIL_MAX_ATTEMPTS = 4
EUROFINS_DETAIL_WORKERS = 8
ORACLE_ADAPTIVE_PAGE_CAP = 1000
ORACLE_PAGE_SIZE = 100


class OffsiteRedirectError(Exception):
    """A discovered page redirected outside the active careers site."""


class UnsupportedContentError(ValueError):
    """A discovered URL returned something other than an HTML/text page."""


class AccessChallengeError(RuntimeError):
    """A bot-protection challenge was returned instead of the requested page."""


def configure_playwright_browser_path() -> None:
    """Prefer the project-local browser cache when it has been installed."""

    local_browsers = Path(".playwright-browsers").resolve()
    if local_browsers.is_dir():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(local_browsers))


@dataclass(slots=True)
class CrawlReport:
    pages: list[PageDocument]
    errors: list[str]
    skipped: list[str] = field(default_factory=list)


class CareersCrawler:
    def __init__(
        self,
        config: CrawlerConfig,
        *,
        user_agent: str,
        progress: ProgressCallback | None = None,
    ):
        self.config = config
        self.user_agent = user_agent
        self.progress = progress
        self._robots: dict[str, str | None] = {}

    def crawl(self, source_url: str) -> CrawlReport:
        ensure_public_http_url(source_url)
        source_canonical = urldefrag(source_url)[0]
        queue: deque[tuple[str, int]] = deque([(source_url, 0)])
        seen: set[str] = set()
        pages: list[PageDocument] = []
        errors: list[str] = []
        skipped: list[str] = []
        emit_progress(
            self.progress,
            "crawl",
            "Discovering careers pages",
            completed=0,
            total=1,
        )

        with httpx.Client(
            # Do not advertise zstd here. Some careers sites return zstd streams
            # that cannot be decoded reliably by every httpx/python-zstandard
            # combination. gzip and deflate are broadly supported.
            headers={
                "User-Agent": self.user_agent,
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=self.config.request_timeout_seconds,
            follow_redirects=False,
        ) as client:
            while queue and len(pages) < self.config.max_pages and len(seen) < self.config.max_pages:
                url, depth = queue.popleft()
                canonical = urldefrag(url)[0]
                if canonical in seen:
                    continue
                seen.add(canonical)
                is_seed = canonical == source_canonical
                try:
                    direct_ashby = ashby_config("", canonical)
                    if direct_ashby is not None:
                        ashby_page, ashby_errors = self._fetch_ashby_jobs(
                            client, direct_ashby, source_url
                        )
                        errors.extend(ashby_errors)
                        if ashby_page is not None:
                            pages.append(ashby_page)
                        elif is_seed:
                            raise RuntimeError(
                                ashby_errors[0]
                                if ashby_errors
                                else "Ashby jobs endpoint returned no usable page"
                            )
                        queue.clear()
                        continue
                    try:
                        html, final_url = self._fetch_http(
                            client,
                            canonical,
                            allow_offsite_redirect=is_seed,
                        )
                    except Exception as exc:
                        if not self.config.use_playwright_fallback or not self._should_try_browser(
                            exc
                        ):
                            raise
                        browser_result = self._fetch_playwright(
                            canonical,
                            allowed_site_url=None if is_seed else canonical,
                        )
                        if browser_result is None:
                            raise
                        html, final_url = browser_result
                    challenge = self._access_challenge_provider(html)
                    if challenge:
                        raise AccessChallengeError(
                            f"{challenge} anti-bot challenge blocked access; the careers page "
                            "was not scanned. Use the company's public ATS or jobs API when "
                            "available."
                        )
                    soup = BeautifulSoup(html, "lxml")
                    text = self._visible_text(soup)
                    if (self.config.use_playwright_fallback and self._needs_browser(html, text)
                            and salesforce_config(html, final_url) is None):
                        browser_result = self._fetch_playwright(
                            final_url,
                            allowed_site_url=None if is_seed else final_url,
                        )
                        if browser_result:
                            html, final_url = browser_result
                            challenge = self._access_challenge_provider(html)
                            if challenge:
                                raise AccessChallengeError(
                                    f"{challenge} anti-bot challenge remained after browser "
                                    "fallback; the careers page was not scanned. Use the "
                                    "company's public ATS or jobs API when available."
                                )
                            soup = BeautifulSoup(html, "lxml")
                            text = self._visible_text(soup)
                    amazon_config = amazon_search_config(html, final_url)
                    detected_ashby = ashby_config(html, final_url)
                    detected_atlassian = atlassian_config(html, final_url)
                    detected_eurofins = eurofins_config(html, final_url)
                    greenhouse_config = greenhouse_jobs_config(html, final_url)
                    detected_workday = workday_config(final_url)
                    detected_eightfold = eightfold_config(html, final_url)
                    detected_oracle = oracle_config(html, final_url)
                    sap_config = successfactors_config(html, final_url)
                    nextjs_listing = is_nextjs_job_listing(html)
                    detected_keka = keka_config(html, final_url)
                    html_cards_listing = is_structured_html_job_listing(html)
                    detected_salesforce = salesforce_config(html, final_url)
                    if detected_salesforce is not None:
                        sf_page, sf_errors = self._fetch_salesforce_jobs(client, detected_salesforce, source_url)
                        errors.extend(sf_errors)
                        if sf_page is not None:
                            pages.append(sf_page)
                        queue.clear()
                    elif detected_atlassian is not None:
                        atlassian_page, atlassian_errors = self._fetch_atlassian_jobs(
                            client, detected_atlassian, source_url
                        )
                        errors.extend(atlassian_errors)
                        if atlassian_page is not None:
                            pages.append(atlassian_page)
                        elif is_seed:
                            raise RuntimeError(
                                atlassian_errors[0]
                                if atlassian_errors
                                else "Atlassian listings endpoint returned no usable page"
                            )
                        queue.clear()
                    elif detected_eurofins is not None:
                        eurofins_page, eurofins_errors = self._fetch_eurofins_jobs(
                            client, detected_eurofins, source_url
                        )
                        errors.extend(eurofins_errors)
                        if eurofins_page is not None:
                            pages.append(eurofins_page)
                        queue.clear()
                    elif detected_ashby is not None:
                        ashby_page, ashby_errors = self._fetch_ashby_jobs(
                            client, detected_ashby, source_url
                        )
                        errors.extend(ashby_errors)
                        if ashby_page is not None:
                            pages.append(ashby_page)
                        queue.clear()
                    elif detected_oracle is not None:
                        oracle_page, oracle_errors = self._fetch_oracle_jobs(
                            client, detected_oracle, source_url
                        )
                        errors.extend(oracle_errors)
                        if oracle_page is not None:
                            pages.append(oracle_page)
                        queue.clear()
                    elif detected_eightfold is not None:
                        eightfold_page, eightfold_errors = self._fetch_eightfold_jobs(
                            client,
                            detected_eightfold,
                            source_url,
                        )
                        errors.extend(eightfold_errors)
                        if eightfold_page is not None:
                            pages.append(eightfold_page)
                        queue.clear()
                    elif detected_workday is not None:
                        workday_page, workday_errors = self._fetch_workday_jobs(
                            client,
                            detected_workday,
                            source_url,
                            max_pages=self.config.max_pages,
                            portal_url=final_url,
                        )
                        errors.extend(workday_errors)
                        if workday_page is not None:
                            pages.append(workday_page)
                        queue.clear()
                    elif amazon_config is not None:
                        amazon_pages, amazon_errors = self._fetch_amazon_search_pages(
                            client,
                            amazon_config,
                            source_url,
                            max_pages=self.config.max_pages - len(pages),
                        )
                        pages.extend(amazon_pages)
                        errors.extend(amazon_errors)
                        queue.clear()
                    elif greenhouse_config is not None:
                        greenhouse_page, greenhouse_errors = self._fetch_greenhouse_jobs(
                            client,
                            greenhouse_config,
                            source_url,
                        )
                        errors.extend(greenhouse_errors)
                        if greenhouse_page is not None:
                            pages.append(greenhouse_page)
                            queue.clear()
                        else:
                            pages.append(
                                PageDocument(
                                    url=final_url,
                                    source_url=source_url,
                                    html=html,
                                    text=text,
                                    depth=depth,
                                )
                            )
                    elif sap_config is not None and self.config.use_playwright_fallback:
                        sap_pages, sap_errors = self._fetch_successfactors_pages(
                            sap_config,
                            source_url,
                            max_pages=self.config.max_pages - len(pages),
                        )
                        errors.extend(sap_errors)
                        if sap_pages:
                            pages.extend(sap_pages)
                            queue.clear()
                        else:
                            pages.append(
                                PageDocument(
                                    url=final_url,
                                    source_url=source_url,
                                    html=html,
                                    text=text,
                                    depth=depth,
                                )
                            )
                    elif nextjs_listing:
                        pages.append(
                            PageDocument(
                                url=final_url,
                                source_url=source_url,
                                html=html,
                                text=text,
                                depth=depth,
                            )
                        )
                        queue.clear()
                    elif detected_keka is not None:
                        keka_page, keka_errors = self._fetch_keka_jobs(
                            client,
                            detected_keka,
                            source_url,
                        )
                        errors.extend(keka_errors)
                        if keka_page is not None:
                            pages.append(keka_page)
                            queue.clear()
                        else:
                            pages.append(
                                PageDocument(
                                    url=final_url,
                                    source_url=source_url,
                                    html=html,
                                    text=text,
                                    depth=depth,
                                )
                            )
                    elif html_cards_listing:
                        pages.append(
                            PageDocument(
                                url=final_url,
                                source_url=source_url,
                                html=html,
                                text=text,
                                depth=depth,
                            )
                        )
                        complete_url = html_job_complete_url(html, final_url)
                        queue.clear()
                        if complete_url and complete_url not in seen:
                            queue.appendleft((complete_url, depth))
                    else:
                        pages.append(
                            PageDocument(
                                url=final_url,
                                source_url=source_url,
                                html=html,
                                text=text,
                                depth=depth,
                            )
                        )
                        if is_phenom_listing(html):
                            phenom_pages, phenom_errors = self._fetch_phenom_pages(
                                client,
                                pages.pop(),
                                source_url,
                            )
                            pages.extend(phenom_pages)
                            errors.extend(phenom_errors)
                            queue.clear()
                        elif depth < self.config.max_depth:
                            for link in self._candidate_links(soup, final_url):
                                if link not in seen:
                                    queue.append((link, depth + 1))
                except AccessChallengeError as exc:
                    # A challenge on the supplied page means there is no trustworthy
                    # document to extract. Let the workflow record a failed scan rather
                    # than exporting a misleading empty result set.
                    if is_seed:
                        raise
                    errors.append(f"{canonical}: {exc}")
                except OffsiteRedirectError as exc:
                    skipped.append(f"{canonical}: {exc}")
                except (PermissionError, UnsupportedContentError) as exc:
                    if is_seed:
                        errors.append(f"{canonical}: {exc}")
                    else:
                        skipped.append(f"{canonical}: {exc}")
                except Exception as exc:  # keep useful pages when one job link fails
                    errors.append(f"{canonical}: {exc}")
                attempted_so_far = max(len(seen), len(pages))
                expected = max(
                    attempted_so_far,
                    min(self.config.max_pages, attempted_so_far + len(queue)),
                )
                emit_progress(
                    self.progress,
                    "crawl",
                    f"Crawling careers pages ({len(pages)} loaded)",
                    completed=attempted_so_far,
                    total=max(expected, 1),
                )
                if queue and self.config.delay_seconds:
                    time.sleep(self.config.delay_seconds)
        if queue:
            errors.append(
                f"Crawl limit reached (crawler.max_pages={self.config.max_pages}); "
                "remaining queued pages were not scanned. Results are partial."
            )
        attempted = max(len(seen), len(pages))
        emit_progress(
            self.progress,
            "crawl",
            f"Crawled {len(pages)} pages ({len(skipped)} skipped, {len(errors)} errors)",
            completed=attempted,
            total=max(attempted, 1),
            finished=True,
        )
        return CrawlReport(pages=pages, errors=errors, skipped=skipped)

    def _fetch_atlassian_jobs(
        self,
        client: httpx.Client,
        config: AtlassianConfig,
        source_url: str,
    ) -> tuple[PageDocument | None, list[str]]:
        try:
            ensure_public_http_url(config.endpoint_url)
            if not self._same_site(source_url, config.endpoint_url):
                raise OffsiteRedirectError(
                    "ignored Atlassian listings endpoint outside the careers site"
                )
            if not self._allowed_by_robots(client, config.endpoint_url):
                raise PermissionError("robots.txt disallows the Atlassian listings endpoint")
            response = self._get_with_decode_retry(client, config.endpoint_url)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("Atlassian listings endpoint returned an invalid payload")
            jobs = [job for job in payload if isinstance(job, dict)]
            body = json.dumps(
                {
                    "atlassian": {
                        "total": len(jobs),
                        "raw_count": len(jobs),
                        "jobs": jobs,
                    }
                }
            )
            emit_progress(
                self.progress,
                "crawl",
                f"Loaded {len(jobs)} Atlassian job listings",
                completed=len(jobs),
                total=len(jobs),
            )
            return (
                PageDocument(
                    url=config.endpoint_url,
                    source_url=source_url,
                    html=body,
                    text="",
                    depth=0,
                ),
                [],
            )
        except Exception as exc:
            return None, [f"{config.endpoint_url}: Atlassian jobs request failed: {exc}"]

    def _fetch_ashby_jobs(
        self,
        client: httpx.Client,
        config: AshbyConfig,
        source_url: str,
    ) -> tuple[PageDocument | None, list[str]]:
        try:
            ensure_public_http_url(config.endpoint_url)
            if (urlsplit(config.endpoint_url).hostname or "").casefold() != "api.ashbyhq.com":
                raise OffsiteRedirectError("ignored endpoint outside Ashby's public API")
            if not self._allowed_by_robots(client, config.endpoint_url):
                raise PermissionError("robots.txt disallows the Ashby jobs endpoint")
            response = self._get_with_decode_retry(client, config.endpoint_url)
            response.raise_for_status()
            payload = response.json()
            raw_jobs = payload.get("jobs") if isinstance(payload, dict) else None
            if not isinstance(raw_jobs, list):
                raise ValueError("Ashby jobs endpoint returned an invalid payload")

            company = config.board_name.replace("-", " ").replace("_", " ").title()
            with suppress(Exception):
                board_response = self._get_with_decode_retry(client, config.board_url)
                board_response.raise_for_status()
                title = BeautifulSoup(board_response.text, "lxml").title
                title_text = title.get_text(" ", strip=True) if title else ""
                title_text = re.sub(r"\s+Jobs\s*$", "", title_text, flags=re.IGNORECASE)
                if title_text:
                    company = title_text

            body = json.dumps(
                {
                    "ashby": {
                        "board_name": config.board_name,
                        "company": company,
                        "total": len(raw_jobs),
                        "raw_count": len(raw_jobs),
                        "jobs": raw_jobs,
                    }
                }
            )
            emit_progress(
                self.progress,
                "crawl",
                f"Loaded {len(raw_jobs)} Ashby jobs",
                completed=len(raw_jobs),
                total=len(raw_jobs),
            )
            return (
                PageDocument(
                    url=config.endpoint_url,
                    source_url=source_url,
                    html=body,
                    text="",
                    depth=0,
                ),
                [],
            )
        except Exception as exc:
            return None, [f"{config.endpoint_url}: Ashby jobs request failed: {exc}"]

    def _fetch_eurofins_jobs(
        self,
        client: httpx.Client,
        config: EurofinsConfig,
        source_url: str,
    ) -> tuple[PageDocument | None, list[str]]:
        errors: list[str] = []
        try:
            ensure_public_http_url(config.endpoint_url)
            if (
                urlsplit(config.endpoint_url).hostname or ""
            ).casefold() != "atsintegration.eurofins.com":
                raise OffsiteRedirectError("ignored endpoint outside Eurofins' public ATS feed")
            if not self._allowed_by_robots(client, config.endpoint_url):
                raise PermissionError("robots.txt disallows the Eurofins jobs endpoint")
            response = self._get_with_decode_retry(client, config.endpoint_url)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("Eurofins jobs endpoint returned an invalid payload")
            raw_jobs = [job for job in payload if isinstance(job, dict)]
            filtered_jobs = filter_eurofins_jobs(raw_jobs, config)
        except Exception as exc:
            return None, [f"{config.endpoint_url}: Eurofins jobs request failed: {exc}"]

        emit_progress(
            self.progress,
            "crawl",
            f"Loaded {len(filtered_jobs)} filtered Eurofins jobs",
            completed=len(filtered_jobs),
            total=len(filtered_jobs),
        )

        def load_detail(job: dict) -> tuple[dict, str | None]:
            enriched = dict(job)
            detail_url = smartrecruiters_detail_url(job)
            if detail_url is None:
                return enriched, f"Eurofins job has no valid SmartRecruiters URL: {job.get('title')}"
            try:
                response = self._get_with_decode_retry(client, detail_url)
                response.raise_for_status()
                detail = response.json()
                if not isinstance(detail, dict):
                    raise ValueError("invalid detail payload")
                enriched["smartrecruitersDetail"] = detail
                return enriched, None
            except Exception as exc:
                return enriched, f"{detail_url}: SmartRecruiters job detail request failed: {exc}"

        enriched_jobs: list[dict] = []
        detail_failures: list[str] = []
        with ThreadPoolExecutor(max_workers=EUROFINS_DETAIL_WORKERS) as executor:
            futures = [executor.submit(load_detail, job) for job in filtered_jobs]
            for completed, future in enumerate(as_completed(futures), start=1):
                job, error = future.result()
                enriched_jobs.append(job)
                if error:
                    detail_failures.append(error)
                emit_progress(
                    self.progress,
                    "crawl",
                    "Loading Eurofins job details",
                    completed=completed,
                    total=len(futures),
                )
        if detail_failures:
            errors.extend(detail_failures[:20])
            if len(detail_failures) > 20:
                errors.append(
                    f"Eurofins detail requests had {len(detail_failures)} failures; "
                    "only the first 20 are shown."
                )

        body = json.dumps(
            {
                "eurofins": {
                    "total": len(filtered_jobs),
                    "raw_count": len(enriched_jobs),
                    "unfiltered_total": len(raw_jobs),
                    "jobs": enriched_jobs,
                }
            }
        )
        return (
            PageDocument(
                url=config.endpoint_url,
                source_url=source_url,
                html=body,
                text="",
                depth=0,
            ),
            errors,
        )

    def _fetch_keka_jobs(
        self,
        client: httpx.Client,
        config: KekaConfig,
        source_url: str,
    ) -> tuple[PageDocument | None, list[str]]:
        try:
            if config.portal_document_url:
                rendered_config = self._fetch_keka_portal_config(source_url)
                if rendered_config is None:
                    raise ValueError("Keka career portal could not be rendered")
                config = rendered_config
            jobs_url, info_url = keka_endpoint_urls(config)
            for endpoint in (jobs_url, info_url):
                ensure_public_http_url(endpoint)
                if not self._same_site(source_url, endpoint):
                    raise OffsiteRedirectError(
                        f"ignored Keka endpoint outside the careers site ({endpoint})"
                    )
                if not self._allowed_by_robots(client, endpoint):
                    raise PermissionError(f"robots.txt disallows Keka endpoint {endpoint}")
            jobs_response = self._get_with_decode_retry(client, jobs_url)
            jobs_response.raise_for_status()
            raw_jobs = jobs_response.json()
            if not isinstance(raw_jobs, list):
                raise ValueError("Keka active-jobs endpoint returned a non-list response")
            info_response = self._get_with_decode_retry(client, info_url)
            info_response.raise_for_status()
            info = info_response.json()
            company = (
                str(info.get("name") or info.get("shortName") or "")
                if isinstance(info, dict)
                else ""
            )
            payload = {
                "provider": "keka",
                "company": company,
                "domain": config.domain,
                "portal_name": config.portal_name,
                "jobs": raw_jobs,
            }
            return (
                PageDocument(
                    url=jobs_url,
                    source_url=source_url,
                    html=json.dumps(payload, ensure_ascii=False),
                    text="",
                    depth=0,
                ),
                [],
            )
        except Exception as exc:
            return None, [f"Keka jobs request failed: {exc}"]

    def _fetch_keka_portal_config(self, url: str) -> KekaConfig | None:
        try:
            from playwright.sync_api import Error, sync_playwright

            configure_playwright_browser_path()
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(user_agent=self.user_agent)
                try:
                    response = page.goto(
                        url,
                        wait_until="domcontentloaded",
                        timeout=int(self.config.request_timeout_seconds * 1000),
                    )
                    if response is not None and response.status >= 400:
                        return None
                    page.wait_for_function(
                        "typeof khConfig !== 'undefined' && Boolean(khConfig.identifier)",
                        timeout=int(self.config.request_timeout_seconds * 1000),
                    )
                    raw_config = page.evaluate(
                        "({identifier: khConfig.identifier, domain: khConfig.domain, portalName: khConfig.portalName || 'default'})"
                    )
                    if not isinstance(raw_config, dict):
                        return None
                    return KekaConfig(
                        domain=str(raw_config.get("domain") or ""),
                        identifier=str(raw_config.get("identifier") or ""),
                        portal_name=str(raw_config.get("portalName") or "default"),
                    )
                finally:
                    browser.close()
        except (ImportError, Error, UnsafeUrlError):
            return None

    def _fetch_phenom_pages(
        self,
        client: httpx.Client,
        first_page: PageDocument,
        source_url: str,
    ) -> tuple[list[PageDocument], list[str]]:
        """Load a Phenom result set to completion with a provider safety cap."""

        pages = [first_page]
        errors: list[str] = []
        next_urls = phenom_next_page_urls(first_page.html, str(first_page.url))
        seen = {str(first_page.url)}
        while next_urls and len(pages) < PHENOM_ADAPTIVE_PAGE_CAP:
            next_url = next_urls[0]
            if next_url in seen:
                break
            seen.add(next_url)
            try:
                ensure_public_http_url(next_url, resolve_dns=False)
                if not self._same_site(str(first_page.url), next_url):
                    raise OffsiteRedirectError(
                        f"ignored Phenom page outside the careers site ({next_url})"
                    )
                html, final_url = self._fetch_http(
                    client,
                    next_url,
                    allow_offsite_redirect=False,
                )
                soup = BeautifulSoup(html, "lxml")
                page = PageDocument(
                    url=final_url,
                    source_url=source_url,
                    html=html,
                    text=self._visible_text(soup),
                    depth=first_page.depth,
                )
                pages.append(page)
                next_urls = phenom_next_page_urls(html, final_url)
                emit_progress(
                    self.progress,
                    "crawl",
                    "Loading Phenom result pages",
                    completed=len(pages),
                    total=max(len(pages), len(pages) + int(bool(next_urls))),
                )
            except Exception as exc:
                errors.append(f"{next_url}: Phenom pagination failed: {exc}")
                break
            if next_urls and self.config.delay_seconds:
                time.sleep(self.config.delay_seconds)

        if next_urls and len(pages) >= PHENOM_ADAPTIVE_PAGE_CAP:
            errors.append(
                "Phenom pagination stopped at the provider safety cap of "
                f"{PHENOM_ADAPTIVE_PAGE_CAP} pages."
            )
        return pages, errors

    def _fetch_greenhouse_jobs(
        self,
        client: httpx.Client,
        config: GreenhouseJobsConfig,
        source_url: str,
    ) -> tuple[PageDocument | None, list[str]]:
        try:
            ensure_public_http_url(config.endpoint_url)
            if not self._same_site(source_url, config.endpoint_url) and not self._is_ats_url(
                config.endpoint_url
            ):
                raise OffsiteRedirectError(
                    f"ignored Greenhouse endpoint outside the careers site ({config.endpoint_url})"
                )
            if not self._allowed_by_robots(client, config.endpoint_url):
                raise PermissionError("robots.txt disallows the Greenhouse jobs endpoint")
            response = self._get_with_decode_retry(client, config.endpoint_url)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
                raise ValueError("Greenhouse jobs endpoint returned an invalid payload")
            return (
                PageDocument(
                    url=config.endpoint_url,
                    source_url=source_url,
                    html=response.text,
                    text="",
                    depth=0,
                ),
                [],
            )
        except Exception as exc:
            return None, [f"{config.endpoint_url}: Greenhouse jobs request failed: {exc}"]

    def _fetch_salesforce_jobs(
        self, client: httpx.Client, config: SalesforceConfig, source_url: str,
    ) -> tuple[PageDocument | None, list[str]]:
        errors: list[str] = []
        feeds: list[dict | None] = []
        for number in (1, 2):
            feed = None
            failures = []
            for suffix in ("", "_backup"):
                url = f"{FEED_ROOT}/jobs_{number}{suffix}.json"
                try:
                    ensure_public_http_url(url)
                    if not self._allowed_by_robots(client, url):
                        raise PermissionError("robots.txt disallows Salesforce's job feed")
                    response = self._get_with_decode_retry(client, url)
                    response.raise_for_status()
                    if response.is_redirect:
                        raise ValueError("unexpected redirect from Salesforce job feed")
                    feed = response.json()
                    if not isinstance(feed, dict) or not isinstance(feed.get("Report_Entry"), list):
                        raise ValueError("Salesforce feed has no Report_Entry list")
                    break
                except Exception as exc:
                    feed = None
                    failures.append(f"{url}: {exc}")
            if feed is None:
                errors.extend(failures)
            feeds.append(feed)
            emit_progress(self.progress, "crawl", "Loading Salesforce's original-site job feeds",
                          completed=number, total=2)
        if all(feed is None for feed in feeds):
            return None, errors
        merged: dict[str, dict] = {}
        complete = all(feed is not None for feed in feeds)
        for number, feed in enumerate(feeds, start=1):
            if feed is None:
                continue
            records = feed["Report_Entry"]
            if int(feed.get("Total_Jobs", len(records))) > len(records):
                complete = False
                errors.append(f"Salesforce feed {number} returned fewer records than Total_Jobs.")
            for raw in records:
                if not isinstance(raw, dict) or not raw.get("Job_Requisition_Ref_ID"):
                    complete = False
                    errors.append("Salesforce feed returned a record without a requisition ID.")
                    continue
                key = str(raw["Job_Requisition_Ref_ID"])
                merged[key] = {**merged.get(key, {}), **raw, "_full_details": number == 2}
        try:
            records = filter_jobs(list(merged.values()), source_url)
        except ValueError as exc:
            return None, errors + [str(exc)]
        if any(not raw.get("_full_details") or not raw.get("Job_Description") for raw in records):
            complete = False
            errors.append("Some Salesforce job descriptions are unavailable; affected jobs will not be shortlisted.")
        body = json.dumps({"salesforce": {
            "board_url": config.board_url, "total": len(records),
            "coverage_complete": complete, "jobs": records,
        }})
        return PageDocument(url=source_url, source_url=source_url, html=body, text=""), errors

    def _fetch_workday_jobs(
        self,
        client: httpx.Client,
        config: WorkdayConfig,
        source_url: str,
        *,
        max_pages: int,
        portal_url: str | None = None,
    ) -> tuple[PageDocument | None, list[str]]:
        errors: list[str] = []
        records: list[dict] = []
        total = 0
        limit = 20
        try:
            ensure_public_http_url(config.endpoint_url)
            if not self._same_site(portal_url or source_url, config.endpoint_url):
                raise OffsiteRedirectError("ignored Workday endpoint outside the careers site")
            if not self._allowed_by_robots(client, config.endpoint_url):
                raise PermissionError("robots.txt disallows the Workday jobs endpoint")
        except Exception as exc:
            return None, [f"{config.endpoint_url}: Workday jobs request failed: {exc}"]

        for batch in range(max_pages):
            offset = batch * limit
            try:
                response = client.post(
                    config.endpoint_url,
                    json={"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": ""},
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                )
                response.raise_for_status()
                payload = response.json()
                postings = payload.get("jobPostings") if isinstance(payload, dict) else None
                if not isinstance(postings, list):
                    raise ValueError("Workday jobs endpoint returned an invalid payload")
                total = workday_running_total(
                    total,
                    payload,
                    offset=offset,
                    returned=len(postings),
                )
            except Exception as exc:
                errors.append(f"{config.endpoint_url} (offset={offset}): {exc}")
                break

            for posting in postings:
                if not isinstance(posting, dict):
                    continue
                record = dict(posting)
                external_path = str(posting.get("externalPath") or "").strip()
                if not external_path:
                    errors.append("Workday returned a job without an externalPath; skipped it")
                    continue
                record["publicUrl"] = config.public_job_url(external_path)
                detail_url = config.detail_url(external_path)
                try:
                    detail_response = self._get_with_decode_retry(client, detail_url)
                    detail_response.raise_for_status()
                    detail_payload = detail_response.json()
                    info = detail_payload.get("jobPostingInfo")
                    if isinstance(info, dict):
                        record["jobPostingInfo"] = info
                    else:
                        errors.append(f"{detail_url}: Workday job detail payload was incomplete")
                except Exception as exc:
                    errors.append(f"{detail_url}: Workday job detail request failed: {exc}")
                records.append(record)

            emit_progress(
                self.progress,
                "crawl",
                f"Loading Workday jobs ({len(records)} loaded)",
                completed=min(len(records), total),
                total=max(total, len(records)),
            )
            if not postings or len(records) >= total:
                break
            if self.config.delay_seconds:
                time.sleep(self.config.delay_seconds)

        if total > len(records):
            errors.append(
                f"Workday pagination stopped after {len(records)} of {total} reported jobs; "
                f"crawler.max_pages={max_pages}"
            )
        body = json.dumps(
            {
                "workday": {
                    "company": config.site,
                    "total": total,
                    "raw_count": len(records),
                    "jobs": records,
                }
            }
        )
        return (
            PageDocument(
                url=config.endpoint_url,
                source_url=source_url,
                html=body,
                text="",
                depth=0,
            ),
            errors,
        )

    def _fetch_oracle_jobs(
        self,
        client: httpx.Client,
        config: OracleConfig,
        source_url: str,
    ) -> tuple[PageDocument | None, list[str]]:
        errors: list[str] = []
        jobs: list[dict] = []
        total = 0
        offset = 0
        endpoint_host = (urlsplit(config.search_url).hostname or "").casefold()
        try:
            ensure_public_http_url(config.search_url)
            if not endpoint_host.endswith(".oraclecloud.com"):
                raise OffsiteRedirectError(
                    "ignored Oracle endpoint outside an oraclecloud.com host"
                )
            if not self._allowed_by_robots(client, config.search_url):
                raise PermissionError("robots.txt disallows the Oracle jobs endpoint")
        except Exception as exc:
            return None, [f"{config.search_url}: Oracle jobs request failed: {exc}"]

        for _ in range(ORACLE_ADAPTIVE_PAGE_CAP):
            params = {
                "onlyData": "true",
                "expand": (
                    "requisitionList.workLocation,requisitionList.otherWorkLocations,"
                    "requisitionList.secondaryLocations"
                ),
                "finder": config.search_finder(limit=ORACLE_PAGE_SIZE, offset=offset),
            }
            try:
                response = self._get_with_decode_retry(
                    client,
                    f"{config.search_url}?{urlencode(params)}",
                )
                response.raise_for_status()
                payload = response.json()
                wrappers = payload.get("items") if isinstance(payload, dict) else None
                wrapper = wrappers[0] if isinstance(wrappers, list) and wrappers else None
                batch = wrapper.get("requisitionList") if isinstance(wrapper, dict) else None
                if not isinstance(batch, list):
                    raise ValueError("Oracle jobs endpoint returned an invalid payload")
                try:
                    reported_total = int(wrapper.get("TotalJobsCount") or 0)
                except (TypeError, ValueError):
                    reported_total = 0
                total = max(total, reported_total, offset + len(batch))
            except Exception as exc:
                errors.append(f"{config.search_url} (offset={offset}): {exc}")
                break

            jobs.extend(job for job in batch if isinstance(job, dict))
            emit_progress(
                self.progress,
                "crawl",
                f"Loading Oracle job listings ({len(jobs)} loaded)",
                completed=min(len(jobs), total),
                total=max(total, len(jobs)),
            )
            if not batch or len(jobs) >= total:
                break
            offset += len(batch)
            if self.config.delay_seconds:
                time.sleep(self.config.delay_seconds)
        else:
            errors.append(
                "Oracle pagination stopped at the provider safety cap of "
                f"{ORACLE_ADAPTIVE_PAGE_CAP} pages."
            )

        if total > len(jobs):
            errors.append(
                f"Oracle pagination stopped after {len(jobs)} of {total} reported jobs."
            )
        body = json.dumps(
            {
                "oracle": {
                    "api_origin": config.api_origin,
                    "public_origin": config.public_origin,
                    "locale": config.locale,
                    "site_number": config.site_name,
                    "company": config.company,
                    "total": total,
                    "raw_count": len(jobs),
                    "jobs": jobs,
                }
            }
        )
        return (
            PageDocument(
                url=config.search_url,
                source_url=source_url,
                html=body,
                text="",
                depth=0,
            ),
            errors,
        )

    def _fetch_eightfold_jobs(
        self,
        client: httpx.Client,
        config: EightfoldConfig,
        source_url: str,
    ) -> tuple[PageDocument | None, list[str]]:
        errors: list[str] = []
        positions: list[dict] = []
        total = 0
        start = 0
        try:
            ensure_public_http_url(config.search_url)
            if not self._same_site(source_url, config.search_url):
                raise OffsiteRedirectError("ignored Eightfold endpoint outside the careers site")
            if not self._allowed_by_robots(client, config.search_url):
                raise PermissionError("robots.txt disallows the Eightfold jobs endpoint")
        except Exception as exc:
            return None, [f"{config.search_url}: Eightfold jobs request failed: {exc}"]

        for _ in range(EIGHTFOLD_ADAPTIVE_PAGE_CAP):
            try:
                response = self._get_with_decode_retry(
                    client,
                    config.search_page_url(start),
                )
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                batch = data.get("positions") if isinstance(data, dict) else None
                if not isinstance(batch, list):
                    raise ValueError("Eightfold search endpoint returned an invalid payload")
                total = max(total, int(data.get("count") or 0), start + len(batch))
            except Exception as exc:
                errors.append(f"{config.search_url} (start={start}): {exc}")
                break
            positions.extend(position for position in batch if isinstance(position, dict))
            emit_progress(
                self.progress,
                "crawl",
                f"Loading Eightfold result cards ({len(positions)} loaded)",
                completed=min(len(positions), total),
                total=max(total, len(positions)),
            )
            if not batch or len(positions) >= total:
                break
            start += len(batch)
            if self.config.delay_seconds:
                time.sleep(self.config.delay_seconds)

        if total > len(positions):
            errors.append(
                f"Eightfold pagination stopped after {len(positions)} of {total} reported jobs."
            )

        detail_failures: list[str] = []

        def load_detail(position: dict) -> tuple[dict, str | None]:
            enriched = dict(position)
            position_id = position.get("id")
            if position_id is None:
                return enriched, "Eightfold returned a position without an id"
            detail_url = f"{config.detail_url}?{urlencode({'position_id': position_id, 'domain': config.group_id, 'hl': 'en'})}"
            last_error: Exception | None = None
            for attempt in range(EIGHTFOLD_DETAIL_MAX_ATTEMPTS):
                try:
                    response = self._get_with_decode_retry(client, detail_url)
                    response.raise_for_status()
                    payload = response.json()
                    detail = payload.get("data") if isinstance(payload, dict) else None
                    if not isinstance(detail, dict):
                        raise ValueError("invalid detail payload")
                    description = str(detail.get("jobDescription") or "").strip()
                    if not description:
                        raise ValueError("detail payload did not contain a job description")
                    enriched.update(detail)
                    return enriched, None
                except Exception as exc:
                    last_error = exc
                    retryable = not isinstance(exc, httpx.HTTPStatusError) or (
                        exc.response.status_code in BROWSER_RECOVERY_STATUS_CODES
                    )
                    if not retryable or attempt + 1 >= EIGHTFOLD_DETAIL_MAX_ATTEMPTS:
                        break
                    delay = max(0.25, self.config.delay_seconds) * (2**attempt)
                    time.sleep(delay)
            return (
                enriched,
                f"{detail_url}: Eightfold job detail request failed after "
                f"{EIGHTFOLD_DETAIL_MAX_ATTEMPTS} attempts: {last_error}",
            )

        enriched_positions: list[dict] = []
        with ThreadPoolExecutor(max_workers=EIGHTFOLD_DETAIL_WORKERS) as executor:
            futures = [executor.submit(load_detail, position) for position in positions]
            for completed, future in enumerate(as_completed(futures), start=1):
                position, error = future.result()
                enriched_positions.append(position)
                if error:
                    detail_failures.append(error)
                emit_progress(
                    self.progress,
                    "crawl",
                    "Loading Eightfold job details",
                    completed=completed,
                    total=len(futures),
                )
        if detail_failures:
            errors.extend(detail_failures[:20])
            if len(detail_failures) > 20:
                errors.append(
                    f"Eightfold detail requests had {len(detail_failures)} failures; "
                    "only the first 20 are shown."
                )

        missing_details = sum(
            not str(position.get("jobDescription") or "").strip()
            for position in enriched_positions
        )
        if missing_details:
            errors.append(
                "Incomplete Eightfold job details: "
                f"{missing_details} of {len(enriched_positions)} jobs have no full "
                "description after retries. These jobs will not be shortlisted."
            )

        body = json.dumps(
            {
                "eightfold": {
                    "origin": config.origin,
                    "company": config.group_id.split(".", 1)[0].title(),
                    "total": total,
                    "raw_count": len(positions),
                    "missing_detail_count": missing_details,
                    "positions": enriched_positions,
                }
            }
        )
        return (
            PageDocument(
                url=config.search_url,
                source_url=source_url,
                html=body,
                text="",
                depth=0,
            ),
            errors,
        )

    def _fetch_amazon_search_pages(
        self,
        client: httpx.Client,
        config: AmazonSearchConfig,
        source_url: str,
        *,
        max_pages: int,
    ) -> tuple[list[PageDocument], list[str]]:
        pages: list[PageDocument] = []
        errors: list[str] = []
        start = 0
        total_hits = 0
        page_size = int(config.request_template.get("size", 100))
        try:
            ensure_public_http_url(config.endpoint_url)
            if not self._same_site(source_url, config.endpoint_url):
                raise OffsiteRedirectError(
                    f"ignored Amazon search endpoint outside the careers site ({config.endpoint_url})"
                )
            if not self._allowed_by_robots(client, config.endpoint_url):
                raise PermissionError("robots.txt disallows the Amazon search endpoint")
        except Exception as exc:
            return [], [f"{config.endpoint_url}: {exc}"]

        for _ in range(max(0, max_pages)):
            payload = amazon_request_payload(config, start=start)
            try:
                response = self._post_amazon_search(client, config, payload)
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if "json" not in content_type:
                    raise UnsupportedContentError(
                        f"unsupported Amazon search content type: {content_type or 'unknown'}"
                    )
                response_payload = response.json()
                if not isinstance(response_payload, dict):
                    raise ValueError("Amazon search returned a non-object JSON response")
                total_hits, returned = amazon_result_counts(response_payload)
                pages.append(
                    PageDocument(
                        url=amazon_result_page_url(config, start=start),
                        source_url=source_url,
                        html=response.text,
                        text="",
                        depth=0,
                    )
                )
            except Exception as exc:
                errors.append(f"{config.endpoint_url} (start={start}): {exc}")
                break

            expected_pages = max(1, math.ceil(total_hits / max(page_size, 1)))
            emit_progress(
                self.progress,
                "crawl",
                f"Loading Amazon job batches ({len(pages)} loaded)",
                completed=len(pages),
                total=min(max_pages, expected_pages),
            )
            next_start = start + returned
            if returned <= 0 or next_start >= total_hits:
                break
            start = next_start
            if self.config.delay_seconds:
                time.sleep(self.config.delay_seconds)

        loaded = sum(amazon_result_counts(json.loads(page.html))[1] for page in pages)
        if total_hits > loaded:
            errors.append(
                f"Amazon pagination stopped after {loaded} of {total_hits} reported jobs; "
                f"crawler.max_pages={self.config.max_pages}"
            )
        return pages, errors

    @staticmethod
    def _post_amazon_search(
        client: httpx.Client,
        config: AmazonSearchConfig,
        payload: dict,
    ) -> httpx.Response:
        headers = {
            "Accept": "application/json",
            "Content-Type": "text/plain;charset=UTF-8",
            "X-Api-Key": config.api_key,
        }
        content = json.dumps(payload, separators=(",", ":"))
        try:
            return client.post(config.endpoint_url, content=content, headers=headers)
        except httpx.DecodingError:
            return client.post(
                config.endpoint_url,
                content=content,
                headers={**headers, "Accept-Encoding": "identity", "Cache-Control": "no-cache"},
            )

    def _fetch_http(
        self,
        client: httpx.Client,
        url: str,
        *,
        allow_offsite_redirect: bool = True,
    ) -> tuple[str, str]:
        current = url
        for _ in range(6):
            ensure_public_http_url(current)
            if not self._allowed_by_robots(client, current):
                raise PermissionError("robots.txt disallows this URL")
            response = self._get_with_decode_retry(client, current)
            challenge = self._access_challenge_provider(
                response.text,
                headers=response.headers,
                status_code=response.status_code,
            )
            if challenge:
                raise AccessChallengeError(
                    f"{challenge} anti-bot challenge blocked access; the careers page "
                    "was not returned"
                )
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise httpx.HTTPError("redirect response did not include a Location header")
                redirected_url = urljoin(current, location)
                if not allow_offsite_redirect and not self._same_site(url, redirected_url):
                    raise OffsiteRedirectError(
                        f"ignored redirect outside the careers site ({redirected_url})"
                    )
                current = redirected_url
                continue
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "html" not in content_type and "text" not in content_type:
                raise UnsupportedContentError(
                    f"unsupported content type: {content_type or 'unknown'}"
                )
            return response.text, str(response.url)
        raise httpx.TooManyRedirects("more than five redirects")

    def _allowed_by_robots(self, client: httpx.Client, url: str) -> bool:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self._robots:
            robots_url = f"{origin}/robots.txt"
            try:
                response = self._get_with_decode_retry(client, robots_url)
                if response.status_code == 200:
                    self._robots[origin] = response.text
                else:
                    self._robots[origin] = None
            except httpx.HTTPError:
                self._robots[origin] = None
        robots_text = self._robots[origin]
        return robots_text is None or self._robots_can_fetch(
            robots_text,
            self.user_agent,
            url,
        )

    @staticmethod
    def _robots_can_fetch(robots_text: str, user_agent: str, url: str) -> bool:
        """Apply longest-match robots rules, including explicit Allow entries."""

        groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        seen_rule = False
        for raw_line in robots_text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            field, value = (part.strip() for part in line.split(":", 1))
            field = field.casefold()
            if field == "user-agent":
                if seen_rule and agents:
                    groups.append((agents, rules))
                    agents, rules, seen_rule = [], [], False
                agents.append(value.casefold())
            elif field in {"allow", "disallow"} and agents:
                rules.append((field == "allow", value))
                seen_rule = True
        if agents:
            groups.append((agents, rules))

        lowered_agent = user_agent.casefold()
        matching: list[tuple[int, list[tuple[bool, str]]]] = []
        for group_agents, group_rules in groups:
            matches = [
                0 if agent == "*" else len(agent)
                for agent in group_agents
                if agent == "*" or agent in lowered_agent
            ]
            if matches:
                matching.append((max(matches), group_rules))
        if not matching:
            return True
        best_agent_length = max(length for length, _ in matching)
        applicable = [
            rule
            for length, group_rules in matching
            if length == best_agent_length
            for rule in group_rules
        ]
        path = urlsplit(url).path or "/"
        matched: list[tuple[int, bool]] = []
        for allowed, pattern in applicable:
            if not pattern:
                continue
            expression = re.escape(pattern).replace(r"\*", ".*")
            if expression.endswith(r"\$"):
                expression = expression[:-2] + "$"
            if re.match(expression, path):
                matched.append((len(pattern.replace("*", "")), allowed))
        if not matched:
            return True
        longest = max(length for length, _ in matched)
        return any(allowed for length, allowed in matched if length == longest)

    @staticmethod
    def _get_with_decode_retry(client: httpx.Client, url: str) -> httpx.Response:
        """Retry a malformed compressed response without content encoding."""

        try:
            return client.get(url)
        except httpx.DecodingError:
            return client.get(
                url,
                headers={
                    "Accept-Encoding": "identity",
                    "Cache-Control": "no-cache",
                },
            )

    @staticmethod
    def _visible_text(soup: BeautifulSoup) -> str:
        copy = BeautifulSoup(str(soup), "lxml")
        for element in copy(["script", "style", "noscript", "svg"]):
            element.decompose()
        return "\n".join(copy.stripped_strings)

    @staticmethod
    def _needs_browser(html: str, text: str) -> bool:
        scripts = html.lower().count("<script")
        return len(text) < 500 and scripts >= 3

    @staticmethod
    def _access_challenge_provider(
        html: str,
        *,
        headers: httpx.Headers | dict[str, str] | None = None,
        status_code: int | None = None,
    ) -> str | None:
        """Identify challenge/interstitial HTML that must not be treated as job content."""

        lowered = html[:100_000].casefold()
        normalized_headers = {
            str(key).casefold(): str(value).casefold()
            for key, value in (headers.items() if headers is not None else [])
        }
        cloudflare_markers = (
            "cf-chl-",
            "/cdn-cgi/challenge-platform/",
            "enable javascript and cookies to continue",
            "attention required! | cloudflare",
            "cloudflare ray id",
        )
        cloudflare_header = (
            "cf-ray" in normalized_headers
            or normalized_headers.get("cf-mitigated") == "challenge"
        )
        challenge_title = "<title>just a moment...</title>" in re.sub(
            r"\s+", " ", lowered
        )
        if (
            any(marker in lowered for marker in cloudflare_markers)
            or cloudflare_header and (status_code or 0) in {403, 429, 503}
            or challenge_title and "cloudflare" in lowered
        ):
            return "Cloudflare"
        return None

    @staticmethod
    def _should_try_browser(exc: Exception) -> bool:
        if isinstance(exc, (AccessChallengeError, httpx.DecodingError, httpx.RequestError)):
            return True
        return (
            isinstance(exc, httpx.HTTPStatusError)
            and exc.response.status_code in BROWSER_RECOVERY_STATUS_CODES
        )

    def _fetch_playwright(
        self,
        url: str,
        *,
        allowed_site_url: str | None = None,
    ) -> tuple[str, str] | None:
        try:
            from playwright.sync_api import Error, sync_playwright

            configure_playwright_browser_path()
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(user_agent=self.user_agent)
                response = page.goto(
                    url,
                    wait_until="networkidle",
                    timeout=int(self.config.request_timeout_seconds * 1000),
                )
                if response is not None and response.status >= 400:
                    browser.close()
                    return None
                final_url = page.url
                ensure_public_http_url(final_url)
                if allowed_site_url and not self._same_site(allowed_site_url, final_url):
                    browser.close()
                    return None
                html = page.content()
                browser.close()
                return html, final_url
        except (ImportError, Error, UnsafeUrlError):
            return None

    def _fetch_successfactors_pages(
        self,
        config: SuccessFactorsConfig,
        source_url: str,
        *,
        max_pages: int,
    ) -> tuple[list[PageDocument], list[str]]:
        pages: list[PageDocument] = []
        errors: list[str] = []
        try:
            ensure_public_http_url(config.search_url)
            from playwright.sync_api import Error, sync_playwright

            configure_playwright_browser_path()
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(user_agent=self.user_agent)
                try:
                    total_pages = max_pages
                    base_search_url = config.search_url
                    for index in range(max_pages):
                        target_url = self._successfactors_page_url(base_search_url, index)
                        page.goto(
                            target_url,
                            wait_until="domcontentloaded",
                            timeout=int(self.config.request_timeout_seconds * 1000),
                        )
                        page.locator('[data-testid="jobCard"]').first.wait_for(
                            timeout=int(self.config.request_timeout_seconds * 1000)
                        )
                        if index == 0:
                            base_search_url = page.url
                            first_label = (
                                page.locator('[aria-label*="job posting"]').first.get_attribute(
                                    "aria-label"
                                )
                                or ""
                            )
                            total_match = re.search(
                                r"\bof\s+(\d[\d,]*)\b",
                                first_label,
                                re.IGNORECASE,
                            )
                            cards_per_page = page.locator('[data-testid="jobCard"]').count()
                            if total_match and cards_per_page:
                                total_jobs = int(total_match.group(1).replace(",", ""))
                                total_pages = min(
                                    max_pages,
                                    math.ceil(total_jobs / cards_per_page),
                                )
                        html = page.content()
                        soup = BeautifulSoup(html, "lxml")
                        text = self._visible_text(soup)
                        pages.append(
                            PageDocument(
                                url=page.url,
                                source_url=source_url,
                                html=html,
                                text=text,
                                depth=0,
                            )
                        )
                        emit_progress(
                            self.progress,
                            "crawl",
                            "Loading SuccessFactors result pages",
                            completed=index + 1,
                            total=total_pages,
                        )
                        if index + 1 >= total_pages:
                            break
                        if self.config.delay_seconds:
                            page.wait_for_timeout(int(self.config.delay_seconds * 1000))
                finally:
                    browser.close()
        except (ImportError, Error, UnsafeUrlError) as exc:
            errors.append(f"SuccessFactors scan failed for {config.search_url}: {exc}")
        return pages, errors

    @staticmethod
    def _successfactors_page_url(search_url: str, page_number: int) -> str:
        parsed = urlsplit(search_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["searchResultView"] = "LIST"
        if page_number:
            query["pageNumber"] = str(page_number)
        else:
            query.pop("pageNumber", None)
        return urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
        )

    @staticmethod
    def _candidate_links(soup: BeautifulSoup, base_url: str) -> list[str]:
        candidates: list[tuple[int, str]] = []
        elements = [(element, "href") for element in soup.find_all("a", href=True)] + [
            (element, "src") for element in soup.find_all("iframe", src=True)
        ]
        # Some careers navigation is serialized as JSON instead of anchor tags.
        # Feed discovered destinations through the same URL and site checks below.
        for script in soup.find_all("script"):
            for match in re.finditer(
                r'"(?:url|href)"\s*:\s*("(?:[^"\\]|\\.)*")',
                script.string or script.get_text(),
            ):
                try:
                    href = json.loads(match.group(1))
                except ValueError:
                    continue
                anchor = soup.new_tag("a", href=href)
                elements.append((anchor, "href"))
        for element, attribute in elements:
            href = str(element.get(attribute) or "").strip()
            label = " ".join(
                filter(
                    None,
                    (
                        " ".join(element.stripped_strings),
                        str(element.get("title") or ""),
                        str(element.get("id") or ""),
                    ),
                )
            )
            haystack = f"{href} {label}".lower()
            if any(term in haystack for term in SKIP_LINK_HINTS):
                continue
            href_lower = href.lower()
            if any(term in href_lower for term in SKIP_URL_HINTS):
                continue
            if any(marker in href_lower for marker in UNRESOLVED_TEMPLATE_MARKERS):
                continue
            score = sum(term in haystack for term in JOB_LINK_HINTS)
            if score == 0:
                continue
            absolute = urldefrag(urljoin(base_url, href))[0]
            try:
                ensure_public_http_url(absolute, resolve_dns=False)
            except UnsafeUrlError:
                continue
            parsed = urlparse(absolute)
            if parsed.path.lower().endswith(SKIP_FILE_SUFFIXES):
                continue
            if not CareersCrawler._same_site(base_url, absolute) and not CareersCrawler._is_ats_url(
                absolute
            ):
                continue
            candidates.append((score, absolute))
        candidates.sort(key=lambda item: (-item[0], item[1]))
        return list(dict.fromkeys(url for _, url in candidates))

    @staticmethod
    def _site_key(url: str) -> str:
        hostname = (urlparse(url).hostname or "").lower().rstrip(".")
        labels = hostname.split(".")
        if len(labels) <= 2:
            return hostname
        if len(labels[-1]) == 2 and labels[-2] in COMMON_COUNTRY_SECOND_LEVEL_DOMAINS:
            return ".".join(labels[-3:])
        return ".".join(labels[-2:])

    @staticmethod
    def _same_site(first_url: str, second_url: str) -> bool:
        return CareersCrawler._site_key(first_url) == CareersCrawler._site_key(second_url)

    @staticmethod
    def _is_ats_url(url: str) -> bool:
        hostname = (urlparse(url).hostname or "").lower().rstrip(".")
        return any(
            hostname == domain or hostname.endswith(f".{domain}") for domain in KNOWN_ATS_DOMAINS
        )

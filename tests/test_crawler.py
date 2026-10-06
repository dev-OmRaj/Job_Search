import json

import httpx
import pytest
from bs4 import BeautifulSoup

from job_search_agent import crawler as crawler_module
from job_search_agent.adapters.amazon import extract_amazon_jobs
from job_search_agent.adapters.ashby import AshbyConfig
from job_search_agent.adapters.atlassian import AtlassianConfig
from job_search_agent.adapters.eightfold import EightfoldConfig, extract_eightfold_jobs
from job_search_agent.adapters.eurofins import EurofinsConfig
from job_search_agent.adapters.greenhouse import GreenhouseJobsConfig
from job_search_agent.adapters.workday import WorkdayConfig
from job_search_agent.crawler import AccessChallengeError, CareersCrawler
from job_search_agent.models import CrawlerConfig, PageDocument


class DecodeRetryClient:
    def __init__(self):
        self.calls = []

    def get(self, url, *, headers=None):
        self.calls.append((url, headers))
        if len(self.calls) == 1:
            raise httpx.DecodingError("cannot use a decompressobj multiple times")
        return httpx.Response(200, request=httpx.Request("GET", url), text="recovered")


def test_decode_failure_retries_with_uncompressed_response():
    client = DecodeRetryClient()

    response = CareersCrawler._get_with_decode_retry(client, "https://example.com/jobs")

    assert response.text == "recovered"
    assert len(client.calls) == 2
    assert client.calls[1][1] == {
        "Accept-Encoding": "identity",
        "Cache-Control": "no-cache",
    }


def test_decode_failure_falls_back_to_browser(monkeypatch):
    crawler = CareersCrawler(
        CrawlerConfig(max_pages=1, max_depth=0, delay_seconds=0),
        user_agent="test-agent",
    )

    monkeypatch.setattr(
        crawler_module,
        "ensure_public_http_url",
        lambda url, **_kwargs: url,
    )

    def fail_http(_client, _url, **_kwargs):
        raise httpx.DecodingError("cannot use a decompressobj multiple times")

    monkeypatch.setattr(crawler, "_fetch_http", fail_http)
    monkeypatch.setattr(
        crawler,
        "_fetch_playwright",
        lambda url, **_kwargs: ("<html><body>Recovered jobs page</body></html>", url),
    )

    report = crawler.crawl("https://example.com/jobs")

    assert report.errors == []
    assert len(report.pages) == 1
    assert report.pages[0].text == "Recovered jobs page"


def test_forbidden_http_page_falls_back_to_browser(monkeypatch):
    crawler = CareersCrawler(
        CrawlerConfig(max_pages=1, max_depth=0, delay_seconds=0),
        user_agent="test-agent",
    )
    monkeypatch.setattr(
        crawler_module,
        "ensure_public_http_url",
        lambda url, **_kwargs: url,
    )

    def forbid_http(_client, _url, **_kwargs):
        request = httpx.Request("GET", "https://example.com/jobs")
        response = httpx.Response(403, request=request)
        response.raise_for_status()

    monkeypatch.setattr(crawler, "_fetch_http", forbid_http)
    monkeypatch.setattr(
        crawler,
        "_fetch_playwright",
        lambda url, **_kwargs: ("<html><body>Browser-accessible jobs</body></html>", url),
    )

    report = crawler.crawl("https://example.com/jobs")

    assert report.errors == []
    assert report.pages[0].text == "Browser-accessible jobs"


def test_detects_cloudflare_challenge_page():
    html = """
    <html><head><title>Just a moment...</title></head>
    <body><script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>
    Enable JavaScript and cookies to continue</body></html>
    """

    assert CareersCrawler._access_challenge_provider(html) == "Cloudflare"


def test_crawler_can_recover_after_repeated_challenges_on_same_host(monkeypatch):
    crawler = CareersCrawler(CrawlerConfig(max_pages=100, delay_seconds=0), user_agent="test")
    monkeypatch.setattr(crawler_module, "ensure_public_http_url", lambda url, **kwargs: url)
    calls = []
    browsers = []

    def fetch(_client, url, **kwargs):
        calls.append(url)
        if url.endswith(("/jobs", "/6")):
            return "<html><body>Public job directory</body></html>", url
        raise AccessChallengeError("Cloudflare anti-bot challenge blocked access")

    monkeypatch.setattr(crawler, "_fetch_http", fetch)
    monkeypatch.setattr(crawler, "_fetch_playwright", lambda url, **kwargs: browsers.append(url))
    monkeypatch.setattr(crawler, "_needs_browser", lambda *args: False)
    monkeypatch.setattr(crawler, "_candidate_links", lambda *args: [
        f"https://example.com/jobs/{i}" for i in range(1, 7)
    ])
    report = crawler.crawl("https://example.com/jobs")

    assert len(report.pages) == 2
    assert len(calls) == 7
    assert len(browsers) == 5
    assert len(report.errors) == 5
    assert calls[-1].endswith("/6")


def test_crawl_limit_counts_failed_attempts(monkeypatch):
    crawler = CareersCrawler(CrawlerConfig(max_pages=3, delay_seconds=0), user_agent="test")
    monkeypatch.setattr(crawler_module, "ensure_public_http_url", lambda url, **kwargs: url)
    calls = []

    def fetch(_client, url, **kwargs):
        calls.append(url)
        if url.endswith("/jobs"):
            return "<html><body>Job directory</body></html>", url
        raise ValueError("Unavailable page")

    monkeypatch.setattr(crawler, "_fetch_http", fetch)
    monkeypatch.setattr(crawler, "_needs_browser", lambda *args: False)
    monkeypatch.setattr(crawler, "_candidate_links", lambda *args: [
        f"https://example.com/jobs/{i}" for i in range(20)
    ])
    report = crawler.crawl("https://example.com/jobs")
    assert len(calls) == 3
    assert len(report.pages) == 1
    assert "Crawl limit reached" in report.errors[-1]


def test_seed_cloudflare_challenge_fails_instead_of_returning_zero_pages(monkeypatch):
    crawler = CareersCrawler(
        CrawlerConfig(max_pages=1, max_depth=0, delay_seconds=0),
        user_agent="test-agent",
    )
    monkeypatch.setattr(crawler_module, "ensure_public_http_url", lambda url, **_kwargs: url)

    def challenge_http(_client, _url, **_kwargs):
        raise AccessChallengeError("Cloudflare anti-bot challenge blocked access")

    monkeypatch.setattr(crawler, "_fetch_http", challenge_http)
    monkeypatch.setattr(crawler, "_fetch_playwright", lambda _url, **_kwargs: None)

    with pytest.raises(AccessChallengeError, match="Cloudflare anti-bot challenge"):
        crawler.crawl("https://example.com/careers")


def test_candidate_links_ignore_external_noise_and_downloads():
    soup = BeautifulSoup(
        """
        <a href="https://www.amazon.jobs/en/jobs/123/data-scientist">Job details</a>
        <a href="https://passport.amazon.jobs/createaccount">Amazon Jobs account</a>
        <a href="https://www.linkedin.com/showcase/amazon-careers">Amazon careers</a>
        <a href="https://jobs.amazon.in">Amazon India jobs</a>
        <a href="https://cdn.cms.amazon.jobs/transcript.docx">Job transcript</a>
        <a href="/gb/en/job-human-health/${jobId}/${title}">Job template</a>
        <a href="https://company.wd5.myworkdayjobs.com/jobs">External jobs portal</a>
        <iframe title="Current job openings" src="greenhouse-embed-local.html"></iframe>
        """,
        "lxml",
    )

    links = CareersCrawler._candidate_links(soup, "https://www.amazon.jobs/en/teams/data-science")

    assert set(links) == {
        "https://www.amazon.jobs/en/jobs/123/data-scientist",
        "https://company.wd5.myworkdayjobs.com/jobs",
        "https://www.amazon.jobs/en/teams/greenhouse-embed-local.html",
    }


def test_candidate_links_find_embedded_navigation_without_external_noise():
    soup = BeautifulSoup('''<script>window.navigation = {
        "url": "https://example.wd12.myworkdayjobs.com/en-US/External",
        "other": {"href": "https://unrelated.example/jobs"},
        "private": {"url": "http://127.0.0.1/jobs"}
    };</script>''', "lxml")
    assert CareersCrawler._candidate_links(soup, "https://example.com/careers") == [
        "https://example.wd12.myworkdayjobs.com/en-US/External"
    ]


def test_salesforce_original_page_uses_its_own_feeds(monkeypatch):
    source = "https://www.salesforce.com/company/careers/jobs/"
    crawler = CareersCrawler(CrawlerConfig(max_pages=1, delay_seconds=0), user_agent="test")
    monkeypatch.setattr(crawler_module, "ensure_public_http_url", lambda url, **kwargs: url)
    visited = []

    def fetch(client, url, **kwargs):
        visited.append(url)
        return '<script src="https://c.salesforce.com/digital/xsf/components/v1-stable/careers.js"></script>', url

    monkeypatch.setattr(crawler, "_fetch_http", fetch)
    monkeypatch.setattr(crawler, "_allowed_by_robots", lambda client, url: True)
    monkeypatch.setattr(httpx.Client, "post", lambda *args, **kwargs: pytest.fail("Should not call Workday"))
    monkeypatch.setattr(crawler, "_get_with_decode_retry", lambda client, url: httpx.Response(
        200, request=httpx.Request("GET", url), json={"Total_Jobs": 1, "Report_Entry": [{
            "Job_Requisition_Ref_ID": "JR123", "Job_Posting_Title": "Software Engineer",
            "Job_Description": "Build software in India.",
            "Job_Requisition_Primary_Location": "India - Bangalore",
        }]}
    ))
    report = crawler.crawl(source)
    assert visited == [source]
    assert len(report.pages) == 1
    assert str(report.pages[0].source_url) == source
    assert json.loads(report.pages[0].html)["salesforce"]["total"] == 1
    assert report.errors == []


def test_country_aware_site_boundary():
    assert CareersCrawler._same_site(
        "https://careers.example.co.uk/jobs",
        "https://jobs.example.co.uk/123",
    )
    assert not CareersCrawler._same_site(
        "https://jobs.amazon.jobs/123",
        "https://jobs.amazon.in/123",
    )


def test_robots_allow_rule_overrides_less_specific_disallow():
    robots = """
    User-agent: *
    Disallow: /
    Allow: /careers
    Allow: /careers/
    """

    assert CareersCrawler._robots_can_fetch(
        robots,
        "JobSearchAgent/0.1",
        "https://example.com/careers/",
    )
    assert CareersCrawler._robots_can_fetch(
        robots,
        "JobSearchAgent/0.1",
        "https://example.com/careers/api/embedjobs/default/active/id",
    )
    assert not CareersCrawler._robots_can_fetch(
        robots,
        "JobSearchAgent/0.1",
        "https://example.com/ats/documents/private.html",
    )


def test_phenom_pagination_does_not_consume_crawl_depth(monkeypatch):
    first_url = "https://jobs.example.com/gb/en/c/data-jobs"
    second_url = f"{first_url}?from=10&s=1"
    first_html = f"""
    <html><head><link rel="next" href="{second_url}"></head><body>Jobs
    <script>phApp.ddo = {{"eagerLoadRefineSearch": {{"totalHits": 11,
    "data": {{"jobs": [{{"title": "First"}}]}}}}}};</script></body></html>
    """
    second_html = """
    <html><body>Jobs
    <script>phApp.ddo = {"eagerLoadRefineSearch": {"totalHits": 11,
    "data": {"jobs": [{"title": "Second"}]}}};</script></body></html>
    """
    crawler = CareersCrawler(
        CrawlerConfig(max_pages=5, max_depth=0, delay_seconds=0),
        user_agent="test-agent",
    )
    monkeypatch.setattr(
        crawler_module,
        "ensure_public_http_url",
        lambda url, **_kwargs: url,
    )
    offsite_permissions = []

    def fetch_page(_client, url, *, allow_offsite_redirect):
        offsite_permissions.append(allow_offsite_redirect)
        return (first_html if url == first_url else second_html), url

    monkeypatch.setattr(crawler, "_fetch_http", fetch_page)

    report = crawler.crawl(first_url)

    assert [str(page.url) for page in report.pages] == [first_url, second_url]
    assert offsite_permissions == [True, False]
    assert report.errors == []


def test_phenom_pagination_uses_embedded_result_counts_without_next_link(monkeypatch):
    first_url = "https://careers.example.com/global/en/search-results"
    second_url = f"{first_url}?from=10&s=1"
    pages = {
        first_url: """
        <script>phApp.ddo = {"eagerLoadRefineSearch": {"hits": 10, "totalHits": 15,
        "data": {"jobs": [{"title": "First"}]}}};</script>
        """,
        second_url: """
        <script>phApp.ddo = {"eagerLoadRefineSearch": {"hits": 5, "totalHits": 15,
        "data": {"jobs": [{"title": "Last"}]}}};</script>
        """,
    }
    crawler = CareersCrawler(
        CrawlerConfig(max_pages=5, max_depth=0, delay_seconds=0),
        user_agent="test-agent",
    )
    monkeypatch.setattr(crawler_module, "ensure_public_http_url", lambda url, **_kwargs: url)
    monkeypatch.setattr(
        crawler,
        "_fetch_http",
        lambda _client, url, **_kwargs: (
            pages[
                first_url
                if "from=" not in url
                else f"{first_url}?from={url.split('from=', 1)[1].split('&', 1)[0]}&s=1"
            ],
            url,
        ),
    )

    report = crawler.crawl(first_url)

    assert [str(page.url) for page in report.pages] == [first_url, second_url]
    assert report.errors == []


def test_phenom_adaptive_pagination_can_exceed_generic_page_limit(monkeypatch):
    first_url = "https://careers.example.com/global/en/search-results"
    pages = {}
    for offset in (0, 10, 20):
        url = first_url if offset == 0 else f"{first_url}?from={offset}&s=1"
        pages[url] = f"""
        <script>phApp.ddo = {{"eagerLoadRefineSearch": {{"hits": 10,
        "totalHits": 30, "data": {{"jobs": [{{"title": "Job {offset}"}}]}}}}}};</script>
        """
    crawler = CareersCrawler(
        CrawlerConfig(max_pages=2, max_depth=0, delay_seconds=0),
        user_agent="test-agent",
    )
    monkeypatch.setattr(crawler_module, "ensure_public_http_url", lambda url, **_kwargs: url)
    monkeypatch.setattr(
        crawler,
        "_fetch_http",
        lambda _client, url, **_kwargs: (
            pages[
                first_url
                if "from=" not in url
                else f"{first_url}?from={url.split('from=', 1)[1].split('&', 1)[0]}&s=1"
            ],
            url,
        ),
    )

    report = crawler.crawl(first_url)

    assert [str(page.url) for page in report.pages] == [
        first_url,
        f"{first_url}?from=10&s=1",
        f"{first_url}?s=1&from=20",
    ]
    assert report.errors == []


def test_amazon_adapter_fetches_all_api_batches(monkeypatch):
    source_url = "https://amazon.jobs/content/en/job-categories/data-science"
    cms_data = {
        "runtimeConfig": {
            "searchEndpoint": "/api/jobs/search",
            "searchKey": "public-search-key",
        },
        "props": {
            "pageProps": {
                "content": {
                    "locale": "en",
                    "renderSearch": True,
                    "searchFilters": [{"jobCategories": ["Data Science"]}],
                }
            }
        },
    }
    landing_html = (
        f'<script id="jobs-cms-next-data" type="application/json">{json.dumps(cms_data)}</script>'
    )
    crawler = CareersCrawler(
        CrawlerConfig(max_pages=5, max_depth=0, delay_seconds=0),
        user_agent="test-agent",
    )
    monkeypatch.setattr(crawler_module, "ensure_public_http_url", lambda url, **_kwargs: url)
    monkeypatch.setattr(
        crawler,
        "_fetch_http",
        lambda _client, url, **_kwargs: (landing_html, url),
    )
    monkeypatch.setattr(crawler, "_allowed_by_robots", lambda _client, _url: True)
    starts = []

    def post_search(_client, config, payload):
        start = payload["start"]
        starts.append(start)
        count = 100 if start == 0 else 50
        search_hits = [
            {
                "fields": {
                    "title": [f"Data Scientist {index}"],
                    "icimsJobId": [str(index)],
                    "location": ["IN, KA, Bengaluru"],
                }
            }
            for index in range(start, start + count)
        ]
        return httpx.Response(
            200,
            request=httpx.Request("POST", config.endpoint_url),
            json={"found": 150, "start": start, "searchHits": search_hits},
        )

    monkeypatch.setattr(crawler, "_post_amazon_search", post_search)

    report = crawler.crawl(source_url)
    jobs = [job for page in report.pages for job in extract_amazon_jobs(page).jobs]

    assert starts == [0, 100]
    assert len(report.pages) == 2
    assert len(jobs) == 150
    assert report.errors == []


def test_greenhouse_mirror_is_fetched_as_structured_job_data(monkeypatch):
    source_url = "https://example.com/greenhouse-embed-local.html"
    landing_html = """
    <div id="gh-board"></div>
    <script src="javascripts/greenhouse-embed-local.js"></script>
    """
    crawler = CareersCrawler(
        CrawlerConfig(max_pages=2, max_depth=0, delay_seconds=0),
        user_agent="test-agent",
    )
    monkeypatch.setattr(crawler_module, "ensure_public_http_url", lambda url, **_kwargs: url)
    monkeypatch.setattr(
        crawler,
        "_fetch_http",
        lambda _client, url, **_kwargs: (landing_html, url),
    )
    received_configs = []

    def fetch_greenhouse(_client, config: GreenhouseJobsConfig, original_url):
        received_configs.append(config)
        return (
            PageDocument(
                url=config.endpoint_url,
                source_url=original_url,
                html='{"board": {}, "jobs": []}',
                text="",
            ),
            [],
        )

    monkeypatch.setattr(crawler, "_fetch_greenhouse_jobs", fetch_greenhouse)

    report = crawler.crawl(source_url)

    assert len(received_configs) == 1
    assert received_configs[0].source_kind == "local_mirror"
    assert str(report.pages[0].url) == "https://example.com/data/greenhouse_jobs.json"
    assert report.errors == []


def test_workday_portal_is_fetched_as_structured_job_data(monkeypatch):
    source_url = "https://visa.wd5.myworkdayjobs.com/Visa"
    crawler = CareersCrawler(
        CrawlerConfig(max_pages=3, max_depth=0, delay_seconds=0),
        user_agent="test-agent",
    )
    monkeypatch.setattr(crawler_module, "ensure_public_http_url", lambda url, **_kwargs: url)
    monkeypatch.setattr(crawler, "_fetch_http", lambda _client, url, **_kwargs: ("<html></html>", url))
    received = []

    def fetch_workday(_client, config: WorkdayConfig, original_url, *, max_pages, portal_url=None):
        received.append((config, original_url, max_pages))
        return (
            PageDocument(
                url=config.endpoint_url,
                source_url=original_url,
                html='{"workday":{"total":0,"raw_count":0,"jobs":[]}}',
                text="",
            ),
            [],
        )

    monkeypatch.setattr(crawler, "_fetch_workday_jobs", fetch_workday)

    report = crawler.crawl(source_url)

    assert len(received) == 1
    assert received[0][0].tenant == "visa"
    assert received[0][0].site == "Visa"
    assert received[0][2] == 3
    assert str(report.pages[0].url).endswith("/wday/cxs/visa/Visa/jobs")
    assert report.errors == []


def test_eightfold_job_details_retry_transient_failures(monkeypatch):
    crawler = CareersCrawler(
        CrawlerConfig(max_pages=2, max_depth=0, delay_seconds=0),
        user_agent="test-agent",
    )
    config = EightfoldConfig(origin="https://jobs.example.com", group_id="example.com")
    detail_attempts = 0

    class Client:
        def get(self, url, *, headers=None):
            nonlocal detail_attempts
            request = httpx.Request("GET", url)
            if "/api/pcsx/search" in url:
                return httpx.Response(
                    200,
                    request=request,
                    json={
                        "data": {
                            "count": 1,
                            "positions": [
                                {
                                    "id": 123,
                                    "name": "Graduate Engineer",
                                    "positionUrl": "/careers/job/123",
                                }
                            ],
                        }
                    },
                )
            detail_attempts += 1
            if detail_attempts == 1:
                return httpx.Response(429, request=request)
            if detail_attempts == 2:
                raise httpx.ReadError("connection reset", request=request)
            return httpx.Response(
                200,
                request=request,
                json={"data": {"jobDescription": "<p>Build reliable systems.</p>"}},
            )

    monkeypatch.setattr(crawler, "_allowed_by_robots", lambda _client, _url: True)
    monkeypatch.setattr(crawler_module, "ensure_public_http_url", lambda url, **_kwargs: url)
    monkeypatch.setattr(crawler_module.time, "sleep", lambda _seconds: None)

    page, errors = crawler._fetch_eightfold_jobs(
        Client(), config, "https://jobs.example.com/careers"
    )

    assert page is not None
    assert errors == []
    assert detail_attempts == 3
    extraction = extract_eightfold_jobs(page)
    assert extraction is not None
    assert extraction.missing_detail_count == 0
    assert extraction.jobs[0].description == "Build reliable systems."


def test_eightfold_missing_details_are_reported_and_marked_incomplete(monkeypatch):
    crawler = CareersCrawler(
        CrawlerConfig(max_pages=2, max_depth=0, delay_seconds=0),
        user_agent="test-agent",
    )
    config = EightfoldConfig(origin="https://jobs.example.com", group_id="example.com")

    class Client:
        def get(self, url, *, headers=None):
            request = httpx.Request("GET", url)
            if "/api/pcsx/search" in url:
                return httpx.Response(
                    200,
                    request=request,
                    json={
                        "data": {
                            "count": 1,
                            "positions": [
                                {
                                    "id": 123,
                                    "name": "Graduate Engineer",
                                    "positionUrl": "/careers/job/123",
                                }
                            ],
                        }
                    },
                )
            return httpx.Response(503, request=request)

    monkeypatch.setattr(crawler, "_allowed_by_robots", lambda _client, _url: True)
    monkeypatch.setattr(crawler_module, "ensure_public_http_url", lambda url, **_kwargs: url)
    monkeypatch.setattr(crawler_module.time, "sleep", lambda _seconds: None)

    page, errors = crawler._fetch_eightfold_jobs(
        Client(), config, "https://jobs.example.com/careers"
    )

    assert page is not None
    assert any("failed after 4 attempts" in error for error in errors)
    assert any("will not be shortlisted" in error for error in errors)
    extraction = extract_eightfold_jobs(page)
    assert extraction is not None
    assert extraction.missing_detail_count == 1
    assert extraction.jobs[0].description_complete is False


def test_embedded_ashby_job_url_fetches_full_board_without_loading_blocked_page(monkeypatch):
    source_url = (
        "https://lgads.tv/careers/"
        "?ashby_jid=02b11bae-83d2-47f5-9ff2-6114f13ee360"
    )
    crawler = CareersCrawler(
        CrawlerConfig(max_pages=2, max_depth=0, delay_seconds=0),
        user_agent="test-agent",
    )
    monkeypatch.setattr(crawler_module, "ensure_public_http_url", lambda url, **_kwargs: url)
    monkeypatch.setattr(
        crawler,
        "_fetch_http",
        lambda *_args, **_kwargs: pytest.fail("blocked marketing page should not be fetched"),
    )
    received = []

    def fetch_ashby(_client, config: AshbyConfig, original_url):
        received.append((config, original_url))
        return (
            PageDocument(
                url=config.endpoint_url,
                source_url=original_url,
                html='{"ashby":{"total":0,"raw_count":0,"jobs":[]}}',
                text="",
            ),
            [],
        )

    monkeypatch.setattr(crawler, "_fetch_ashby_jobs", fetch_ashby)

    report = crawler.crawl(source_url)

    assert received[0][0].board_name == "lgads"
    assert str(report.pages[0].url).endswith("/posting-api/job-board/lgads")
    assert report.errors == []


def test_eurofins_page_uses_structured_feed(monkeypatch):
    source_url = "https://careers.eurofins.com/in/jobs?countryId=in&deptid=2,3,4,5,7"
    landing_html = """
    <input id="apiJobsURL"
           value="https://atsintegration.eurofins.com/ATSWebService.asmx/GetJobs?language=">
    """
    crawler = CareersCrawler(
        CrawlerConfig(max_pages=2, max_depth=0, delay_seconds=0),
        user_agent="test-agent",
    )
    monkeypatch.setattr(crawler_module, "ensure_public_http_url", lambda url, **_kwargs: url)
    monkeypatch.setattr(
        crawler,
        "_fetch_http",
        lambda _client, url, **_kwargs: (landing_html, url),
    )
    received = []

    def fetch_eurofins(_client, config: EurofinsConfig, original_url):
        received.append((config, original_url))
        return (
            PageDocument(
                url=config.endpoint_url,
                source_url=original_url,
                html='{"eurofins":{"total":0,"raw_count":0,"jobs":[]}}',
                text="",
            ),
            [],
        )

    monkeypatch.setattr(crawler, "_fetch_eurofins_jobs", fetch_eurofins)

    report = crawler.crawl(source_url)

    assert received[0][0].filters["countryCode"] == ("in",)
    assert received[0][0].filters["departmentId"] == ("2", "3", "4", "5", "7")
    assert str(report.pages[0].url).startswith("https://atsintegration.eurofins.com/")
    assert report.errors == []


def test_atlassian_page_uses_structured_listings_endpoint(monkeypatch):
    source_url = "https://www.atlassian.com/company/careers/all-jobs?"
    landing_html = '<script type="text/jsx-component">{"type": "Careers"}</script>'
    crawler = CareersCrawler(
        CrawlerConfig(max_pages=2, max_depth=0, delay_seconds=0),
        user_agent="test-agent",
    )
    monkeypatch.setattr(crawler_module, "ensure_public_http_url", lambda url, **_kwargs: url)
    monkeypatch.setattr(
        crawler,
        "_fetch_http",
        lambda _client, url, **_kwargs: (landing_html, url),
    )
    received = []

    def fetch_atlassian(_client, config: AtlassianConfig, original_url):
        received.append((config, original_url))
        return (
            PageDocument(
                url=config.endpoint_url,
                source_url=original_url,
                html='{"atlassian":{"total":0,"raw_count":0,"jobs":[]}}',
                text="",
            ),
            [],
        )

    monkeypatch.setattr(crawler, "_fetch_atlassian_jobs", fetch_atlassian)

    report = crawler.crawl(source_url)

    assert received[0][0].endpoint_url.endswith("/endpoint/careers/listings")
    assert str(report.pages[0].url).endswith("/endpoint/careers/listings")
    assert report.errors == []
